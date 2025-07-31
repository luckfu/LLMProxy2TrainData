import json
import logging
import time
import asyncio
import uuid
from aiohttp import web
import aiohttp
import argparse
from typing import Dict, Any, Optional, Set
import traceback
import sqlite3
import aiosqlite
from utils import format_to_sharegpt, init_async_logger, get_async_logger, init_db_path, get_db_connection, save_conversation_async
import re
from aiohttp.web_middlewares import middleware
from collections import defaultdict, deque
import hashlib
from urllib.parse import urlparse

# 自定义日志过滤器，屏蔽探针请求的日志
class ProbeRequestFilter(logging.Filter):
    """过滤探针请求的日志记录"""
    
    def __init__(self):
        super().__init__()
        # 定义探针请求的特征模式
        self.probe_patterns = [
            r'GET / HTTP',  # 根路径探测
            r'GET /favicon.ico',  # 图标请求
            r'GET /\.well-known/',  # 安全文件探测
            r'GET /locales/',  # 本地化文件探测
            r'UNKNOWN / HTTP',  # 未知协议请求
            r'CensysInspect',  # Censys扫描器
            r'Mozilla/5\.0.*Chrome/90\.0\.4430\.85',  # 特定的探针User-Agent
            r'Go-http-client',  # Go客户端探测
            r'BadHttpMessage',  # HTTP协议错误
            r'BadStatusLine',  # HTTP状态行错误
            r'Invalid method encountered',  # 无效HTTP方法
            r'Pause on PRI/Upgrade',  # HTTP/2升级错误
            r"'NoneType' object is not callable",  # 空对象调用错误
            r'Task exception was never retrieved',  # 异步任务异常
            r'Error handling request',  # 请求处理错误
            r'193\.34\.212\.110',  # 特定的探针IP
            r'185\.191\.127\.222',  # 特定的探针IP
            r'162\.142\.125\.124',  # 特定的探针IP
            r'194\.62\.248\.69',  # 特定的探针IP
            r'209\.38\.219\.203',  # 特定的探针IP
            r'\\x16\\x03\\x01',  # SSL/TLS握手数据
            r'bytearray\(b\'\\x16\\x03\\x01',  # SSL握手字节数组
        ]
        
        # 编译正则表达式以提高性能
        self.compiled_patterns = [re.compile(pattern) for pattern in self.probe_patterns]
    
    def filter(self, record):
        """过滤日志记录"""
        message = record.getMessage()
        
        # 检查是否匹配任何探针模式
        for pattern in self.compiled_patterns:
            if pattern.search(message):
                return False  # 过滤掉这条日志
        
        return True  # 保留这条日志

def parse_args():
    parser = argparse.ArgumentParser(description="动态代理端点服务器")
    parser.add_argument("--port", type=int, default=8080, help="服务器端口")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="日志级别")
    return parser.parse_args()

args = parse_args()

# 配置日志
logging.basicConfig(level=getattr(logging, args.log_level.upper()))
logger = logging.getLogger(__name__)

# 清除现有的处理器
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# 创建探针过滤器
probe_filter = ProbeRequestFilter()

# 为相关的日志器添加过滤器
aiohttp_access_logger = logging.getLogger('aiohttp.access')
aiohttp_server_logger = logging.getLogger('aiohttp.server')
asyncio_logger = logging.getLogger('asyncio')

# 为每个日志器添加过滤器
for log in [aiohttp_access_logger, aiohttp_server_logger, asyncio_logger]:
    log.addFilter(probe_filter)
    # 也为现有的处理器添加过滤器
    for handler in log.handlers:
        handler.addFilter(probe_filter)

# 全局异步异常处理器
def handle_asyncio_exception(loop, context):
    """处理asyncio中未捕获的异常"""
    exception = context.get('exception')
    if exception:
        # 检查是否是探针相关的异常
        error_message = str(exception)
        probe_filter_instance = ProbeRequestFilter()
        
        # 创建一个模拟的日志记录来测试过滤器
        class MockRecord:
            def getMessage(self):
                return error_message
        
        mock_record = MockRecord()
        if not probe_filter_instance.filter(mock_record):
            return  # 如果是探针相关异常，忽略它
        
        logger.error(f"未捕获的asyncio异常: {exception}", exc_info=exception)
    else:
        logger.error(f"未捕获的asyncio错误: {context}")

@middleware
async def probe_request_middleware(request, handler):
    """中间件：过滤探针请求"""
    # 获取客户端IP
    client_ip = request.remote
    if 'X-Forwarded-For' in request.headers:
        client_ip = request.headers['X-Forwarded-For'].split(',')[0].strip()
    elif 'X-Real-IP' in request.headers:
        client_ip = request.headers['X-Real-IP']
    
    # 检查是否为探针请求
    user_agent = request.headers.get('User-Agent', '')
    path = request.path
    method = request.method
    
    # 探针请求特征
    probe_indicators = [
        path in ['/', '/favicon.ico'],
        path.startswith('/.well-known/'),
        path.startswith('/locales/'),
        'CensysInspect' in user_agent,
        'Go-http-client' in user_agent,
        method not in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'],
        # 特定的探针IP地址
        client_ip in ['193.34.212.110', '185.191.127.222', '162.142.125.124', '194.62.248.69', '209.38.219.203']
    ]
    
    if any(probe_indicators):
        # 静默返回404，不记录日志
        return web.Response(status=404, text="Not Found")
    
    # 正常请求，继续处理
    return await handler(request)

class DynamicProxyEndpoint:
    """动态代理端点，无需配置文件"""
    
    def __init__(self, port: int = 8080):
        self.port = port
        self.app = web.Application(middlewares=[probe_request_middleware])
        self.setup_routes()
        
        # 性能优化相关
        self.http_session = None
        self.async_logger = None
        self.conversation_queue = None
        self.batch_size = 10
        self.batch_timeout = 5.0
        
        # 域名白名单和认证映射
        self.allowed_domains = {
            'api.openai.com': {'auth_type': 'openai', 'https': True},
            'api.anthropic.com': {'auth_type': 'anthropic', 'https': True},
            'api.moonshot.cn': {'auth_type': 'anthropic', 'https': True},
            'api.deepseek.com': {'auth_type': 'openai', 'https': True},
            'api.siliconflow.cn': {'auth_type': 'openai', 'https': True},
            'dashscope.aliyuncs.com': {'auth_type': 'openai', 'https': True},
            'models.inference.ai.azure.com': {'auth_type': 'openai', 'https': True},
            'deepsearch.jina.ai': {'auth_type': 'openai', 'https': True},
            # 内网地址
            '36.141.21.137:9081': {'auth_type': 'openai', 'https': False},
            'group.sx.10086.cn': {'auth_type': 'openai', 'https': False},
        }
        
        # 设置应用启动和清理事件
        self.app.on_startup.append(self.init_async_resources)
        self.app.on_cleanup.append(self.cleanup_resources)
    
    def setup_routes(self):
        """设置路由"""
        # 动态代理路由：/{domain}/{path:.*}
        self.app.router.add_post("/{domain}/{path:.*}", self.handle_dynamic_proxy)
        self.app.router.add_get("/health", self.handle_health_check)
        
    async def init_async_resources(self, app):
        """初始化异步资源"""
        # 设置全局异步异常处理器
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(handle_asyncio_exception)
        
        # 初始化异步日志
        await asyncio.to_thread(init_async_logger, "proxy_dynamic", "proxy_dynamic.log", getattr(logging, args.log_level.upper()))
        self.async_logger = get_async_logger()
        if self.async_logger is None:
            raise ValueError("Failed to initialize async_logger")
        await self.async_logger.info("✅ 异步日志初始化完成")
        
        # 初始化数据库
        await init_db_path("interactions.db")
        await self.async_logger.info("✅ 数据库初始化完成")
        
        # 初始化HTTP连接池
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=30,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=30,
            enable_cleanup_closed=True
        )
        
        timeout = aiohttp.ClientTimeout(
            total=600,
            connect=30,
            sock_connect=30,
            sock_read=600
        )
        
        self.http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={'User-Agent': 'DynamicProxy/1.0'}
        )
        
        await self.async_logger.info("✅ HTTP连接池初始化完成")
        
        # 初始化批量处理队列
        self.conversation_queue = asyncio.Queue(maxsize=1000)
        asyncio.create_task(self._batch_save_conversations())
        await self.async_logger.info("✅ 批量处理队列初始化完成")
        
        await self.async_logger.info("🚀 动态代理服务器启动完成")
    
    async def cleanup_resources(self, app):
        """清理资源"""
        if self.http_session:
            await self.http_session.close()
        if self.async_logger:
            await self.async_logger.info("🔄 资源清理完成")
    
    def detect_auth_type_from_path(self, path: str) -> str:
        """根据路径模式识别认证类型"""
        # 方案B: 路径模式识别
        if "/anthropic/" in path or "/v1/messages" in path:
            return "anthropic"
        elif "/v1/chat/completions" in path or "/chat/completions" in path:
            return "openai"
        elif "/v1/embeddings" in path:
            return "openai"
        elif "/v1/rerank" in path:
            return "openai"
        else:
            # 默认使用openai格式
            return "openai"
    
    def prepare_auth_headers(self, request_headers: Dict[str, str], auth_type: str) -> Dict[str, str]:
        """根据认证类型准备请求头"""
        base_headers = {"Content-Type": "application/json"}
        
        if auth_type == "anthropic":
            # Anthropic认证处理
            auth_header = request_headers.get("Authorization", "")
            api_key = ""
            if auth_header.startswith("Bearer "):
                api_key = auth_header.replace("Bearer ", "").strip()
            elif request_headers.get("x-api-key"):
                api_key = request_headers.get("x-api-key")
            
            base_headers["Authorization"] = f"Bearer {api_key}"
        else:
            # OpenAI及其他API认证处理
            base_headers["Authorization"] = request_headers.get("Authorization", "")
        
        return base_headers
    
    def is_domain_allowed(self, domain: str) -> bool:
        """检查域名是否在白名单中"""
        return domain in self.allowed_domains
    
    def get_target_url(self, domain: str, path: str) -> str:
        """构建目标URL"""
        domain_config = self.allowed_domains.get(domain, {'https': True})
        protocol = 'https' if domain_config.get('https', True) else 'http'
        return f"{protocol}://{domain}{path}"
    
    async def handle_dynamic_proxy(self, request: web.Request) -> web.StreamResponse:
        """处理动态代理请求"""
        try:
            # 解析URL参数
            domain = request.match_info['domain']
            path = '/' + request.match_info['path']
            
            # 安全检查：验证域名白名单
            if not self.is_domain_allowed(domain):
                await self.async_logger.warning(f"❌ 不允许的域名: {domain}")
                return web.Response(
                    status=403,
                    text=json.dumps({"error": f"域名 {domain} 不在允许列表中"})
                )
            
            # 获取请求数据
            headers = dict(request.headers)
            request_data = await request.json()
            
            # 请求体大小检查
            if not await self._validate_request_size(request_data):
                return web.Response(
                    status=413,
                    text=json.dumps({"error": "请求体过大，请减小输入数据大小或分批处理"})
                )
            
            # 根据路径识别认证类型
            auth_type = self.detect_auth_type_from_path(path)
            
            # 准备认证头
            forward_headers = self.prepare_auth_headers(headers, auth_type)
            
            # 构建目标URL
            target_url = self.get_target_url(domain, path)
            
            # 判断是否为流式请求
            is_stream = request_data.get("stream", False)
            model = request_data.get("model", "unknown")
            
            await self.async_logger.info(
                f"📡 动态代理请求: {domain}{path}, 认证类型: {auth_type}, 流式: {is_stream}, 模型: {model}"
            )
            
            # 发送请求
            async with self.http_session.post(
                target_url,
                headers=forward_headers,
                json=request_data
            ) as resp:
                if is_stream:
                    return await self._handle_stream_response(resp, request, auth_type, model, request_data)
                else:
                    return await self._handle_non_stream_response(resp, auth_type, model, request_data)
        
        except json.JSONDecodeError:
            await self.async_logger.error("❌ 无效的请求数据格式")
            return web.Response(status=400, text=json.dumps({"error": "无效的请求数据格式"}))
        except Exception as e:
            await self.async_logger.error(f"处理动态代理请求时发生错误: {e}\n{traceback.format_exc()}")
            return web.Response(status=500, text=json.dumps({"error": "服务器内部错误"}))
    
    async def _validate_request_size(self, request_data: Dict[str, Any]) -> bool:
        """验证请求体大小"""
        messages = request_data.get("messages", [])
        total_chars = sum(len(str(msg)) for msg in messages)
        max_chars = 8000000
        
        if total_chars > max_chars:
            await self.async_logger.warning(
                f"❌ 请求体过大: {total_chars} 字符，超过限制 {max_chars} 字符"
            )
            return False
        return True
    
    async def _handle_stream_response(self, resp: aiohttp.ClientResponse, request: web.Request,
                                    auth_type: str, model: str, request_data: Dict[str, Any]) -> web.StreamResponse:
        """处理流式响应"""
        response = web.StreamResponse(
            status=resp.status,
            headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'}
        )
        await response.prepare(request)
        
        complete_response = ""
        complete_reasoning = ""
        response_id = None
        
        try:
            async for line in resp.content:
                line_str = line.decode('utf-8').strip()
                if line_str:
                    await response.write(line.rstrip() + b'\n')
                    
                    # 解析响应内容
                    if auth_type == "anthropic":
                        complete_response, response_id, _ = self._parse_anthropic_stream_chunk(
                            line_str, complete_response, response_id
                        )
                    else:
                        complete_response, response_id, complete_reasoning = self._parse_openai_stream_chunk(
                            line_str, complete_response, response_id
                        )
        
        except Exception as e:
            await self.async_logger.error(f"流式响应处理错误: {e}")
        
        finally:
            # 保存对话
            if response_id and complete_response:
                await self._queue_conversation(response_id, model, {
                    'request': request_data,
                    'response': complete_response,
                    'reasoning': complete_reasoning
                })
        
        return response
    
    async def _handle_non_stream_response(self, resp: aiohttp.ClientResponse,
                                        auth_type: str, model: str, request_data: Dict[str, Any]) -> web.Response:
        """处理非流式响应"""
        response_text = await resp.text()
        
        try:
            response_json = json.loads(response_text)
            
            # 解析响应内容
            if auth_type == "anthropic":
                complete_response = self._parse_anthropic_final_response(response_json)
            else:
                complete_response = self._parse_openai_final_response(response_json)
            
            # 保存对话
            response_id = response_json.get('id', str(uuid.uuid4()))
            if complete_response:
                await self._queue_conversation(response_id, model, {
                    'request': request_data,
                    'response': complete_response,
                    'reasoning': ''
                })
        
        except Exception as e:
            await self.async_logger.error(f"解析响应时发生错误: {e}")
        
        return web.Response(
            status=resp.status,
            text=response_text,
            headers={'Content-Type': 'application/json'}
        )
    
    def _parse_openai_stream_chunk(self, line_str: str, complete_response: str, response_id: Optional[str]):
        """解析OpenAI流式响应块"""
        complete_reasoning = ""
        
        if line_str.startswith("data: "):
            if line_str.strip() == "data: [DONE]":
                return complete_response, response_id, complete_reasoning
            
            try:
                json_data = line_str[6:].strip()
                if json_data:
                    json_chunk = json.loads(json_data)
                    if "id" in json_chunk and not response_id:
                        response_id = json_chunk["id"]
                    if "choices" in json_chunk and json_chunk["choices"]:
                        delta = json_chunk["choices"][0].get("delta", {})
                        reasoning = delta.get("reasoning_content")
                        if reasoning is not None:
                            complete_reasoning += reasoning
                        content = delta.get("content")
                        if content is not None:
                            complete_response += content
            except json.JSONDecodeError:
                pass
            except Exception:
                pass
        
        return complete_response, response_id, complete_reasoning
    
    def _parse_anthropic_stream_chunk(self, line_str: str, complete_response: str, response_id: Optional[str]):
        """解析Anthropic流式响应块"""
        if line_str.startswith("data: "):
            if line_str.strip() == "data: [DONE]":
                return complete_response, response_id, ""
            
            try:
                json_chunk = json.loads(line_str[6:])
                
                if json_chunk.get("type") == "message_start" and "message" in json_chunk:
                    message = json_chunk["message"]
                    if "id" in message and not response_id:
                        response_id = message["id"]
                
                elif json_chunk.get("type") == "content_block_delta":
                    delta = json_chunk.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        complete_response += text
                        
            except json.JSONDecodeError:
                pass
        
        return complete_response, response_id, ""
    
    def _parse_openai_final_response(self, response_json: Dict[str, Any]) -> str:
        """解析OpenAI最终响应内容"""
        if "choices" in response_json and response_json["choices"]:
            choice = response_json["choices"][0]
            if isinstance(choice, dict) and "message" in choice and isinstance(choice["message"], dict):
                reasoning_content = choice["message"].get("reasoning_content", "")
                response_content = choice["message"].get("content", "")
                return f"<think>\n{reasoning_content}\n</think>\n\n\n{response_content}" if reasoning_content else response_content
        return ""
    
    def _parse_anthropic_final_response(self, response_json: Dict[str, Any]) -> str:
        """解析Anthropic最终响应内容"""
        content_list = response_json.get("content", [])
        response_content = ""
        
        if isinstance(content_list, list) and content_list:
            for content_item in content_list:
                if isinstance(content_item, dict):
                    if content_item.get("type") == "text":
                        response_content += content_item.get("text", "")
        
        return response_content
    
    async def _queue_conversation(self, id: str, model: str, conversation: dict):
        """将对话加入队列等待批量保存"""
        try:
            conversation_data = {
                'id': id,
                'model': model,
                'conversation': conversation,
                'timestamp': time.time()
            }
            await self.conversation_queue.put(conversation_data)
        except Exception as e:
            await self.async_logger.error(f"加入对话队列失败: {e}")
    
    async def _batch_save_conversations(self):
        """批量保存对话"""
        batch = []
        last_save_time = time.time()
        
        while True:
            try:
                # 等待新的对话数据或超时
                try:
                    conversation_data = await asyncio.wait_for(
                        self.conversation_queue.get(), 
                        timeout=self.batch_timeout
                    )
                    batch.append(conversation_data)
                except asyncio.TimeoutError:
                    pass
                
                current_time = time.time()
                
                # 检查是否需要保存批次
                should_save = (
                    len(batch) >= self.batch_size or
                    (batch and current_time - last_save_time >= self.batch_timeout)
                )
                
                if should_save and batch:
                    await self._save_batch(batch)
                    batch = []
                    last_save_time = current_time
                    
            except Exception as e:
                await self.async_logger.error(f"批量保存对话时发生错误: {e}")
                await asyncio.sleep(1)
    
    async def _save_batch(self, batch):
        """保存一批对话"""
        try:
            for conversation_data in batch:
                # 格式化为ShareGPT格式
                sharegpt_data = format_to_sharegpt(
                    conversation_data['conversation']['request'],
                    conversation_data['conversation']['response'],
                    conversation_data['conversation'].get('reasoning', '')
                )
                
                # 保存到数据库
                await save_conversation_async(
                    conversation_data['id'],
                    conversation_data['model'],
                    json.dumps(sharegpt_data, ensure_ascii=False)
                )
            
            await self.async_logger.info(f"✅ 成功保存 {len(batch)} 条对话")
            
        except Exception as e:
            await self.async_logger.error(f"保存对话批次失败: {e}")
    
    async def handle_health_check(self, request: web.Request) -> web.Response:
        """健康检查端点"""
        return web.Response(
            status=200,
            text=json.dumps({
                "status": "healthy",
                "service": "dynamic-proxy",
                "timestamp": time.time()
            }),
            headers={'Content-Type': 'application/json'}
        )

if __name__ == "__main__":
    args = parse_args()
    proxy = DynamicProxyEndpoint(port=args.port)
    try:
        web.run_app(proxy.app, host="0.0.0.0", port=args.port)
    except Exception as e:
        logger.error(f"启动服务器时发生致命错误: {e}", exc_info=True)
    finally:
        logger.info("服务器已关闭")
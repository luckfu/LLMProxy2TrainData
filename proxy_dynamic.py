import json
import logging
import time
import asyncio
import uuid
from aiohttp import web
import aiohttp
import argparse
from typing import Dict, Any, Optional
import traceback


from utils import format_to_sharegpt, init_async_logger, get_async_logger, init_db_path, get_db_connection, save_conversation_async
import re
from aiohttp.web_middlewares import middleware



import os

# 自定义日志过滤器，屏蔽探针请求的日志
class ProbeRequestFilter(logging.Filter):
    """过滤探针请求的日志记录"""
    
    def __init__(self, config_file: str = None):
        super().__init__()
        
        # 默认探针请求的特征模式
        default_patterns = []
        
        # 默认探针IP地址模式（使用更通用的模式）
        default_probe_ips = []
        
        # 尝试从配置文件加载自定义模式
        self.probe_patterns = default_patterns.copy()
        self.probe_ip_patterns = default_probe_ips.copy()
        
        if config_file:
            self._load_config(config_file)
        
        # 编译正则表达式以提高性能
        all_patterns = self.probe_patterns + self.probe_ip_patterns
        self.compiled_patterns = [re.compile(pattern) for pattern in all_patterns]
    
    def _load_config(self, config_file: str):
        """从配置文件加载自定义过滤模式"""
        try:
            import json
            import os
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                # 加载自定义探针模式
                if 'probe_filter' in config:
                    filter_config = config['probe_filter']
                    
                    # 直接替换：若提供 patterns / ip_patterns，则覆盖默认
                    if 'patterns' in filter_config and isinstance(filter_config['patterns'], list):
                        self.probe_patterns = filter_config['patterns']
                    if 'ip_patterns' in filter_config and isinstance(filter_config['ip_patterns'], list):
                        self.probe_ip_patterns = filter_config['ip_patterns']
                    
                    # 兼容：追加自定义模式
                    if 'custom_patterns' in filter_config:
                        self.probe_patterns.extend(filter_config['custom_patterns'])
                    
                    # 兼容：追加自定义IP模式
                    if 'custom_ip_patterns' in filter_config:
                        self.probe_ip_patterns.extend(filter_config['custom_ip_patterns'])
                    
                    # 兼容：通过disable_*清空默认并采用custom_*
                    if filter_config.get('disable_default_patterns', False):
                        self.probe_patterns = filter_config.get('custom_patterns', [])
                    
                    if filter_config.get('disable_default_ip_patterns', False):
                        self.probe_ip_patterns = filter_config.get('custom_ip_patterns', [])
                        
        except Exception as e:
            # 配置文件加载失败时使用默认配置
            print(f"警告: 无法加载探针过滤器配置文件 {config_file}: {e}")
    
    def add_pattern(self, pattern: str):
        """动态添加过滤模式"""
        try:
            compiled_pattern = re.compile(pattern)
            self.compiled_patterns.append(compiled_pattern)
            self.probe_patterns.append(pattern)
        except re.error as e:
            print(f"警告: 无效的正则表达式模式 '{pattern}': {e}")
    
    def remove_pattern(self, pattern: str):
        """动态移除过滤模式"""
        if pattern in self.probe_patterns:
            self.probe_patterns.remove(pattern)
            # 重新编译所有模式
            all_patterns = self.probe_patterns + self.probe_ip_patterns
            self.compiled_patterns = [re.compile(p) for p in all_patterns]
    
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
# 创建探针过滤器实例，尝试加载配置文件
probe_filter = ProbeRequestFilter("config.json")

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
    
    # 探针请求特征（可配置）
    cfg = request.app.get('config', {}) if hasattr(request, 'app') else {}
    probe_cfg = cfg.get('probe_request', {}) if isinstance(cfg, dict) else {}
    path_blocklist = probe_cfg.get('path_blocklist', ['/', '/favicon.ico'])
    path_prefix_blocklist = probe_cfg.get('path_prefix_blocklist', ['/.well-known/', '/locales/'])
    ua_blocklist = probe_cfg.get('user_agent_substrings', ['CensysInspect', 'Go-http-client'])
    methods_allowed = probe_cfg.get('allowed_methods', ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
    ip_blocklist = probe_cfg.get('ip_blocklist', ['193.34.212.110', '185.191.127.222', '162.142.125.124', '194.62.248.69', '209.38.219.203'])
    
    probe_indicators = [
        (path in path_blocklist) or any(path.startswith(p) for p in path_prefix_blocklist),
        any(s in user_agent for s in ua_blocklist),
        method not in methods_allowed,
        client_ip in ip_blocklist
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
        
        # 加载配置文件（可选）
        self.config = {}
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as f:
                    self.config = json.load(f)
        except Exception:
            self.config = {}
        
        # 性能优化相关
        self.http_session = None
        self.async_logger = None
        self.conversation_queue = None
        self.batch_size = 10
        self.batch_timeout = 5.0
        self.batch_save_task = None  # 添加批量保存任务的引用
        
        # 域名白名单和认证映射
        # 最小白名单（完整列表迁移至 config.json）
        self.allowed_domains = {
            'generativelanguage.googleapis.com': {'auth_type': 'google', 'https': True},
            'api.openai.com': {'auth_type': 'openai', 'https': True}
        }
        
        # 允许用配置覆盖 allowed_domains
        try:
            cfg_domains = self.config.get("allowed_domains") if isinstance(self.config, dict) else None
            if isinstance(cfg_domains, dict):
                self.allowed_domains = cfg_domains
        except Exception:
            pass
        
        # 设置应用启动和清理事件
        self.app.on_startup.append(self.init_async_resources)
        self.app.on_cleanup.append(self.cleanup_resources)
    
    def setup_routes(self):
        """设置路由"""
        # 标准OpenAI API端点路由（优先级更高）
        self.app.router.add_post("/v1/chat/completions", self.handle_openai_api)
        self.app.router.add_post("/v1/completions", self.handle_openai_api)
        self.app.router.add_post("/v1/embeddings", self.handle_openai_api)
        
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
        
        # 将配置注入app，供中间件等使用
        self.app['config'] = getattr(self, 'config', {})
        
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
            total=900,  # 增加总超时时间到15分钟
            connect=60,  # 增加连接超时到60秒
            sock_connect=60,  # 增加socket连接超时到60秒
            sock_read=900  # 增加读取超时到15分钟
        )
        
        self.http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={'User-Agent': 'DynamicProxy/1.0'}
        )
        
        await self.async_logger.info("✅ HTTP连接池初始化完成")
        
        # 初始化批量处理队列
        self.conversation_queue = asyncio.Queue(maxsize=1000)
        self.batch_save_task = asyncio.create_task(self._batch_save_conversations())
        await self.async_logger.info("✅ 批量处理队列初始化完成")
        
        await self.async_logger.info("🚀 动态代理服务器启动完成")
    
    async def cleanup_resources(self, app):
        """清理资源"""
        # 停止批量保存任务并等待完成
        if self.batch_save_task and not self.batch_save_task.done():
            self.batch_save_task.cancel()
            try:
                await self.batch_save_task
            except asyncio.CancelledError:
                pass
            
        # 处理队列中剩余的对话数据
        if self.conversation_queue and not self.conversation_queue.empty():
            remaining_conversations = []
            while not self.conversation_queue.empty():
                try:
                    conversation_data = self.conversation_queue.get_nowait()
                    remaining_conversations.append(conversation_data)
                except asyncio.QueueEmpty:
                    break
            
            # 保存剩余的对话数据
            if remaining_conversations:
                await self._save_batch(remaining_conversations)
                if self.async_logger:
                    await self.async_logger.info(f"💾 保存了 {len(remaining_conversations)} 条剩余对话数据")
        
        # 关闭HTTP会话
        if self.http_session:
            await self.http_session.close()
            
        if self.async_logger:
            await self.async_logger.info("🔄 资源清理完成")
    
    def detect_auth_type_from_path(self, path: str) -> str:
        """根据路径模式识别认证类型"""
        # Google Gemini API 路径模式
        if "/v1beta/models/" in path and ":generateContent" in path:
            return "google"
        # Anthropic API 路径模式
        elif "/anthropic/" in path or "/v1/messages" in path:
            return "anthropic"
        # OpenAI API 路径模式
        elif "/v1/chat/completions" in path or "/chat/completions" in path:
            return "openai"
        elif "/v1/embeddings" in path:
            return "openai"
        elif "/v1/rerank" in path:
            return "openai"
        else:
            # 默认使用openai格式
            return "openai"
    
    def extract_model_from_request(self, request_data: Dict[str, Any], path: str, auth_type: str) -> str:
        """从请求中提取模型名称"""
        # 对于Google Gemini API，从URL路径中提取模型名称
        if auth_type == "google" and "/v1beta/models/" in path:
            # 路径格式: /v1beta/models/gemini-pro:generateContent
            # 或: /v1beta/models/gemini-2.5-pro:streamGenerateContent
            import re
            match = re.search(r'/v1beta/models/([^:]+)', path)
            if match:
                return match.group(1)
        
        # 对于其他API，从请求体中获取模型名称
        return request_data.get("model", "unknown")
    
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
        elif auth_type == "google":
            # Google API认证处理
            auth_header = request_headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                base_headers["Authorization"] = auth_header
            else:
                # 如果没有Bearer token，尝试从x-goog-api-key获取
                api_key = request_headers.get("x-goog-api-key", "")
                if api_key:
                    base_headers["x-goog-api-key"] = api_key
                else:
                    base_headers["Authorization"] = auth_header
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
        # 保留原始路径和查询参数
        return f"{protocol}://{domain}{path}"
    
    async def handle_openai_api(self, request: web.Request) -> web.StreamResponse:
        """处理标准OpenAI API端点请求"""
        try:
            # 获取请求数据
            headers = dict(request.headers)
            request_data = await request.json()
            
            # 调试：打印客户端发送的消息
            await self.async_logger.info(f"🔍 OpenAI API - 客户端请求数据: {json.dumps(request_data, ensure_ascii=False, indent=2)}")
            
            # 请求体大小检查
            if not await self._validate_request_size(request_data):
                return web.Response(
                    status=413,
                    text=json.dumps({"error": "请求体过大，请减小输入数据大小或分批处理"})
                )
            
            # 从请求头中获取Authorization，确定目标域名
            auth_header = headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                return web.Response(
                    status=401,
                    text=json.dumps({"error": "缺少有效的Authorization头"})
                )
            
            # 根据API Key或模型名称确定目标域名
            # 这里使用默认的Google Gemini API配置
            target_domain = 'generativelanguage.googleapis.com'
            auth_type = 'google'
            
            # 构建目标URL
            path = str(request.url.path)
            if request.query_string:
                path += '?' + request.query_string
            
            # 对于Google API，需要转换路径格式
            if auth_type == 'google':
                model = request_data.get('model', 'gemini-2.0-flash-exp')
                path = f"/v1beta/models/{model}:generateContent"
                if request_data.get('stream', False):
                    path = f"/v1beta/models/{model}:streamGenerateContent"
            
            target_url = self.get_target_url(target_domain, path)
            
            # 准备认证头
            auth_headers = self.prepare_auth_headers(headers, auth_type)
            
            # 转换请求数据格式
            if auth_type == 'google':
                # 转换OpenAI格式到Google格式
                google_request = self._convert_openai_to_google(request_data)
                request_data = google_request
            
            # 发送请求到目标API
            async with self.http_session.post(
                target_url,
                headers=auth_headers,
                json=request_data,
                timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                
                # 检查是否为流式响应
                is_stream = request_data.get('stream', False) or 'text/event-stream' in resp.headers.get('content-type', '')
                
                if is_stream:
                    return await self._handle_stream_response(resp, request, auth_type, 
                                                            request_data.get('model', 'unknown'), request_data)
                else:
                    return await self._handle_non_stream_response(resp, auth_type, 
                                                                request_data.get('model', 'unknown'), request_data)
                    
        except Exception as e:
            await self.async_logger.error(f"❌ OpenAI API处理异常: {e}", exc_info=True)
            return web.Response(
                status=500,
                text=json.dumps({"error": f"服务器内部错误: {str(e)}"})
            )
    
    def _convert_openai_to_google(self, openai_request: Dict[str, Any]) -> Dict[str, Any]:
        """将OpenAI格式请求转换为Google格式"""
        messages = openai_request.get('messages', [])
        
        # 转换消息格式
        contents = []
        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            
            if role == 'system':
                # Google API中system消息需要特殊处理
                contents.append({
                    "role": "user",
                    "parts": [{"text": f"System: {content}"}]
                })
            elif role == 'user':
                contents.append({
                    "role": "user", 
                    "parts": [{"text": content}]
                })
            elif role == 'assistant':
                contents.append({
                    "role": "model",
                    "parts": [{"text": content}]
                })
        
        # 构建Google API请求
        google_request = {
            "contents": contents,
            "generationConfig": {
                "temperature": openai_request.get('temperature', 0.7),
                "maxOutputTokens": openai_request.get('max_tokens', 2048),
                "topP": openai_request.get('top_p', 1.0),
            }
        }
        
        return google_request

    async def handle_dynamic_proxy(self, request: web.Request) -> web.StreamResponse:
        """处理动态代理请求"""
        try:
            # 解析URL参数
            domain = request.match_info['domain']
            path = '/' + request.match_info['path']
            
            # 保留查询参数
            if request.query_string:
                path += '?' + request.query_string
            
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
            
            # 调试：打印客户端发送的消息
            await self.async_logger.debug(f"🔍 调试 - 客户端请求数据: {json.dumps(request_data, ensure_ascii=False, indent=2)}")
            
            # 请求体大小检查
            if not await self._validate_request_size(request_data):
                return web.Response(
                    status=413,
                    text=json.dumps({"error": "请求体过大，请减小输入数据大小或分批处理"})
                )
            
            # 根据域名配置或路径识别认证类型
            domain_config = self.allowed_domains.get(domain, {})
            if 'auth_type' in domain_config:
                # 优先使用域名配置的认证类型
                auth_type = domain_config['auth_type']
            else:
                # 回退到路径模式识别
                auth_type = self.detect_auth_type_from_path(path)
            
            # 准备认证头
            forward_headers = self.prepare_auth_headers(headers, auth_type)
            
            # 构建目标URL
            target_url = self.get_target_url(domain, path)
            
            # 判断是否为流式请求
            is_stream = request_data.get("stream", False)
            
            # Google API特殊处理：检查URL中是否包含streamGenerateContent
            if auth_type == "google" and "streamGenerateContent" in path:
                is_stream = True
                await self.async_logger.debug(f"🔍 调试 - Google流式请求检测: URL包含streamGenerateContent，设置为流式")
            
            # 解析模型名称
            model = self.extract_model_from_request(request_data, path, auth_type)
            
            await self.async_logger.info(
                f"📡 动态代理请求: {domain}{path}, 认证类型: {auth_type}, 流式: {is_stream}, 模型: {model}"
            )
            
            # 发送请求（带重试机制）
            max_retries = 3
            retry_delay = 1  # 初始重试延迟（秒）
            
            for attempt in range(max_retries):
                try:
                    async with self.http_session.post(
                        target_url,
                        headers=forward_headers,
                        json=request_data
                    ) as resp:
                        if is_stream:
                            return await self._handle_stream_response(resp, request, auth_type, model, request_data)
                        else:
                            return await self._handle_non_stream_response(resp, auth_type, model, request_data)
                
                except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as e:
                    if attempt < max_retries - 1:  # 不是最后一次尝试
                        await self.async_logger.warning(f"🔄 连接失败，第{attempt + 1}次重试 (共{max_retries}次): {str(e)}")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # 指数退避
                    else:
                        await self.async_logger.error(f"❌ 连接失败，已达到最大重试次数: {str(e)}")
                        return web.Response(status=500, text=json.dumps({"error": "连接超时，请稍后重试"}))
        
        except json.JSONDecodeError:
            await self.async_logger.error("❌ 无效的请求数据格式")
            return web.Response(status=400, text=json.dumps({"error": "无效的请求数据格式"}))
        except Exception as e:
            await self.async_logger.error(f"处理动态代理请求时发生错误: {e}\n{traceback.format_exc()}")
            return web.Response(status=500, text=json.dumps({"error": "服务器内部错误"}))
    
    async def _validate_request_size(self, request_data: Dict[str, Any]) -> bool:
        """验证请求体大小（兼容 OpenAI messages 与 Google contents.parts）"""
        max_chars = 8000000
        total_chars = 0

        # OpenAI/Anthropic 风格
        messages = request_data.get("messages", [])
        if isinstance(messages, list) and messages:
            try:
                for msg in messages:
                    if isinstance(msg, dict):
                        # 只统计主要文本
                        total_chars += len(str(msg.get("content", "")))
                    else:
                        total_chars += len(str(msg))
            except Exception:
                total_chars += len(str(messages))

        # Google Gemini 风格
        if total_chars == 0:
            contents = request_data.get("contents", [])
            if isinstance(contents, list) and contents:
                try:
                    for content in contents:
                        if isinstance(content, dict):
                            parts = content.get("parts", [])
                            if isinstance(parts, list):
                                for part in parts:
                                    if isinstance(part, dict):
                                        t = part.get("text")
                                        if isinstance(t, str):
                                            total_chars += len(t)
                except Exception:
                    total_chars += len(str(contents))

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
                # 原样透传上游数据，保留换行与空行（增加客户端断开防护）
                transport = getattr(request, "transport", None)
                if transport is None or transport.is_closing():
                    await self.async_logger.info("🔌 客户端连接已关闭，停止继续写入流式数据")
                    break
                try:
                    await response.write(line)
                    await response.drain()
                except (ConnectionResetError, BrokenPipeError, aiohttp.ClientConnectionResetError, asyncio.CancelledError):
                    await self.async_logger.info("🔌 客户端断开连接，停止写入")
                    break
                # 其他异常交由外层捕获
                 
                # 为日志与解析单独构造字符串，不影响透传
                try:
                    line_text = line.decode('utf-8', errors='ignore')
                except Exception:
                    line_text = ''
                line_str = line_text.strip()
                
                if line_str:
                    # 调试：打印接收到的流式数据
                    await self.async_logger.debug(f"🔍 调试 - 接收到流式数据: {line_str[:200]}...")
                    
                    # 解析响应内容
                    if auth_type == "anthropic":
                        chunk_reasoning = ""
                        complete_response, response_id, chunk_reasoning = self._parse_anthropic_stream_chunk(
                            line_str, complete_response, response_id
                        )
                        if chunk_reasoning:
                            complete_reasoning += chunk_reasoning
                    elif auth_type == "google":
                        chunk_reasoning = ""
                        complete_response, response_id, chunk_reasoning = await self._parse_google_stream_chunk(
                            line_str, complete_response, response_id
                        )
                        if chunk_reasoning:
                            complete_reasoning += chunk_reasoning
                    else:
                        chunk_reasoning = ""
                        complete_response, response_id, chunk_reasoning = self._parse_openai_stream_chunk(
                            line_str, complete_response, response_id
                        )
                        if chunk_reasoning:
                            complete_reasoning += chunk_reasoning
        
        except Exception as e:
            await self.async_logger.error(f"流式响应处理错误: {e}")
            await self.async_logger.error(f"错误详情: {traceback.format_exc()}")
        
        finally:
            # 保存对话 - 处理不同API格式的消息转换
            if response_id and complete_response:
                # 处理不同API格式的消息转换
                messages = []
                if auth_type == "google":
                    # Google API格式转换：contents -> messages
                    if "contents" in request_data:
                        for content in request_data["contents"]:
                            # Google API的contents通常没有role字段，默认为用户消息
                            # 提取用户消息
                            text_parts = []
                            for part in content.get("parts", []):
                                if "text" in part:
                                    text_parts.append(part["text"])
                            if text_parts:
                                messages.append({
                                    "role": "user",
                                    "content": "\n".join(text_parts)
                                })
                else:
                    # OpenAI/Anthropic格式
                    messages = request_data.get('messages', [])
                
                # 存档：仅在 OpenAI 或 Google 且存在结构化思考时附加<think>，不做启发式
                if auth_type in ("openai", "google") and complete_reasoning:
                    formatted_response = f"<think>\n{complete_reasoning}\n</think>\n\n{complete_response}"
                else:
                    formatted_response = complete_response
                
                # 调试：打印最终保存的内容
                await self.async_logger.debug(f"🔍 调试 - 流式响应最终内容长度: {len(formatted_response)}")
                await self.async_logger.debug(f"🔍 调试 - 流式响应前100字符: {formatted_response[:100]}...")
                
                await self._queue_conversation(response_id, model, {
                    'request': request_data,
                    'response': formatted_response,
                    'reasoning': complete_reasoning,
                    'messages': messages
                })
        
        return response
    
    async def _handle_non_stream_response(self, resp: aiohttp.ClientResponse,
                                        auth_type: str, model: str, request_data: Dict[str, Any]) -> web.Response:
        """处理非流式响应"""
        response_text = await resp.text()
        
        # 记录上游响应状态
        if resp.status >= 400:
            await self.async_logger.warning(
                f"⚠️ 上游服务器返回错误: {resp.status} - {response_text[:200]}..."
            )
        
        try:
            response_json = json.loads(response_text)
            
            # 解析响应内容
            complete_response = ""
            reasoning = ""
            
            if auth_type == "anthropic":
                complete_response = self._parse_anthropic_final_response(response_json)
            elif auth_type == "google":
                complete_response, reasoning = await self._parse_google_final_response(response_json)
            else:
                complete_response = self._parse_openai_final_response(response_json)
            
            # 保存对话
            response_id = response_json.get('id', str(uuid.uuid4()))
            if complete_response:
                # 不做思考抽取，直接保存原文
                # 处理不同API格式的消息转换
                messages = []
                if auth_type == "google":
                    # Google API格式转换：contents -> messages
                    # Google API的contents通常没有role字段，默认为用户消息
                    if "contents" in request_data and isinstance(request_data["contents"], list):
                        for content in request_data["contents"]:
                            if isinstance(content, dict) and "parts" in content:
                                # 提取用户消息（Google API的contents默认为用户输入）
                                text_parts = []
                                parts = content.get("parts", [])
                                if isinstance(parts, list):
                                    for part in parts:
                                        if isinstance(part, dict) and "text" in part:
                                            text_parts.append(part["text"])
                                if text_parts:
                                    messages.append({
                                        "role": "user",
                                        "content": "\n".join(text_parts)
                                    })
                else:
                    # OpenAI/Anthropic格式
                    messages = request_data.get('messages', [])
                
                # 调试：打印转换后的消息
                await self.async_logger.debug(f"🔍 调试 - 转换后的消息格式: {json.dumps(messages, ensure_ascii=False, indent=2)}")
                
                # 对非流式：仅在 OpenAI 且存在结构化 reasoning_content 时，把思考并入 response
                combined_response = (
                    f"<think>\n{reasoning}\n</think>\n\n{complete_response}"
                    if (auth_type in ["openai", "google"] and reasoning) else complete_response
                )
                
                # 调试：打印保存的对话数据
                conversation_to_save = {
                    'request': request_data,
                    'response': combined_response,
                    'reasoning': reasoning,
                    'messages': messages
                }
                await self.async_logger.debug(f"🔍 调试 - 准备保存的对话数据: {json.dumps(conversation_to_save, ensure_ascii=False, indent=2)}")
                
                # 确保传递完整的请求消息
                await self._queue_conversation(response_id, model, conversation_to_save)
        
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
                        rc = delta.get("reasoning_content")
                        # 兼容多种结构的 reasoning_content，仅用于存档累加
                        if rc is not None:
                            try:
                                if isinstance(rc, str):
                                    complete_reasoning += rc
                                elif isinstance(rc, dict):
                                    # 常见字段尝试展开
                                    for k in ["text", "content", "message"]:
                                        v = rc.get(k)
                                        if isinstance(v, str):
                                            complete_reasoning += v
                                        elif isinstance(v, list):
                                            complete_reasoning += "".join(
                                                x.get("text", "") if isinstance(x, dict) else str(x) for x in v
                                            )
                                    # parts 结构
                                    parts = rc.get("parts")
                                    if isinstance(parts, list):
                                        complete_reasoning += "".join(
                                            x.get("text", "") if isinstance(x, dict) else str(x) for x in parts
                                        )
                                elif isinstance(rc, list):
                                    complete_reasoning += "".join(
                                        x.get("text", "") if isinstance(x, dict) else str(x) for x in rc
                                    )
                                else:
                                    # 兜底序列化
                                    complete_reasoning += str(rc)
                            except Exception:
                                pass
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
                rc = choice["message"].get("reasoning_content", "")
                # 兼容对象/数组形式的 reasoning_content
                if not isinstance(rc, str) and rc:
                    try:
                        if isinstance(rc, dict):
                            buf = []
                            for k in ["text", "content", "message"]:
                                v = rc.get(k)
                                if isinstance(v, str):
                                    buf.append(v)
                                elif isinstance(v, list):
                                    buf.extend(x.get("text", "") if isinstance(x, dict) else str(x) for x in v)
                            parts = rc.get("parts")
                            if isinstance(parts, list):
                                buf.extend(x.get("text", "") if isinstance(x, dict) else str(x) for x in parts)
                            rc = "".join(buf).strip()
                        elif isinstance(rc, list):
                            rc = "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in rc).strip()
                        else:
                            rc = str(rc)
                    except Exception:
                        rc = ""
                reasoning_content = rc if isinstance(rc, str) else ""
                response_content = choice["message"].get("content", "")
                return f"<think>\n{reasoning_content}\n</think>\n\n{response_content}" if reasoning_content else response_content
        return ""
    
    async def _parse_google_final_response(self, response_json: Dict[str, Any]) -> tuple:
        """解析Google API最终响应内容，返回(响应内容, 思考过程)"""
        response_text = ""
        reasoning = ""
        
        # 调试：打印完整响应结构
        import json
        await self.async_logger.debug(f"🔍 调试 - Google API完整响应: {json.dumps(response_json, ensure_ascii=False, indent=2)}")
        
        # 检查是否有错误状态
        finish_reason = None
        if "candidates" in response_json and response_json["candidates"]:
            candidate = response_json["candidates"][0]
            await self.async_logger.debug(f"🔍 调试 - candidate结构: {json.dumps(candidate, ensure_ascii=False, indent=2)}")
            
            # 获取finishReason
            finish_reason = candidate.get("finishReason")
            await self.async_logger.debug(f"🔍 调试 - finishReason: {finish_reason}")
            
            if finish_reason and finish_reason != "STOP":
                # 处理错误状态
                error_message = f"API错误: {finish_reason}"
                if finish_reason == "MAX_TOKENS":
                    error_message = "达到最大token限制，请减少输入内容或调整maxOutputTokens参数"
                elif finish_reason == "SAFETY":
                    error_message = "内容被安全过滤器阻止"
                elif finish_reason == "RECITATION":
                    error_message = "检测到内容重复"
                
                response_text = error_message
                await self.async_logger.warning(f"⚠️ Google API返回错误状态: {finish_reason}")
                return response_text, reasoning
            
            if isinstance(candidate, dict) and "content" in candidate:
                content = candidate["content"]
                await self.async_logger.debug(f"🔍 调试 - content结构: {json.dumps(content, ensure_ascii=False, indent=2)}")
                
                # 检查content是否有parts字段
                if isinstance(content, dict) and "parts" in content:
                    parts = content["parts"]
                    if isinstance(parts, list) and parts:
                        # 分别处理思考过程和响应内容
                        text_parts = []
                        thought_parts = []
                        
                        for part in parts:
                            if isinstance(part, dict):
                                is_thought_part = False
                                # 优先解析结构化思考：part.thinking.thought
                                if "thinking" in part and isinstance(part.get("thinking"), dict):
                                    t = part["thinking"].get("thought")
                                    if isinstance(t, str) and t:
                                        thought_parts.append(t)
                                        is_thought_part = True
                                # 兼容旧结构：part.thought == True 且 text 属于思考
                                elif "thought" in part and isinstance(part.get("thought"), bool) and part.get("thought") is True:
                                    t = part.get("text", "")
                                    if isinstance(t, str) and t:
                                        thought_parts.append(t)
                                        is_thought_part = True
                                # 普通文本内容（面向用户可见的回答），仅在非思考片段时纳入
                                if (not is_thought_part) and "text" in part and isinstance(part.get("text"), str):
                                    text_parts.append(part["text"])
                        
                        # 合并响应内容
                        response_text = "\n".join(text_parts)
                        
                        # 合并思考过程（仅依赖结构化字段）
                        if thought_parts:
                            reasoning = "\n".join(thought_parts).strip()
                
                # 如果content没有parts字段但有其他字段，检查是否直接包含文本
                elif isinstance(content, dict):
                    # 检查是否有直接的text字段
                    if "text" in content:
                        response_text = content["text"]
                    # 检查是否有thought字段
                    if "thought" in content:
                        reasoning = content["thought"]
                    
                    # 如果content只有role字段，可能响应内容为空
                    if not response_text and "role" in content:
                        await self.async_logger.debug(f"🔍 调试 - content只有role字段，响应内容为空")
        
        # 调试：打印提取结果
        await self.async_logger.debug(f"🔍 调试 - 提取的响应内容长度: {len(response_text)}")
        await self.async_logger.debug(f"🔍 调试 - 提取的思考过程长度: {len(reasoning)}")
        
        return response_text, reasoning
    
    async def _parse_google_stream_chunk(self, line_str: str, complete_response: str, response_id: Optional[str]):
        """解析Google API流式响应块"""
        complete_reasoning = ""
        
        # 调试：打印接收到的原始数据
        await self.async_logger.debug(f"🔍 调试 - Google流式原始数据: {repr(line_str[:100])}")
        
        # Google API流式数据可能不带 "data: " 前缀，直接是JSON片段
        json_data = line_str.strip()
        if json_data.startswith("data: "):
            if json_data.strip() == "data: [DONE]":
                return complete_response, response_id, complete_reasoning
            json_data = json_data[6:].strip()
        
        # 跳过空行和非JSON数据
        if not json_data or json_data in ['{', '}', '[', ']', ',']:
            return complete_response, response_id, complete_reasoning
            
        # Google API返回的是JSON片段，需要特殊处理
        try:
            # 尝试直接解析完整JSON
            if json_data.startswith('{') and json_data.endswith('}'):
                json_chunk = json.loads(json_data)
                await self.async_logger.debug(f"🔍 调试 - 解析完整JSON成功")
                
                # 获取response_id
                if not response_id and "responseId" in json_chunk:
                    response_id = json_chunk["responseId"]
                    await self.async_logger.debug(f"🔍 调试 - 获取到responseId: {response_id}")
                
                # 解析candidates中的内容
                if "candidates" in json_chunk and json_chunk["candidates"]:
                    candidate = json_chunk["candidates"][0]
                    if "content" in candidate and "parts" in candidate["content"]:
                        for part in candidate["content"]["parts"]:
                            is_thought_part = False
                            # 新结构：thinking 对象里包含思考文本
                            if "thinking" in part and isinstance(part.get("thinking"), dict):
                                t = part["thinking"].get("thought")
                                if isinstance(t, str) and t:
                                    complete_reasoning += t
                                    is_thought_part = True
                                    await self.async_logger.debug(f"🔍 调试 - 提取思维链(part.thinking.thought): {t[:50]}...")
                            # 旧结构：thought 为 True 时，text 属于思考
                            elif "thought" in part and isinstance(part.get("thought"), bool) and part.get("thought") is True:
                                t = part.get("text", "")
                                if isinstance(t, str) and t:
                                    complete_reasoning += t
                                    is_thought_part = True
                                    await self.async_logger.debug(f"🔍 调试 - 提取思维链(part.thought=true): {t[:50]}...")
                            # 普通文本片段，计入最终可见回答（仅非思考片段）
                            if (not is_thought_part) and "text" in part and isinstance(part.get("text"), str):
                                text_content = part["text"]
                                complete_response += text_content
                                await self.async_logger.debug(f"🔍 调试 - 提取文本内容: {text_content[:50]}...")
            else:
                # 对于JSON片段，尝试提取关键信息
                if '"text":' in json_data:
                    # 若片段包含思考标记，则跳过把 text 并入可见答案
                    if ('"thought": true' in json_data) or ('"thinking"' in json_data):
                        await self.async_logger.debug("🔍 调试 - 片段包含思考标记，跳过将 text 并入可见答案")
                    else:
                        # 使用正则表达式提取text内容
                        import re
                        text_match = re.search(r'"text":\s*"([^"]*)"', json_data)
                        if text_match:
                            text_content = text_match.group(1)
                            complete_response += text_content
                            await self.async_logger.debug(f"🔍 调试 - 从片段提取文本: {text_content[:50]}...")
                
                if '"responseId":' in json_data and not response_id:
                    # 提取responseId
                    import re
                    id_match = re.search(r'"responseId":\s*"([^"]*)"', json_data)
                    if id_match:
                        response_id = id_match.group(1)
                        await self.async_logger.debug(f"🔍 调试 - 从片段提取responseId: {response_id}")
                
        except json.JSONDecodeError as e:
            await self.async_logger.debug(f"🔍 调试 - JSON解析失败: {str(e)[:100]}")
            # 尝试处理SSE格式：data: {json}
            if line_str.startswith("data: ") and len(line_str) > 6:
                sse_data = line_str[6:].strip()
                if sse_data and sse_data != "[DONE]":
                    try:
                        json_chunk = json.loads(sse_data)
                        await self.async_logger.debug(f"🔍 调试 - SSE格式解析成功")
                        
                        # 获取response_id
                        if not response_id and "responseId" in json_chunk:
                            response_id = json_chunk["responseId"]
                            await self.async_logger.debug(f"🔍 调试 - 获取到responseId: {response_id}")
                        
                        # 解析candidates中的内容
                        if "candidates" in json_chunk and json_chunk["candidates"]:
                            candidate = json_chunk["candidates"][0]
                            if "content" in candidate and "parts" in candidate["content"]:
                                for part in candidate["content"]["parts"]:
                                    is_thought_part = False
                                    if "thinking" in part and isinstance(part.get("thinking"), dict):
                                        t = part["thinking"].get("thought")
                                        if isinstance(t, str) and t:
                                            complete_reasoning += t
                                            is_thought_part = True
                                    elif "thought" in part and isinstance(part.get("thought"), bool) and part.get("thought") is True:
                                        t = part.get("text", "")
                                        if isinstance(t, str) and t:
                                            complete_reasoning += t
                                            is_thought_part = True
                                    if (not is_thought_part) and "text" in part:
                                        text_content = part["text"]
                                        complete_response += text_content
                                        await self.async_logger.debug(f"🔍 调试 - 提取文本内容: {text_content[:50]}...")
                    except json.JSONDecodeError:
                        await self.async_logger.debug(f"🔍 调试 - SSE数据仍然不是有效JSON")
        except Exception as e:
            await self.async_logger.error(f"Google流式解析错误: {e}")
            await self.async_logger.error(f"错误详情: {traceback.format_exc()}")
        
        return complete_response, response_id, complete_reasoning
    
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
        """保存一批对话（复用单个 DB 连接提升性能）"""
        db_conn = None
        try:
            db_conn = await get_db_connection()
            for conversation_data in batch:
                # 检查数据结构
                if not isinstance(conversation_data, dict):
                    await self.async_logger.error(f"无效的对话数据类型: {type(conversation_data)}")
                    continue
                
                if 'conversation' not in conversation_data:
                    await self.async_logger.error(f"对话数据缺少conversation字段: {conversation_data}")
                    continue
                
                conversation = conversation_data['conversation']
                if not isinstance(conversation, dict):
                    await self.async_logger.error(f"conversation字段类型错误: {type(conversation)}")
                    continue
                
                # 格式化为ShareGPT格式
                # 优先使用conversation中的messages，如果没有则使用request中的messages
                messages = conversation.get('messages', conversation.get('request', {}).get('messages', []))
                
                # 调试：打印格式化前的数据
                await self.async_logger.debug(f"🔍 调试 - 格式化前的消息: {json.dumps(messages, ensure_ascii=False, indent=2)}")
                await self.async_logger.debug(f"🔍 调试 - 响应内容: {conversation.get('response', '')}")
                
                sharegpt_data = format_to_sharegpt(
                    conversation_data.get('model', 'unknown'),
                    messages,
                    conversation.get('response', ''),
                    conversation.get('request', {})
                )
                
                # 调试：打印格式化后的数据
                await self.async_logger.debug(f"🔍 调试 - 格式化后的ShareGPT数据: {json.dumps(sharegpt_data, ensure_ascii=False, indent=2)}")
                
                # 保存到数据库（复用连接）
                await save_conversation_async(
                    db_conn,
                    conversation_data.get('id', str(uuid.uuid4())),
                    conversation_data.get('model', 'unknown'),
                    sharegpt_data
                )
            
            await self.async_logger.info(f"✅ 成功保存 {len(batch)} 条对话")
            
        except Exception as e:
            await self.async_logger.error(f"保存对话批次失败: {e}\n{traceback.format_exc()}")
        finally:
            if db_conn is not None:
                try:
                    await db_conn.close()
                except Exception:
                    pass
    
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
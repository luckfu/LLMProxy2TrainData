import json
import logging
import asyncio
import aiosqlite
import traceback
from logging.handlers import QueueHandler, QueueListener
from queue import Queue
from typing import Optional

# 配置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.NullHandler())

# 异步日志类
class AsyncLogger:
    def __init__(self, name: str, log_file: str, level=logging.DEBUG):
        self.queue = Queue()
        self.logger = logging.getLogger(name)
        
        # 清除所有现有的处理器，防止重复日志
        if self.logger.handlers:
            for handler in self.logger.handlers[:]:  # 使用副本进行迭代
                self.logger.removeHandler(handler)
        
        self.logger.setLevel(level)
        self.logger.propagate = False  # 防止日志传播到根记录器
        
        # 创建文件处理器和控制台处理器
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        console_handler = logging.StreamHandler()
        
        # 设置日志格式
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # 设置队列处理器
        queue_handler = QueueHandler(self.queue)
        self.logger.addHandler(queue_handler)
        
        # 创建队列监听器
        self.listener = QueueListener(
            self.queue,
            file_handler,
            console_handler,
            respect_handler_level=True
        )
        self.listener.start()
    
    def __del__(self):
        self.listener.stop()
    
    async def debug(self, msg: str):
        self.logger.debug(msg)
    
    async def info(self, msg: str):
        self.logger.info(msg)
    
    async def warning(self, msg: str):
        self.logger.warning(msg)
    
    async def error(self, msg: str):
        self.logger.error(msg)

# 全局异步日志实例
_async_logger: Optional[AsyncLogger] = None

# 初始化异步日志
def init_async_logger(name: str, log_file: str, level=logging.DEBUG) -> AsyncLogger:
    """初始化并返回异步日志实例"""
    global _async_logger
    # 如果已存在实例，先尝试清理资源
    if _async_logger is not None:
        try:
            _async_logger.listener.stop()
        except Exception as e:
            logger.warning(f"停止现有日志监听器时出错: {e}")
    
    # 确保根日志配置不会干扰我们的日志器
    root_logger = logging.getLogger()
    root_level = root_logger.level
    
    # 创建新的实例
    _async_logger = AsyncLogger(name, log_file, level)
    
    # 恢复根日志器的级别
    root_logger.setLevel(root_level)
    
    return _async_logger

# 获取异步日志实例
def get_async_logger() -> Optional[AsyncLogger]:
    """获取全局异步日志实例"""
    return _async_logger

# 数据库路径全局变量
_db_path: str = "interactions.db"

# 初始化数据库路径
async def init_db_path(db_path: str = "interactions.db") -> str:
    """初始化数据库路径"""
    global _db_path
    _db_path = db_path
    # 测试连接并确保表存在
    conn = None
    try:
        conn = await aiosqlite.connect(db_path)
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS interactions (
                id TEXT PRIMARY KEY,
                model TEXT,
                conversation TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        await conn.commit()
        logger.info("✅ 数据库初始化完成")
    except Exception as e:
        logger.error(f"初始化数据库时出错: {e}\n{traceback.format_exc()}")
        raise
    finally:
        if conn:
            await conn.close()
    return _db_path

# 简化版：直接创建数据库连接
async def get_db_connection() -> aiosqlite.Connection:
    """创建并返回一个新的数据库连接"""
    global _db_path
    try:
        conn = await aiosqlite.connect(_db_path)
        return conn
    except Exception as e:
        logger.error(f"创建数据库连接时出错: {e}\n{traceback.format_exc()}")
        raise

def format_to_sharegpt(model: str, messages: list, response: str, request_data: dict = None) -> dict:
    """将对话格式化为目标格式"""
    system_message = ""
    conversations = []
    tools = []
    
    # 如果有完整的请求数据，优先从中提取system和tools
    if request_data:
        # 提取system信息
        if "system" in request_data:
            system_data = request_data["system"]
            if isinstance(system_data, list):
                # 处理system数组格式
                system_parts = []
                for item in system_data:
                    if isinstance(item, dict) and "text" in item:
                        system_parts.append(item["text"])
                    elif isinstance(item, str):
                        system_parts.append(item)
                system_message = "\n".join(system_parts)
            elif isinstance(system_data, str):
                system_message = system_data
        
        # 提取tools信息
        if "tools" in request_data and request_data["tools"]:
            tools = request_data["tools"]
    
    # 处理原始消息
    for msg in messages:
        if msg["role"] == "system":
            # 如果没有从request_data中获取到system，则从messages中提取
            if not system_message:
                system_message = msg["content"] if isinstance(msg["content"], str) else str(msg["content"])
        elif msg["role"] == "tool":
            # 将 OpenAI 的工具执行结果映射为 observation
            content = msg.get("content", "")
            if isinstance(content, list):
                # 展平为文本
                parts = []
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        parts.append(str(item.get("text", "")))
                    else:
                        parts.append(str(item))
                text_content = "\n".join(parts)
            elif isinstance(content, str):
                text_content = content
            else:
                text_content = str(content)
            if text_content.strip():
                conversations.append({
                    "from": "observation",
                    "value": text_content.strip()
                })
            continue
        elif msg["role"] == "function_call":
            # 直接记录为 function_call（content 建议为 {"name":..., "arguments":...} 的 JSON 串）
            fc_content = msg.get("content", "")
            if not isinstance(fc_content, str):
                try:
                    fc_content = json.dumps(fc_content, ensure_ascii=False)
                except Exception:
                    fc_content = str(fc_content)
            conversations.append({
                "from": "function_call",
                "value": fc_content
            })
            continue
        elif msg["role"] in ("function", "tool_response"):
            # 工具返回，映射为 observation
            fr_content = msg.get("content", "")
            if isinstance(fr_content, list):
                parts = []
                for item in fr_content:
                    parts.append(item.get("text", "") if isinstance(item, dict) else str(item))
                fr_text = "\n".join(parts)
            elif isinstance(fr_content, str):
                fr_text = fr_content
            else:
                fr_text = str(fr_content)
            if fr_text.strip():
                conversations.append({
                    "from": "observation",
                    "value": fr_text.strip()
                })
            continue
        else:
            # 将 user 转换为 human，assistant 转换为 gpt
            role = "human" if msg["role"] == "user" else "gpt"
            content = msg["content"]
            text_content = ""
            tool_calls_found = []
            tool_results_found = []
            
            if isinstance(content, list):
                # 处理复杂内容格式，提取文本部分和工具调用
                content_parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text" and "text" in item:
                            content_parts.append(item["text"])
                        elif item.get("type") == "tool_use":
                            # Anthropic格式的工具调用
                            tool_calls_found.append({
                                "id": item.get("id"),
                                "type": "function",
                                "function": {
                                    "name": item.get("name"),
                                    "arguments": json.dumps(item.get("input", {}), ensure_ascii=False)
                                }
                            })
                        elif item.get("type") == "tool_result":
                            # Anthropic格式的工具结果
                            tool_results_found.append({
                                "tool_call_id": item.get("tool_use_id"),
                                "content": item.get("content", "")
                            })
                        else:
                            content_parts.append(str(item))
                    else:
                        content_parts.append(str(item))
                text_content = "\n".join(content_parts)
            elif not isinstance(content, str):
                text_content = str(content)
            else:
                text_content = content
            
            # 添加主要对话内容
            if text_content.strip():
                conversations.append({
                    "from": role,
                    "value": text_content.strip()
                })
            
            # 处理OpenAI格式的工具调用
            if msg.get("tool_calls"):
                for tool_call in msg["tool_calls"]:
                    conversations.append({
                        "from": "function_call",
                        "value": json.dumps(tool_call, ensure_ascii=False)
                    })
                    # 如果有工具调用结果，添加 observation
                    if "function" in tool_call and "output" in tool_call:
                        conversations.append({
                            "from": "observation",
                            "value": tool_call["output"]
                        })
            
            # 处理Anthropic格式的工具调用
            for tool_call in tool_calls_found:
                conversations.append({
                    "from": "function_call",
                    "value": json.dumps(tool_call, ensure_ascii=False)
                })
            
            # 处理Anthropic格式的工具结果
            for tool_result in tool_results_found:
                conversations.append({
                    "from": "observation",
                    "value": tool_result["content"]
                })
    
    # 处理助手的回复，需要检查是否包含Anthropic的工具调用
    response_text = response.strip()
    anthropic_tool_calls = []
    
    # 检查是否包含Anthropic工具调用标记
    if "[ANTHROPIC_TOOL_CALLS:" in response_text:
        import re
        # 提取工具调用信息 - 使用更精确的匹配
        start_marker = "[ANTHROPIC_TOOL_CALLS:"
        end_marker = "]\n"
        start_pos = response_text.find(start_marker)
        if start_pos != -1:
            # 找到JSON内容的开始位置
            json_start = start_pos + len(start_marker)
            # 寻找匹配的结束位置，考虑嵌套的方括号
            bracket_count = 0
            json_end = json_start
            for i, char in enumerate(response_text[json_start:]):
                if char == '[':
                    bracket_count += 1
                elif char == ']':
                    if bracket_count == 0:
                        json_end = json_start + i
                        break
                    else:
                        bracket_count -= 1
            
            if json_end > json_start:
                try:
                    json_content = response_text[json_start:json_end]
                    anthropic_tool_calls = json.loads(json_content)
                    # 移除工具调用标记，保留纯文本内容
                    response_text = response_text[:start_pos] + response_text[json_end+1:]
                    response_text = response_text.strip()
                except json.JSONDecodeError as e:
                    print(f"JSON解析错误: {e}, 内容: {json_content}")
                    pass
    
    # 处理流式响应中的工具调用标记
    elif "[TOOL_CALL_START:" in response_text:
        import re
        # 解析流式工具调用
        tool_start_pattern = r'\[TOOL_CALL_START:(.+?)\]'
        tool_input_pattern = r'\[TOOL_INPUT_DELTA:(.+?)\]'
        
        tool_starts = re.findall(tool_start_pattern, response_text)
        tool_inputs = re.findall(tool_input_pattern, response_text)
        
        for i, tool_start_str in enumerate(tool_starts):
            try:
                tool_info = json.loads(tool_start_str)
                # 合并所有输入增量
                full_input = ""
                if i < len(tool_inputs):
                    full_input = "".join(tool_inputs[i:i+1])  # 简化处理，实际可能需要更复杂的逻辑
                
                tool_call = {
                    "id": tool_info.get("id"),
                    "type": "function",
                    "function": {
                        "name": tool_info.get("name"),
                        "arguments": full_input if full_input else json.dumps(tool_info.get("input", {}), ensure_ascii=False)
                    }
                }
                anthropic_tool_calls.append(tool_call)
            except json.JSONDecodeError:
                continue
        
        # 清理响应文本中的标记
        response_text = re.sub(r'\[TOOL_CALL_START:.+?\]', '', response_text)
        response_text = re.sub(r'\[TOOL_INPUT_DELTA:.+?\]', '', response_text)
        response_text = re.sub(r'\[TOOL_CALL_END\]', '', response_text)
        response_text = response_text.strip()
    
    # 添加主要响应内容
    if response_text:
        conversations.append({
            "from": "gpt",
            "value": response_text
        })
    
    # 添加Anthropic工具调用
    for tool_call in anthropic_tool_calls:
        conversations.append({
            "from": "function_call",
            "value": json.dumps(tool_call, ensure_ascii=False)
        })
    
    # 如果最后的响应包含OpenAI格式的工具调用，也需要添加
    try:
        response_data = json.loads(response)
        if isinstance(response_data, dict) and response_data.get("tool_calls"):
            for tool_call in response_data["tool_calls"]:
                conversations.append({
                    "from": "function_call",
                    "value": json.dumps(tool_call, ensure_ascii=False)
                })
    except json.JSONDecodeError:
        pass
    
    return {
        "conversations": conversations,
        "system": system_message,
        "tools": json.dumps(tools, ensure_ascii=False) if tools else "[]"  # 确保tools是JSON字符串格式
    }

def save_conversation(conn, response_id: str, model: str, conversation: dict):
    """保存对话数据到数据库（同步版本）"""
    try:
        with conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO interactions (id, model, conversation)
                VALUES (?, ?, ?)""",
                (response_id, model, json.dumps(conversation, ensure_ascii=False))
            )
    except Exception as e:
        logger.error(f"保存对话数据时发生错误: {e}")
        raise

async def save_conversation_async(conn, response_id: str, model: str, conversation: dict):
    """保存对话数据到数据库（异步版本）"""
    try:
        # 检查连接类型，确保使用正确的方法
        if isinstance(conn, aiosqlite.Connection):
            await conn.execute(
                """INSERT INTO interactions (id, model, conversation)
                VALUES (?, ?, ?)""",
                (response_id, model, json.dumps(conversation, ensure_ascii=False))
            )
            await conn.commit()
        else:
            # 如果不是异步连接，记录错误
            logger.error(f"错误的连接类型: {type(conn)}，需要aiosqlite.Connection")
            raise TypeError(f"需要aiosqlite.Connection类型，但收到了{type(conn)}")
    except Exception as e:
        logger.error(f"异步保存对话数据时发生错误: {e}")
        raise
# 动态代理服务器 (Dynamic Proxy)

## 概述

动态代理服务器是一个无需配置文件的智能API代理服务，支持通过URL路径直接指定目标API端点，并自动识别和处理不同的认证方式。

## 核心特性

### 🚀 无配置文件设计
- 不依赖 `endpoint_config.json` 配置文件
- 通过URL路径直接指定目标API
- 即插即用，无需重启服务

### 🔐 智能认证识别
- **路径模式识别**: 根据URL路径自动识别API类型
- **自动认证转换**: 支持OpenAI和Anthropic两种认证格式
- **安全域名白名单**: 防止恶意请求

### 🎯 全功能API支持
- **对话生成**: `/v1/chat/completions`、`/chat/completions`
- **文本嵌入**: `/v1/embeddings` 
- **文档重排**: `/v1/rerank`
- **Anthropic消息**: `/anthropic/v1/messages`、`/v1/messages`

### 📊 数据收集
- 自动保存所有对话为ShareGPT格式
- 支持流式和非流式响应
- 批量保存优化性能

## 使用方法

### 启动服务器

```bash
# 基本启动
python proxy_dynamic.py

# 指定端口和日志级别
python proxy_dynamic.py --port 8080 --log-level INFO
```

### API调用格式

```
POST http://localhost:8080/{domain}/{path}
```

📋 使用说明:
1. 启动动态代理服务器: python proxy_dynamic.py --port 8080
2. 使用格式: POST http://localhost:8080/{domain}/{path}
3. 支持的API类型:
   - 对话生成: POST /api.deepseek.com/v1/chat/completions
   - 文本嵌入: POST /api.siliconflow.cn/v1/embeddings
   - 文档重排: POST /deepsearch.jina.ai/v1/rerank
   - Anthropic消息: POST /api.moonshot.cn/anthropic/v1/messages
4. 认证会根据路径自动识别和转换
5. 所有请求会自动保存到数据库

### 支持的域名

当前白名单包含以下域名：

| 域名 | 认证类型 | 协议 | 说明 |
|------|----------|------|------|
| `api.openai.com` | OpenAI | HTTPS | OpenAI官方API |
| `api.anthropic.com` | Anthropic | HTTPS | Anthropic官方API |
| `api.moonshot.cn` | Anthropic | HTTPS | 月之暗面API |
| `api.deepseek.com` | OpenAI | HTTPS | DeepSeek API |
| `api.siliconflow.cn` | OpenAI | HTTPS | SiliconFlow API |
| `dashscope.aliyuncs.com` | OpenAI | HTTPS | 阿里云百炼API |
| `models.inference.ai.azure.com` | OpenAI | HTTPS | GitHub Models |
| `deepsearch.jina.ai` | OpenAI | HTTPS | Jina AI |
| `36.141.21.137:9081` | OpenAI | HTTP | 内网服务 |
| `group.sx.10086.cn` | OpenAI | HTTP | 山西移动API |

## 认证类型识别规则

### 路径模式识别 (方案B)

系统根据URL路径自动识别认证类型：

```python
# Anthropic类型识别
if "/anthropic/" in path or "/v1/messages" in path:
    auth_type = "anthropic"

# OpenAI类型识别
elif "/v1/chat/completions" in path or "/chat/completions" in path:
    auth_type = "openai"

# 其他路径默认使用OpenAI格式
else:
    auth_type = "openai"
```

### 认证头处理

**OpenAI格式**:
```python
headers = {
    "Content-Type": "application/json",
    "Authorization": request_headers.get("Authorization", "")
}
```

**Anthropic格式**:
```python
# 从Authorization或x-api-key提取密钥
auth_header = request_headers.get("Authorization", "")
api_key = ""
if auth_header.startswith("Bearer "):
    api_key = auth_header.replace("Bearer ", "").strip()
elif request_headers.get("x-api-key"):
    api_key = request_headers.get("x-api-key")

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}
```

## 使用示例

### 1. OpenAI风格API调用

```bash
curl -X POST "http://localhost:8080/api.deepseek.com/v1/chat/completions" \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

### 2. Anthropic风格API调用

```bash
curl -X POST "http://localhost:8080/api.moonshot.cn/anthropic/v1/messages" \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-haiku-20241022",
    "messages": [
      {"role": "user", "content": "你好"}
    ],
    "max_tokens": 100
  }'
```

### 3. 文本嵌入API

```bash
curl -X POST "http://localhost:8080/api.siliconflow.cn/v1/embeddings" \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "BAAI/bge-large-zh-v1.5",
    "input": "人工智能是计算机科学的一个分支"
  }'
```

### 4. 文档重排API

```bash
curl -X POST "http://localhost:8080/deepsearch.jina.ai/v1/rerank" \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "jina-reranker-v1-base-en",
    "query": "What is artificial intelligence?",
    "documents": [
      "Artificial intelligence is a branch of computer science.",
      "Machine learning is a subset of AI.",
      "Deep learning uses neural networks."
    ],
    "top_k": 3
  }'
```

### 5. 流式请求

```bash
curl -X POST "http://localhost:8080/api.siliconflow.cn/v1/chat/completions" \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "messages": [
      {"role": "user", "content": "介绍一下Python"}
    ],
    "stream": true
  }'
```

## 测试

运行测试脚本验证功能：

```bash
python test_dynamic_proxy.py
```

测试包括：
- 健康检查
- 域名白名单验证
- OpenAI风格API调用
- Anthropic风格API调用
- 流式响应处理

## 优势对比

### 传统配置文件方式
```json
{
  "endpoints": {
    "provider": {
      "base_url": "https://api.example.com",
      "models": ["model1", "model2"],
      "auth_type": "bearer"
    }
  }
}
```

**缺点**:
- 需要预先配置
- 添加新API需要修改配置文件
- 需要重启服务
- 配置文件维护成本高

### 动态代理方式
```
POST /api.example.com/v1/chat/completions
```

**优点**:
- 无需配置文件
- 即插即用
- 支持任意符合格式的API
- 自动识别认证类型
- 更高的灵活性

## 安全特性

### 域名白名单
- 只允许预定义的可信域名
- 防止SSRF攻击
- 可根据需要扩展白名单

### 请求大小限制
- 限制请求体大小（默认8MB）
- 防止内存溢出攻击

### 探针请求过滤
- 自动过滤扫描器和探针请求
- 减少日志噪音
- 提高服务稳定性

## 性能优化

### 连接池管理
- HTTP连接复用
- DNS缓存
- 连接超时控制

### 批量数据保存
- 异步队列处理
- 批量写入数据库
- 减少I/O开销

### 内存优化
- 流式响应处理
- 及时释放资源
- 垃圾回收优化

## 监控和日志

### 健康检查
```bash
GET http://localhost:8080/health
```

### 日志级别
- DEBUG: 详细调试信息
- INFO: 一般信息（默认）
- WARNING: 警告信息
- ERROR: 错误信息

### 关键指标
- 请求成功率
- 响应时间
- 错误类型统计
- 数据保存状态

## 扩展白名单

如需添加新的域名，修改 `allowed_domains` 字典：

```python
self.allowed_domains = {
    'new-api.example.com': {'auth_type': 'openai', 'https': True},
    # ... 其他域名
}
```

## 故障排除

### 常见问题

1. **403 Forbidden**: 域名不在白名单中
2. **400 Bad Request**: 请求格式错误
3. **413 Request Too Large**: 请求体过大
4. **500 Internal Server Error**: 服务器内部错误

### 调试方法

1. 检查日志文件 `proxy_dynamic.log`
2. 使用DEBUG日志级别获取详细信息
3. 验证API密钥和请求格式
4. 确认目标API服务可用性

## 总结

动态代理服务器通过路径模式识别实现了无配置文件的智能API代理，具有以下核心优势：

1. **高灵活性**: 无需预配置，支持动态添加API端点
2. **智能认证**: 自动识别和转换不同的认证格式
3. **安全可靠**: 域名白名单和请求过滤机制
4. **性能优化**: 连接池、批量处理等优化措施
5. **易于使用**: 简洁的URL格式，即插即用

这种设计让API代理服务更像一个通用的网关，而不是绑定特定配置的服务，大大提高了使用的便利性和灵活性。
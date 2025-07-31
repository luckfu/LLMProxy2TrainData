# LLM Proxy ShareGPT

一个功能强大的AI训练数据收集解决方案，集成动态代理服务器、对话记录管理和ShareGPT格式数据导出。

## 🚀 核心特性

### 🔄 动态代理服务器
- **无配置文件设计**：通过URL路径直接指定目标API，即插即用
- **智能认证识别**：自动识别OpenAI和Anthropic认证格式并转换
- **全API支持**：支持聊天对话、文本嵌入、文档重排等所有AI API
- **安全防护**：内置SSRF防护、域名白名单、请求大小限制、探针过滤
- **性能优化**：连接池复用、批量数据保存、内存优化

### 📊 数据收集与管理
- **自动记录**：所有API请求自动保存为ShareGPT格式
- **Web管理界面**：可视化查看、筛选和管理对话记录
- **数据确认**：支持对话质量确认，筛选高质量训练数据
- **批量处理**：高效的数据库批量操作

### 🎯 训练数据导出
- **格式转换**：将对话记录转换为标准ShareGPT训练数据格式
- **质量控制**：自动修复function_call格式，验证数据完整性
- **分类导出**：有效数据和无效数据分别导出
- **一键导出**：提供便捷的导出脚本

## 📦 安装依赖

```bash
pip install -r requirements.txt
```

## 🎯 快速开始

### 1. 启动代理服务器

```bash
# 使用启动脚本（推荐）
./start.sh

# 或者直接运行
python proxy_dynamic.py --port 8080
```

代理服务器启动后，监听端口8080，支持以下URL格式：
```
http://localhost:8080/{target_domain}/{api_path}
```

### 2. 启动Web管理界面（可选）

```bash
# 使用启动脚本
./start_web.sh

# 或者直接运行
python app.py
```

访问 http://localhost:5000 查看和管理对话记录。

### 3. 导出训练数据（可选）

```bash
# 使用导出脚本（推荐）
./export_data.sh

# 或者直接运行
python process_conversations.py
```

## 🔧 动态代理原理

### URL格式说明

动态代理通过特殊的URL格式实现无配置文件的API代理：

```
POST http://localhost:8080/{target_domain}/{api_path}
```

**示例**：
- 原始API: `https://api.deepseek.com/v1/chat/completions`
- 代理URL: `http://localhost:8080/api.deepseek.com/v1/chat/completions`

### 认证类型自动识别

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


## 📝 API调用示例

### OpenAI风格API调用

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

### Anthropic风格API调用

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

### 文本嵌入API

```bash
curl -X POST "http://localhost:8080/api.siliconflow.cn/v1/embeddings" \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "BAAI/bge-large-zh-v1.5",
    "input": "人工智能是计算机科学的一个分支"
  }'
```

### 文档重排API

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

### 流式请求

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

## 📁 项目结构

```
llm_proxy_sharegpt/
├── proxy_dynamic.py          # 主服务器文件
├── app.py                    # Web管理界面
├── process_conversations.py  # 数据处理和导出
├── utils.py                  # 工具函数
├── test_dynamic_proxy.py     # 基础功能测试
├── test_embedding_rerank.py  # 嵌入和重排测试
├── requirements.txt          # 项目依赖
├── start.sh                  # 代理服务器启动脚本
├── start_web.sh              # Web界面启动脚本
├── export_data.sh            # 数据导出脚本
├── config.example.json       # 配置文件示例
├── .gitignore               # Git忽略文件
├── README.md                 # 项目说明（本文件）
├── templates/               # Web界面模板
│   └── index.html
└── static/                  # 静态资源
    ├── css/
    └── js/
```

## 🧪 测试

```bash
# 测试基础功能
python test_dynamic_proxy.py

# 测试嵌入和重排功能
python test_embedding_rerank.py
```

测试包括：
- 健康检查
- 域名白名单验证
- OpenAI风格API调用
- Anthropic风格API调用
- 流式响应处理

## 🔄 完整工作流程

1. **启动代理服务器** - 接收和转发AI API请求
2. **记录对话数据** - 自动保存所有对话到数据库
3. **Web界面管理** - 查看、筛选和确认有价值的对话
4. **导出训练数据** - 将确认的对话转换为ShareGPT格式
5. **用于模型训练** - 使用导出的数据进行模型微调

## 🛡️ 安全特性

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

## 📊 性能优化

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

## 🔧 配置选项

### 命令行参数
- `--port`: 服务器端口 (默认: 8080)
- `--log-level`: 日志级别 (DEBUG/INFO/WARNING/ERROR)

### 扩展白名单

如需添加新的域名，修改 `allowed_domains` 字典：

```python
self.allowed_domains = {
    'new-api.example.com': {'auth_type': 'openai', 'https': True},
    # ... 其他域名
}
```

## 🔍 监控和日志

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

## 🚨 故障排除

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

## 🆚 优势对比

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

## 🤝 贡献

欢迎提交Issue和Pull Request来改进这个项目！

## 📄 许可证

MIT License

---

## 📖 总结

动态代理服务器通过路径模式识别实现了无配置文件的智能API代理，具有以下核心优势：

1. **高灵活性**: 无需预配置，支持动态添加API端点
2. **智能认证**: 自动识别和转换不同的认证格式
3. **安全可靠**: 域名白名单和请求过滤机制
4. **性能优化**: 连接池、批量处理等优化措施
5. **易于使用**: 简洁的URL格式，即插即用
6. **完整工作流**: 从数据收集到训练数据导出的完整解决方案

这种设计让API代理服务更像一个通用的网关，而不是绑定特定配置的服务，大大提高了使用的便利性和灵活性，特别适合AI训练数据的收集和管理。
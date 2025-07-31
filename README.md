# 动态代理服务器项目

一个功能强大的动态代理服务器，支持多种AI API的统一代理和管理。

## 🚀 项目特性

- **动态域名代理**：支持任意域名的动态代理，无需预配置
- **智能认证转换**：自动识别OpenAI和Anthropic认证格式
- **全API支持**：支持聊天对话、文本嵌入、文档重排等所有AI API
- **安全防护**：内置SSRF防护、请求大小限制、探针过滤
- **性能优化**：连接池复用、批量数据保存、内存优化
- **监控日志**：完整的请求日志和健康检查
- **Web管理界面**：可视化管理对话记录和数据库
- **数据导出处理**：将对话记录转换为标准训练数据格式

## 📦 安装依赖

```bash
pip install -r requirements.txt
```

## 🎯 快速开始

1. **启动服务器**
```bash
python proxy_dynamic.py --port 8080
```

2. **启动Web管理界面**（可选）
```bash
python app.py
```
访问 http://localhost:5000 查看和管理对话记录

3. **处理和导出数据**（可选）
```bash
# 方式1: 使用脚本（推荐）
./export_data.sh

# 方式2: 直接运行
python process_conversations.py
```
将确认的对话记录转换为ShareGPT格式的训练数据

4. **使用代理**
```bash
# 聊天对话
curl -X POST "http://localhost:8080/api.deepseek.com/v1/chat/completions" \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Hello"}]}'

# 文本嵌入
curl -X POST "http://localhost:8080/api.siliconflow.cn/v1/embeddings" \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-ada-002","input":"Hello world"}'```

## 📁 项目结构

```
dynamic_proxy_project/
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
├── README.md                 # 项目说明
├── DYNAMIC_PROXY_README.md   # 详细文档
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

# 处理和导出训练数据
python process_conversations.py
```

## 🔄 完整工作流程

1. **启动代理服务器** - 接收和转发AI API请求
2. **记录对话数据** - 自动保存所有对话到数据库
3. **Web界面管理** - 查看、筛选和确认有价值的对话
4. **导出训练数据** - 将确认的对话转换为ShareGPT格式
5. **用于模型训练** - 使用导出的数据进行模型微调

## 📖 详细文档

查看 [DYNAMIC_PROXY_README.md](./DYNAMIC_PROXY_README.md) 获取完整的使用说明和配置选项。

## 🔧 配置选项

- `--port`: 服务器端口 (默认: 8080)
- `--log-level`: 日志级别 (DEBUG/INFO/WARNING/ERROR)

## 🛡️ 安全特性

- SSRF攻击防护
- 请求大小限制 (8MB)
- 探针请求过滤
- 域名白名单机制

## 📊 性能特性

- HTTP连接池复用
- 批量数据库保存
- 内存使用优化
- 异步请求处理

## 🤝 贡献

欢迎提交Issue和Pull Request来改进这个项目！

## 📄 许可证

MIT License
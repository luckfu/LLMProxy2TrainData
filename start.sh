#!/bin/bash

# 动态代理服务器启动脚本

echo "🚀 启动动态代理服务器..."

# 检查Python环境
if ! command -v python &> /dev/null; then
    echo "❌ 错误: 未找到Python环境"
    exit 1
fi

# 检查依赖
echo "📦 检查依赖..."
python -c "import aiohttp, aiosqlite" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  警告: 缺少依赖，正在安装..."
    pip install -r requirements.txt
fi

# 设置默认端口
PORT=${1:-8080}

echo "🌐 服务器将在端口 $PORT 启动"
echo "📋 使用格式: POST http://localhost:$PORT/{domain}/{path}"
echo "🔗 健康检查: http://localhost:$PORT/health"
echo "⏹️  按 Ctrl+C 停止服务器"
echo ""

# 启动服务器
python proxy_dynamic.py --port $PORT
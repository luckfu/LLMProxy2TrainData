#!/bin/bash

# Web管理界面启动脚本

echo "🌐 启动Web管理界面..."

# 检查Python环境
if ! command -v python &> /dev/null; then
    echo "❌ 错误: 未找到Python环境"
    exit 1
fi

# 检查Flask依赖
echo "📦 检查Flask依赖..."
python -c "import flask" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  警告: 缺少Flask依赖，正在安装..."
    pip install Flask
fi

# 设置默认端口
PORT=${1:-5000}

echo "🖥️  Web界面将在端口 $PORT 启动"
echo "🔗 访问地址: http://localhost:$PORT"
echo "📊 功能: 查看和管理对话记录"
echo "⏹️  按 Ctrl+C 停止服务器"
echo ""

# 设置Flask环境变量
export FLASK_APP=app.py
export FLASK_ENV=development

# 启动Flask应用
if [ "$PORT" != "5000" ]; then
    python app.py --port $PORT
else
    python app.py
fi
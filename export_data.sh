#!/bin/bash

# 数据导出脚本

echo "📊 开始导出训练数据..."

# 检查Python环境
if ! command -v python &> /dev/null; then
    echo "❌ 错误: 未找到Python环境"
    exit 1
fi

# 检查数据库文件
if [ ! -f "interactions.db" ]; then
    echo "❌ 错误: 找不到 interactions.db 文件"
    echo "💡 提示: 请先启动代理服务器并产生一些对话数据"
    exit 1
fi

# 检查确认的数据
python -c "
import sqlite3
conn = sqlite3.connect('interactions.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM confirmed_interactions')
count = cursor.fetchone()[0]
conn.close()
if count == 0:
    print('⚠️  警告: 没有找到已确认的对话记录')
    print('💡 提示: 请先在Web界面中确认一些有价值的对话')
    exit(1)
else:
    print(f'✅ 找到 {count} 条已确认的对话记录')
"

if [ $? -ne 0 ]; then
    exit 1
fi

echo "🔄 正在处理数据..."

# 运行数据处理脚本
python process_conversations.py

if [ $? -eq 0 ]; then
    echo ""
    echo "🎉 数据导出完成！"
    echo "📁 输出文件:"
    echo "   - conversations.jsonl (有效的训练数据)"
    echo "   - invalid_conversations.jsonl (无效的数据)"
    echo ""
    echo "📊 文件统计:"
    if [ -f "conversations.jsonl" ]; then
        lines=$(wc -l < conversations.jsonl)
        size=$(du -h conversations.jsonl | cut -f1)
        echo "   - 有效记录: $lines 条"
        echo "   - 文件大小: $size"
    fi
    echo ""
    echo "💡 使用提示:"
    echo "   - 可以直接使用 conversations.jsonl 进行模型训练"
    echo "   - 格式符合 ShareGPT 标准"
    echo "   - 支持 function calling 和工具调用"
else
    echo "❌ 数据导出失败"
    exit 1
fi
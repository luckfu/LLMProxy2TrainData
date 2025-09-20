import sqlite3
from flask import Flask, render_template, jsonify, request
from datetime import datetime
import json

app = Flask(__name__)

def ensure_confirmed_table():
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS confirmed_interactions (
                id TEXT PRIMARY KEY,
                model TEXT,
                conversation TEXT,
                original_timestamp DATETIME,
                confirmed_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    finally:
        conn.close()

# 在首次请求前确保表存在
@app.before_first_request
def _init_tables():
    ensure_confirmed_table()

def get_db_connection():
    conn = sqlite3.connect('interactions.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_interactions')
def get_interactions():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 获取所有记录
    cursor.execute('SELECT id, model, conversation, timestamp FROM interactions')
    rows = cursor.fetchall()
    
    def _pretty_or_raw(text: str, indent: int = 2, max_len: int = 8000) -> str:
        """仅用于展示的美化：能解析JSON则缩进显示，过长添加截断提示"""
        try:
            obj = json.loads(text)
            pretty = json.dumps(obj, ensure_ascii=False, indent=indent)
        except Exception:
            pretty = text
        if isinstance(pretty, str) and len(pretty) > max_len:
            return pretty[:max_len] + "\n... truncated for display ..."
        return pretty

    # 处理数据
    data = []
    for row in rows:
        try:
            # 解析conversation JSON字符串
            conversation = json.loads(row['conversation'])

            # 构建仅用于展示的美化版，不修改原始数据
            conversation_pretty = {
                "conversations": [],
                "system": conversation.get("system", ""),
                "tools": conversation.get("tools", "[]")
            }
            preview = ''
            conv_list = conversation.get('conversations', [])
            # 计算是否为“仅工具调用”记录：存在 function_call，且不存在 gpt/assistant 文本项
            function_call_only = False
            if isinstance(conv_list, list):
                has_function_call = any(isinstance(m, dict) and m.get('from') == 'function_call' for m in conv_list)
                has_gpt_text = any(isinstance(m, dict) and m.get('from') in ('gpt', 'assistant') for m in conv_list)
                function_call_only = bool(has_function_call and not has_gpt_text)

                for msg in conv_list:
                    pretty_msg = dict(msg)
                    # 仅对 function_call/observation 的 value 做 JSON 美化尝试
                    if msg.get('from') in ('function_call', 'observation'):
                        val = msg.get('value', '')
                        if isinstance(val, str):
                            pretty_msg['value'] = _pretty_or_raw(val)
                        else:
                            pretty_msg['value'] = _pretty_or_raw(str(val))
                    conversation_pretty['conversations'].append(pretty_msg)

                    # 预览使用美化后的简短文本
                    pv = pretty_msg.get('value', '')
                    if not isinstance(pv, str):
                        pv = str(pv)
                    preview += f"{pretty_msg.get('from')}: {pv[:50]}...\n"
            
            data.append({
                'id': row['id'],
                'model': row['model'],
                'conversation': preview,                       # 简短预览（已美化）
                'full_conversation': conversation,             # 原始数据（训练/导出）
                'full_conversation_pretty': conversation_pretty,  # 展示用数据
                'function_call_only': function_call_only,       # 新增：仅工具调用标记
                'timestamp': row['timestamp']
            })
        except json.JSONDecodeError:
            continue
    
    conn.close()
    return jsonify({'data': data})

@app.route('/delete', methods=['POST'])
def delete_interaction():
    data = request.get_json()
    interaction_id = data.get('id')
    
    if not interaction_id:
        return jsonify({'error': '未提供ID'}), 400
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM interactions WHERE id = ?', (interaction_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/get_confirmed')
def get_confirmed():
    conn = get_db_connection()
    cursor = conn.cursor()
    # 确保表存在（防止还未调用 confirm 前就查询）
    ensure_confirmed_table()
    cursor.execute('SELECT id, model, conversation, original_timestamp, confirmed_timestamp FROM confirmed_interactions ORDER BY confirmed_timestamp DESC')
    rows = cursor.fetchall()
    data = []
    for row in rows:
        try:
            conv = json.loads(row['conversation'])
        except Exception:
            conv = row['conversation']

        # 计算 confirmed 记录是否为“仅工具调用”
        function_call_only = False
        try:
            conv_list = conv.get('conversations', []) if isinstance(conv, dict) else []
            has_function_call = any(isinstance(m, dict) and m.get('from') == 'function_call' for m in conv_list)
            has_gpt_text = any(isinstance(m, dict) and m.get('from') in ('gpt', 'assistant') for m in conv_list)
            function_call_only = bool(has_function_call and not has_gpt_text)
        except Exception:
            function_call_only = False

        data.append({
            'id': row['id'],
            'model': row['model'],
            'full_conversation': conv,
            'function_call_only': function_call_only,
            'original_timestamp': row['original_timestamp'],
            'confirmed_timestamp': row['confirmed_timestamp']
        })
    conn.close()
    return jsonify({'data': data})

@app.route('/confirm', methods=['POST'])
def confirm_interaction():
    data = request.get_json()
    interaction_id = data.get('id')
    
    if not interaction_id:
        return jsonify({'error': '未提供ID'}), 400
    
    conn = get_db_connection()
    try:
        # 获取要确认的记录
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM interactions WHERE id = ?', (interaction_id,))
        record = cursor.fetchone()
        
        if record:
            # 创建confirmed_interactions表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS confirmed_interactions (
                    id TEXT PRIMARY KEY,
                    model TEXT,
                    conversation TEXT,
                    original_timestamp DATETIME,
                    confirmed_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 插入到confirmed_interactions表
            cursor.execute('''
                INSERT INTO confirmed_interactions (id, model, conversation, original_timestamp)
                VALUES (?, ?, ?, ?)
            ''', (record['id'], record['model'], record['conversation'], record['timestamp']))
            
            # 从原表删除
            cursor.execute('DELETE FROM interactions WHERE id = ?', (interaction_id,))
            
            conn.commit()
            return jsonify({'success': True})
        else:
            return jsonify({'error': '记录不存在'}), 404
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True)
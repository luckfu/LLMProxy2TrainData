import sqlite3
import json
import os

def process_conversations(db_path="interactions.db", output_file="conversations.jsonl", invalid_file="invalid_conversations.jsonl"):
    def convert_tools_to_string(obj):
        """递归转换 'tools' 字段：如果它是列表，转换为 JSON 字符串"""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == 'tools' and isinstance(value, list):
                    obj[key] = json.dumps(value, ensure_ascii=False)
                else:
                    convert_tools_to_string(value)
        elif isinstance(obj, list):
            for item in obj:
                convert_tools_to_string(item)
        return obj
    
    def fix_function_call_format(data):
        """修复 function_call 格式问题"""
        if "conversations" in data and isinstance(data["conversations"], list):
            for conv in data["conversations"]:
                if conv.get("from") == "function_call" and "value" in conv:
                    try:
                        # 解析 function_call 的 value
                        call_data = json.loads(conv["value"])
                        
                        # 检查是否有嵌套的 function 结构
                        if "function" in call_data and isinstance(call_data["function"], dict):
                            function_info = call_data["function"]
                            # 重构为正确的格式
                            new_call_data = {
                                "name": function_info.get("name", ""),
                                "arguments": function_info.get("arguments", "{}")
                            }
                            # 保留其他字段（如 id, type）
                            for key, value in call_data.items():
                                if key != "function":
                                    new_call_data[key] = value
                            
                            # 更新 value
                            conv["value"] = json.dumps(new_call_data, ensure_ascii=False)
                    except (json.JSONDecodeError, KeyError) as e:
                        # 如果解析失败，保持原样
                        pass
        return data
    
    def validate_data(data):
        """验证 ShareGPT 结构"""
        if not isinstance(data, dict):
            raise ValueError("不是字典对象")
        if "conversations" not in data:
            raise ValueError("缺少 'conversations' 字段")
        if not isinstance(data["conversations"], list):
            raise ValueError("'conversations' 不是列表")
        for conv in data["conversations"]:
            if not all(key in conv for key in ["from", "value"]):
                raise ValueError("对话回合缺少 'from' 或 'value'")
        if "system" not in data or not isinstance(data["system"], str):
            raise ValueError("缺少 'system' 字段或不是字符串")
        
        # 修复：允许tools字段为列表或字符串
        if "tools" not in data:
            raise ValueError("缺少 'tools' 字段")
        if isinstance(data["tools"], list):
            # 如果是列表，转换为JSON字符串
            data["tools"] = json.dumps(data["tools"], ensure_ascii=False)
        elif not isinstance(data["tools"], str):
            raise ValueError("'tools' 字段必须是字符串或列表")
        
        # 验证tools字符串是否为有效JSON
        try:
            json.loads(data["tools"])
        except json.JSONDecodeError:
            raise ValueError("'tools' 字符串不是有效的 JSON")
    
    try:
        # 连接数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 查询 conversation 字段 - 修复：使用正确的表名
        query = "SELECT conversation FROM interactions ORDER BY timestamp"
        cursor.execute(query)
        rows = cursor.fetchall()
        
        if not rows:
            print("❌ 数据库中没有找到任何数据")
            return
        
        valid_count = 0
        invalid_count = 0
        errors = []
        
        with open(output_file, 'w', encoding='utf-8') as valid_f, open(invalid_file, 'w', encoding='utf-8') as invalid_f:
            for idx, (conversation_str,) in enumerate(rows, 1):
                try:
                    # 解析 JSON
                    data = json.loads(conversation_str)
                    
                    # 修复 function_call 格式 - 已在保存时处理，这里可以跳过
                    # data = fix_function_call_format(data)
                    
                    # 转换 tools - 已在保存时处理，这里可以跳过
                    # data = convert_tools_to_string(data)
                    
                    # 验证
                    validate_data(data)
                    
                    # 写入有效文件
                    valid_f.write(json.dumps(data, ensure_ascii=False) + '\n')
                    valid_count += 1
                except (json.JSONDecodeError, ValueError) as e:
                    invalid_count += 1
                    invalid_f.write(conversation_str + '\n')  # 写入原始字符串
                    errors.append(f"记录 {idx}: {str(e)}")
        
        print(f"✅ 成功处理 {len(rows)} 条记录")
        print(f"✅ 有效记录: {valid_count} (保存到 {output_file})")
        print(f"❌ 无效记录: {invalid_count} (保存到 {invalid_file})")
        if errors:
            print("\n错误详情:")
            for error in errors[:10]:
                print(error)
        print(f"📁 {output_file} 大小: {os.path.getsize(output_file) / 1024:.2f} KB")
        
    except sqlite3.Error as e:
        print(f"❌ 数据库错误: {e}")
    except Exception as e:
        print(f"❌ 处理过程中发生错误: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    print("🚀 开始处理对话数据...")
    if not os.path.exists("interactions.db"):
        print("❌ 找不到 interactions.db 文件")
        exit(1)
    process_conversations()
    print("🎉 处理完成！")
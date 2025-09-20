import sqlite3
import json
import os
import argparse
from typing import Dict, Any, List, Tuple

def convert_tools_to_string(obj):
    """递归将 'tools' 字段（若为列表）转换为 JSON 字符串。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "tools" and isinstance(v, list):
                obj[k] = json.dumps(v, ensure_ascii=False)
            else:
                convert_tools_to_string(v)
    elif isinstance(obj, list):
        for it in obj:
            convert_tools_to_string(it)
    return obj

def validate_sharegpt(data: Dict[str, Any]) -> None:
    """验证 ShareGPT 扩展结构（包含 function_call/observation）。有问题直接抛异常。"""
    if not isinstance(data, dict):
        raise ValueError("根对象不是字典")
    if "conversations" not in data or not isinstance(data["conversations"], list):
        raise ValueError("缺少或错误的 conversations")
    for conv in data["conversations"]:
        if not isinstance(conv, dict):
            raise ValueError("conversation 条目不是对象")
        if "from" not in conv or "value" not in conv:
            raise ValueError("conversation 条目缺少 from/value")
    if "system" not in data or not isinstance(data["system"], str):
        raise ValueError("缺少或错误的 system 字段")
    if "tools" not in data:
        raise ValueError("缺少 tools 字段")
    # tools 可为字符串或列表
    if isinstance(data["tools"], list):
        data["tools"] = json.dumps(data["tools"], ensure_ascii=False)
    elif not isinstance(data["tools"], str):
        raise ValueError("tools 必须是字符串或列表")
    # 校验 tools 字符串是合法 JSON
    try:
        json.loads(data["tools"])
    except Exception:
        raise ValueError("tools 字符串不是有效 JSON")

def is_function_call_only(conv_list: List[Dict[str, Any]]) -> bool:
    """判断是否为仅工具调用样本：存在 function_call，且不存在 gpt/assistant 文本。"""
    if not isinstance(conv_list, list):
        return False
    has_fc = any(isinstance(m, dict) and m.get("from") == "function_call" for m in conv_list)
    has_gpt = any(isinstance(m, dict) and m.get("from") in ("gpt", "assistant") for m in conv_list)
    return bool(has_fc and not has_gpt)

def has_tool_use(conv_list: List[Dict[str, Any]]) -> bool:
    return any(isinstance(m, dict) and m.get("from") in ("function_call", "observation") for m in conv_list)

def truncate_observation_inplace(data: Dict[str, Any], max_len: int) -> None:
    """对 observation 的 value 进行长度截断（仅导出时生效，不改数据库）。"""
    if max_len <= 0:
        return
    convs = data.get("conversations", [])
    if not isinstance(convs, list):
        return
    for m in convs:
        if isinstance(m, dict) and m.get("from") == "observation":
            v = m.get("value")
            if isinstance(v, str) and len(v) > max_len:
                m["value"] = v[:max_len] + "\n... [truncated at export] ..."

def fetch_rows(conn: sqlite3.Connection, table: str, model_filter: str = "") -> List[Tuple[str]]:
    """从指定表获取 conversation 列内容。"""
    cur = conn.cursor()
    if table == "confirmed_interactions":
        base_sql = "SELECT model, conversation FROM confirmed_interactions ORDER BY confirmed_timestamp"
    else:
        base_sql = "SELECT model, conversation FROM interactions ORDER BY timestamp"
    if model_filter:
        cur.execute(base_sql)
        all_rows = cur.fetchall()
        return [(m, c) for (m, c) in all_rows if model_filter in (m or "")]
    cur.execute(base_sql)
    return cur.fetchall()

def process_conversations(
    db_path: str = "interactions.db",
    table: str = "interactions",
    output_file: str = "conversations.jsonl",
    invalid_file: str = "invalid_conversations.jsonl",
    only_function_call_only: bool = False,
    truncate_observation: int = 0,
    model_filter: str = "",
    max_records: int = 0
) -> None:
    """导出 + 校验：从数据库导出 ShareGPT 扩展格式 JSONL，并输出统计。"""
    conn = None
    if table not in ("interactions", "confirmed_interactions"):
        raise ValueError("table 必须为 interactions 或 confirmed_interactions")

    try:
        conn = sqlite3.connect(db_path)
        rows = fetch_rows(conn, table, model_filter)

        if not rows:
            print("❌ 没有匹配的数据")
            return

        valid_count = 0
        invalid_count = 0
        fc_only_count = 0
        with_tool_count = 0
        with_gpt_count = 0
        errors: List[str] = []
        total = 0

        with open(output_file, "w", encoding="utf-8") as f_ok, open(invalid_file, "w", encoding="utf-8") as f_bad:
            for idx, (model, conv_str) in enumerate(rows, 1):
                if max_records and valid_count + invalid_count >= max_records:
                    break
                total += 1
                try:
                    data = json.loads(conv_str)

                    # 统一 tools 字段为字符串
                    convert_tools_to_string(data)

                    # 校验结构
                    validate_sharegpt(data)

                    # 指标统计
                    conv_list = data.get("conversations", [])
                    if has_tool_use(conv_list):
                        with_tool_count += 1
                    if any(isinstance(m, dict) and m.get("from") in ("gpt", "assistant") for m in conv_list):
                        with_gpt_count += 1

                    fc_only = is_function_call_only(conv_list)
                    if only_function_call_only and not fc_only:
                        # 过滤掉非 function_call-only
                        continue
                    if fc_only:
                        fc_only_count += 1

                    # 导出前对 observation 截断（不影响原数据）
                    if truncate_observation > 0:
                        truncate_observation_inplace(data, truncate_observation)

                    # 写入有效样本
                    f_ok.write(json.dumps(data, ensure_ascii=False) + "\n")
                    valid_count += 1
                except Exception as e:
                    invalid_count += 1
                    # 原样写出无效记录，便于后续排查
                    f_bad.write(conv_str + "\n")
                    if len(errors) < 20:
                        errors.append(f"记录 {idx}: {repr(e)}")

        print("✅ 导出完成")
        print(f"📦 来源表: {table}")
        print(f"🧮 总读取条数: {total}")
        print(f"🟢 有效: {valid_count}  | 🔴 无效: {invalid_count}")
        if valid_count:
            print(f"🛠️ 含工具调用: {with_tool_count} ({with_tool_count / max(valid_count,1):.1%} of valid)")
            print(f"🗣️ 含助手文本: {with_gpt_count} ({with_gpt_count / max(valid_count,1):.1%} of valid)")
            print(f"🧩 function_call-only: {fc_only_count} ({fc_only_count / max(valid_count,1):.1%} of valid)")
        if errors:
            print("\n部分错误样例（最多20条）：")
            for e in errors:
                print("-", e)
        if os.path.exists(output_file):
            print(f"\n📁 导出文件: {output_file}  大小: {os.path.getsize(output_file)/1024:.2f} KB")
        if os.path.exists(invalid_file):
            print(f"🧾 无效样本: {invalid_file}  大小: {os.path.getsize(invalid_file)/1024:.2f} KB")

    finally:
        try:
            conn and conn.close()
        except Exception:
            pass

def parse_args():
    p = argparse.ArgumentParser(description="导出/校验 ShareGPT 扩展数据（支持 function_call/observation）")
    p.add_argument("--db", default="interactions.db", help="数据库路径")
    p.add_argument("--table", default="interactions", choices=["interactions", "confirmed_interactions"], help="导出来源表")
    p.add_argument("--output", default="conversations.jsonl", help="有效样本导出文件")
    p.add_argument("--invalid", default="invalid_conversations.jsonl", help="无效样本导出文件")
    p.add_argument("--only-function-call-only", action="store_true", help="仅导出 function_call-only 样本")
    p.add_argument("--truncate-observation", type=int, default=0, help="对 observation 的 value 进行长度截断（0 表示不截断）")
    p.add_argument("--model-filter", default="", help="按模型名包含匹配过滤（简单包含匹配）")
    p.add_argument("--max-records", type=int, default=0, help="最多处理多少条（0 表示不限制）")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    if not os.path.exists(args.db):
        print(f"❌ 找不到数据库文件: {args.db}")
        raise SystemExit(1)
    print("🚀 开始导出/校验 ...")
    process_conversations(
        db_path=args.db,
        table=args.table,
        output_file=args.output,
        invalid_file=args.invalid,
        only_function_call_only=args.only_function_call_only,
        truncate_observation=args.truncate_observation,
        model_filter=args.model_filter,
        max_records=args.max_records
    )
    print("🎉 完成")
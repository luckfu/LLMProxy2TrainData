import sqlite3
import json
import os
import argparse
import uuid
from typing import Dict, Any, List, Tuple, cast

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

def sharegpt_to_openai_messages(conv_list: List[Dict[str, Any]], system_text: str = "") -> List[Dict[str, Any]]:
    """
    将 ShareGPT 扩展 conversations 转为 OpenAI Chat messages。
    规则：
    - human -> user
    - gpt/assistant -> assistant（content 为文本）
    - function_call -> 收集到 pending tool_calls，稍后作为一条 assistant（content=""）的 tool_calls 一并输出
    - observation -> role=tool，尽量与最近未配对的 tool_call 按顺序配对；若缺 id，则补一个
    - system_text 非空时作为首条 role=system
    """
    messages: List[Dict[str, Any]] = []
    if isinstance(system_text, str) and system_text.strip():
        messages.append({"role": "system", "content": system_text})

    def normalize_tool_call(value: Any, idx: int) -> Dict[str, Any]:
        # value 可能是 JSON 字符串或对象，包含 {id?, type, function:{name, arguments}}
        tc = value
        if isinstance(value, str):
            try:
                tc = json.loads(value)
            except Exception:
                # 兜底：作为未知函数名、原串作为参数
                return {
                    "id": f"toolcall_{idx}",
                    "type": "function",
                    "function": {"name": "unknown_tool", "arguments": json.dumps({"raw": value}, ensure_ascii=False)}
                }
        if not isinstance(tc, dict):
            tc = {}
        func = tc.get("function", {}) if isinstance(tc.get("function", {}), dict) else {}
        name = func.get("name") or "unknown_tool"
        args = func.get("arguments", {})
        if not isinstance(args, str):
            try:
                args = json.dumps(args, ensure_ascii=False)
            except Exception:
                args = json.dumps({"raw": str(func.get("arguments"))}, ensure_ascii=False)
        tool_id = tc.get("id") or f"toolcall_{idx}"
        return {"id": tool_id, "type": "function", "function": {"name": name, "arguments": args}}

    pending_tool_calls: List[Dict[str, Any]] = []
    orphan_counter = 0
    for i, item in enumerate(conv_list or []):
        if not isinstance(item, dict):
            continue
        frm = item.get("from")
        val = item.get("value", "")
        if frm == "human":
            # flush pending tool_calls before new user turn
            if pending_tool_calls:
                messages.append({"role": "assistant", "content": "", "tool_calls": pending_tool_calls})
                pending_tool_calls = []
            messages.append({"role": "user", "content": val if isinstance(val, str) else str(val)})
        elif frm in ("gpt", "assistant"):
            # flush pending tool_calls first
            if pending_tool_calls:
                messages.append({"role": "assistant", "content": "", "tool_calls": pending_tool_calls})
                pending_tool_calls = []
            if isinstance(val, str) and val.strip():
                messages.append({"role": "assistant", "content": val})
        elif frm == "function_call":
            tc = normalize_tool_call(val, len(pending_tool_calls) + 1)
            pending_tool_calls.append(tc)
        elif frm == "observation":
            tool_content = val if isinstance(val, str) else str(val)
            if pending_tool_calls:
                # pair with the earliest unpaired
                tc = pending_tool_calls.pop(0)
                # ensure assistant tool_calls emitted before tool response
                messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_content})
            else:
                # orphan observation: synthesize an id for compatibility
                orphan_counter += 1
                oc_id = f"orphan_tool_{orphan_counter}"
                # 也可直接输出无 tool_call_id 的 tool 消息；此处采用合成 id，兼容性更好
                messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": oc_id, "type": "function", "function": {"name": "unknown_tool", "arguments": "{}"}}]})
                messages.append({"role": "tool", "tool_call_id": oc_id, "content": tool_content})
        else:
            # 其他 from 值忽略或作为注释性文本
            pass

    # flush trailing pending tool_calls
    if pending_tool_calls:
        messages.append({"role": "assistant", "content": "", "tool_calls": pending_tool_calls})

    return messages

def _safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None

def _guess_json_type(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    return "string"

def derive_tools_schema(conv_list: List[Dict[str, Any]], tools_schema_mode: str, tools_raw: Any) -> List[Dict[str, Any]]:
    """
    基于策略生成 OpenAI tools 列表：
    - auto: 若 tools_raw 存在且为合法 JSON 列表则透传；否则当 conv_list 含 function_call 时按实际 arguments 推断最小 schema
    - yes:  与 auto 类似，但即便无 function_call 也尝试透传/推断
    - no:   不输出
    - derive: 忽略 tools_raw，完全按 conv_list 推断
    """
    mode = (tools_schema_mode or "auto").lower()
    baked_tools = []
    if isinstance(tools_raw, str) and tools_raw.strip():
        parsed = _safe_json_loads(tools_raw)
        if isinstance(parsed, list):
            baked_tools = parsed

    if mode == "no":
        return []
    if mode == "auto":
        if baked_tools:
            return baked_tools
        has_fc = any(isinstance(it, dict) and it.get("from") == "function_call" for it in (conv_list or []))
        if not has_fc:
            return []
    elif mode == "yes":
        if baked_tools:
            return baked_tools
        # 需要推断
        pass
    elif mode == "derive":
        pass

    schema_map: Dict[str, Dict[str, Any]] = {}
    for it in (conv_list or []):
        if not (isinstance(it, dict) and it.get("from") == "function_call"):
            continue
        val = it.get("value")
        if isinstance(val, str):
            obj = _safe_json_loads(val)
        elif isinstance(val, dict):
            obj = val
        else:
            obj = None
        if not isinstance(obj, dict):
            obj = {}
        func = obj.get("function", {}) if isinstance(obj.get("function"), dict) else {}
        name = func.get("name") or "unknown_tool"
        args_raw = func.get("arguments", {})
        if isinstance(args_raw, str):
            args_obj = _safe_json_loads(args_raw)
            if not isinstance(args_obj, dict):
                args_obj = {}
        elif isinstance(args_raw, dict):
            args_obj = args_raw
        else:
            args_obj = {}

        if name not in schema_map:
            schema_map[name] = {
                "type": "function",
                "function": {
                    "name": name,
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            }
        params = schema_map[name]["function"]["parameters"]
        props = params["properties"]
        req = params["required"]
        for k, v in args_obj.items():
            if k not in props:
                props[k] = {"type": _guess_json_type(v)}
            if k not in req:
                req.append(k)

    return list(schema_map.values())

def is_function_call_only(conv_list: List[Dict[str, Any]]) -> bool:
    """
    按“最后一轮”判断 fc-only：
    - 在 conversations 顺序中，找到最后一个 from 属于 {human/gpt/function_call/observation} 的条目
    - 若最后一个为 function_call（其后没有 gpt 文本），则视为 fc-only
    注意：历史中出现过 gpt 文本不影响该轮是否为 fc-only
    """
    if not isinstance(conv_list, list) or not conv_list:
        return False
    last_role = None
    for item in conv_list:
        if not isinstance(item, dict):
            continue
        role = item.get("from")
        if role in ("human", "gpt", "function_call", "observation", "assistant"):
            last_role = role
    return last_role == "function_call"

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

def fetch_rows(conn: sqlite3.Connection, table: str, model_filter: str = "") -> List[Tuple[str, str]]:
    """从指定表获取 conversation 列内容。"""
    cur = conn.cursor()
    if table == "confirmed_interactions":
        base_sql = "SELECT model, conversation FROM confirmed_interactions ORDER BY confirmed_timestamp"
    else:
        base_sql = "SELECT model, conversation FROM interactions ORDER BY timestamp"
    if model_filter:
        cur.execute(base_sql)
        all_rows = cur.fetchall()
        # 规范化为字符串元组，避免 None 触发类型告警
        norm_rows: List[Tuple[str, str]] = [(str(m or ""), str(c or "")) for (m, c) in all_rows]
        return [rc for rc in norm_rows if model_filter in rc[0]]
    cur.execute(base_sql)
    # 显式收敛类型
    return cast(List[Tuple[str, str]], [(str(m or ""), str(c or "")) for (m, c) in cur.fetchall()])

def process_conversations(
    db_path: str = "interactions.db",
    table: str = "interactions",
    output_file: str = "conversations.jsonl",
    invalid_file: str = "invalid_conversations.jsonl",
    only_function_call_only: bool = False,
    truncate_observation: int = 0,
    model_filter: str = "",
    max_records: int = 0,
    out_format: str = "sharegpt",
    tools_schema_mode: str = "auto"
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

                    # 写入有效样本（支持两种格式）
                    if out_format == "openai":
                        msgs = sharegpt_to_openai_messages(conv_list, data.get("system", ""))
                        out_obj = {"messages": msgs}
                        # 当且仅当存在函数调用时，按策略输出 tools
                        has_tool_calls = any(isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls") for m in msgs)
                        if has_tool_calls:
                            tools_list = derive_tools_schema(conv_list, tools_schema_mode, data.get("tools"))
                            if tools_list:
                                out_obj["tools"] = tools_list

                        f_ok.write(json.dumps(out_obj, ensure_ascii=False) + "\n")
                    else:
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
    p.add_argument("--format", default="sharegpt", choices=["sharegpt", "openai"], help="导出格式：sharegpt 或 openai")
    p.add_argument("--tools-schema", default="auto", choices=["auto", "yes", "no", "derive"], help="tools 签名策略：auto(默认)/yes/no/derive")
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
        max_records=args.max_records,
        out_format=args.format,
        tools_schema_mode=args.tools_schema
    )
    print("🎉 完成")
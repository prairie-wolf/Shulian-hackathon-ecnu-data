"""Shared, evidence-based summaries and explicit external-model fallback."""
import json


def _extract_names(result: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    keys = ("scholars", "companies", "entities", "samples", "members",
            "collaborators", "ranking", "sources", "matches")
    for key in keys:
        for item in result.get(key) or []:
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                text = item.get("name") or item.get("en_name") or item.get("member") or item.get("title") or ""
            else:
                text = str(item)
            text = str(text).strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)
                if len(names) >= 15:
                    return names
    for item in result.get("data") or []:
        if isinstance(item, dict) and item.get("name"):
            text = str(item["name"]).strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)
                if len(names) >= 15:
                    return names
    return names


def summarize_result(question: str, result: dict) -> str:
    apology = ("抱歉，根据本平台当前已接入的数据，我暂时没找到可回答这项问题的记录。"
               "你可以换成更明确的平台数据问题试试，例如：“华东师范大学有哪些学者？”、"
               "“大语言模型趋势”、“物流快递行业有哪些公司？”。")
    if not isinstance(result, dict):
        return apology
    intent = result.get("intent", "unknown")
    if intent in ("greeting", "not_found", "unsupported", "ambiguous") and result.get("hint"):
        return "根据本平台当前已接入的数据，" + result["hint"]
    if result.get("error"):
        return "抱歉，根据本平台当前已接入的数据，这次查询没有拿到可用结果。你可以换个问法，或稍后再试。"
    if intent in ("unknown", "none", "unsupported"):
        return apology
    if intent in ("trend", "year_distribution"):
        rows = result.get("data") or []
        if rows:
            total = sum(int(x.get("count") or 0) for x in rows)
            detail = "、".join(f"{x.get('name', x.get('year'))} 年 {x.get('count', 0)} 条" for x in rows)
            return f"根据本平台已接入的数据，共统计 {len(rows)} 个年份，累计 {total} 条记录：{detail}。"
    if intent in ("relation_rank", "numeric_rank") and result.get("data"):
        rows = result["data"]
        shown = rows[:15]
        key = "value" if intent == "numeric_rank" else "count"
        detail = "、".join(f"{x['name']}({x[key]})" for x in shown)
        count = result.get("count", len(rows))
        return f"根据本平台已接入的数据，共查到 {count} 条排名结果，展示 {len(shown)} 条：{detail}。"
    if intent == "entity_detail":
        props = result.get("properties") or {}
        props_text = "、".join(f"{k}：{v}" for k, v in list(props.items())[:8])
        if props_text:
            return f"根据本平台已接入的数据，已查到“{result.get('entity', '该实体')}”的详情：{props_text}。"
        return apology
    if intent == "overview":
        overview_terms = ("平台", "数据", "本体", "概览", "总览", "覆盖", "规模", "统计", "有哪些类", "多少")
        if not any(term in question for term in overview_terms):
            return apology
        classes = result.get("classes") or {}
        if classes:
            summary = "、".join(f"{k} {v} 个" for k, v in list(classes.items())[:12])
            return f"根据本平台已接入的数据，平台当前覆盖 {len(classes)} 类数据：{summary}。"
        return apology
    names = _extract_names(result)
    if names:
        count = result.get("count", result.get("total"))
        if count is None:
            count = max((len(result.get(key) or []) for key in
                        ("data", "sources", "entities", "scholars", "companies", "matches")), default=len(names))
        count = int(count)
        label = {
            "scholars_filtered": "学者",
            "scholars": "学者",
            "institution_members": "学者",
            "industry_companies": "公司",
            "companies_in_industry": "公司",
            "field_entities": "条目",
            "relation_rank": "排名",
            "numeric_rank": "排名",
            "year_distribution": "年度记录",
            "trend": "年度记录",
            "search_entities": "实体",
            "list_class": "实例",
            "cls_list": "实例",
            "list_sources": "数据源",
        }.get(intent, "结果")
        shown = len(names)
        display = f"本次展示 {shown} 条" if count > shown else f"展示 {shown} 条"
        return f"根据本平台已接入的数据，这个问题共查到 {count} 条{label}相关数据，{display}：{'、'.join(names)}。"
    return apology


def answer_with_client(question: str, result: dict, picked: dict, call_client) -> tuple[dict, list[dict], str]:
    intent = result.get("intent", "unknown")
    trace = [{"step": "本体语义查询", "intent": intent,
              "preview": json.dumps(result, ensure_ascii=False)[:260]}]
    if picked["kind"] in ("local", "platform"):
        answer = summarize_result(question, result)
        trace.append({"step": "平台引擎返回结构化结果，并生成人话摘要"})
        return result, trace, answer
    response = call_client(picked["key"], [
        {"role": "system", "content": "你是平台内数据问答助手。只能依据【平台查询结果】中本平台已接入的数据作答，用简洁中文回答，不得使用平台外知识，不得编造。平台没有记录时必须明确回答“根据本平台当前已接入的数据，暂未收录该数据”。"},
        {"role": "user", "content": f"用户问题：{question}\n\n平台查询结果：\n{json.dumps(result, ensure_ascii=False)}"},
    ], timeout=180)
    fallback = bool(response.get("error")) or not response.get("content")
    answer = ("外部 AI 未返回可用答案，以下由本地规则引擎生成。\n\n" +
              summarize_result(question, result)) if fallback else response["content"]
    trace.append({"step": "AI 生成答案", "backend": response.get("backend"),
                  "elapsed_ms": response.get("elapsed_ms"), "error": response.get("error"),
                  "answer_source": "本地规则引擎（外部 AI 失败后回退）" if fallback else picked["name"]})
    return result, trace, answer

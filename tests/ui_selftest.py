# -*- coding: utf-8 -*-
"""控制台 UI 无头自测（Streamlit AppTest）
覆盖：首屏渲染 / 登录 / 六页遍历 / 语义问答连问与历史累积 / 输入框对比度 CSS
只读为主；不注册新账号、不写 users.json。
"""
import os, sys, json, re
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui-report.json")
os.chdir(REPO)
sys.path.insert(0, REPO)

from streamlit.testing.v1 import AppTest

report = {"checks": [], "pages": [], "chat": {}, "css": {}, "fatal": None}
def add(name, ok, detail=""):
    report["checks"].append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("  PASS " if ok else "  FAIL ") + name + (("  " + str(detail)[:160]) if detail else ""))

def exceptions_of(at):
    try:
        return [f"{type(e).__name__}: {e}" for e in at.exception]
    except Exception as e:
        return [f"(读异常失败) {e}"]

def current_page(at):
    for m in at.markdown:
        mm = re.search(r"<h1>([^<]+)</h1>", m.value or "")
        if mm:
            return mm.group(1).strip()
    return None

try:
    at = AppTest.from_file(os.path.join(REPO, "app", "console.py"), default_timeout=300)
    at.run()
    add("首屏渲染无异常", len(exceptions_of(at)) == 0, exceptions_of(at))

    # --- 提取注入的 CSS，验输入框对比度 ---
    css = ""
    for m in at.markdown:
        if m.value and "<style>" in m.value:
            css = m.value
            break
    report["css"]["len"] = len(css)
    # 找输入类选择器的 color / background
    hits = re.findall(r"([^{}]*input[^{}]*)\{([^}]*)\}", css, re.I)
    colors = []
    for sel, body in hits:
        c = re.search(r"(?<!-)color\s*:\s*(#[0-9a-fA-F]{3,8})", body)
        bg = re.search(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8})", body)
        if c or bg:
            colors.append({"selector": sel.strip()[:80], "color": c.group(1) if c else None, "background": bg.group(1) if bg else None})
    report["css"]["input_rules"] = colors[:20]
    # 对比度计算（仅当同一条规则里同时有 color 和 background）
    def lum(h):
        h = h.lstrip("#")
        if len(h) == 3: h = "".join(x*2 for x in h)
        r, g, b = (int(h[i:i+2], 16)/255 for i in (0, 2, 4))
        f = lambda c: c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4
        return 0.2126*f(r) + 0.7152*f(g) + 0.0722*f(b)
    ratios = []
    for r in colors:
        if r["color"] and r["background"]:
            l1, l2 = lum(r["color"]), lum(r["background"])
            hi, lo = max(l1, l2), min(l1, l2)
            ratios.append({"selector": r["selector"], "ratio": round((hi+0.05)/(lo+0.05), 2), "color": r["color"], "bg": r["background"]})
    report["css"]["contrast"] = ratios
    add("CSS 中能找到输入框颜色规则", len(colors) > 0, f"{len(colors)} 条")

    # --- 登录 ---
    if len(at.text_input) >= 2:
        at.text_input[0].set_value("demo")
        at.text_input[1].set_value("demo123")
        if len(at.button) > 0:
            at.button[0].click()
        at.run()
    ex = exceptions_of(at)
    add("登录无异常", len(ex) == 0, ex)
    logged_in = (current_page(at) is not None) or any("运行总览" in (m.value or "") for m in at.markdown)
    add("登录后进入控制台", logged_in, f"当前页={current_page(at)}")

    # --- 六页遍历 ---
    # 坑：radio 的 options 已经被 format_func 格式化成 "名称 · 提示"，
    #     再把它 set_value 回去会被二次格式化 -> ValueError: ... is not in list。
    #     必须用未格式化的短名（NAV_ITEMS）。
    from app.ui_kit import NAV_ITEMS
    nav_idx = None
    for i, r in enumerate(at.radio):
        joined = " ".join(r.options or [])
        if any(item in joined for item in NAV_ITEMS):
            nav_idx = i
            break
    if nav_idx is not None:
        report["nav_options"] = list(at.radio[nav_idx].options)
        for item in NAV_ITEMS:
            at.radio[nav_idx].set_value(item)
            at.run()
            at.run()   # AppTest 下页面切换有 1 拍延迟，跑两次才真正落到目标页
            ex = exceptions_of(at)
            page = current_page(at)
            report["pages"].append({"nav": item, "page_title": page, "exceptions": ex,
                                    "chat_input": len(at.chat_input), "buttons": len(at.button)})
            add(f"页面[{item}] 无异常", len(ex) == 0, (page or "") + " " + str(ex))
    else:
        add("找到导航 radio", False, "未找到")

    # --- 语义问答：连问，看历史累积 ---
    if nav_idx is not None:
        at.radio[nav_idx].set_value("语义问答")   # 同样必须用短名
        at.run()
        at.run()
    if len(at.chat_input) > 0:
        counts = []
        for q in ["人工智能行业有哪些公司？", "知识图谱领域有哪些论文？", "华东师范大学有哪些学者？"]:
            at.chat_input[0].set_value(q)
            at.run()
            ex = exceptions_of(at)
            # 数消息条数：聊天区通常用 markdown / 自定义容器，这里用 markdown 数量近似
            counts.append({"q": q, "exceptions": ex, "markdown": len(at.markdown)})
        report["chat"] = {"turns": counts}
        add("语义问答连问 3 次无异常", all(len(c["exceptions"]) == 0 for c in counts),
            [len(c["exceptions"]) for c in counts])
        add("对话历史在累积", counts[-1]["markdown"] > counts[0]["markdown"],
            f"首轮 markdown={counts[0]['markdown']} 末轮={counts[-1]['markdown']}")
    else:
        add("语义问答页有 chat_input", False, "未找到 chat_input")

except Exception:
    import traceback
    report["fatal"] = traceback.format_exc()
    print(report["fatal"])

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
ok = sum(1 for c in report["checks"] if c["ok"]); bad = len(report["checks"]) - ok
print(f"\n结果：PASS={ok} FAIL={bad}  -> {OUT}")

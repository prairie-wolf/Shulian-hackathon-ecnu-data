# -*- coding: utf-8 -*-
"""Shared product UI primitives for the platform console."""
from __future__ import annotations

import html
from typing import Iterable, Mapping, Sequence

import streamlit as st


NAV_ITEMS = ["运行总览", "数据资产", "语义问答", "本体与工具", "AI 接入", "系统设置"]
NAV_HINTS = {
    "运行总览": "平台状态与规模",
    "数据资产": "接入、分区与私人库",
    "语义问答": "自然语言调用数据",
    "本体与工具": "语义契约与端点",
    "AI 接入": "模型登记与协议",
    "系统设置": "账户、额度与运行信息",
}

_CSS = r"""
<style>
:root{
  --bg:#f4f6fa;--panel:#fff;--soft:#f8fafc;--line:#e3e8ef;--ink:#101828;--muted:#667085;
  --subtle:#98a2b3;--teal:#0f766e;--blue:#2563eb;--amber:#b45309;--red:#b42318;--green:#15803d;
  --shadow:0 1px 2px rgba(16,24,40,.05),0 1px 3px rgba(16,24,40,.08);
  --shadow2:0 18px 34px -24px rgba(16,24,40,.42),0 2px 8px rgba(16,24,40,.06);
}
html,body,.stApp,p,li,td,th,label,input,textarea,.stButton>button,[data-testid="stMetric"],
[data-testid="stSidebar"]{font-family:Inter,"Segoe UI",-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif!important}
[data-testid="stAppViewContainer"],.stApp{background:var(--bg)!important}
#MainMenu,footer,[data-testid="stDecoration"]{visibility:hidden!important}
[data-testid="stToolbar"]{display:flex!important;visibility:visible!important;background:transparent!important;
  border:0!important;box-shadow:none!important;z-index:100000!important}
[data-testid="stToolbar"] [data-testid="stStatusWidget"],
[data-testid="stToolbar"] [data-testid="stAppDeployButton"],
[data-testid="stToolbar"] [data-testid="stMainMenu"]{display:none!important}
[data-testid="stExpandSidebarButton"]{display:inline-flex!important;visibility:visible!important;opacity:1!important;
  color:#101828!important;background:#fff!important;border:1px solid #d0d5dd!important;border-radius:7px!important;
  min-width:36px!important;min-height:36px!important;box-shadow:var(--shadow)!important}
[data-testid="stSidebarCollapseButton"]{display:inline-flex!important;visibility:visible!important;opacity:1!important}
header[data-testid="stHeader"]{background:transparent!important;min-height:0!important}
[data-testid="stSidebar"]{display:none!important}
@media(min-width:901px){
  div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:has(.st-key-side_rail){
    flex:0 0 272px!important;min-width:272px!important;max-width:272px!important
  }
}
.st-key-side_rail{background:#0b1f33!important;border:0!important;border-radius:8px!important;
  padding:16px 14px 14px!important;min-height:calc(100vh - 52px)!important;box-shadow:var(--shadow2);
  position:sticky!important;top:12px}
.st-key-side_rail p,.st-key-side_rail span,.st-key-side_rail label,.st-key-side_rail .stCaption{color:#c9d5e3!important}
.st-key-side_rail h1,.st-key-side_rail h2,.st-key-side_rail h3{color:#f8fafc!important}
.st-key-side_rail hr{border-color:rgba(255,255,255,.12)!important}
.st-key-side_rail .stButton>button{background:rgba(255,255,255,.06)!important;color:#eff6ff!important;
  border:1px solid rgba(255,255,255,.16)!important}
.st-key-side_rail [data-testid="stTabs"] [data-baseweb="tab"]{color:#aebccf!important}
.st-key-side_rail [data-testid="stRadio"] [role="radiogroup"]{display:grid!important;grid-template-columns:1fr!important;
  gap:3px!important;width:100%!important}
.st-key-side_rail [data-testid="stRadio"] [role="radio"]{width:100%!important;border-radius:7px!important;
  padding:9px 11px!important;color:#c9d5e3!important;background:transparent!important}
.st-key-side_rail [data-testid="stRadio"] [role="radio"]:hover{background:rgba(255,255,255,.07)!important}
.st-key-side_rail [data-testid="stRadio"] [role="radio"][aria-checked="true"]{
  background:rgba(20,184,166,.16)!important;color:#f0fdfa!important;box-shadow:inset 3px 0 0 #2dd4bf!important}
[data-testid="stSidebar"]>div:first-child{padding-top:1.1rem}
[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] span{color:#c9d5e3!important}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{color:#f8fafc!important}
[data-testid="stSidebar"] hr{border-color:rgba(255,255,255,.12)!important}
[data-testid="stSidebar"] .stButton>button{background:rgba(255,255,255,.06)!important;color:#eff6ff!important;border:1px solid rgba(255,255,255,.16)!important}
.block-container{max-width:1480px!important;padding:1.6rem 2.2rem 3.2rem 2.2rem!important}
h1,h2,h3,h4{color:var(--ink)!important;letter-spacing:0!important}
h1{font-size:2rem!important;font-weight:650!important;line-height:1.2!important}
h2{font-size:1.45rem!important;font-weight:650!important}
h3{font-size:1.12rem!important;font-weight:650!important}
p,span,li,label{color:var(--ink)}.stCaption{color:var(--muted)!important}
a{color:var(--teal)!important;text-decoration:none!important}
.brand{padding:.2rem .35rem 1.15rem .35rem;border-bottom:1px solid rgba(255,255,255,.1);margin-bottom:1rem}
.brand-lockup{display:flex;align-items:center;gap:10px}
.brand-mark{width:34px;height:34px;border-radius:8px;background:linear-gradient(135deg,#14b8a6,#2563eb);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:13px}
.brand-name{font-size:1.02rem;font-weight:720;color:#fff;line-height:1.1}
.brand-sub{font-size:.72rem;color:#93a4b8;margin-top:2px}
.nav-caption{font-size:.68rem;color:#7f95ad;text-transform:uppercase;letter-spacing:.12em;margin:.3rem 0 .45rem .45rem}
.sidebar-stat{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:.75rem .2rem .1rem}
.sidebar-stat div{background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.09);border-radius:8px;padding:9px 10px}
.sidebar-stat b{display:block;color:#f8fafc;font-size:1.05rem;line-height:1}
.sidebar-stat span{font-size:.68rem;color:#93a4b8!important}
.page-header{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;padding:.15rem 0 1.25rem 0;border-bottom:1px solid var(--line);margin-bottom:1.35rem}
.page-header h1{margin:0 0 .32rem 0}.page-header p{margin:0;color:var(--muted);font-size:.94rem;line-height:1.55}
.status-pill{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 10px;border-radius:999px;font-size:.76rem;font-weight:650;white-space:nowrap;background:#ecfdf5;color:#047857;border:1px solid #bbf7d0}
.status-pill.info{background:#eff6ff;color:#1d4ed8;border-color:#bfdbfe}.status-pill.warn{background:#fffbeb;color:#b45309;border-color:#fde68a}.status-pill.bad{background:#fef3f2;color:#b42318;border-color:#fecaca}
.kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:0 0 1.35rem 0}
@media(max-width:1100px){.kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:620px){.kpi-grid{grid-template-columns:1fr}}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px 17px 15px;box-shadow:var(--shadow)}
.kpi .label{font-size:.76rem;color:var(--muted);font-weight:650}.kpi .value{font-size:1.92rem;line-height:1.05;font-weight:720;color:var(--ink);margin-top:10px;font-variant-numeric:tabular-nums}
.kpi .sub{font-size:.75rem;color:var(--subtle);margin-top:7px;line-height:1.35}
.kpi.teal{border-top:3px solid var(--teal)}.kpi.blue{border-top:3px solid var(--blue)}.kpi.amber{border-top:3px solid var(--amber)}.kpi.indigo{border-top:3px solid #4f46e5}
.section-title{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:1.15rem 0 .7rem 0}
.section-title h3{margin:0;font-size:1.05rem}.section-title p{margin:.22rem 0 0 0;color:var(--muted);font-size:.82rem}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:var(--shadow);padding:16px 17px}
.panel-soft{background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:14px 15px}
.data-list{display:grid;gap:9px}.data-row{display:grid;grid-template-columns:minmax(0,1.45fr) .7fr .55fr auto;gap:12px;align-items:center;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;box-shadow:var(--shadow)}
.data-row .name{font-weight:650;color:var(--ink);font-size:.88rem;overflow:hidden;text-overflow:ellipsis}.data-row .meta{font-size:.76rem;color:var(--muted)}.data-row .num{font-variant-numeric:tabular-nums;font-weight:650;font-size:.84rem;text-align:right}
.tag{display:inline-flex;align-items:center;height:22px;padding:0 8px;border-radius:999px;font-size:.68rem;font-weight:650;background:#f2f4f7;color:#475467;border:1px solid #e4e7ec}
.tag.teal{background:#f0fdfa;color:#0f766e;border-color:#99f6e4}.tag.blue{background:#eff6ff;color:#1d4ed8;border-color:#bfdbfe}.tag.amber{background:#fffbeb;color:#b45309;border-color:#fde68a}
.code-note{background:#0b1f33;color:#dbeafe;border-radius:8px;padding:12px 13px;font-family:"Cascadia Code",Consolas,monospace;font-size:.78rem;line-height:1.65;overflow:auto;white-space:pre-wrap;word-break:break-word}
.empty{padding:26px 18px;text-align:center;border:1px dashed #cfd6e0;border-radius:8px;background:rgba(255,255,255,.58);color:var(--muted);font-size:.86rem}
[data-testid="stMetric"]{background:#fff!important;border:1px solid var(--line)!important;border-radius:8px!important;box-shadow:var(--shadow)!important;padding:.85rem 1rem!important}
[data-testid="stMetricValue"]{color:var(--ink)!important;font-size:1.55rem!important;font-weight:700!important}
[data-testid="stMetricLabel"]{color:var(--muted)!important;font-weight:600!important}
.stButton>button{border-radius:7px!important;border:1px solid #d0d5dd!important;background:#fff!important;color:#344054!important;font-weight:650!important;box-shadow:var(--shadow)!important;min-height:38px}
.stButton>button:hover{border-color:#98a2b3!important;background:#f9fafb!important}
.stButton>button[kind="primary"]{background:var(--teal)!important;border-color:var(--teal)!important;color:#fff!important}
.stTextInput input,.stTextArea textarea,.stNumberInput input,
.stSelectbox div[data-baseweb="select"]>div,.stChatInput textarea{
  background:#fff!important;border-radius:7px!important;border-color:#d0d5dd!important;
  color:#101828!important;-webkit-text-fill-color:#101828!important;caret-color:#101828!important
}
.stTextInput input::placeholder,.stTextArea textarea::placeholder,.stChatInput textarea::placeholder{
  color:#98a2b3!important;-webkit-text-fill-color:#98a2b3!important;opacity:1!important
}
.stSelectbox div[data-baseweb="select"] *{color:#101828!important}
.stTextInput input:focus,.stTextArea textarea:focus,.stNumberInput input:focus,
.stChatInput textarea:focus{border-color:var(--teal)!important;box-shadow:0 0 0 3px rgba(15,118,110,.12)!important}
[data-testid="stChatInput"]{background:#fff!important;border:1px solid #d0d5dd!important;border-radius:8px!important}
[data-testid="stFileUploader"]{background:#fff;border:1px dashed #cfd6e0;border-radius:8px;padding:.25rem}
[data-testid="stExpander"]{background:#fff!important;border:1px solid var(--line)!important;border-radius:8px!important;box-shadow:var(--shadow)!important}
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:8px;overflow:hidden;box-shadow:var(--shadow)}
[data-testid="stChatMessage"]{background:#fff!important;border:1px solid var(--line)!important;border-radius:8px!important;box-shadow:var(--shadow)!important;margin:.35rem 0!important}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]){background:#f0fdfa!important;border-color:#99f6e4!important;margin-left:5%!important}
[data-testid="stTabs"] [data-baseweb="tab"]{font-weight:650;color:var(--muted)!important}
[data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"]{color:var(--teal)!important;border-bottom:2px solid var(--teal)!important}
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"]{display:grid!important;grid-template-columns:1fr!important;gap:3px!important;width:100%!important}
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"]{width:100%!important;border-radius:7px!important;padding:9px 11px!important;color:#c9d5e3!important;background:transparent!important}
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"]:hover{background:rgba(255,255,255,.07)!important}
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"][aria-checked="true"]{background:rgba(20,184,166,.16)!important;color:#f0fdfa!important;box-shadow:inset 3px 0 0 #2dd4bf!important}
</style>
"""


def apply_theme() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def brand_block() -> str:
    return ('<div class="brand"><div class="brand-lockup"><div class="brand-mark">AI</div>'
            '<div><div class="brand-name">ECNU DataOS</div>'
            '<div class="brand-sub">面向 AI 的大数据平台</div></div></div></div>')


def page_header(title: str, subtitle: str = "", badge: str = "服务在线", tone: str = "") -> None:
    tone_cls = f" {tone}" if tone else ""
    st.markdown(f'<div class="page-header"><div><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div>'
                f'<span class="status-pill{tone_cls}">{esc(badge)}</span></div>', unsafe_allow_html=True)


def section_title(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="section-title"><div><h3>{esc(title)}</h3><p>{esc(subtitle)}</p></div></div>',
                unsafe_allow_html=True)


def status_chip(text: str, tone: str = "") -> str:
    return f'<span class="status-pill{" " + tone if tone else ""}">{esc(text)}</span>'


def tag(text: str, tone: str = "") -> str:
    return f'<span class="tag{" " + tone if tone else ""}">{esc(text)}</span>'


def metric_card(label: str, value: str, sub: str = "", tone: str = "") -> str:
    return (f'<div class="kpi{" " + tone if tone else ""}"><div class="label">{esc(label)}</div>'
            f'<div class="value">{esc(value)}</div><div class="sub">{esc(sub)}</div></div>')


def render_kpis(items: Sequence[Mapping[str, object]]) -> None:
    cards = "".join(metric_card(str(i.get("label", "")), str(i.get("value", "")),
                                str(i.get("sub", "")), str(i.get("tone", ""))) for i in items)
    st.markdown(f'<div class="kpi-grid">{cards}</div>', unsafe_allow_html=True)


def panel(inner_html: str, soft: bool = False) -> None:
    st.markdown(f'<div class="{"panel-soft" if soft else "panel"}">{inner_html}</div>', unsafe_allow_html=True)


def code_note(text: str) -> None:
    st.markdown(f'<div class="code-note">{esc(text)}</div>', unsafe_allow_html=True)


def empty_state(text: str) -> None:
    st.markdown(f'<div class="empty">{esc(text)}</div>', unsafe_allow_html=True)


def data_source_rows(items: Iterable[Mapping[str, object]]) -> str:
    rows = []
    for item in items:
        rows.append('<div class="data-row">'
                    f'<div class="name">{esc(item.get("name", ""))}</div>'
                    f'<div class="meta">{esc(item.get("kind", ""))}</div>'
                    f'<div class="num">{int(item.get("rows") or 0):,}</div>'
                    f'<div class="pull-right">{tag(str(item.get("group", "数据源")))}</div></div>')
    return f'<div class="data-list">{"".join(rows)}</div>'

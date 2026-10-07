# -*- coding: utf-8 -*-
"""ECNU DataOS - 面向 AI 的大数据平台 Web 控制台 v5."""
from __future__ import annotations

import hashlib
import io
import uuid
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import streamlit as st
from rdflib import Graph

from app.ui_kit import (
    NAV_HINTS,
    NAV_ITEMS,
    apply_theme,
    brand_block,
    code_note,
    data_source_rows,
    empty_state,
    esc,
    page_header,
    panel,
    render_kpis,
    section_title,
    status_chip,
    tag,
)
from aiplatform import privates
from aiplatform.answers import answer_with_client
from aiplatform.ai_clients import PRESETS, call_client, list_clients, register_client
from aiplatform.build_platform import build
from aiplatform.semantic import GenericSemanticQuery
from aiplatform.upload import ingest_file, remove_uploaded_source, save_upload_bytes

st.set_page_config(page_title="ECNU DataOS · 面向 AI 的大数据平台", layout="wide",
                   page_icon="AI", initial_sidebar_state="expanded")
apply_theme()

GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8610").rstrip("/")
API_PORT = os.environ.get("PLATFORM_API_PORT", "8610")
USER_DB = os.path.join(BASE, "data", "users.json")

TOOL_CATALOG = [
    ("list_ontology", "列出平台本体覆盖的类、关系和属性", "GET /v1/ontology"),
    ("list_sources", "列出已接入的数据源与规模", "GET /v1/tools/list_sources"),
    ("explore_class", "浏览某个本体类的实例与样例", "POST /v1/tools/explore_class"),
    ("find_entity", "按关键词在指定类中查找实体", "POST /v1/tools/find_entity"),
    ("entity_detail", "查看实体的属性和关系", "POST /v1/tools/entity_detail"),
    ("query_relation", "按关系聚合两个本体类之间的连接", "POST /v1/tools/query_relation"),
    ("sparql", "执行只读 SPARQL 查询", "POST /v1/tools/sparql"),
    ("semantic_ask", "自然语言问数，自动路由到本体查询", "POST /v1/tools/semantic_ask"),
]


@st.cache_resource(show_spinner="正在构建平台：本体 -> 数据接入 -> 本体性转化 ...")
def init_platform():
    return build()


onto, cat, graph = init_platform()
graph._public_store.refresh()


def _stable_hash(text: str, mod: int = 100000) -> int:
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:10]
    return int(digest, 16) % mod


def _hash_pw(pw: str) -> str:
    return hashlib.sha256(str(pw).encode("utf-8")).hexdigest()


def _load_users() -> dict:
    if os.path.exists(USER_DB):
        try:
            return json.load(open(USER_DB, encoding="utf-8"))
        except Exception:
            pass
    users = {"admin": {"pw": _hash_pw("admin123"), "role": "admin"},
             "demo": {"pw": _hash_pw("demo123"), "role": "user"}}
    try:
        os.makedirs(os.path.dirname(USER_DB), exist_ok=True)
        json.dump(users, open(USER_DB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass
    return users


def _save_users(users: dict) -> None:
    try:
        json.dump(users, open(USER_DB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as exc:
        print("[users] save error", exc)


_USERS = _load_users()


def _rebuild_query() -> None:
    uid = st.session_state.get("uid")
    if uid:
        try:
            data_dir, user_graph = privates.load_user_priv(uid)
        except (OSError, ValueError, RuntimeError) as exc:
            st.session_state["gq"] = None
            st.session_state["query_error"] = str(exc)
            return
        st.session_state["priv_dir"] = data_dir
        st.session_state["gq"] = GenericSemanticQuery(privates.merge_public_user(graph.g, user_graph))
    else:
        st.session_state.pop("priv_dir", None)
        st.session_state["gq"] = GenericSemanticQuery(graph.g)
    st.session_state.pop("query_error", None)


def current_query() -> GenericSemanticQuery:
    graph._public_store.refresh()
    _rebuild_query()
    if st.session_state.get("query_error"):
        raise ValueError(st.session_state["query_error"])
    return st.session_state["gq"]


def public_sources() -> list[dict]:
    return [s for s in cat.list() if not s["source_id"].startswith("priv_")]


def _stats() -> dict:
    return graph.stats()


def _go(page: str) -> None:
    st.session_state.nav = page
    st.rerun()


def _top_page_switcher(current: str) -> None:
    """Persistent page switch that remains available when the sidebar is collapsed."""
    cols = st.columns(len(NAV_ITEMS))
    for col, item in zip(cols, NAV_ITEMS):
        active = item == current
        kwargs = {"type": "primary"} if active else {}
        if col.button(item, key=f"topnav_{item}", width="stretch", **kwargs) and not active:
            st.session_state.nav = item
            st.rerun()


def login_widget() -> bool:
    st.markdown('<div class="nav-caption">工作区</div>', unsafe_allow_html=True)
    if st.session_state.get("uid"):
        uid = st.session_state.uid
        role = st.session_state.get("role", "user")
        try:
            _d, pg = privates.load_user_priv(uid)
            private_summary = f"私有三元组 {len(pg):,}"
            st.session_state.pop("query_error", None)
        except (OSError, ValueError, RuntimeError) as exc:
            private_summary = "私人数据暂不可用"
            st.session_state["query_error"] = str(exc)
        st.markdown(
            f'<div class="panel-soft"><b>{esc(uid)}</b> · {esc(role)}<br>'
            f'<span class="stCaption">{private_summary}</span></div>',
            unsafe_allow_html=True,
        )
        if st.button("退出登录", width="stretch"):
            for key in ("uid", "role", "gq", "priv_dir", "view_priv", "query_error"):
                st.session_state.pop(key, None)
            st.rerun()
        return True

    login_tab, reg_tab = st.tabs(["登录", "注册"])
    with login_tab:
        username = st.text_input("用户名", key="login_user")
        password = st.text_input("密码", type="password", key="login_pw")
        if st.button("登录", width="stretch"):
            if username in _USERS and _USERS[username]["pw"] == _hash_pw(password):
                st.session_state.uid = username
                st.session_state.role = _USERS[username]["role"]
                _rebuild_query()
                st.rerun()
            else:
                st.error("用户名或密码错误")
    with reg_tab:
        new_user = st.text_input("新用户名", key="reg_user")
        new_pw = st.text_input("新密码", type="password", key="reg_pw")
        if st.button("注册", width="stretch"):
            if not new_user or not new_pw:
                st.error("用户名和密码不能为空")
            elif new_user in _USERS:
                st.error("用户名已存在")
            else:
                _USERS[new_user] = {"pw": _hash_pw(new_pw), "role": "user"}
                _save_users(_USERS)
                st.session_state.uid = new_user
                st.session_state.role = "user"
                _rebuild_query()
                st.rerun()
    return False


def _sidebar(stats: dict) -> None:
    with st.container(key="side_rail"):
        st.markdown(brand_block(), unsafe_allow_html=True)
        login_widget()
        if st.session_state.get("query_error"):
            st.error(st.session_state["query_error"])
        st.divider()
        st.markdown('<div class="nav-caption">控制台</div>', unsafe_allow_html=True)
        current = st.session_state.get("nav", NAV_ITEMS[0])
        selected = st.radio(
            "导航",
            NAV_ITEMS,
            index=NAV_ITEMS.index(current) if current in NAV_ITEMS else 0,
            label_visibility="collapsed",
            format_func=lambda item: f"{item}  ·  {NAV_HINTS.get(item, '')}",
        )
        st.session_state.nav = selected
        st.divider()
        st.markdown(
            f'<div class="sidebar-stat"><div><b>{len(public_sources()):,}</b><span>公共数据源</span></div>'
            f'<div><b>{stats["triples"]:,}</b><span>RDF 三元组</span></div>'
            f'<div><b>{stats["entities"]:,}</b><span>语义实体</span></div>'
            f'<div><b>{len(TOOL_CATALOG)}</b><span>AI 可调工具</span></div></div>',
            unsafe_allow_html=True,
        )
        st.caption(f"网关 {GATEWAY}" if GATEWAY else "网关未配置")


def render_overview() -> None:
    stats = _stats()
    page_header("运行总览", "科研与企业数据的一体化语义底座，面向 AI 发现、理解与调用。", "服务在线")
    render_kpis([
        {"label": "公共数据源", "value": f"{len(public_sources()):,}", "sub": "已登记并完成本体性转化", "tone": "teal"},
        {"label": "RDF 三元组", "value": f"{stats['triples']:,}", "sub": "统一语义图中的可查询事实", "tone": "blue"},
        {"label": "语义实体", "value": f"{stats['entities']:,}", "sub": "学者、论文、机构、企业等领域对象", "tone": "indigo"},
        {"label": "本体类", "value": f"{len(stats['classes']):,}", "sub": "由 OWL 本体约束语义边界", "tone": "amber"},
    ])
    actions = st.columns(4)
    if actions[0].button("进入语义问答", width="stretch", type="primary"):
        _go("语义问答")
    if actions[1].button("管理数据资产", width="stretch"):
        _go("数据资产")
    if actions[2].button("查看本体与工具", width="stretch"):
        _go("本体与工具")
    if actions[3].button("配置 AI 接入", width="stretch"):
        _go("AI 接入")

    left, right = st.columns([1.45, 1], gap="large")
    with left:
        section_title("数据规模", "按业务分区查看当前完成本体性转化的数据源")
        sources = public_sources()[:9]
        panel(data_source_rows([
            {"name": s["name"], "kind": s["kind"], "rows": s["rows"], "group": "已接入"}
            for s in sources
        ]))
    with right:
        section_title("平台能力", "面向 Agent 的标准化数据能力")
        tool_tags = " ".join(tag(name, "teal") for name, _, _ in TOOL_CATALOG[:4])
        endpoint_tags = " ".join(tag(name, "blue") for name in ["OpenAI", "MCP", "llms.txt", "OpenAPI"])
        panel(
            f'<p><b>{len(TOOL_CATALOG)}</b> 个本体驱动工具可直接调用</p>'
            f'<div style="margin:.65rem 0 1rem 0">{tool_tags}</div>'
            f'<p><b>4</b> 个标准入口同域开放</p><div style="margin-top:.65rem">{endpoint_tags}</div>'
        )


def _preview_uploaded(uploaded):
    if uploaded is None:
        return None
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    try:
        if name.endswith(".csv"):
            return pd.read_csv(io.StringIO(data.decode("utf-8-sig")), nrows=5)
        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(io.BytesIO(data), nrows=5)
        if name.endswith(".json"):
            return pd.read_json(io.StringIO(data.decode("utf-8-sig"))).head(5)
    except Exception as exc:
        return f"预览失败：{type(exc).__name__}: {exc}"
    return None


def _remove_public_source(source_id: str) -> None:
    remove_uploaded_source(graph, cat, source_id)
    _rebuild_query()


def _delete_private_partition(uid: str, partition_id: str) -> None:
    privates.delete_partition(uid, partition_id)
    remaining = privates.list_partitions(uid)
    st.session_state.cur_partition = remaining[0]["id"] if remaining else "user_0"
    _rebuild_query()


def _handle_upload(uploaded, allow_generic=False, skip_empty=True,
                   max_rows=5000, include_unmapped=False) -> None:
    if uploaded is None:
        st.warning("请先选择一个文件。")
        return
    try:
        tmp_path, display_name = save_upload_bytes(uploaded.name, uploaded.getvalue())
    except Exception as exc:
        st.error(f"接入失败：{type(exc).__name__}: {exc}")
        return
    with st.spinner(f"解析 {display_name} -> 推断语义映射 -> 写入统一图 ..."):
        uid = st.session_state.get("uid")
        try:
            if uid:
                partition = st.session_state.get("cur_partition", "user_0")
                # Private sources must never enter the shared public catalog.
                from aiplatform.core import SourceCatalog
                with privates.edit_partition(uid, partition) as priv_graph:
                    proxy = privates.UserGraphProxy(onto, priv_graph)
                    source_id = f"priv_{privates.safe_uid(uid)}_{partition}_{uuid.uuid4().hex}"
                    report = ingest_file(proxy, SourceCatalog(), tmp_path, display_name, source_id=source_id,
                                         allow_generic=allow_generic, skip_empty=skip_empty,
                                         max_rows=int(max_rows), include_unmapped=include_unmapped)
                _rebuild_query()
            else:
                source_id = f"ds_upload_{uuid.uuid4().hex}"
                report = ingest_file(graph, cat, tmp_path, display_name, source_id=source_id,
                                     allow_generic=allow_generic, skip_empty=skip_empty,
                                     max_rows=int(max_rows), include_unmapped=include_unmapped)
                _rebuild_query()
            st.session_state.setdefault("uploads", []).append(report)
            if report.get("error"):
                st.error(f"接入失败：{report['error']}")
            else:
                count = (report.get("report") or {}).get("triples", 0)
                scope = "私人库" if uid else "公共资产"
                st.success(f"{display_name} 已写入{scope}：{count} 条三元组")
                st.rerun()
        except Exception as exc:
            st.error(f"接入失败：{type(exc).__name__}: {exc}")


def render_data() -> None:
    stats = _stats()
    page_header("数据资产", "上传异构文件、管理公共分区，并查看登录用户隔离的私人知识图谱。", "数据可用")
    logged = bool(st.session_state.get("uid"))
    private_count = 0
    if logged:
        _d, pg = privates.load_user_priv(st.session_state.uid)
        private_count = len(pg)
    render_kpis([
        {"label": "公共数据源", "value": f"{len(public_sources()):,}", "sub": "全平台共享", "tone": "teal"},
        {"label": "公共三元组", "value": f"{stats['triples']:,}", "sub": "统一语义图", "tone": "blue"},
        {"label": "私有三元组", "value": f"{private_count:,}", "sub": "仅当前账户可见" if logged else "登录后可上传", "tone": "indigo"},
        {"label": "本体实体", "value": f"{stats['entities']:,}", "sub": "跨域语义对象", "tone": "amber"},
    ])
    generic_count = stats["classes"].get("GenericRecord", 0)
    with st.expander("GenericRecord（通用记录）是什么？", expanded=generic_count > 0):
        st.markdown(
            "当上传文件无法自动判断为 Scholar、Publication、Institution、Company 等本体类时，"
            "系统会把它标记为 **GenericRecord（通用记录）**。它主要保留名称、描述和来源，"
            "能参与基础检索，但不保证预算、负责人等业务字段都能问答。\n\n"
            "如果这些字段对后续分析重要，建议在上传前统一列名、删除无关列，或关闭“允许写入 GenericRecord”。"
        )
        if generic_count:
            st.caption(f"当前图谱中有 {generic_count:,} 条 GenericRecord 记录。")
    left, right = st.columns([1, 1.15], gap="large")
    with left:
        section_title("接入新数据", "支持 CSV、Excel、Word、PDF、JSON、图片和文本；导入前可预览与筛选")
        uploaded = st.file_uploader(
            "选择文件",
            type=["csv", "xlsx", "xls", "docx", "pdf", "json", "png", "jpg", "jpeg", "txt", "md"],
            label_visibility="collapsed",
        )
        with st.expander("导入筛选与提炼", expanded=False):
            allow_generic = st.toggle(
                "允许写入 GenericRecord（未识别类别）",
                value=False,
                help="关闭后，无法识别本体类别的文件会被跳过，不会产生低价值通用记录。",
            )
            include_unmapped = st.toggle(
                "保留未识别字段到描述",
                value=False,
                help="关闭后，未匹配到本体属性的列会被过滤，避免低质量字段混入图谱。",
            )
            skip_empty = st.toggle("跳过空行和空主键", value=True)
            max_rows = st.number_input("整个文件最多导入行数", min_value=1, max_value=100000,
                                       value=5000, step=500)
        preview = _preview_uploaded(uploaded)
        if isinstance(preview, str):
            st.warning(preview)
        elif preview is not None and not preview.empty:
            st.caption(f"文件预览：{uploaded.name}（前 5 行）")
            st.dataframe(preview, width="stretch", hide_index=True)
        if st.button("开始本体化", type="primary", width="stretch"):
            _handle_upload(uploaded, allow_generic=allow_generic, skip_empty=skip_empty,
                           max_rows=int(max_rows), include_unmapped=include_unmapped)
        scope = "私人库分区" if logged else "公共数据资产"
        st.caption(f"当前写入范围：{scope}。单文件上限 25 MB，默认最多导入 5000 行。")
    with right:
        section_title("公共分区", "平台内置的 19 个数据源已完成统一建模")
        rows = public_sources()
        panel(data_source_rows([
            {"name": s["name"], "kind": s["kind"], "rows": s["rows"], "group": "公共"}
            for s in rows[:10]
        ]))
        with st.expander("查看全部公共数据源", expanded=False):
            st.dataframe(pd.DataFrame([{"数据源": s["name"], "类型": s["kind"], "行数": s["rows"]}
                                       for s in rows]), width="stretch", hide_index=True)
        uploaded_sources = [s for s in rows if s["source_id"].startswith("ds_upload_")]
        if uploaded_sources:
            section_title("手动清理上传数据", "删除本次上传进入图谱的数据和临时原件，内置数据源不受影响")
            for source in uploaded_sources:
                item_left, item_right = st.columns([4, 1])
                item_left.markdown(f"**{source['name']}** · {source['kind']} · {source['rows']:,} 行")
                if item_right.button("删除", key=f"delete_public_{source['source_id']}"):
                    try:
                        _remove_public_source(source["source_id"])
                    except (OSError, ValueError) as exc:
                        st.error(f"删除失败：{exc}")
                    else:
                        st.rerun()
    if logged:
        section_title("私人库", "上传到私人分区的数据只参与当前账户的语义查询")
        uid = st.session_state.uid
        try:
            parts = privates.list_partitions(uid)
        except (OSError, ValueError) as exc:
            st.error(str(exc))
            return
        col1, col2 = st.columns([1, 2], gap="large")
        with col1:
            selected = st.selectbox(
                "当前分区",
                [p["id"] for p in parts],
                format_func=lambda pid: next((p["name"] for p in parts if p["id"] == pid), pid),
                key="part_sel",
            )
            st.session_state.cur_partition = selected
            new_name = st.text_input("新建分区", placeholder="例如：项目A / 竞品调研")
            if st.button("创建分区", width="stretch"):
                if new_name.strip():
                    try:
                        st.session_state.cur_partition = privates.new_partition(uid, new_name.strip())
                    except (OSError, ValueError) as exc:
                        st.error(f"创建失败：{exc}")
                    else:
                        st.rerun()
                else:
                    st.warning("请输入分区名称")
            st.divider()
            confirm_delete = st.checkbox("确认删除当前分区", key=f"confirm_delete_{selected}")
            if st.button("删除当前分区", key=f"delete_private_{selected}", width="stretch"):
                if confirm_delete:
                    try:
                        _delete_private_partition(uid, selected)
                    except (OSError, ValueError) as exc:
                        st.error(f"删除失败：{exc}")
                    else:
                        st.rerun()
                else:
                    st.warning("请先勾选确认删除。")
        with col2:
            for partition in parts:
                if partition.get("error"):
                    st.error(partition["error"])
            st.dataframe(pd.DataFrame([{"分区": p["name"], "标识": p["id"], "三元组": p["triples"]}
                                       for p in parts]), width="stretch", hide_index=True)
    else:
        empty_state("登录后可以创建私人分区，并把上传数据限定在个人语义范围内。")


def _render_result(result: dict) -> None:
    if "error" in result:
        st.error(result["error"])
        return
    if "companies" in result:
        st.dataframe(pd.DataFrame(result["companies"]), width="stretch", hide_index=True)
    elif "scholars" in result:
        st.dataframe(pd.DataFrame({"学者": result["scholars"]}), width="stretch", hide_index=True)
    elif "entities" in result:
        st.dataframe(pd.DataFrame(result["entities"]), width="stretch", hide_index=True)
    elif "samples" in result:
        st.dataframe(pd.DataFrame(result["samples"]), width="stretch", hide_index=True)
    elif result.get("intent") == "trend" and result.get("data"):
        frame = pd.DataFrame(result["data"]).rename(columns={"name": "年份", "count": "论文数"})
        st.line_chart(frame.set_index("年份")["论文数"], height=240)
        st.dataframe(frame, width="stretch", hide_index=True)
    elif "data" in result and isinstance(result["data"], list) and result["data"]:
        frame = pd.DataFrame(result["data"]).rename(columns={
            "name": "名称", "en_name": "英文名", "papers": "论文数",
            "institution": "所属机构", "count": "数量", "value": "数值",
        })
        st.dataframe(frame, width="stretch", hide_index=True)
        for col in ("论文数", "数量", "数值"):
            if col in frame.columns and "名称" in frame.columns:
                st.bar_chart(frame.head(20).set_index("名称")[col], height=220)
                break
    elif "classes" in result:
        st.dataframe(pd.DataFrame([{"本体类": k, "实例数": v} for k, v in result["classes"].items()]),
                     width="stretch", hide_index=True)
    else:
        st.json(result)


def _answer_with_ai(question: str, picked: dict) -> tuple[dict, list[dict], str]:
    return answer_with_client(question, current_query().ask(question), picked, call_client)


def _scroll_to_latest() -> None:
    if st.session_state.pop("scroll_to_chat", False):
        st.html(
            """<script>
setTimeout(() => {
  const el = document.querySelector('.st-key-chat_history');
  if (el) {
    el.scrollTop = el.scrollHeight;
    el.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }
}, 80);
</script>""",
            unsafe_allow_javascript=True,
        )


def render_chat() -> None:
    page_header("语义问答", "自然语言问题会先进入本体语义层，再交给选定 AI 生成可核验答案。", "语义引擎就绪")
    clients = list_clients()
    clients.sort(key=lambda c: c["kind"] != "local")
    labels = [c["name"] for c in clients]
    picked_name = st.selectbox("驱动 AI", labels, index=0, key="chat_ai", label_visibility="collapsed")
    picked = next((c for c in clients if c["name"] == picked_name), None)
    if picked is None:
        empty_state("选择一个 AI 客户端开始问答；平台自身和本地规则引擎无需外部 Key。")
        return

    demos = [("学者检索", "华东师范大学有哪些学者？"), ("领域趋势", "大语言模型趋势"),
             ("企业查询", "物流快递行业有哪些公司？"), ("机构排名", "哪个机构论文最多？")]
    demo_cols = st.columns(len(demos))
    for col, (label, question) in zip(demo_cols, demos):
        if col.button(label, width="stretch"):
            st.session_state["pending_chat"] = question

    left, right = st.columns([1.28, 1], gap="large")
    with left:
        if st.session_state.get("messages"):
            nav_a, nav_b = st.columns(2)
            if nav_a.button("跳到最新", key="scroll_button", width="stretch"):
                st.session_state["scroll_to_chat"] = True
                st.rerun()
            if nav_b.button("清空对话", key="clear_chat_button", width="stretch"):
                st.session_state["messages"] = []
                st.session_state["last"] = None
                st.session_state.pop("pending_chat", None)
                st.session_state["scroll_to_chat"] = False
                st.rerun()
            latest_user = next((m["content"] for m in reversed(st.session_state.messages)
                                if m["role"] == "user"), None)
            if latest_user:
                st.markdown(
                    f'<div class="panel-soft"><b>最新问题</b><br>{esc(latest_user)}</div>',
                    unsafe_allow_html=True,
                )
        chat_history = st.container(key="chat_history", height=440)
        with chat_history:
            for msg in st.session_state.get("messages", []):
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
                    if msg.get("meta"):
                        st.caption(msg["meta"])
        submitted = st.chat_input("输入问题，例如：知识图谱领域有哪些论文？")
        question = submitted or st.session_state.pop("pending_chat", None)
        if question:
            st.session_state.setdefault("messages", []).append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                with st.spinner("正在执行本体语义查询 ..."):
                    try:
                        result, trace, answer = _answer_with_ai(question, picked)
                    except (OSError, ValueError, RuntimeError) as exc:
                        result = {"error": str(exc)}
                        trace = [{"step": "数据校验失败", "answer_source": "本地存储校验"}]
                        answer = f"当前数据无法用于查询：{exc}"
                st.markdown(answer)
                answer_source = trace[-1].get("answer_source", picked["name"])
                st.caption(answer_source)
            st.session_state.messages.append({"role": "assistant", "content": answer, "meta": answer_source})
            st.session_state.last = {"trace": trace, "result": result, "question": question, "picked": picked}
            st.session_state["scroll_to_chat"] = True
        _scroll_to_latest()

    with right:
        section_title("调用链与结果", "每次问答都会保留语义意图、后端模型和原始数据")
        last = st.session_state.get("last")
        if not last:
            empty_state("提问后这里会显示调用链、结构化结果和图表。")
            return
        for step in last.get("trace", []):
            with st.expander(step.get("step", "步骤"), expanded=True):
                st.json(step)
        _render_result(last.get("result") or {})


def render_ontology() -> None:
    stats = _stats()
    described = onto.describe()
    page_header("本体与工具", "OWL 本体是平台语义契约，所有数据和工具都从这份契约中生成。", "语义契约已加载")
    render_kpis([
        {"label": "本体类", "value": f"{len(described['classes']):,}", "sub": "Scholar、Publication、Company 等", "tone": "teal"},
        {"label": "对象属性", "value": f"{len(described['object_properties']):,}", "sub": "实体之间的关系", "tone": "blue"},
        {"label": "数据属性", "value": f"{len(described['data_properties']):,}", "sub": "实体携带的字段", "tone": "indigo"},
        {"label": "数据实例", "value": f"{stats['entities']:,}", "sub": "已进入统一语义图", "tone": "amber"},
    ])
    left, right = st.columns([1, 1], gap="large")
    with left:
        section_title("本体结构", "当前语义图谱中的类与实例分布")
        class_frame = pd.DataFrame([{"本体类": k, "实例数": v} for k, v in stats["classes"].items()])
        st.dataframe(class_frame.sort_values("实例数", ascending=False), width="stretch", hide_index=True)
        with st.expander("查看对象属性与数据属性", expanded=False):
            st.markdown("**对象属性**")
            st.code("、".join(described["object_properties"]), language=None)
            st.markdown("**数据属性**")
            st.code("、".join(described["data_properties"]), language=None)
    with right:
        section_title("工具目录", "Agent 可通过标准接口直接调用这些数据能力")
        st.dataframe(pd.DataFrame([{"工具": name, "说明": desc, "调用入口": endpoint}
                                   for name, desc, endpoint in TOOL_CATALOG]),
                     width="stretch", hide_index=True)
    section_title("机器入口", "同一服务同时提供 OpenAI、MCP、OpenAPI 和 llms.txt 发现协议")
    tabs = st.tabs(["OpenAI 兼容", "MCP", "llms.txt", "健康检查"])
    with tabs[0]:
        code_note(f'base_url = "{GATEWAY}/v1"\nmodel = "platform-semantic"\nAuthorization: Bearer anonymous')
    with tabs[1]:
        code_note(f'{{"mcpServers": {{"ecnu-dataos": {{"url": "{GATEWAY}/.well-known/mcp"}}}}}}')
    with tabs[2]:
        code_note(f"curl {GATEWAY}/llms.txt")
    with tabs[3]:
        code_note(f"GET {GATEWAY}/health\nGET {GATEWAY}/v1/models\nGET {GATEWAY}/v1/tools")


def _http_get(url: str, timeout: int = 6):
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            try:
                return resp.status_code, resp.json()
            except Exception:
                return resp.status_code, {"_raw": resp.text[:300]}
    except Exception as exc:
        return None, {"_err": f"{type(exc).__name__}: {exc}"}


def _http_post(url: str, payload: dict, timeout: int = 20):
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            try:
                return resp.status_code, resp.json()
            except Exception:
                return resp.status_code, {"_raw": resp.text[:300]}
    except Exception as exc:
        return None, {"_err": f"{type(exc).__name__}: {exc}"}


def render_connectors() -> None:
    clients = list_clients()
    n_http = sum(1 for c in clients if c["kind"] == "http")
    n_cli = sum(1 for c in clients if c["kind"] == "cli")
    page_header("AI 接入", "平台不锁定任何厂商，同一份语义能力可被任意 OpenAI 兼容客户端调用。", "开放登记")
    render_kpis([
        {"label": "已登记 AI", "value": f"{len(clients):,}", "sub": "含平台自身与本地兜底", "tone": "teal"},
        {"label": "云端 API", "value": f"{n_http:,}", "sub": "OpenAI 兼容服务", "tone": "blue"},
        {"label": "本地 Agent", "value": f"{n_cli:,}", "sub": "CLI 客户端", "tone": "indigo"},
        {"label": "标准入口", "value": "4", "sub": "OpenAI / MCP / OpenAPI / llms.txt", "tone": "amber"},
    ])
    section_title("客户端目录", "按接入方式概览当前可被平台调用的 AI")
    client_frame = pd.DataFrame([{
        "名称": c["name"],
        "方式": {"http": "云端 API", "cli": "本地 Agent", "local": "本地兜底", "platform": "平台自身"}.get(c["kind"], c["kind"]),
        "模型": c["model"],
        "可用": "是" if c.get("available", True) else "待配置",
    } for c in clients])
    st.dataframe(client_frame, width="stretch", hide_index=True)

    st.divider()
    if st.session_state.get("uid"):
        section_title("登记新 AI", "填写任意 OpenAI 兼容服务的 Base URL、模型名和 Key 即可接入")
        with st.expander("开放登记表单", expanded=False):
            preset_name = st.selectbox("从预设选择（可再改）",
                                       ["— 手动填写 —"] + [p["name"] for p in PRESETS],
                                       key="preset_sel")
            preset = next((p for p in PRESETS if p["name"] == preset_name), None)
            c1, c2, c3 = st.columns(3)
            r_name = c1.text_input("显示名称", value=(preset["name"] if preset else ""))
            r_url = c2.text_input("Base URL", value=(preset["base_url"] if preset else ""))
            r_model = c3.text_input("模型名", value=(preset["model"] if preset else ""))
            env_name = st.text_input("Key 环境变量名（推荐）", value=(preset.get("api_key_env", "") if preset else ""))
            api_key = st.text_input("或直接填 API Key", type="password")
            if st.button("接入这个 AI", type="primary"):
                if not (r_name and r_url and r_model):
                    st.warning("名称、Base URL、模型名都要填写。")
                elif not (env_name or api_key):
                    st.warning("请填写环境变量名或直接填写 API Key。")
                else:
                    register_client(r_name, r_url, r_model, api_key=(api_key or None),
                                    api_key_env=(env_name or None), note="控制台登记")
                    st.rerun()
    else:
        empty_state("登录后可登记新的 AI 客户端；当前游客模式可读取公共数据并使用已登记客户端。")

    st.divider()
    section_title("外部接入示例", "任何支持标准协议的 Agent 都可以把平台当作数据服务")
    tabs = st.tabs(["OpenAI SDK", "curl", "MCP 配置"])
    with tabs[0]:
        code_note("from openai import OpenAI\n"
                  f'client = OpenAI(base_url="{GATEWAY}/v1", api_key="anonymous")\n'
                  'resp = client.chat.completions.create(model="platform-semantic", '
                  'messages=[{"role":"user","content":"华东师范大学有哪些学者？"}])\n'
                  "print(resp.choices[0].message.content)")
    with tabs[1]:
        code_note(f'curl {GATEWAY}/v1/chat/completions \\\n'
                  '  -H "Content-Type: application/json" \\\n'
                  '  -d \'{"model":"platform-semantic","messages":[{"role":"user",'
                  '"content":"物流快递行业有哪些公司？"}]}\'')
    with tabs[2]:
        code_note(f'{{"mcpServers": {{"ecnu-dataos": {{"url": "{GATEWAY}/mcp"}}}}}}')


def render_settings() -> None:
    page_header("系统设置", "账户、服务状态和运行配额集中在同一个地方。", "配置可查看")
    logged = bool(st.session_state.get("uid"))
    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        section_title("账户", "登录状态与当前数据范围")
        if logged:
            st.markdown(f'<div class="panel"><b>{st.session_state.uid}</b> · {st.session_state.get("role", "user")}<br>'
                        '<span class="stCaption">私人库已与公共语义图谱合并查询</span></div>', unsafe_allow_html=True)
        else:
            empty_state("当前为游客模式：可查询公共数据，无法管理私人分区。")
    with c2:
        section_title("服务健康", "统一网关与语义引擎状态")
        status, body = _http_get(f"{GATEWAY}/health")
        if status == 200:
            st.markdown(status_chip("网关在线", "info"), unsafe_allow_html=True)
            st.json({"http_status": status, "sources": body.get("sources"), "triples": body.get("triples"),
                     "entities": body.get("entities")})
        else:
            st.markdown(status_chip("网关未连接", "warn"), unsafe_allow_html=True)
            st.json({"http_status": status, "body": body})
    section_title("API Key", "提升外部 Agent 调用额度的匿名只读凭据")
    if st.button("领取平台 API Key", type="primary"):
        code, payload = _http_post(f"{GATEWAY}/v1/keys", {"label": "console-ui"})
        if payload and payload.get("key"):
            st.session_state["issued_key"] = payload["key"]
            st.success("已签发 Key，仅提高只读调用额度。")
        else:
            st.error(f"签发失败（HTTP {code}）：{payload}")
    if st.session_state.get("issued_key"):
        code_note("Authorization: Bearer " + st.session_state.issued_key)
    section_title("运行说明", "默认服务端口与入口")
    st.dataframe(pd.DataFrame([
        {"服务": "Web 控制台", "地址": "http://localhost:8603", "说明": "Streamlit 人面"},
        {"服务": "统一网关", "地址": f"http://localhost:{API_PORT}", "说明": "人面 + 机面同域"},
        {"服务": "MCP", "地址": f"http://localhost:{API_PORT}/mcp", "说明": "MCP Streamable HTTP"},
        {"服务": "OpenAI 兼容", "地址": f"http://localhost:{API_PORT}/v1", "说明": "AI 数据端点"},
    ]), width="stretch", hide_index=True)


def main() -> None:
    stats = _stats()
    rail, content = st.columns([1.05, 3.55], gap="large")
    with rail:
        _sidebar(stats)
    page = st.session_state.get("nav", NAV_ITEMS[0])
    with content:
        _top_page_switcher(page)
        if page == "运行总览":
            render_overview()
        elif page == "数据资产":
            render_data()
        elif page == "语义问答":
            render_chat()
        elif page == "本体与工具":
            render_ontology()
        elif page == "AI 接入":
            render_connectors()
        else:
            render_settings()


if __name__ == "__main__":
    main()

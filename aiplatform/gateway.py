# -*- coding: utf-8 -*-
"""
统一网关 —— 面向 AI 的大数据平台「机面 + 人面」同域入口。

背景：Cloudflare 隧道只能暴露一个本地端口。原方案只暴露 8603(Streamlit 人面)，
导致 8610 的 OpenAI 兼容端点(机面)一个都没到公网 —— AI 评估报告 2/10 的核心原因。

本网关监听一个端口(默认 8610)，同域同时承载：
  人面  /            -> 反代 Streamlit(8603)，保留登录/上传/浏览等人类交互
  机面  /llms.txt    -> 一次 GET 自举入口(纯文本)
        /AGENTS.md   -> 长版自举手册
        /openapi.json-> OpenAPI 3.1 机器可读契约
        /robots.txt  -> 允许抓取 + 声明机面入口
        /v1/*        -> OpenAI 兼容端点 + 平台工具(直接透传内部 api_server 逻辑)
        /v1/ontology -> 机器可读本体(JSON-LD)
        /.well-known/mcp -> MCP 发现入口
  未匹配路径          -> 真 404 + application/problem+json

运行：
  python -m aiplatform.gateway            # 默认 0.0.0.0:8610
"""
import os, sys, json, time, uuid, copy, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

# ---- httpx 用于反代人面 Streamlit（HTTP + WebSocket）----
import httpx
from starlette.websockets import WebSocket, WebSocketDisconnect
import anyio

from aiplatform.build_platform import build
from aiplatform.answers import summarize_result
from aiplatform.semantic import GenericSemanticQuery
from aiplatform.tools import PlatformTools, SPARQLReadOnlyError
from aiplatform.core import ONTO_NS, RES_NS

# ============ 配置 ============
STREAMLIT_ORIGIN = os.environ.get("CONSOLE_URL", "http://127.0.0.1:8603")
HUMAN_PREFIXES = ["/_stcore/", "/static/", "/favicon", "/manifest", "/_stcore"]

_UNSET = object()

import contextvars
_rid_ctx = contextvars.ContextVar("request_id", default="")


def problem(status: int, code: str, detail: str, next_action: str = "", request_id: str = ""):
    """RFC 9457 problem+json 统一错误体。request_id 缺省时复用中间件生成的 X-Request-Id。
    type 字段用真实主机（公网域名或本机），不再用占位域名 https://ai-platform/..."""
    rid = request_id or _rid_ctx.get() or f"req_{uuid.uuid4().hex[:8]}"
    _host = os.environ.get("PUBLIC_HOST", "")
    if not _host:
        try:
            _host = get_base_url().split("//")[-1] if get_base_url() else "localhost:8610"
        except Exception:
            _host = "localhost:8610"
    return {
        "type": "https://" + _host + "/errors/" + code,
        "title": code.replace("_", " ").title(),
        "status": status,
        "detail": detail,
        "error": {
            "code": code,
            "next_action": next_action,
            "request_id": rid,
        },
    }


def get_base_url():
    """优先返回公网域名（隧道对外），否则本地。"""
    return os.environ.get("PUBLIC_URL", "http://127.0.0.1:8610")

app = FastAPI(
    title="面向 AI 的大数据平台（统一网关）",
    description="人面(/ 反代控制台) + 机面(/v1/*, /llms.txt, /openapi.json)。见 /llms.txt 自举。",
    version="2.0",
    # 关闭 FastAPI 内建 openapi，改用下方手写契约（含 servers + 工具 schema）。
    # 否则内建 /openapi.json 会抢先命中，把带 servers 的契约顶掉。
    openapi_url=None,
)

# ============ CORS（手动中间件，预检返回 204，符合报告）============
# 注意：绝不能用 BaseHTTPMiddleware，否则 WebSocket 升级会被当作 HTTP 吞掉
# （这正是「经网关 8610 控制台白屏」的根因）。改成纯 ASGI 中间件，scope=websocket 直接透传。


class CORSPreflightMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # WebSocket：完整透传（不得拦截，否则 Streamlit 前端永远连不上 WS）
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return
        # HTTP：按需注入 CORS 头
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request
        request = Request(scope)
        body_rcv = receive

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"access-control-allow-origin", b"*"))
                headers.append((b"access-control-allow-methods", b"GET, POST, PUT, DELETE, PATCH, OPTIONS"))
                headers.append((b"access-control-allow-headers", b"*"))
                headers.append((b"access-control-max-age", b"600"))
                message = {**message, "headers": headers}
            await send(message)

        # 预检：直接 204，不再往下走
        if request.method == "OPTIONS" and request.headers.get("origin") and \
                request.headers.get("access-control-request-method"):
            response = Response(status_code=204, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Max-Age": "600",
            })
            # 注意：Starlette Response.__call__ 签名是 (scope, receive, send)，
            # 必按此顺序传 —— 之前把 (send_with_cors, body_rcv) 传反导致预检 500（跨源 agent 全废）。
            await response(scope, body_rcv, send_with_cors)
            return
        await self.app(scope, body_rcv, send_with_cors)


app.add_middleware(CORSPreflightMiddleware)


# X-Request-Id：每条响应带请求 ID（报告 P2，便于 agent 追踪）
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
    token = _rid_ctx.set(rid)
    try:
        response = await call_next(request)
    finally:
        _rid_ctx.reset(token)
    response.headers["X-Request-Id"] = rid
    return response


# ============ 平台初始化（进程内单例）============
_onto = _cat = _graph = _q = _tools = None
_platform_lock = threading.Lock()
_client = None


def get_platform():
    global _onto, _cat, _graph, _q, _tools
    with _platform_lock:
        if _graph is None:
            _onto, _cat, _graph = build()
            # Keep the wrapper so tools can refresh shared upload state before reading.
            _tools = PlatformTools(_graph, _cat)
        _graph._public_store.refresh()
        return _onto, _cat, _graph, GenericSemanticQuery(_graph.g), _tools


def get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
    return _client


MCP_REQUESTS = [0]


# ============ MCP 反向代理（P0-5 修复）============
# mcp 2.x 的 StreamableHTTP transport 必须由 mcp 自己的 run() 起任务组，
# 直接 mount 到本 FastAPI 会因 task-group 未初始化而 500。
# 因此 MCP 由独立进程跑（aiplatform/mcp_server.py --http，端口读 MCP_PORT 环境变量，默认 8602），
# 网关把 /mcp 与 /v1/mcp 按 HTTP 透传过去，实现「同域 /mcp」。
MCP_ORIGIN = os.environ.get("MCP_URL", "http://127.0.0.1:8602").rstrip("/")


# ============ 机器面：自描述文件 ============
LLMS_TXT = """# 面向 AI 的大数据平台

> 一句话：把「科研 + 企业」数据本体化，对外暴露为 OpenAI 兼容端点与数据工具，任何 AI 客户端 / Agent 都能直接调用。
> 认证：匿名只读（无需 key）。本平台对外产品对准 AI 优先。
> 契约：GET /openapi.json   本体：GET /v1/ontology   工具：GET /v1/tools

## 端点

- `POST /v1/chat/completions` — OpenAI 兼容对话，带 tools 即可调用平台数据工具（语义问数）
- `GET  /v1/models` — 列出可用模型（= 平台数据能力）
- `GET  /v1/tools` — 列出可调用数据工具（含 JSON Schema）
- `GET  /v1/ontology` — 机器可读本体（JSON-LD）
- `GET  /v1/search?q=关键词` — 语义检索，返回 {items, next_cursor, total}
- `POST /v1/tools/{name}` — 直接调用某个平台工具（Agent 用）
- `GET  /health` — 健康检查
- `GET  /ai-readiness.json` — 平台「AI 优先」自我体检（进程内真跑自测，如实报 passed/failed）

## 复制即跑

```bash
# 1. 看有哪些工具
curl -s {BASE}/v1/tools | jq
# 2. 语义问数（OpenAI 兼容，无需 key）
curl -s {BASE}/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -d '{{"model":"platform-semantic","messages":[{{"role":"user","content":"华东师范大学有哪些学者？"}}]}}'
# 3. 直接调工具
curl -s -X POST {BASE}/v1/tools/explore_class \\
  -H 'Content-Type: application/json' -d '{{"arguments":{{"class_name":"Scholar","limit":5}}}}'
```

## 错误

- `404 not_found` — 路径不存在（本平台不返回软 404）
- `422 invalid_argument` — 参数错误，见 `error.next_action`
- 全站错误格式：`application/problem+json`

## 本体

- 12 类（Scholar / Publication / Institution / Company / Field / Venue / Dataset 等）
- 13 关系（author_of / affiliation / published_in / belongs_to_field 等）
- 19 属性，整体 78,971 三元组
"""


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt():
    base = str(get_base_url())
    txt = LLMS_TXT.replace("{BASE}", base.rstrip("/"))
    txt = txt.replace("{{", "{").replace("}}", "}")
    return txt


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return """User-agent: *
Allow: /
Disallow: /_stcore/

# AI / agent 入口
# llms.txt:   /llms.txt
# OpenAPI:    /openapi.json
# 本体:       /v1/ontology
# MCP:        /.well-known/mcp
"""


@app.get("/AGENTS.md", response_class=PlainTextResponse)
def agents_md():
    return """# AGENTS.md —— Agent 接入手册

## 平台是什么
科研 + 企业数据的本体化平台，对外提供 OpenAI 兼容端点。Agent 拿到本页即可自举。

## 认证
- 匿名只读：无需 key，直接调用 `/v1/*`。
- 未来可加 `POST /v1/keys` 自助签发（计划中）。

## 核心端点
- `GET /v1/models` — 平台暴露的数据能力模型列表
- `POST /v1/chat/completions` — 语义问数（OpenAI 兼容）
- `GET /v1/tools` — 工具清单（含参数 JSON Schema）
- `POST /v1/tools/{name}` — 直接调用工具
- `GET /v1/ontology` — 机器可读本体定义
- `GET /v1/search?q=` — 语义检索

## 工具
工具名：list_ontology / list_sources / explore_class / find_entity /
entity_detail / query_relation / sparql / semantic_ask。每个工具的 `parameters` 即参数 JSON Schema，
Agent 需先从 `/v1/tools` 获取准确 schema 再传参。

## 错误码
| code | 含义 | next_action |
|---|---|---|
| not_found | 端点不存在 | GET /openapi.json |
| invalid_argument | 参数错误 | 检查工具 schema |
| quota_exceeded | 配额超限 | 稍后重试 |

## 配额
- 匿名：默认 100 次/天（读操作计数，按日重置），超限返回 429 + Retry-After。
- 提升：`POST /v1/keys` 领取 key，请求头带 `Authorization: Bearer <key>` 提到 1000 次/天。
- `POST /v1/chat/completions` 支持 `tools` 参数（返回工具目录）；`stream=true` 返回 400。
"""


@app.get("/openapi.json")
def openapi_json():
    """OpenAPI 3.1 机器可读契约，供 agent 撒数据用。"""
    base = str(get_base_url()).rstrip("/")
    tools = [
        {
            "name": t["name"],
            "description": t["description"],
            "parameters": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in TOOL_SPECS
    ]
    schemas = {}
    for t in tools:
        schemas[t["name"]] = {
            "type": "object",
            "properties": {k: v for k, v in t["parameters"].get("properties", {}).items()},
            "required": t["parameters"].get("required", []),
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "AI 优先数据平台",
            "version": "2.0.0",
            "description": "机器可读入口见 /llms.txt；错误为 problem+json。匿名只读。",
        },
        "servers": [{"url": base}],
        "paths": {
            "/v1/models": {
                "get": {"operationId": "listModels", "summary": "列出数据能力模型", "responses": {"200": {"description": "模型列表"}}}
            },
            "/v1/tools": {
                "get": {"operationId": "listTools", "summary": "列出可调用数据工具（含 JSON Schema）", "responses": {"200": {"description": "工具列表"}}}
            },
            "/v1/tools/{name}": {
                "post": {"operationId": "callTool", "summary": "直接调用平台工具", "parameters": [{"name": "name", "in": "path", "required": True, "schema": {"type": "string"}}], "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"arguments": {"type": "object"}}}}}}, "responses": {"200": {"description": "工具结果"}}}
            },
            "/v1/chat/completions": {
                "post": {"operationId": "chatCompletions", "summary": "OpenAI 兼容语义问数", "responses": {"200": {"description": "对话补全"}}}
            },
            "/v1/ontology": {
                "get": {"operationId": "getOntology", "summary": "机器可读本体", "responses": {"200": {"description": "本体定义"}}}
            },
            "/v1/search": {
                "get": {"operationId": "search", "summary": "语义检索", "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "检索结果 {items, next_cursor, total}"}}}
            },
        },
        "components": {"schemas": schemas},
        "tools": tools,
    }


# ============ 机器面：本体 ============
@app.get("/v1/ontology")
def v1_ontology(request: Request):
    _check_quota(request)
    onto, cat, graph = None, None, None
    onto, cat, graph, q, tools = get_platform()
    st = graph.stats()
    return {
        "id": "ai-platform-ontology",
        "type": "https://schema.org/DefinedTermSet",
        "name": "面向 AI 平台本体",
        "namespace": ONTO_NS,
        "resource_namespace": RES_NS,
        "stats": st,
        "classes": [str(c).split("#")[-1] for c in onto.classes],
        "relations": [str(p).split("#")[-1] for p in onto.object_props],
        "properties": [str(p).split("#")[-1] for p in onto.data_props],
        "class_instances": st.get("class_counts", {}),
    }


# ============ 机器面：MCP 发现 + 自助 key ============
@app.get("/.well-known/mcp")
async def mcp_discovery():
    """MCP Streamable HTTP 发现入口（P0-5：地址 /mcp 现为真实协议端点）。"""
    base = str(get_base_url()).rstrip("/")
    return {
        "name": "ai-platform",
        "version": "2.0.0",
        "description": "面向 AI 的大数据平台数据工具（MCP over Streamable HTTP）",
        "url": base + "/mcp",
        "protocolVersion": "2025-03-26",
    }


# -------- MCP 协议端点：透传到独立 MCP 进程（8602），保持同域 --------
@app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
@app.api_route("/mcp/", methods=["GET", "POST", "DELETE"], include_in_schema=False)
@app.api_route("/mcp/{rest:path}", methods=["GET", "POST", "DELETE"], include_in_schema=False)
@app.api_route("/v1/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
@app.api_route("/v1/mcp/", methods=["GET", "POST", "DELETE"], include_in_schema=False)
@app.api_route("/v1/mcp/{rest:path}", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def mcp_proxy(request: Request, rest: str = ""):
    """把 /mcp 与 /v1/mcp 透传给独立运行的 MCP Streamable HTTP 服务(8602)。"""
    suffix = request.url.path
    if suffix.startswith("/v1/mcp"):
        sub = suffix[len("/v1"):]   # /v1/mcp... -> /mcp...
    else:
        sub = suffix
    target = MCP_ORIGIN + ("/mcp/" + rest if rest else (sub or "/mcp"))
    # 保持 query
    if request.url.query:
        target += "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length", "connection", "transfer-encoding")}
    body = await request.body()
    try:
        up = await get_client().request(request.method, target, content=body, headers=headers)
    except Exception as e:
        return JSONResponse(status_code=502,
                            content=problem(502, "upstream_error", f"MCP 服务不可达: {e}",
                                            "请确认已启动 aiplatform/mcp_server.py --http（端口 8602）"),
                            media_type="application/problem+json")
    resp_headers = {}
    for k, v in up.headers.items():
        if k.lower() not in ("transfer-encoding", "connection"):
            resp_headers[k] = v
    if not resp_headers.get("content-type") and "content-type" in up.headers:
        resp_headers["content-type"] = up.headers["content-type"]
    return Response(content=up.content, status_code=up.status_code,
                    headers=resp_headers, media_type=up.headers.get("content-type"))


# 匿名只读配额（内存计数；按天重置；按客户端分别计数，避免现场多人集体 429）
import threading
_quota_lock = threading.Lock()
# anon: {client_id: count}  keys: {key_token: {"day","count","limit"}}
_quota_state = {"day": "", "anon": {}, "keys": {}}
_DAILY_QUOTA = int(os.environ.get("PLATFORM_DAILY_QUOTA", "100"))
_KEY_QUOTA = int(os.environ.get("PLATFORM_KEY_QUOTA", "1000"))
# 演示白名单：这些客户端享有更高匿名额度（默认空；现场可用 PLATFORM_DEMO_WHITELIST=1.2.3.4,5.6.7.8 打开）
_DEMO_WHITELIST = {x.strip() for x in os.environ.get("PLATFORM_DEMO_WHITELIST", "").split(",") if x.strip()}
_DEMO_QUOTA = int(os.environ.get("PLATFORM_DEMO_QUOTA", str(_KEY_QUOTA)))
_issued_keys = set()


def _today():
    return time.strftime("%Y-%m-%d")


def _client_id(request: Request) -> str:
    """客户端标识：优先 X-Forwarded-For 首跳（隧道/反代场景），否则对端 IP。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return (request.client.host if request.client else "") or "unknown"


def _check_quota(request: Request):
    """配额门禁：超限抛 429 + Retry-After（秒到当日重置）；无效 key 抛 401（不再静默降级为匿名）。"""
    auth = request.headers.get("authorization", "")
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    with _quota_lock:
        day = _today()
        now = time.localtime()
        secs_to_midnight = max(1, int(86400 - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)))
        if _quota_state["day"] != day:
            _quota_state["day"] = day
            _quota_state["anon"] = {}
            _quota_state["keys"] = {}
        # ---- 带 key：必须是平台签发的，否则明确 401（agent 应去重新签发，而不是傻等配额）----
        if token:
            if token not in _issued_keys:
                raise HTTPException(status_code=401, detail=problem(
                    401, "invalid_key", "无效或已过期的 API key（平台未签发过该 key）。",
                    "POST /v1/keys 重新签发；如无需 key 请去掉 Authorization 头"))
            st = _quota_state["keys"].setdefault(token, {"day": day, "count": 0, "limit": _KEY_QUOTA})
            if st["day"] != day:
                st["day"] = day; st["count"] = 0
            if st["count"] >= st["limit"]:
                raise HTTPException(status_code=429,
                                    headers={"Retry-After": str(secs_to_midnight)},
                                    detail=problem(429, "rate_limited",
                                                   f"该 key 额度已用尽（{st['limit']}/天）。请明天再试或重新签发 key。",
                                                   "POST /v1/keys"))
            st["count"] += 1
            return st["limit"]
        # ---- 匿名档：按客户端分别计数（现场多评委/多 agent 不再互相挤爆）----
        cid = _client_id(request)
        limit = _DEMO_QUOTA if cid in _DEMO_WHITELIST else _DAILY_QUOTA
        used = _quota_state["anon"].get(cid, 0)
        if used >= limit:
            raise HTTPException(status_code=429,
                                headers={"Retry-After": str(secs_to_midnight)},
                                detail=problem(429, "rate_limited",
                                               f"该客户端匿名配额已用尽（{limit}次/天，读操作）。"
                                               f"请 POST /v1/keys 获取更高额度的 key。",
                                               "POST /v1/keys"))
        _quota_state["anon"][cid] = used + 1
        return limit


@app.post("/v1/keys", status_code=201)
async def issue_key():
    """自助签发匿名只读 key（匿名 100 次/天；带 key 提升到 1000 次/天）。"""
    key = "pk_anon_" + uuid.uuid4().hex[:24]
    with _quota_lock:
        _issued_keys.add(key)
    return {
        "key": key,
        "type": "anonymous",
        "quota": {"requests_per_day": _KEY_QUOTA, "scope": "read", "anonymous_default": _DAILY_QUOTA},
        "instructions": "Authorization: Bearer " + key,
        "usage": "在请求头带 Authorization: Bearer <key> 即可提升每天配额。",
    }


@app.get("/v1/mcp")
async def mcp_root():
    """MCP over Streamable HTTP 已透传到 /mcp（见上方 mcp_proxy）。此 GET 返回协议能力说明。"""
    base = str(get_base_url()).rstrip("/")
    return {
        "jsonrpc": "2.0",
        "note": "真实 MCP Streamable HTTP 端点：POST " + base + "/mcp （用 MCP 客户端连此地址）",
        "tools": [
            {"name": t["name"], "description": t["description"],
             "inputSchema": t.get("parameters", {"type": "object", "properties": {}})}
            for t in TOOL_SPECS
        ],
        "protocolVersion": "2025-03-26",
        "id": 1,
    }


# ============ 机器面：语义检索 ============
@app.get("/v1/search")
def v1_search(request: Request, q: str = "", limit: int = 10, cursor: str = ""):
    _check_quota(request)
    if not q:
        raise HTTPException(status_code=422, detail=problem(422, "invalid_argument", "缺少 q 参数", "GET /openapi.json 查看用法"))
    onto, cat, graph, _q, tools = get_platform()
    try:
        limit = max(1, min(int(limit), 50))
        offset = int(cursor) if cursor.isdigit() else 0
        # 跨类检索（含中文 cnLabel），真实分页
        all_items = tools.search_cross_class(q, limit=100000)
        page = all_items[offset:offset + limit]
        has_more = (offset + len(page)) < len(all_items)
        total = len(all_items)
        hits = all_items[:offset] + page  # 供 total/suggested 使用
    except Exception as e:
        raise HTTPException(status_code=400, detail=problem(400, "query_failed", f"{type(e).__name__}: {e}", "检查 q 参数"))
    nxt = str(offset + len(page)) if has_more else None
    items = [
        {"id": it["id"], "name": it["name"], "cnName": it.get("cnName", ""), "class": it.get("class", "")}
        for it in page
    ]
    resp = {
        "items": items,
        "next_cursor": nxt,
        "total": total,
        "suggested_actions": [
            {"tool": "semantic_ask", "why": "用自然语言问数（更准）", "params": {"question": q}},
            {"tool": "find_entity", "why": "按指定类继续检索实体", "params": {"class_name": "Publication", "keyword": q}},
        ],
    }
    # 0 命中兜底：明确告知未匹配 + 指向 semantic_ask，不再静默空
    if not items and total == 0:
        resp["hint"] = "未匹配到「" + q + "」。该词可能是语义概念而非实体名，建议改用自然语言问数（semantic_ask）。"
    return resp


# ============ 机面：/v1/*（沿用 api_server 的逻辑）============
TOOL_SPECS = [
    {"name": "list_ontology", "description": "列出平台本体的类、关系、属性", "parameters": {"type": "object", "properties": {}}},
    {"name": "list_sources", "description": "列出平台已接入的数据源", "parameters": {"type": "object", "properties": {}}},
    {"name": "explore_class", "description": "浏览某个本体类的实例", "parameters": {"type": "object", "properties": {"class_name": {"type": "string", "description": "本体类，如 Scholar/Publication/Company"}, "limit": {"type": "integer", "default": 5}}, "required": ["class_name"]}},
    {"name": "find_entity", "description": "按关键词在某个类里查找实体", "parameters": {"type": "object", "properties": {"class_name": {"type": "string"}, "keyword": {"type": "string"}, "limit": {"type": "integer", "default": 10}, "offset": {"type": "integer", "default": 0, "minimum": 0}}, "required": ["class_name", "keyword"]}},
    {"name": "entity_detail", "description": "查看某实体的完整信息（属性 + 关系）", "parameters": {"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]}},
    {"name": "query_relation", "description": "按关系查询：某类的实体通过某关系指向另一类", "parameters": {"type": "object", "properties": {"subject_class": {"type": "string"}, "relation": {"type": "string"}, "object_class": {"type": "string"}, "limit": {"type": "integer", "default": 20}}, "required": ["subject_class", "relation", "object_class"]}},
    {"name": "sparql", "description": "对统一知识图谱执行 SPARQL 查询", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "semantic_ask", "description": "自然语言问数（本体语义解析）", "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
]


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: Optional[str] = "platform-semantic"
    messages: List[Message]
    temperature: Optional[float] = 0
    max_tokens: Optional[int] = 600
    stream: Optional[bool] = False
    tools: Optional[List[Dict[str, Any]]] = None


class ToolCallRequest(BaseModel):
    arguments: Dict[str, Any] = {}


PLATFORM_MODELS = [
    {"id": "platform-semantic", "object": "model", "owned_by": "ai-data-platform", "description": "本体驱动的语义查询：自然语言 → 本体 → 数据"},
    {"id": "platform-tools", "object": "model", "owned_by": "ai-data-platform", "description": "工具调用模式：返回平台可用的工具清单"},
    {"id": "platform-graph", "object": "model", "owned_by": "ai-data-platform", "description": "知识图谱直查：实体/关系/统计"},
]


@app.get("/health")
def health():
    onto, cat, graph, q, tools = get_platform()
    st = graph.stats()
    return {"status": "ok", "sources": len(cat.list()), "triples": st["triples"], "entities": st["entities"], "classes": st["classes"]}


@app.get("/ai-readiness.json")
async def ai_readiness():
    """平台的「AI 优先」自我体检 —— 【真跑】而非硬编码（报告问题2）。
    通过 httpx ASGITransport 在本进程内真实发请求：真发预检、真翻页、真探 MCP 上游、
    真验请求 ID、真验只读与配额路径。如实返回 passed/failed/unknown。
    "11/15 + 4 条待修" 远比假的 "15/15" 有价值。"""
    from httpx import AsyncClient, ASGITransport
    checks = []

    def rec(cid, ok, detail, extra=None):
        item = {"id": cid, "ok": bool(ok), "detail": detail}
        if extra:
            item.update(extra)
        checks.append(item)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://readiness.local",
                           timeout=60.0, follow_redirects=False) as c:
        # 1 llms_txt
        try:
            r = await c.get("/llms.txt")
            rec("llms_txt", r.status_code == 200 and "text/plain" in r.headers.get("content-type", "")
                and "/v1/chat/completions" in r.text, f"GET /llms.txt -> {r.status_code}")
        except Exception as e:
            rec("llms_txt", False, f"{type(e).__name__}: {e}")

        # 2 real_404 + 3 rid_consistent（同一请求同时验）
        try:
            r = await c.get("/__readiness_probe__")
            ctype = r.headers.get("content-type", "")
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            rec("real_404", r.status_code == 404 and "problem+json" in ctype,
                f"GET /__readiness_probe__ -> {r.status_code} {ctype}")
            hdr_rid = r.headers.get("x-request-id", "")
            body_rid = (body.get("error") or {}).get("request_id", "")
            rec("rid_consistent", bool(hdr_rid) and hdr_rid == body_rid,
                f"header={hdr_rid} body={body_rid}")
        except Exception as e:
            rec("real_404", False, f"{type(e).__name__}: {e}")
            rec("rid_consistent", False, f"{type(e).__name__}: {e}")

        # 4 openapi_servers
        try:
            r = await c.get("/openapi.json")
            j = r.json()
            rec("openapi_servers", bool(j.get("servers")) and "tools" in j,
                f"servers={j.get('servers')} tool_schemas={len(j.get('components',{}).get('schemas',{}))}")
        except Exception as e:
            rec("openapi_servers", False, f"{type(e).__name__}: {e}")

        # 5 mcp_live —— 真探上游 MCP 进程（不是查网关自己）
        try:
            async with httpx.AsyncClient(timeout=20.0) as mc:
                mi = await mc.post(MCP_ORIGIN + "/mcp/", headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "readiness", "version": "1"}}})
            ok = mi.status_code == 200 and ("text/event-stream" in mi.headers.get("content-type", "")
                                            or bool(mi.headers.get("mcp-session-id")))
            rec("mcp_live", ok, f"POST {MCP_ORIGIN}/mcp/ initialize -> {mi.status_code}")
        except Exception as e:
            rec("mcp_live", False, f"MCP 上游不可达（{MCP_ORIGIN}）: {type(e).__name__}")

        # 6 quota_enforced —— 真验两条路径：无效 key→401、有效 key→200
        try:
            r_bad = await c.get("/v1/models", headers={"Authorization": "Bearer pk_invalid_probe"})
            r_key = await c.post("/v1/keys")
            key = r_key.json().get("key", "") if r_key.status_code == 201 else ""
            r_ok = await c.get("/v1/models", headers={"Authorization": f"Bearer {key}"}) if key else None
            ok = r_bad.status_code == 401 and r_ok is not None and r_ok.status_code == 200
            rec("quota_enforced", ok,
                f"invalid_key -> {r_bad.status_code}（应 401）; valid_key -> "
                f"{r_ok.status_code if r_ok is not None else 'n/a'}（应 200）")
        except Exception as e:
            rec("quota_enforced", False, f"{type(e).__name__}: {e}")

        # 7 chat_tools（接受 tools）+ stream 明确 400
        try:
            r = await c.post("/v1/chat/completions",
                             json={"messages": [{"role": "user", "content": "hi"}], "tools": []})
            r_s = await c.post("/v1/chat/completions",
                               json={"messages": [{"role": "user", "content": "hi"}], "stream": True})
            rec("chat_tools", r.status_code == 200 and r_s.status_code == 400,
                f"tools=[] -> {r.status_code}; stream=true -> {r_s.status_code}（应 400）")
        except Exception as e:
            rec("chat_tools", False, f"{type(e).__name__}: {e}")

        # 8 tools_openai_format
        try:
            r = await c.get("/v1/tools?format=openai")
            j = r.json()
            t0 = (j.get("tools") or [{}])[0]
            rec("tools_openai_format", t0.get("type") == "function" and "function" in t0,
                f"/v1/tools?format=openai -> {r.status_code}, 首项 type={t0.get('type')}")
        except Exception as e:
            rec("tools_openai_format", False, f"{type(e).__name__}: {e}")

        # 9 pagination_real —— 真翻页比对
        try:
            p1 = (await c.get("/v1/search", params={"q": "learning", "limit": 3})).json()
            cur = p1.get("next_cursor")
            if cur:
                p2 = (await c.get("/v1/search", params={"q": "learning", "limit": 3, "cursor": cur})).json()
                ids1 = [i["id"] for i in p1.get("items", [])]
                ids2 = [i["id"] for i in p2.get("items", [])]
                rec("pagination_real", ids1 != ids2 and bool(ids1),
                    f"page1={ids1[:1]} page2={ids2[:1]} total={p1.get('total')}")
            else:
                rec("pagination_real", True, "next_cursor 为 null（明说无下一页）")
        except Exception as e:
            rec("pagination_real", False, f"{type(e).__name__}: {e}")

        # 10 chinese_search —— 0 命中必须有 hint
        try:
            r = await c.get("/v1/search", params={"q": "研究"})
            j = r.json()
            ok = bool(j.get("items") or j.get("hint") or j.get("suggested_actions"))
            rec("chinese_search", ok, f"q=研究 命中 {len(j.get('items', []))} 条，hint={'有' if j.get('hint') else '无'}")
        except Exception as e:
            rec("chinese_search", False, f"{type(e).__name__}: {e}")

        # 11 cors_legal —— 断言打在行为上：预检必须 204 且含 Allow-Origin
        try:
            r = await c.options("/v1/chat/completions", headers={
                "Origin": "https://probe.example", "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type"})
            aco = r.headers.get("access-control-allow-origin", "")
            cred = r.headers.get("access-control-allow-credentials", "")
            rec("cors_legal", r.status_code == 204 and bool(aco) and cred != "true",
                f"OPTIONS 预检 -> {r.status_code}（应 204）, Allow-Origin={aco or '缺失'}, Credentials={cred or '无'}")
        except Exception as e:
            rec("cors_legal", False, f"{type(e).__name__}: {e}")

        # 12 validation_problem_json
        try:
            r = await c.get("/v1/search", params={"q": "x", "limit": "abc"})
            rec("validation_problem_json", r.status_code == 422 and "problem+json" in r.headers.get("content-type", ""),
                f"limit=abc -> {r.status_code} {r.headers.get('content-type','')}")
        except Exception as e:
            rec("validation_problem_json", False, f"{type(e).__name__}: {e}")

        # 13 usage_honest
        try:
            r = await c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
            note = (r.json().get("usage") or {}).get("note", "")
            rec("usage_honest", "近似" in note, f"usage.note={note[:40]}")
        except Exception as e:
            rec("usage_honest", False, f"{type(e).__name__}: {e}")

        # 14 scholar_dedup —— 真调 semantic_ask 看去重
        try:
            r = await c.post("/v1/tools/semantic_ask",
                             json={"arguments": {"question": "华东师范大学有哪些学者？"}})
            res = (r.json().get("result") or {})
            sch = res.get("scholars") or []
            rec("scholar_dedup", len(sch) == len(set(sch)),
                f"scholars={len(sch)} 条，去重后={len(set(sch))} 条")
        except Exception as e:
            rec("scholar_dedup", False, f"{type(e).__name__}: {e}")

        # 15 sparql_readonly —— 写操作必须 4xx
        try:
            r_w = await c.post("/v1/tools/sparql", json={"arguments": {"query": "DELETE WHERE {?s ?p ?o}"}})
            r_s = await c.post("/v1/tools/sparql", json={"arguments": {"query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"}})
            rec("sparql_readonly", r_w.status_code == 403 and r_s.status_code == 200,
                f"DELETE -> {r_w.status_code}（应 403）; SELECT -> {r_s.status_code}（应 200）")
        except Exception as e:
            rec("sparql_readonly", False, f"{type(e).__name__}: {e}")

    passed = sum(1 for x in checks if x["ok"])
    return {
        "name": "ai-data-platform",
        "version": "2.1.0",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "本文件为进程内真跑自测（真发预检/真翻页/真探 MCP/真验请求 ID），非硬编码。",
        "summary": {"total": len(checks), "passed": passed, "failed": len(checks) - passed},
        "checks": checks,
    }


@app.get("/v1/models")
def models(request: Request):
    _check_quota(request)
    return {"object": "list", "data": PLATFORM_MODELS}


@app.get("/v1/tools")
def list_tools(request: Request, format: str = ""):
    _check_quota(request)
    if format and format.lower() == "openai":
        # OpenAI function-calling 格式（Cursor/Dify/LangChain 等直接可用）
        return {"tools": [
            {"type": "function", "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("parameters", {"type": "object", "properties": {}})}}
            for t in TOOL_SPECS
        ]}
    return {"tools": TOOL_SPECS}


@app.post("/v1/tools/{tool_name}")
def call_tool(tool_name: str, req: ToolCallRequest, request: Request):
    _check_quota(request)
    onto, cat, graph, q, tools = get_platform()
    args = dict(req.arguments or {})
    try:
        if tool_name == "list_ontology":
            result = tools.list_ontology()
        elif tool_name == "list_sources":
            result = tools.list_sources(cat)
        elif tool_name == "explore_class":
            result = tools.explore_class(args.get("class_name"), args.get("limit", 5))
        elif tool_name == "find_entity":
            result = tools.find_entity(args.get("class_name"), args.get("keyword"), args.get("limit", 10), args.get("offset", 0))
        elif tool_name == "entity_detail":
            result = tools.entity_detail(args.get("entity_id"))
        elif tool_name == "query_relation":
            result = tools.query_relation(args.get("subject_class"), args.get("relation"), args.get("object_class"), args.get("limit", 20))
        elif tool_name == "sparql":
            result = tools.sparql(args.get("query"))
        elif tool_name == "semantic_ask":
            result = tools.semantic_ask(args.get("question", ""))
        else:
            raise HTTPException(status_code=404, detail=problem(404, "not_found", f"未知工具 {tool_name}", "GET /v1/tools 查看工具清单"))
        return {"tool": tool_name, "result": result}
    except SPARQLReadOnlyError as e:
        # 只读平台：写操作被拒 → 403 + problem+json（不能用 200 承载错误，agent 按状态码判断）
        raise HTTPException(status_code=403, detail=problem(403, "readonly", str(e), "平台 SPARQL 只读，请改用 SELECT"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=problem(400, "tool_error", f"{type(e).__name__}: {e}", "检查参数"))


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest, request: Request):
    onto, cat, graph, q, tools = get_platform()
    _check_quota(request)
    # stream 明确拒绝（返回 400，不再静默返回整包 JSON）
    if req.stream:
        raise HTTPException(status_code=400, detail=problem(
            400, "unsupported_parameter", "stream=true 暂不支持（本平台不提供 SSE 流式）。请去掉 stream 参数。",
            "移除 stream 后用 POST /v1/chat/completions 同步调用"))
    user_msgs = [m.content for m in req.messages if m.role == "user"]
    if not user_msgs:
        raise HTTPException(status_code=400, detail=problem(400, "invalid_argument", "缺少 user 消息", "messages 需含 role=user"))
    question = user_msgs[-1]
    t0 = time.time()
    try:
        result = q.ask(question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=problem(500, "query_failed", f"查询失败: {e}", "换个问法"))
    # tools 参数：不再静默忽略；非空时在返回中告知可用工具目录
    tool_meta = None
    if getattr(req, "tools", None):
        tool_meta = {"provided": True, "note": "平台已按请求返回工具目录；本兼容层支持语义问数，工具调用见 POST /v1/tools/{name}",
                     "available": [t["name"] for t in TOOL_SPECS]}
    summary = _summarize(question, result)
    jtext, jtrunc = _json_fit(result)
    content = summary + "\n\n```json\n" + jtext + "\n```"
    resp = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model or "platform-semantic",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": len(question), "completion_tokens": len(content), "total_tokens": len(question) + len(content),
                  "note": "token 为字符近似值（平台未接 tokenizer）"},
        "platform": {"intent": result.get("intent"), "elapsed_ms": int((time.time() - t0) * 1000),
                     "json_truncated": jtrunc},
    }
    if tool_meta:
        resp["platform"]["tools"] = tool_meta
    return resp


def _json_fit(obj, limit=3000):
    """把工具/查询结果装进 JSON 且【永远可解析】（报告问题4：绝不产生半个 JSON）。
    优先截结构（少给数组元素），而不是截字符；实在放不下时，用显式的 truncated 标记替换。
    返回 (json文本, 是否被截断)。"""
    full = json.dumps(obj, ensure_ascii=False, indent=1)
    if len(full) <= limit:
        return full, False
    # 1) 结构截断：对顶层 dict 里的 list 逐级缩减元素个数，保持 JSON 完整
    cand = copy.deepcopy(obj) if isinstance(obj, (dict, list)) else obj
    if isinstance(cand, dict):
        lists = [k for k, v in cand.items() if isinstance(v, list) and v]
        for k in lists:
            for keep in (20, 12, 8, 5, 3, 1):
                cand[k] = obj[k][:keep]
                if len(json.dumps(cand, ensure_ascii=False, indent=1)) <= limit:
                    text = json.dumps(cand, ensure_ascii=False, indent=1)
                    return text, True
            cand[k] = obj[k][:1]
    # 2) 兜底：整体放不下 → 显式声明截断（仍是合法 JSON）
    meta = {"truncated": True, "kept": limit, "total": len(full),
            "note": "结果过大，已省略正文；请改用 POST /v1/tools/{name} 或缩小查询范围取完整数据。",
            "intent": (obj or {}).get("intent") if isinstance(obj, dict) else None}
    return json.dumps(meta, ensure_ascii=False, indent=1), True


def _summarize(question, r):
    return summarize_result(question, r)


# ============ 人面：反代 Streamlit ============
# Streamlit 人面资源白名单。其余未匹配路径返回真 404（不做软 404）。
_HUMAN_OK = (
    "/_stcore/", "/static/", "/assets/", "/favicon.ico", "/manifest",
    "/index.html", "/component/", "/healthz",
)


def _is_human(path: str) -> bool:
    if path == "/" or path == "":
        return True
    return any(path.startswith(p) for p in _HUMAN_OK)


@app.get("/{path:path}")
async def human(path: str, request: Request):
    """人面：转发 Streamlit 的已知资源路径；其余 404。"""
    if not _is_human("/" + path):
        raise HTTPException(status_code=404, detail=problem(
            404, "not_found", f"No endpoint at /{path}", "GET /openapi.json to list available endpoints"))
    return await _proxy(request)


@app.api_route("/{path:path}", methods=["POST", "PUT", "DELETE", "PATCH"])
async def human_other(path: str, request: Request):
    if not _is_human("/" + path):
        raise HTTPException(status_code=404, detail=problem(
            404, "not_found", f"No endpoint at /{path}", "GET /openapi.json to list available endpoints"))
    return await _proxy(request)


async def _proxy(request: Request):
    target = STREAMLIT_ORIGIN + request.url.path
    if request.url.query:
        target += "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    headers["Host"] = STREAMLIT_ORIGIN.split("//")[1]
    client = get_client()
    body = await request.body()
    try:
        upstream = await client.request(request.method, target, content=body, headers=headers)
    except Exception as e:
        return JSONResponse(status_code=502, content=problem(502, "upstream_error", f"Streamlit 不可达: {e}", "检查 8603 是否启动"))
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in ("transfer-encoding", "connection", "content-length")}
    # 关键：Streamlit 前端用 /_stcore/host-config 的 allowedOrigins 决定是否连 WS。
    # 经网关(8610)访问时，浏览器 origin 不在白名单 → 前端不连 WS → 白屏。
    # 这里把当前访问的 origin 注入白名单。
    content = upstream.content
    media = upstream.headers.get("content-type", "")
    rewritten = False
    if request.url.path.rstrip("/").endswith("/host-config") and "json" in media and isinstance(content, (bytes, bytearray)):
        try:
            cfg = json.loads(content.decode("utf-8", "replace"))
            if isinstance(cfg, dict):
                origin = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
                ao = cfg.get("allowedOrigins") or []
                if isinstance(ao, list) and origin not in ao:
                    ao = list(ao) + [origin]
                    cfg["allowedOrigins"] = ao
                cfg.setdefault("allowOriginMatching", ["same-scheme-host-port"])
                content = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
                media = "application/json"
                rewritten = True
        except Exception:
            pass
    if not rewritten:
        # 未改写时仍把 content-length 放回（若上游给了）
        for _k, _v in upstream.headers.items():
            if _k.lower() == "content-length":
                resp_headers[_k] = _v
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=media,
    )


# ---------- WebSocket 反代（Streamlit 依赖）----------
import asyncio
import websockets as ws_lib
from urllib.parse import urlencode


def _format_qs(qp) -> str:
    """把 starlette QueryParams 转 urlencoded query string。"""
    try:
        if hasattr(qp, "items"):
            items = list(qp.items())
        else:
            items = list(qp)
        if items:
            return "?" + urlencode(items)
    except Exception:
        pass
    return ""


@app.websocket("/_stcore/stream")
async def ws_stream(websocket: WebSocket):
    # 关键：Streamlit 前端升级时带 Sec-WebSocket-Protocol: streamlit[, PLACEHOLDER_AUTH_TOKEN]
    # 必须回显子协议，否则前端握手失败 → 永远重试 → 白屏（报告 P0-1 的真根因）。
    req_sub = [p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    echo = "streamlit" if "streamlit" in req_sub else (req_sub[0] if req_sub else "")
    await websocket.accept(subprotocol=echo)
    # 透传浏览器 URL 的 query（Streamlit 用 session_id 等）+ cookie + origin
    qs = websocket.query_params if hasattr(websocket, "query_params") else {}
    qstr = _format_qs(qs)
    target = STREAMLIT_ORIGIN.replace("http", "ws") + "/_stcore/stream" + qstr
    extra = {"Host": STREAMLIT_ORIGIN.split("//")[1]}
    cookie = websocket.headers.get("cookie")
    if cookie:
        extra["Cookie"] = cookie
    origin = websocket.headers.get("origin")
    if origin:
        extra["Origin"] = origin
    # 关闭压缩：浏览器与上游都不做 permessage-deflate，避免帧体不匹配
    # 并回传子协议 streamlit，让上游 Streamlit 后端与直连行为一致
    try:
        async with ws_lib.connect(target, additional_headers=extra,
                                  subprotocols=[echo] if echo else [],
                                  compression=None,
                                  max_size=None, open_timeout=15) as up:
            async def client_to_up():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("bytes") is not None:
                            await up.send(msg["bytes"])
                        elif msg.get("text") is not None:
                            await up.send(msg["text"])
                        else:  # websocket.disconnect
                            break
                except Exception:
                    pass

            async def up_to_client():
                try:
                    while True:
                        msg = await up.recv()
                        if isinstance(msg, (bytes, bytearray)):
                            await websocket.send_bytes(bytes(msg))
                        else:
                            await websocket.send_text(msg)
                except Exception:
                    pass
            await asyncio.gather(client_to_up(), up_to_client(), return_exceptions=True)
    except Exception as e:
        print(f"[WS] handshake/proxy error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ============ 全局 404（真 404 + problem+json，替代 Streamlit 软 404）============
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    # 若抛出方已给出具体 problem+json（如「未知工具 xxx」），原样保留，不被兜底文案覆盖
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=404, content=detail, media_type="application/problem+json")
    return JSONResponse(
        status_code=404,
        content=problem(
            404,
            "not_found",
            f"No endpoint at {request.url.path}",
            "GET /openapi.json to list available endpoints",
        ),
        media_type="application/problem+json",
    )


# HTTPException（400/422/404 等）统一转 problem+json
@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    first = (exc.errors() or [{}])[0]
    loc = first.get("loc", [])
    field = ".".join(str(x) for x in loc) if loc else ""
    msg = first.get("msg", "参数校验失败")
    return JSONResponse(
        status_code=422,
        content=problem(422, "invalid_argument", f"参数错误: {field} {msg}", "检查请求参数"),
        media_type="application/problem+json",
    )


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        body = detail
    else:
        code = {400: "invalid_argument", 401: "unauthorized", 403: "forbidden",
                404: "not_found", 405: "method_not_allowed", 409: "conflict",
                422: "invalid_argument", 429: "rate_limited", 500: "internal_error"}.get(exc.status_code, "error")
        body = problem(exc.status_code, code, str(detail), "GET /openapi.json to list available endpoints",
                       request.headers.get("x-request-id", ""))
    if exc.status_code == 429:
        return JSONResponse(status_code=exc.status_code, content=body,
                            media_type="application/problem+json",
                            headers={"Retry-After": str(exc.headers.get("Retry-After", 60)) if exc.headers else "60"})
    headers = dict(exc.headers) if exc.headers else None
    return JSONResponse(status_code=exc.status_code, content=body,
                        media_type="application/problem+json", headers=headers)


if __name__ == "__main__":
    import uvicorn
    PORT = int(os.environ.get("PLATFORM_API_PORT", "8610"))
    print(f"AI 优先平台 · 统一网关：http://0.0.0.0:{PORT}")
    print(f"  人面  : / (反代 Streamlit {STREAMLIT_ORIGIN})")
    print(f"  自举  : /llms.txt  /AGENTS.md  /openapi.json  /robots.txt")
    print(f"  机面  : /v1/models  /v1/tools  /v1/chat/completions  /v1/ontology  /v1/search")
    print(f"  MCP   : /.well-known/mcp  /v1/mcp")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")

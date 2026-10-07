#!/usr/bin/env bash
# check.sh —— AI 友好度门禁（赛题 v7）：全绿才算交付。
# 用法: bash check.sh http://localhost:8610    （建议在修完任一机器面后必跑）
# 说明：每项断言都对应对外文档里的一条「承诺」，文档与实现双向绑定。
B=${1:?usage: $0 http://localhost:8610}
B=${B%/}
pass=0; fail=0; skip=0
PYBIN=${PYTHON:-"$(dirname "$0")/.venv/Scripts/python.exe"}
if ! "$PYBIN" -c 'import json' >/dev/null 2>&1; then PYBIN=python; fi
if ! "$PYBIN" -c 'import json' >/dev/null 2>&1; then
  echo "BLOCKED: 需要可运行的 Python（可通过 PYTHON 指定）"; exit 2
fi
chk(){ if [ "$2" = "$3" ]; then echo "  PASS  $1"; pass=$((pass+1));
       else echo "  FAIL  $1  期望[$2] 实际[$3]"; fail=$((fail+1)); fi; }
code(){ curl -s -o /dev/null -w '%{http_code}' -m 20 "$1"; }
ctype(){ curl -s -o /dev/null -w '%{content_type}' -m 20 "$1" | cut -d';' -f1; }

echo "== 自举与自描述 =="
chk "llms.txt 纯文本"          "text/plain"   "$(ctype "$B/llms.txt")"
chk "llms.txt 含可复制 URL"    "yes" "$(curl -s -m 20 "$B/llms.txt" | grep -q "$B" && echo yes || echo no)"
chk "openapi.json is JSON"     "application/json" "$(ctype "$B/openapi.json")"
chk "openapi.json 有 servers"  "yes" "$(curl -s -m 20 "$B/openapi.json" | grep -q '"servers"' && echo yes || echo no)"

echo "== 真 404（软 404 检测）=="
chk "随机路径 404"             "404" "$(code "$B/zzz-$RANDOM")"
chk "随机深路径 404"           "404" "$(code "$B/a/$RANDOM/b")"
chk "404 是 problem+json"      "application/problem+json" "$(ctype "$B/zzz-$RANDOM")"
_httpt=$(curl -s -m 20 -D - "$B/zzz" 2>/dev/null | tr -d '\r')
rid=$(printf '%s' "$_httpt" | awk 'tolower($1)=="x-request-id:"{print $2; exit}')
brid=$(printf '%s' "$_httpt" | tail -1 | "$PYBIN" -c "import json,sys,re;d=sys.stdin.read();m=re.search(r'\"request_id\"\s*:\s*\"([^\"]+)\"',d);print(m.group(1) if m else '')" 2>/dev/null)
if [ -n "$rid" ] && [ "$rid" = "$brid" ]; then
  chk "X-Request-Id 与 body 一致" yes yes
else chk "X-Request-Id 与 body 一致" yes no; fi

echo "== 人面同域（WebSocket 反代）=="
# 发真实 Streamlit 二进制 BackMsg 帧，看是否收到 ForwardMsg（此前 0 帧 = 死）
# 缺依赖计 SKIP，不能计 PASS；临时输出按运行独立创建。
WSOUT=$(mktemp) || exit 2
trap 'rm -f "$WSOUT"' EXIT
"$PYBIN" - <<PY > "$WSOUT" 2>&1
import asyncio, os
async def m():
    try:
        from streamlit.proto.BackMsg_pb2 import BackMsg
        import websockets
    except ImportError:
        print("FRAMES_SKIP_NO_PROTO"); return
    b=BackMsg(); b.rerun_script.query_string=""; b.rerun_script.page_script_hash=""
    try:
        async with websockets.connect("${B/http/ws}/_stcore/stream", subprotocols=["streamlit"], compression=None, max_size=None, open_timeout=15) as ws:
            await ws.send(b.SerializeToString())
            r=await asyncio.wait_for(ws.recv(), timeout=30); print("FRAMES_OK", len(r))
    except Exception as e: print("FRAMES_FAIL", type(e).__name__)
asyncio.run(m())
PY
_wsres=$(grep -o '^FRAMES_[A-Z_]*' "$WSOUT" | head -1)
if [ "$_wsres" = "FRAMES_SKIP_NO_PROTO" ]; then
  echo "  SKIP  人面 WS（缺少依赖，尚未验证）"; skip=$((skip+1))
else
  chk "人面 WS 收到帧（同域可用）" "FRAMES_OK" "$_wsres"
fi
rm -f "$WSOUT"

echo "== 检索与分页 =="
p1=$(curl -s -m 30 "$B/v1/search?q=learning&limit=3")
c=$(echo "$p1" | "$PYBIN" -c "import json,sys;print(json.load(sys.stdin).get('next_cursor') or '')")
if [ -n "$c" ]; then
  p2=$(curl -s -m 30 "$B/v1/search?q=learning&limit=3&cursor=$c")
  a=$(echo "$p1"|"$PYBIN" -c 'import json,sys;print([i["id"] for i in json.load(sys.stdin)["items"]])')
  b2=$(echo "$p2"|"$PYBIN" -c 'import json,sys;print([i["id"] for i in json.load(sys.stdin)["items"]])')
  [ "$a" != "$b2" ] && chk "游标真的翻页" different different || chk "游标真的翻页" different same
else
  chk "next_cursor 为 null（明说无下一页）" yes "$(echo "$p1" | "$PYBIN" -c 'import json,sys;d=json.load(sys.stdin);print("yes" if "items" in d and "total" in d and d.get("next_cursor") is None else "no")' 2>/dev/null)"
fi
chk "中文检索不静默为空"  "yes" "$(curl -s -m 30 "$B/v1/search?q=$("$PYBIN" -c 'import urllib.parse;print(urllib.parse.quote("研究"))')" | "$PYBIN" -c "
import json,sys;d=json.load(sys.stdin)
print('yes' if (d.get('items') or d.get('suggested_actions') or d.get('hint')) else 'no')")"

echo "== 机器面铁律（报告建议3）=="
# 内嵌 JSON 必须零解析失败（v7 问题4：截断成非法 JSON）
chk "chat 内嵌 JSON 可解析" "ok" "$(curl -s -m 60 -X POST "$B/v1/chat/completions" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"华东师范大学有哪些学者？"}]}' | "$PYBIN" -c "
import json,sys,re
try: c=json.load(sys.stdin)['choices'][0]['message']['content']
except Exception as e: print('bad:',e); raise SystemExit
bl=re.findall(r'\`\`\`json\n(.*?)\`\`\`',c,re.S)
if not bl: print('bad:no-json-block'); raise SystemExit
for j in bl:
    try: json.loads(j)
    except Exception as e: print('bad:',type(e).__name__); raise SystemExit
print('ok')")"
# SPARQL 写操作必须 4xx（v7 问题5：拒绝时返回 200）
chk "SPARQL 写操作 4xx" "403" "$(curl -s -o /dev/null -w '%{http_code}' -m 30 -X POST "$B/v1/tools/sparql" \
  -H 'Content-Type: application/json' -d '{"arguments":{"query":"DELETE WHERE {?s ?p ?o}"}}')"
chk "SPARQL SELECT 可用" "200" "$(curl -s -o /dev/null -w '%{http_code}' -m 30 -X POST "$B/v1/tools/sparql" \
  -H 'Content-Type: application/json' -d '{"arguments":{"query":"SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"}}')"
# 无效 key 必须 401（v7 问题6：静默降级为匿名）
chk "无效 key 401" "401" "$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$B/v1/models" -H 'Authorization: Bearer pk_anon_fake')"
# 自述文件必须是真跑的（非硬编码 15/15）：明细里要有真实 detail
chk "ai-readiness 真跑(有细节)" "yes" "$(curl -s -m 90 "$B/ai-readiness.json" | "$PYBIN" -c "
import json,sys
d=json.load(sys.stdin)
c=d.get('checks') or []
print('yes' if c and all('detail' in x for x in c) and '真跑' in d.get('note','') else 'no')")"
chk "ai-readiness 已纳入自举" "yes" "$(curl -s -m 20 "$B/llms.txt" | grep -q 'ai-readiness.json' && echo yes || echo no)"

echo "== 契约一致性 =="
chk "chat 接受 tools 参数(200)" "200" "$(curl -s -o /dev/null -w '%{http_code}' -m 30 -X POST "$B/v1/chat/completions" -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"hi"}],"tools":[]}')"
chk "stream=true 明确 400 或 SSE" "ok" "$(h=$(curl -s -o /dev/null -D - -m 30 -X POST "$B/v1/chat/completions" -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"hi"}],"stream":true}' 2>&1); echo "$h" | grep -qiE 'text/event-stream|unsupported_parameter|"400"|HTTP.*400' && echo ok || echo bad)"
chk "MCP 发现地址可连接" "yes" "$(u=$(curl -s -m 20 "$B/.well-known/mcp" | "$PYBIN" -c "import json,sys;print(json.load(sys.stdin).get('url',''))" 2>/dev/null); [ -n "$u" ] && curl -s -o /dev/null -w '%{http_code}' -m 20 -X POST "$u" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}' | grep -qE '^(200|202|307|308)$' && echo yes || echo no)"
# CORS 断言打在行为上（报告问题3①）：预检必须 204 且含 Allow-Origin，且无非法 Credentials
_cors=$(curl -s -m 20 -D - -o /dev/null -X OPTIONS -H 'Origin: https://x.com' \
        -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: content-type' \
        "$B/v1/chat/completions" | tr -d '\r')
chk "CORS 预检 204"            "204" "$(printf '%s' "$_cors" | head -1 | awk '{print $2}')"
chk "CORS 预检含 Allow-Origin" "yes" "$(printf '%s' "$_cors" | grep -qi 'access-control-allow-origin' && echo yes || echo no)"
chk "CORS 无非法凭证组合"      "no"  "$(printf '%s' "$_cors" | grep -qi 'access-control-allow-credentials: true' && echo yes || echo no)"

echo
echo "结果：PASS=$pass  FAIL=$fail  SKIP=$skip"
if [ "$fail" -ne 0 ]; then echo "仍有 $fail 项不达标"; exit 1; fi
if [ "$skip" -ne 0 ]; then echo "有 $skip 项未验证，门禁未完成"; exit 2; fi
echo "AI 友好度门禁通过"
exit 0

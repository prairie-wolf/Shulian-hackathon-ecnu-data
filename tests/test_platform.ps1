# ============================================================
# AI 大数据平台 v9.5 —— 功能测试脚本 v2（修正 v1 的参数形状错误）
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File test-platform-v2.ps1
# ============================================================
#Requires -Version 5.1
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$B = if ($args.Count -ge 1) { $args[0].TrimEnd('/') } else { 'http://127.0.0.1:8610' }
$script:pass = 0; $script:fail = 0; $script:rows = @()

function Chk([string]$name, [string]$expect, [string]$actual) {
  $ok = ($expect -ceq $actual)
  if ($ok) { $script:pass++ } else { $script:fail++ }
  $script:rows += [pscustomobject]@{ 结果 = $(if ($ok) { 'PASS' } else { 'FAIL' }); 检查 = $name; 期望 = $expect; 实际 = $actual }
}
function Status([string]$u) { try { return [string](Invoke-WebRequest -Uri $u -TimeoutSec 30 -UseBasicParsing).StatusCode } catch { if ($_.Exception.Response) { return [string][int]$_.Exception.Response.StatusCode } else { return 'ERR' } } }
function CType([string]$u) { return ((curl.exe -s -o NUL -w '%{content_type}' -m 20 $u 2>$null) -split ';')[0] }
function Body([string]$u) { try { return (Invoke-WebRequest -Uri $u -TimeoutSec 30 -UseBasicParsing).Content } catch { return '' } }
function PostJson([string]$u, $obj, [hashtable]$extra = @{}) {
  $h = @{}; foreach ($k in $extra.Keys) { $h[$k] = $extra[$k] }
  $bytes = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress -Depth 8))
  try { return Invoke-WebRequest -Uri $u -Method Post -ContentType 'application/json' -Headers $h -Body $bytes -TimeoutSec 60 -UseBasicParsing }
  catch { if ($_.Exception.Response) { return $_.Exception.Response } else { throw } }
}
function CallTool([string]$name, $argsObj) { return PostJson "$B/v1/tools/$name" @{ arguments = $argsObj } }
function RespBody($r) { try { $sr = New-Object IO.StreamReader($r.GetResponseStream()); $t = $sr.ReadToEnd(); $sr.Dispose(); return $t } catch { return '' } }

Write-Host "测试目标: $B`n"

Write-Host '== A. 服务与自举 =='
$h = Body "$B/health"
Chk 'health status=ok' 'True' ([string]($h -match '"status":"ok"'))
Chk 'health 含三元组统计' 'True' ([string]($h -match '"triples":\d+'))
Chk 'llms.txt 是纯文本' 'text/plain' (CType "$B/llms.txt")
Chk 'llms.txt 含可复制 URL' 'True' ([string]((Body "$B/llms.txt") -match [regex]::Escape($B)))
Chk 'openapi.json 是 JSON' 'application/json' (CType "$B/openapi.json")
Chk 'openapi.json 含 servers' 'True' ([string]((Body "$B/openapi.json") -match '"servers"'))

Write-Host '== B. 真 404 与错误语义 =='
$rand = [guid]::NewGuid().ToString('N').Substring(0, 8)
Chk '随机路径 404' '404' (Status "$B/zzz-$rand")
Chk '404 是 problem+json' 'application/problem+json' (CType "$B/zzz-$rand")
$raw404 = (curl.exe -s -i -m 20 "$B/zzz-$rand" 2>$null | Out-String)
$ridH = [regex]::Match($raw404, '(?im)^x-request-id:\s*(\S+)').Groups[1].Value
$ridB = [regex]::Match($raw404, '"request_id"\s*:\s*"([^"]+)"').Groups[1].Value
Chk 'X-Request-Id 与 body 一致' $ridH $ridB

Write-Host '== C. 机器面 =='
Chk '/v1/models 200' '200' (Status "$B/v1/models")
$toolNames = [regex]::Matches((Body "$B/v1/tools"), '"name"\s*:\s*"([a-z_]+)"') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
Chk '/v1/tools 列出 8 个工具' '8' ([string]$toolNames.Count)
Chk '/v1/ontology 200' '200' (Status "$B/v1/ontology")

Write-Host '== D. 检索与分页（分页参数是 cursor）=='
$s1 = Body "$B/v1/search?q=$([uri]::EscapeDataString('研究'))"
Chk '中文检索有命中' 'True' ([string]([int][regex]::Match($s1, '"total"\s*:\s*(\d+)').Groups[1].Value -gt 0))
$p1 = Body "$B/v1/search?q=learning&limit=3"
$cur = [regex]::Match($p1, '"next_cursor"\s*:\s*"?(\d+)"?').Groups[1].Value
Chk '第一页返回 next_cursor' 'True' ([string]($cur -ne ''))
if ($cur) {
  $p2 = Body "$B/v1/search?q=learning&limit=3&cursor=$cur"
  $id1 = [regex]::Matches($p1, '"id"\s*:\s*"([^"]+)"') | ForEach-Object { $_.Groups[1].Value }
  $id2 = [regex]::Matches($p2, '"id"\s*:\s*"([^"]+)"') | ForEach-Object { $_.Groups[1].Value }
  Chk '分页两页不重复' '0' ([string]@($id1 | Where-Object { $id2 -contains $_ }).Count)
}

Write-Host '== E. 契约与安全 =='
Chk 'CORS 预检 204' '204' ([string](curl.exe -s -o NUL -w '%{http_code}' -m 20 -X OPTIONS -H 'Origin: https://x.com' -H 'Access-Control-Request-Method: POST' "$B/v1/chat/completions" 2>$null))
$corsHdr = (curl.exe -s -i -m 20 -X OPTIONS -H 'Origin: https://x.com' -H 'Access-Control-Request-Method: POST' "$B/v1/chat/completions" 2>$null | Out-String)
Chk 'CORS 含 Allow-Origin' 'True' ([string]($corsHdr -match '(?i)access-control-allow-origin'))
Chk '无 key 可用（匿名额度）' '200' (Status "$B/v1/models")
Chk '伪造 key → 401' '401' ([string](curl.exe -s -o NUL -w '%{http_code}' -m 20 "$B/v1/models" -H 'Authorization: Bearer pk_anon_fake' 2>$null))
Chk 'SPARQL SELECT 200' '200' ([string](CallTool 'sparql' @{ query = 'SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }' }).StatusCode)
Chk 'SPARQL 写操作被拒 403' '403' ([string][int](CallTool 'sparql' @{ query = 'DROP ALL' }).StatusCode)
Chk 'chat/completions 200' '200' ([string](PostJson "$B/v1/chat/completions" @{ messages = @(@{ role = 'user'; content = 'hi' }) }).StatusCode)

Write-Host '== F. REST 工具面（8 个逐个真调）=='
$tools = @(
  @{ n = 'list_ontology'; a = @{} },
  @{ n = 'list_sources'; a = @{} },
  @{ n = 'explore_class'; a = @{ class_name = 'Company' } },
  @{ n = 'find_entity'; a = @{ class_name = 'Institution'; keyword = '华东师范' } },
  @{ n = 'entity_detail'; a = @{ entity_id = 'i_I66867065' } },
  @{ n = 'query_relation'; a = @{ subject_class = 'Scholar'; relation = 'affiliatedWith'; object_class = 'Institution' } },
  @{ n = 'sparql'; a = @{ query = 'SELECT ?s WHERE { ?s ?p ?o } LIMIT 1' } },
  @{ n = 'semantic_ask'; a = @{ question = '人工智能行业有哪些公司？' } }
)
foreach ($t in $tools) { Chk "REST $($t.n) 200" '200' ([string](CallTool $t.n $t.a).StatusCode) }

Write-Host '== G. MCP 工具逐个真调 =='
$H = @{ 'Content-Type' = 'application/json'; 'Accept' = 'application/json, text/event-stream' }
$initBody = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"tester","version":"1"}}}'
$sess = ''
try {
  $ir = Invoke-WebRequest -Uri "$B/mcp" -Method Post -Headers $H -Body $initBody -TimeoutSec 40 -UseBasicParsing
  $sess = [string]$ir.Headers['mcp-session-id']
  Chk 'MCP initialize 200' '200' ([string]$ir.StatusCode)
  Chk 'MCP 返回 session-id' 'True' ([string]($sess -ne ''))
} catch { Chk 'MCP initialize 200' '200' 'ERR'; Chk 'MCP 返回 session-id' 'True' 'ERR' }
$H2 = $H.Clone(); if ($sess) { $H2['mcp-session-id'] = $sess }
$id = 100
foreach ($c in $tools) {
  $id++
  $body = @{ jsonrpc = '2.0'; id = $id; method = 'tools/call'; params = @{ name = $c.n; arguments = $c.a } } | ConvertTo-Json -Compress -Depth 8
  try {
    $rc = Invoke-WebRequest -Uri "$B/mcp" -Method Post -Headers $H2 -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 60 -UseBasicParsing
    Chk "MCP tools/call $($c.n)" 'false' ([regex]::Match($rc.Content, '"isError":(true|false)').Groups[1].Value)
  } catch { Chk "MCP tools/call $($c.n)" 'false' 'ERR' }
}

Write-Host '== H. 人面入口 =='
Chk '网关根路径返回 HTML' 'text/html' (CType "$B/")
Chk 'Streamlit 健康' '200' (Status 'http://127.0.0.1:8603/_stcore/health')

Write-Host ''
$script:rows | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
Write-Host ("结果：PASS={0}  FAIL={1}" -f $script:pass, $script:fail)
if ($script:fail -gt 0) { Write-Host '存在失败项，见上表 FAIL 行。' -ForegroundColor Yellow } else { Write-Host '全部通过。' -ForegroundColor Green }

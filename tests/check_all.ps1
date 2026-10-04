<#
  平台一键自检
  ------------------------------------------------------------
  1) 接口验收   42 项：REST 8 接口 + MCP 握手/8 工具 + 鉴权 + SPARQL 防注入 + 网关 + Streamlit 健康
  2) UI 巡检    12 项：首屏 / 登录 / 六个页面遍历 / 连问历史累积 / 输入框对比度 CSS
  3) 语义回测   36 题：意图识别与「不许硬答」行为回归

  前置：三个服务已启动
        网关      python -m aiplatform.gateway
        MCP HTTP  python aiplatform/mcp_server.py --http --port 8602
        控制台    python -m streamlit run app/console.py --server.port 8603

  用法：powershell -NoProfile -ExecutionPolicy Bypass -File tests\check_all.ps1
        可选参数：-Gateway http://127.0.0.1:8610
#>
param([string]$Gateway = 'http://127.0.0.1:8610')
$ErrorActionPreference = 'Continue'
$TESTS = $PSScriptRoot
$REPO  = Split-Path -Parent $TESTS
$PY    = Join-Path $REPO '.venv\Scripts\python.exe'
$PS5   = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$env:PYTHONIOENCODING = 'utf-8'

if (-not (Test-Path $PY)) { $PY = 'python' }
$summary = @()

Write-Host "`n===== 1/4  接口验收（42 项）=====" -ForegroundColor Cyan
$r1 = & $PS5 -NoProfile -ExecutionPolicy Bypass -File (Join-Path $TESTS 'test_platform.ps1') $Gateway *>&1
$r1 | Select-Object -Last 3 | ForEach-Object { Write-Host $_ }
$summary += ($r1 | Select-String '结果：PASS' | Select-Object -Last 1).Line

Write-Host "`n===== 2/4  UI 巡检（Streamlit AppTest，12 项）=====" -ForegroundColor Cyan
$r2 = & $PY (Join-Path $TESTS 'ui_selftest.py') 2>$null
$r2 | ForEach-Object { Write-Host $_ }
$summary += ($r2 | Select-String '结果：PASS' | Select-Object -Last 1).Line

Write-Host "`n===== 3/4  语义问答回测（36 题）=====" -ForegroundColor Cyan
$r3 = & $PY (Join-Path $TESTS 'semantic_battery.py') 2>$null
$r3 | ForEach-Object { Write-Host $_ }

Write-Host "`n===== 4/4  数据质量巡检 =====" -ForegroundColor Cyan
$r4 = & $PY (Join-Path $TESTS 'data_quality.py') 2>$null
$r4 | ForEach-Object { Write-Host $_ }
$summary += ($r4 | Select-String '结果：PASS' | Select-Object -Last 1).Line

Write-Host "`n===== 汇总 =====" -ForegroundColor Yellow
$summary | Where-Object { $_ } | ForEach-Object { Write-Host "  $_" }
$bad = ($r1 + $r2 + $r4) | Select-String 'FAIL=[1-9]'
if ($bad) { Write-Host '  有失败项，请查看上方输出' -ForegroundColor Red; exit 1 }
Write-Host '  全部通过 ✔' -ForegroundColor Green

# 平台自测套件

三层自测，全部只读（不注册账号、不改 `users.json`、不写业务数据）。

## 一键跑全部

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\check_all.ps1
```

先确保三个服务已启动：

```powershell
python -m aiplatform.gateway                                    # 网关   :8610
python aiplatform/mcp_server.py --http --port 8602              # MCP    :8602
python -m streamlit run app/console.py --server.port 8603       # 控制台 :8603
```

## 单个跑

| 脚本 | 覆盖 | 用法 |
|---|---|---|
| `test_platform.ps1` | **42 项**接口/协议验收 | `-File tests\test_platform.ps1 [网关地址]` |
| `ui_selftest.py` | **12 项**控制台 UI 巡检（Streamlit `AppTest`） | 直接 python 运行 |
| `semantic_battery.py` | **36 题**语义问答回测 | 直接 python 运行 |

三个脚本的参数默认值都以本仓库为基准，可在任意工作目录下运行。

## 各层查什么

**`test_platform.ps1`（42 项）**
- `/health`、`/llms.txt`、`/openapi.json`、`/.well-known/mcp` 等自举端点
- REST `/v1/tools/*` 8 个工具（`{"arguments": {...}}` 参数形状）
- MCP JSON-RPC：`initialize` + `tools/call` × 8
- 鉴权与错误语义：无 key / 无效 key / 404
- SPARQL 写操作防护（DROP / INSERT / DELETE / LOAD 应为 403，SELECT 应为 200）
- 网关根路径反代 Streamlit、Streamlit 健康检查

**`ui_selftest.py`（12 项）**
- 首屏渲染、登录、退出
- 六个导航页逐个渲染（`运行总览` / `数据资产` / `语义问答` / `本体与工具` / `AI 接入` / `系统设置`）
- 语义问答连问 3 轮、历史累积
- 注入 CSS 中输入框的 `color` / `background` 与对比度

**`semantic_battery.py`（36 题）**
- 意图识别：行业公司、学者筛选、趋势、排名、实体详情、列举
- 中文别名（清华 / 北大 / 复旦 / 麻省理工 …）
- 「不许硬答」行为：未收录实体 → `not_found`；未收录字段（校长/电话）→ `unsupported_attribute`
- 寒暄 → `smalltalk`；闲聊 → 明确说明不在数据范围内
- 回归基线：正常列举（数据源 19 / 数据集 5）不得被上面的保护误伤

## 已知坑（改脚本时注意）

1. **PS 5.1 按 GBK 读无 BOM 的 `.ps1`** —— 脚本必须存成 **UTF-8 带 BOM**，否则中文乱码报语法错。
2. **Streamlit `AppTest` 的 radio 要用未格式化的短名**（`app.ui_kit.NAV_ITEMS`）。`options` 里是 `format_func` 处理过的 `"名称 · 提示"`，回填会被二次格式化，抛 `ValueError: ... is not in list`。
3. **`AppTest` 页面切换有 1 拍延迟** —— `set_value` 后要连跑两次 `at.run()` 才落到目标页。
4. **REST 请求体必须是 UTF-8 字节** —— PowerShell 5.1 直接把中文 JSON 字符串当 body 会编码错，语义层会退化成 `overview`。

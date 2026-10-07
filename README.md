# 面向 AI 的大数据平台（赛题二 · 方向一）

> 本体作语义契约 · 异构数据本体性转化 · 任意 AI 可接入

传统大数据平台服务于**人、报表、BI**；本平台服务于 **AI 智能体**：把异构数据统一
本体性转化成语义图，通过 **MCP** 暴露给任意 AI，让 AI 真正「调用、理解、使用」数据。

---

## 一、核心能力

| 能力 | 实现 | 证据 |
|---|---|---|
| **本体性转化** | 声明式语义映射（`mappings.json`）把原始行转成 RDF 三元组 | 当前构建登记 19 个公开数据源 |
| **任意数据接入** | 上传 Excel/CSV/Word/PDF/图片 → 自动推断语义映射 → 入图可查 | 回归覆盖 CSV、Excel、文本及上传边界；其他格式需另测 |
| **多 AI 交互** | 多种 AI 客户端驱动同一平台；**开放登记** OpenAI 兼容服务 | 本轮验证本地问答和外部 AI 失败回退，未调用外部服务 |
| **对外端点** | 平台自身是 OpenAI 兼容服务，AI/Agent 改 base_url 即可接入 | 两个 REST 入口及 MCP stdio/HTTP 的八个工具均有实际调用测试 |
| **跨域语义查询** | 通用语义查询引擎（本体驱动，非写死关键词） | 学术 + 企业 + 数据集全覆盖 |

### 数据接入与筛选

- 上传前会显示表格前 5 行预览，并提供“允许写入 GenericRecord”“保留未识别字段”“跳过空行”“最多导入行数”等筛选开关。
- 默认单文件上限 25 MB，整个文件默认最多导入 5000 行（所有工作表共用配额）；空主键及被过滤的类别不占用配额。大文件建议先在本地清洗后再上传。
- **GenericRecord（通用记录）** 是无法自动判断为 Scholar、Publication、Institution、Company 等本体类时的兜底类别，主要保留名称、描述和来源。它能参与基础检索，但不保证业务字段完整可问答。
- 控制台支持删除本次上传的公共数据和私人分区；公共删除只撤回该次上传的三元组贡献，保留内置数据及其他上传共享的属性、实体和关系。同名文件分别登记来源。
- 公共上传的三元组贡献、目录及删除溯源记录原子保存到 `data/processed/upload_state.json`；重启恢复快照，不重复解析原件。控制台、REST 和 MCP 在查询前检查更新，共享同一项目目录时可看到其他进程的上传和撤回。原件位置相对上传目录保存，便于整体移动项目。
- 快照损坏时明确报错并保留原文件。`platform_graph.ttl` 是构建时生成的派生导出，恢复上传后再导出；实时查询以共享快照为准。备份需同时保留快照及 `data/raw/uploads/` 原件。
- 私人分区使用进程锁、原子保存和旧版本检查；同名分区拒绝覆盖，损坏文件拒绝当作空图保存。删除中断会在下次加载恢复或完成清理；已完成的删除仍需备份或重新上传才能恢复。
- 新私人目录为中文、特殊字符和大写用户名使用独立散列标识。没有归属记录的旧目录若存在净化或大小写歧义，会保留原文件并要求确认账号归属后人工迁移，程序不会自动分配这些数据。

---

## 二、规模指标

- **19 个数据源**（960 个机构节点 / 15 个领域节点 / 2,611 篇论文 / 9,941 位学者 / 74 个企业节点 / 5 个数据集）
- **78,998 个 RDF 三元组**（含有证据的企业等价链接）
- **13,934 个原始实体节点**；74 个企业节点经保守消歧后为 47 家企业。原始图保留来源，工具同时提供原始数量与消歧数量。
- 本体：**12 个类 / 13 个关系 / 19 个属性**

以上为本轮在临时工作区重建公开快照的结果，不含真实私人数据或用户上传。

---

## 三、接入的 AI（7 个 + 可任意扩展）

| AI | 类型 | 后端 | 状态 |
|---|---|---|---|
| 华东师大开放平台 `ecnu-max` | 云端 API | DeepSeek | 支持配置，本轮未测 |
| 华东师大开放平台 `ecnu-plus` | 云端 API | Qwen | 支持配置，本轮未测 |
| Codex CLI | 本地 Agent | OpenAI Codex | 本轮未测 |
| Claude Code CLI | 本地 Agent | Anthropic | 本轮未测 |
| Hermes Agent | 本机 Agent | Hermes | 本轮未测 |
| 本平台端点 | 平台自身 | OpenAI 兼容 | 本轮已测 |
| 本地规则引擎 | 无 AI 兜底 | 确定性规则 | 本轮已测，默认可问答 |

**换 AI 只换一个下拉框**，本体、数据、工具、调用链完全不变。

### 接入任意 AI（不写死）

平台是**对外产品**，不绑定任何厂商。两种方式接新 AI：

1. 控制台「③ AI 客户端 → ➕ 接入新的 AI」：填 名称 + Base URL + 模型名 + Key
2. 写入 `ai_clients.json`（key 建议存环境变量）

### Git 提交与个人配置

阶段性更新使用普通本地 commit，正文附简短 `Notes:`。
Git hook 和 Codex hook 属于个人本地配置，其配置、辅助脚本、专用测试及说明均不纳入版本控制；项目成员可自行配置。

`.gitignore` 排除本机 `ai_clients.json`、环境秘密、账号库、私人图、上传原件、
虚拟环境、可重新生成的数据库/RDF/展示页，以及本地方案文档。
运行所需的公开 CSV/JSON、语义映射和 OWL 本体仍纳入版本控制。

新检出项目时，可以将 `ai_clients.example.json` 复制为 `ai_clients.json`，
并在启动服务的终端中设置 `DEEPSEEK_API_KEY`。示例不包含真实凭据；程序不会自动读取 `.env`。
不需要外部 AI 时，可以使用平台自身和本地规则引擎。

`.gitignore` 仅防止未跟踪文件被普通 `git add` 加入；它不会清除已有历史，也不能阻止
`git add -f`。如果真实凭据曾提交或分享，应撤销或轮换凭据，不能只依靠忽略规则。

内置预设覆盖 11 家：华东师大开放平台、DeepSeek、通义千问、豆包、Kimi、智谱 GLM、
硅基流动、OpenAI、本地 Ollama、自建 vLLM、任意兼容服务。

## 四、对外端点（任何 AI 可连本平台 · 统一网关）

平台自身是一个 **OpenAI 兼容服务**，且面向 AI 优先（人面 + 机面同域）：

```bash
python -m aiplatform.gateway           # → 统一网关 8610（人面 + 机面）
# 另需 Streamlit 人面在 8603：
# python -m streamlit run app/console.py --server.port 8603 --server.headless true
```

| 端点 | 方法 | 说明 |
|---|---|---|
| `/llms.txt` | GET | ⭐ AI 自举入口（纯文本，一次 GET 上手） |
| `/AGENTS.md` | GET | 长版自举手册 |
| `/openapi.json` | GET | OpenAPI 3.1 机器契约 |
| `/.well-known/mcp` | GET | MCP 发现入口 |
| `/health` | GET | 健康检查 |
| `/v1/models` | GET | 列出平台「模型」（= 数据能力） |
| `/v1/tools` | GET | 列出 8 个本体驱动工具 |
| `/v1/tools/{tool}` | POST | 直接调用工具（供 Agent） |
| `/v1/chat/completions` | POST | OpenAI 兼容对话接口 |
| `/v1/ontology` | GET | 机器可读本体 |
| `/v1/search?q=` | GET | 语义检索（`{items,next_cursor,total}`） |
| `/v1/keys` | POST | 自助签发匿名只读 key |

> 错误语义：未匹配路径返回真 404 + `application/problem+json`（含 `error.code` / `error.next_action`）。
> 详见 `API接入说明.md`。

外部 AI 接入示例：

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8610/v1", api_key="any")
resp = client.chat.completions.create(model="platform-semantic",
    messages=[{"role": "user", "content": "人工智能行业有哪些公司？"}])
```

详见 [`API接入说明.md`](API接入说明.md)。

---

## 五、快速开始

```bash
# 1) 激活环境
.venv/Scripts/activate

# 2) 重建本体与平台（可选）
python ontology/build_ontology.py
python aiplatform/build_platform.py

# 3) 启动 Web 控制台
python -m streamlit run app/console.py --server.port 8603
# 打开 http://localhost:8603

# 3b) 启动统一网关（人面 + 机面，任何 AI 可连）
python -m aiplatform.gateway           # → http://localhost:8610（llms.txt / v1/* 等）

# 4) 静态控制台（路演 / 内嵌预览）
python app/generate_console.py     # 跑真实 AI 调用，产出 console_data.json
python app/build_static.py         # 渲染成 app/static_console.html

# 5) 全量回归测试
python test_all.py

# 6) 生成方案文档
python 方案文档/generate_doc.py     # → 方案文档/方案文档_初稿.docx
```

---

## 六、目录结构

```
├── ontology/            本体（12 类 / 13 关系 / 19 属性）+ RDF 构建 + SHACL 校验
├── ingest/              数据抓取（OpenAlex）+ 清洗准备
├── aiplatform/          平台核心
│   ├── core.py          本体加载 · 数据源目录 · 语义映射引擎 · 统一图
│   ├── tools.py         本体驱动工具生成 + 跨语言消歧
│   ├── semantic.py      通用语义查询引擎（本体驱动，非写死）
│   ├── documents.py     docx/pdf/xlsx/图片 摄取
│   ├── auto_map.py      自动语义映射推断（上传即用的关键）
│   ├── upload.py        上传编排：摄取 → 映射 → 转化 → 可查
│   ├── ai_clients.py    多 AI 接入层（API / CLI / 本地兜底）
│   ├── mcp_server.py    MCP 服务（8 个工具，任意 MCP 客户端可接）
│   └── build_platform.py 平台构建入口
│   ├── gateway.py      统一网关（人面 /llms.txt / v1/* 同域，AI 优先入口）
├── app/                 console.py（Streamlit 控制台）· static_console.html · showcase.html
├── ai_clients.json        AI 客户端登记表（可任意扩展）
├── mappings.json        语义映射的声明式配置（本体性转化的核心证据）
├── data/raw/            原始数据 + 上传目录
└── 方案文档/             方案文档生成器 + 成果 docx
```

---

## 七、验证方式

```bash
python test_all.py        # 项目回归：临时数据、AppTest、REST、真实 MCP 与临时服务门禁
python -m unittest tests.test_console_ui -v
python -m unittest tests.test_machine_interfaces -v
# 服务已启动时也可单独执行：
bash check.sh http://127.0.0.1:8610
```

使用依赖完整且可启动的 Python 环境。搬移项目后若 `.venv` 指向旧解释器路径，
可重建虚拟环境并重新安装 `requirements.txt`，不要仅以启动脚本存在判断可用。
测试入口返回 0 表示全部通过，1 表示失败，2 表示存在跳过或环境阻塞。
私人图、账号库、外部 AI 配置及上传原件不会被复制到测试工作区；个人 hook 测试不纳入项目回归。
本轮界面验证为 Streamlit AppTest 和网关 WebSocket 实测，没有重新验证历史截图的像素布局。
详细范围、时间及限制见 `项目更新日志.md`。

---

## 八、已知限制

- 公开数据有快照滞后（OpenAlex），不反映最新论文。
- 本体规范领域为 12 个抓取主题，边缘术语消歧可能失败。
- 机构跨语言消歧依赖别名表，未做全自动实体对齐（sameAs）。
- LLM 存在幻觉风险，语义查询结果需工具侧二次校验（本地兜底可保证确定性）。
- 图片仅提取元数据/OCR，未做视觉理解。
- 未实现数据脱敏 / Agent 权限鉴权（演示为公开数据）。

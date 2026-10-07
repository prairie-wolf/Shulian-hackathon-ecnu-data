# 面向 AI 的大数据平台（赛题二 · 方向一）

> 本体作语义契约 · 异构数据本体性转化 · 任意 AI 可接入

传统大数据平台服务于**人、报表、BI**；本平台服务于 **AI 智能体**：把异构数据统一
本体性转化成语义图，通过 **MCP** 暴露给任意 AI，让 AI 真正「调用、理解、使用」数据。

---

## 一、核心能力

| 能力 | 实现 | 证据 |
|---|---|---|
| **本体性转化** | 声明式语义映射（`mappings.json`）把任意原始行转成 RDF 三元组 | 18 个数据源全部经映射入图 |
| **任意数据接入** | 上传 Excel/CSV/Word/PDF/图片 → 自动推断语义映射 → 立即入图可查 | 5 种格式实测通过 |
| **多 AI 交互** | 7 个 AI 客户端驱动同一平台；**开放登记**任意 OpenAI 兼容服务 | 5 个真实调用验证 |
| **对外端点** | 平台自身是 OpenAI 兼容服务，任何 AI/Agent 改 base_url 即可接入 | 端点 5 项测试通过 |
| **跨域语义查询** | 通用语义查询引擎（本体驱动，非写死关键词） | 学术 + 企业 + 数据集全覆盖 |

### 数据接入与筛选

- 上传前会显示表格前 5 行预览，并提供“允许写入 GenericRecord”“保留未识别字段”“跳过空行”“最多导入行数”等筛选开关。
- 默认单文件上限 25 MB，整个文件默认最多导入 5000 行（所有工作表共用配额）；空主键及被过滤的类别不占用配额。大文件建议先在本地清洗后再上传。
- **GenericRecord（通用记录）** 是无法自动判断为 Scholar、Publication、Institution、Company 等本体类时的兜底类别，主要保留名称、描述和来源。它能参与基础检索，但不保证业务字段完整可问答。
- 控制台支持删除本次上传的公共数据和私人分区；公共删除只撤回该次上传的三元组贡献，保留内置数据及其他上传共享的属性、实体和关系。同名文件分别登记来源。
- 公共上传及其删除溯源记录仅保存在当前进程内；重启后重新构建内置图，不自动重载上传原件。缺少删除溯源记录时拒绝删除；私人分区文件删除后需要备份或重新上传才能恢复。

---

## 二、规模指标

- **18 个数据源**（12 所高校 / 12 个领域 / 2,611 篇论文 / 9,941 位学者 / 74 家企业 / 5 个数据集）
- **78,958 个 RDF 三元组**
- **13,933 个实体**
- 本体：**12 个类 / 13 个关系 / 19 个属性**

---

## 三、接入的 AI（7 个 + 可任意扩展）

| AI | 类型 | 后端 | 状态 |
|---|---|---|---|
| 华东师大开放平台 `ecnu-max` | 云端 API | DeepSeek | ✅ |
| 华东师大开放平台 `ecnu-plus` | 云端 API | Qwen | ✅ |
| Codex CLI | 本地 Agent | OpenAI Codex | ✅ |
| Claude Code CLI | 本地 Agent | Anthropic | ⚠️ 未登录（接口通） |
| Hermes Agent | 本机 Agent | Hermes | ✅ |
| 本平台端点 | 平台自身 | OpenAI 兼容 | ✅ |
| 本地规则引擎 | 无 AI 兜底 | 确定性规则 | ✅ 断网可用 |

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
python test_all.py        # 37 项检查：平台 / 多 AI / 上传闭环 / 语义查询 / MCP / 对外端点 / 开放登记
python test_ui.py         # Streamlit 官方 AppTest 真实界面断言
python test_api_endpoint.py  # 以外部 AI 客户端身份测平台端点
```

截图见 `screenshots/`（Edge 无头模式 + CDP 实拍，非手绘）。

---

## 八、已知限制

- 公开数据有快照滞后（OpenAlex），不反映最新论文。
- 本体规范领域为 12 个抓取主题，边缘术语消歧可能失败。
- 机构跨语言消歧依赖别名表，未做全自动实体对齐（sameAs）。
- LLM 存在幻觉风险，语义查询结果需工具侧二次校验（本地兜底可保证确定性）。
- 图片仅提取元数据/OCR，未做视觉理解。
- 未实现数据脱敏 / Agent 权限鉴权（演示为公开数据）。

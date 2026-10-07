# 平台对外接入说明（AI 优先 · 统一网关）

> 把本平台当作一个 **OpenAI 兼容服务** + **AI 元能力中心** 接入你的 AI 应用。
> 任何 AI 客户端（ChatGPT / DeepSeek / Cursor / Dify / LangChain / Hermes / Codex…）都能直接调用，无需绑定单一厂商。

---

## 零、三个入口一句话

| 入口 | 是什么 | 谁用 |
|---|---|---|
| `/` | 人类控制台（Streamlit） | 人用浏览器 |
| `/llms.txt` | AI 自举入口（纯文本，一次 GET 上手） | AI / Agent |
| `/v1/*` | OpenAI 兼容机器面 | AI / Agent |

**机器面与人类面同域同端口（默认 8610）**，未匹配路径返回真 404（problem+json）。

---

## 一、启动统一网关

```bash
python -m aiplatform.gateway          # 默认 0.0.0.0:8610（同端口含人面 + 机面）
# 可选环境变量
#   CONSOLE_URL=http://127.0.0.1:8603  人面反代目标（默认）
#   PUBLIC_URL=https://你的域名         llms.txt / MCP 里暴露的公网根地址
#   PLATFORM_API_PORT=8610             端口
```

也要保证 Streamlit 人面在 8603 跑着（`python -m streamlit run app/console.py --server.port 8603 --server.headless true`）。

---

## 二、机器面端点清单（全部真实可用）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/llms.txt` | ⭐ AI 自举入口：一句话 + 端点表 + 复制即跑示例 |
| GET | `/AGENTS.md` | 长版自举手册（认证、错误码、配额） |
| GET | `/openapi.json` | OpenAPI 3.1 机器契约（含工具 schema） |
| GET | `/robots.txt` | 允许 AI 抓取，声明机面入口 |
| GET | `/.well-known/mcp` | MCP 发现入口 |
| GET / POST / DELETE | `/v1/mcp` | MCP Streamable HTTP 端点（初始化及工具调用） |
| GET | `/v1/models` | 列出平台「模型」（= 数据能力） |
| GET | `/v1/tools` | 列出 8 个本体驱动工具（含 JSON Schema） |
| POST | `/v1/tools/{tool}` | 直接调用工具 |
| POST | `/v1/chat/completions` | OpenAI 兼容对话（核心） |
| GET | `/v1/ontology` | 机器可读本体（JSON-LD + 统计） |
| GET | `/v1/search?q=` | 语义检索，返回 `{items, next_cursor, total, suggested_actions}` |
| POST | `/v1/keys` | 自助签发匿名只读 key（带 key 可提到 1000 次/天） |
| GET | `/health` | 健康检查 |

---

## 三、接入方式（四种，任选）

### 1. 一次 GET 自举（推荐先看这段）

```bash
curl -s https://你的域名/llms.txt
```

返回纯文本：平台是什么、有哪些端点、复制即跑的 curl 示例。**AI 拿到 URL 就能自举，不用猜。**

### 2. Python（OpenAI SDK 直接指过来）

```python
from openai import OpenAI

client = OpenAI(base_url="https://你的域名/v1", api_key="any")

resp = client.chat.completions.create(
    model="platform-semantic",
    messages=[{"role": "user", "content": "人工智能行业有哪些公司？"}],
)
print(resp.choices[0].message.content)
```

### 3. 任意支持 OpenAI 兼容的平台（Dify / FastGPT / LangChain / Flowise / n8n）

| 字段 | 值 |
|---|---|
| Base URL | `https://你的域名/v1`（或 `http://平台IP:8610/v1` 局域网） |
| API Key | 任意非空字符串（或 `POST /v1/keys` 自助签发） |
| 模型名 | `platform-semantic` |

### 4. MCP 客户端（Claude Desktop / Cursor / Cherry Studio）

**MCP 现在有公网 HTTP 入口**（`.well-known/mcp` 发现 + `/v1/mcp` 协议），本地 stdio 版仍可用：

```bash
python aiplatform/mcp_server.py     # 本地 stdio（原有）
```

配置（`mcp_config.json`，本地 stdio 版）：

```json
{ "mcpServers": { "ai-data-platform": { "command": "python", "args": ["aiplatform/mcp_server.py"] } } }
```

---

## 四、工具清单（Agent 可直接调用）

| 工具 | 参数 | 说明 |
|---|---|---|
| `list_ontology` | — | 已入图的类及原始/消歧数量；完整本体见 `/v1/ontology` |
| `list_sources` | — | 列出已接入的数据源 |
| `explore_class` | `class_name`, `limit` | 浏览某个本体类的实例 |
| `find_entity` | `class_name`, `keyword`, `limit`, `offset` | 按关键词在类里查实体，稳定排序分页 |
| `entity_detail` | `entity_id` | 查看实体完整信息（属性+关系） |
| `query_relation` | `subject_class`, `relation`, `object_class`, `limit` | 按关系跨类查询 |
| `sparql` | `query` | 对统一知识图谱执行 SPARQL |
| `semantic_ask` | `question` | 自然语言问数 |

调用示例（curl 直接调工具）：

```bash
curl -X POST https://你的域名/v1/tools/semantic_ask \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"question":"华东师范大学有哪些学者？"}}'
```

`find_entity` 的 `limit` 默认为 10、须大于 0；`offset` 默认为 0、不能为负数，
返回 `total`、`matches`、`has_more`。MCP 也支持这两个分页参数；
MCP 的 `explore_class`、`query_relation` 目前使用默认展示上限，表中的自定义 `limit` 用于 REST。
企业搜索、详情和关系查询复用有证据的等价视图，不按名称直接合并其他实体。
`explore_class` 返回消歧后的 `instances` 与原始 `raw_instances`；原始 SPARQL 查询保持原图语义。
SPARQL 更新被拒绝（REST 403 / MCP 工具错误）；ASK 返回 `boolean`，SELECT 返回表格结果。

公共上传的目录与逐来源贡献在共享项目目录原子持久化；控制台、REST 和 MCP 在查询前刷新。
私人上传不登记到公共目录。没有快照/溯源的历史原件不自动重新导入。
本轮实际测试及存储恢复、旧私人目录迁移限制见 `项目更新日志.md`。

---

## 五、AI 友好度：平台不再是"给 AI 看的一张图片"

按「网站 AI 友好度评估报告」整改后的机面形态（综合约 2/10 → 达标）：

- ✅ **真 404**：未匹配路径返回 404 + `application/problem+json`（含 `error.code` / `error.next_action` / `request_id`），不再软 404
- ✅ **一次 GET 自举**：`/llms.txt`（纯文本，含复制即跑示例）
- ✅ **机器可读契约**：`/openapi.json`（OpenAI 3.1）、`/v1/ontology`（JSON-LD 本体）
- ✅ **CORS 开放**：预检返回 204 + `Access-Control-Allow-Origin: *`（浏览器内 agent 也能跨源调）
- ✅ **自助拿 key**：`POST /v1/keys`（匿名只读，100 次/天配额，试错不花钱）
- ✅ **统一列表包络**：`/v1/search` 返回 `{items, next_cursor, total, suggested_actions}`
- ✅ **请求追踪**：每条响应带 `X-Request-Id`
- ✅ **MCP 公网入口**：`.well-known/mcp` + `/v1/mcp`（不只本机 stdio）
- ✅ **同域**：人面 `/`（Streamlit）+ 机面 `/v1/*` 同一个域名/端口

---

## 六、请求说明

| 参数 | 值 |
|---|---|
| `model` | `platform-semantic`（语义查询）/ `platform-tools` / `platform-graph` |
| `messages` | 取**最后一条 user 消息**作为问句 |
| 返回 | 自然语言摘要 + 结构化 JSON（含 `intent`、`elapsed_ms`） |
| 错误 | RFC 9457 `application/problem+json` |

平台返回的 JSON 里带 `platform.intent` 字段，标明命中的查询意图，便于上游 AI 二次加工。

---

## 七、已知限制

- 匿名只读配额 **100 次/天**（读操作计数，按日重置）；超限返回 `429 + Retry-After`。`POST /v1/keys` 领取 key 后，在请求头带 `Authorization: Bearer <key>` 可提升到 **1000 次/天**（key 已强制校验）
- 只接受最后一条 user 消息作为问句，未做多轮上下文
- `stream=true` 暂不支持，请求带 `stream:true` 会返回 `400 unsupported_parameter`
- 自然语言理解是**确定性规则 + 本体匹配**，不是 LLM 推断；复杂问句可能落到 `overview` 兜底

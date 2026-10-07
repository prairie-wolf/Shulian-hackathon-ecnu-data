# -*- coding: utf-8 -*-
"""
MCP Server —— 平台的 AI 无关接入层（与不同 AI 交互的关键）
通过 Model Context Protocol 把平台能力暴露给任何 MCP 客户端：
Claude / GPT / Gemini / Cursor / 各类 Agent 都能以同一套标准协议接入。
运行（stdio）：.venv/Scripts/python.exe aiplatform/mcp_server.py
运行（HTTP）：.venv/Scripts/python.exe aiplatform/mcp_server.py --http --port 8602

统一网关将 /v1/mcp 代理到独立 MCP HTTP 服务；各进程共享公共上传快照。
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from aiplatform.tools import SPARQLReadOnlyError


def create_mcp_server(pt, catalog=None):
    """用已构建的平台工具(pt)与数据源目录(catalog)创建 MCP Server（复用图，省内存）。"""
    server = MCPServer(
        "AI 科研数据平台",
        instructions=(
            "面向 AI 智能体的大数据平台。核心：本体(Ontology)作为语义契约，"
            "把异构数据源「本体性转化」成统一知识图谱，并通过工具化接口供任何 AI 调用。"
        ),
    )

    @server.tool()
    def list_ontology() -> dict:
        """列出平台本体覆盖的类及其实例数（本体是平台的语义契约）"""
        return pt.list_ontology()

    @server.tool()
    def list_sources() -> dict:
        """列出平台已接入的数据源（学术 + 企业多源）"""
        return pt.list_sources(catalog)

    @server.tool()
    def explore_class(class_name: str) -> dict:
        """探索某类实体（实例数 + 样例）。class_name 如 Scholar/Company/Publication/Institution/Industry"""
        return pt.explore_class(class_name)

    @server.tool()
    def find_entity(class_name: str, keyword: str, limit: int = 10, offset: int = 0) -> dict:
        """按名称搜索某类实体（跨中文标签，支持分页）"""
        return pt.find_entity(class_name, keyword, limit, offset)

    @server.tool()
    def entity_detail(entity_id: str) -> dict:
        """查看实体的全部属性与关系"""
        return pt.entity_detail(entity_id)

    @server.tool()
    def query_relation(subject_class: str, relation: str, object_class: str) -> dict:
        """查询某类关系（如 Scholar -affiliatedWith-> Institution）"""
        return pt.query_relation(subject_class, relation, object_class)

    @server.tool()
    def sparql(query: str) -> dict:
        """执行原始 SPARQL 查询（高级入口，跨全图）"""
        try:
            result = pt.sparql(query)
        except (SPARQLReadOnlyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        if "error" in result:
            raise ToolError(result["error"])
        return result

    @server.tool()
    def semantic_ask(question: str) -> dict:
        """自然语言问数：覆盖学术（学者/论文/领域）与企业（公司/行业）数据，含跨语言消歧"""
        return pt.semantic_ask(question)

    return server


if __name__ == "__main__":
    from aiplatform.build_platform import build
    from aiplatform.tools import PlatformTools

    # 平台启动时构建一次（本体 + 数据接入 + 转化 + 统一图）
    print("正在构建平台实例...", file=sys.stderr)
    onto, catalog, graph = build()
    pt = PlatformTools(graph, catalog)
    mcp = create_mcp_server(pt, catalog)
    print("平台就绪。", file=sys.stderr)

    if "--http" in sys.argv:
        port = 8602
        if "--port" in sys.argv:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        import uvicorn
        print(f"MCP HTTP 服务启动: http://localhost:{port}/mcp", file=sys.stderr)
        uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=port)
    else:
        mcp.run()  # stdio

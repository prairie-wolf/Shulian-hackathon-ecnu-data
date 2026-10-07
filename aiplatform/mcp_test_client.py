# -*- coding: utf-8 -*-
"""
MCP 测试客户端：模拟一个外部 AI 客户端，通过 MCP 协议连接平台并调用工具
证明：任何 MCP 兼容的 AI（Claude/GPT/Cursor/Agent）都能以同一套协议接入平台
"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(BASE, "aiplatform", "mcp_server.py")

async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER], cwd=BASE)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            info = initialized.server_info
            print(f"== 已连接平台: {info.name} ==")
            print(f"   说明: {(initialized.instructions or '')[:80]}...")

            tools = await session.list_tools()
            print(f"\n== 平台暴露 {len(tools.tools)} 个工具（任何 AI 都可用）==")
            for t in tools.tools:
                print(f"  · {t.name}: {t.description[:55]}")

            print("\n== 调用 semantic_ask（企业数据）==")
            r = await session.call_tool("semantic_ask", {"question": "哪些公司在人工智能行业"})
            _print_result(r)

            print("\n== 调用 semantic_ask（学术数据）==")
            r = await session.call_tool("semantic_ask", {"question": "华东师大哪些老师研究知识图谱"})
            _print_result(r)

def _print_result(r):
    # mcp 2.x ToolResult：尝试多个字段
    if r.is_error:
        raise RuntimeError(f"MCP 工具调用失败：{r.content}")
    data = None
    if hasattr(r, "structured_content") and r.structured_content:
        data = r.structured_content
    elif hasattr(r, "content") and r.content:
        data = r.content
    else:
        data = r
    print(json.dumps(data, ensure_ascii=False, default=str)[:600])

if __name__ == "__main__":
    asyncio.run(main())

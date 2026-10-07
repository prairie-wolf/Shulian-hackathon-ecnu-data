import asyncio
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from rdflib import Graph, Literal, RDF
from aiplatform.core import ONTO, RES, SourceCatalog, UnifiedGraph
from aiplatform.tools import PlatformTools
from aiplatform.semantic import GenericSemanticQuery
from tests.workspace_fixture import public_workspace

CALLS = {
    "list_ontology": {}, "list_sources": {},
    "explore_class": {"class_name": "Company"},
    "find_entity": {"class_name": "Company", "keyword": "fixture", "limit": 1, "offset": 1},
    "entity_detail": {"entity_id": "fixture1"},
    "query_relation": {"subject_class": "Company", "relation": "belongsToIndustry", "object_class": "Industry"},
    "sparql": {"query": "SELECT ?s WHERE {?s a onto:Company} ORDER BY ?s"},
    "semantic_ask": {"question": "有哪些公司？"},
}


class RESTInterfaceTests(unittest.TestCase):
    def test_concurrent_initial_requests_build_once(self):
        from aiplatform import api_server, gateway
        for module in (api_server, gateway):
            graph = SimpleNamespace(g=Graph(), _public_store=SimpleNamespace(refresh=lambda: None))
            def delayed_build():
                time.sleep(0.05)
                return None, SourceCatalog(), graph
            barrier = threading.Barrier(2)
            def request():
                barrier.wait(timeout=5)
                return module.get_platform()
            with self.subTest(app=module.__name__), \
                 patch.multiple(module, _onto=None, _cat=None, _graph=None, _q=None, _tools=None), \
                 patch.object(module, "build", side_effect=delayed_build) as build:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(lambda _: request(), range(2)))
                self.assertEqual(build.call_count, 1)

    def test_eight_tools_and_errors_on_both_apps(self):
        from aiplatform import api_server, gateway
        g = Graph()
        for i in (1, 2):
            e = RES[f"fixture{i}"]
            g.add((e, RDF.type, ONTO.Company))
            g.add((e, ONTO.name, Literal(f"fixture{i}")))
            g.add((e, ONTO.belongsToIndustry, RES.industry))
        g.add((RES.industry, RDF.type, ONTO.Industry))
        catalog = SourceCatalog()
        catalog.sources["fixture"] = {"source_id": "fixture", "name": "fixture", "kind": "csv", "rows": 2}
        graph = UnifiedGraph(None)
        graph.g = g
        platform = (None, catalog, graph, GenericSemanticQuery(g), PlatformTools(graph, catalog))
        for module in (api_server, gateway):
            with self.subTest(app=module.__name__), patch.object(module, "get_platform", return_value=platform), TestClient(module.app) as client:
                for name, args in CALLS.items():
                    response = client.post("/v1/tools/" + name, json={"arguments": args})
                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()["result"]
                    self.assertNotIn("error", result)
                    if name == "semantic_ask": self.assertEqual(result["count"], 2)
                    if name == "find_entity": self.assertEqual(result["matches"][0]["id"], "fixture2")
                    if name == "query_relation": self.assertEqual(result["count"], 2)
                self.assertEqual(client.post("/v1/tools/sparql", json={"arguments": {"query": "DELETE WHERE {?s ?p ?o}"}}).status_code, 403)
                self.assertEqual(client.post("/v1/tools/missing", json={}).status_code, 404)
                self.assertEqual(client.post("/v1/tools/find_entity", json={"arguments": {"class_name": "Company", "limit": -1}}).status_code, 400)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MCPInterfaceTests(unittest.TestCase):
    def test_real_stdio_and_http_tools(self):
        from mcp.client.stdio import stdio_client, StdioServerParameters
        from mcp.client.streamable_http import streamable_http_client
        from mcp.client.session import ClientSession
        with public_workspace() as (workspace, env):
            async def verify(streams):
                async with ClientSession(streams[0], streams[1]) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.server_info.name, "AI 科研数据平台")
                    registry = await session.list_tools()
                    self.assertEqual({t.name for t in registry.tools}, set(CALLS))
                    calls = dict(CALLS)
                    calls["find_entity"] = {"class_name": "Institution", "keyword": "University", "limit": 1, "offset": 1}
                    calls["entity_detail"] = {"entity_id": "i_I001"}
                    calls["semantic_ask"] = {"question": "复旦大学有哪些论文？"}
                    for name, args in calls.items():
                        result = await session.call_tool(name, args)
                        self.assertFalse(result.is_error, str(result.content))
                        value = result.structured_content or json.loads(result.content[0].text)
                        self.assertIsInstance(value, dict)
                        self.assertNotIn("error", value)
                        if name == "semantic_ask": self.assertEqual(value["count"], 115)
                        if name == "list_sources": self.assertEqual(len(value["sources"]), 19)
                        if name == "explore_class":
                            self.assertEqual(value["instances"], 47)
                            self.assertEqual(value["raw_instances"], 74)
                        if name == "find_entity": self.assertEqual(value["offset"], 1)
                    rejected = await session.call_tool("sparql", {"query": "DELETE WHERE {?s ?p ?o}"})
                    self.assertTrue(rejected.is_error)
            async def stdio():
                params = StdioServerParameters(command=sys.executable, args=["-m", "aiplatform.mcp_server"], cwd=str(workspace), env=env)
                with open(workspace / "stdio.log", "w", encoding="utf-8") as diagnostics:
                    async with stdio_client(params, errlog=diagnostics) as streams:
                        await verify(streams)
            asyncio.run(stdio())
            smoke = subprocess.run([sys.executable, "-m", "aiplatform.mcp_test_client"], cwd=workspace,
                                   env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)
            self.assertEqual(smoke.returncode, 0, smoke.stdout + smoke.stderr)
            self.assertIn("已连接平台", smoke.stdout)
            self.assertNotIn("== 本体加载 ==", smoke.stdout)
            port = free_port()
            with open(workspace / "mcp.log", "w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, "-m", "aiplatform.mcp_server", "--http", "--port", str(port)],
                                           cwd=workspace, env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 40
                    url = f"http://127.0.0.1:{port}/mcp"
                    while True:
                        if process.poll() is not None: self.fail((workspace / "mcp.log").read_text(encoding="utf-8"))
                        try:
                            if httpx.get(url, timeout=1).status_code != 503: break
                        except httpx.TransportError:
                            pass
                        if time.monotonic() > deadline: self.fail("MCP HTTP startup timed out")
                        time.sleep(0.1)
                    async def http():
                        async with streamable_http_client(url) as streams:
                            await verify(streams)
                    asyncio.run(http())
                finally:
                    process.terminate()
                    process.wait(timeout=15)

    def test_live_gateway_gate(self):
        bash = os.environ.get("BASH_EXE") or (
            str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe")
            if os.name == "nt" else shutil.which("bash"))
        if not bash or not Path(bash).exists():
            self.skipTest("Bash unavailable")
        with public_workspace() as (workspace, env):
            gateway_port, streamlit_port, mcp_port = free_port(), free_port(), free_port()
            base = f"http://127.0.0.1:{gateway_port}"
            env.update(PLATFORM_API_PORT=str(gateway_port), PUBLIC_URL=base,
                       CONSOLE_URL=f"http://127.0.0.1:{streamlit_port}",
                       MCP_URL=f"http://127.0.0.1:{mcp_port}", PLATFORM_DAILY_QUOTA="10000",
                       PYTHON=sys.executable.replace("\\", "/"))
            commands = {
                "streamlit": ["-m", "streamlit", "run", "app/console.py", "--server.address", "127.0.0.1",
                              "--server.port", str(streamlit_port), "--server.headless", "true", "--browser.gatherUsageStats", "false"],
                "mcp": ["-m", "aiplatform.mcp_server", "--http", "--port", str(mcp_port)],
                "gateway": ["-m", "aiplatform.gateway"],
            }
            processes, logs = [], []
            try:
                for name, command in commands.items():
                    log = open(workspace / (name + ".log"), "w", encoding="utf-8")
                    logs.append(log)
                    processes.append(subprocess.Popen([sys.executable] + command, cwd=workspace, env=env,
                                                      stdout=log, stderr=log))
                for url in (base + "/health", f"http://127.0.0.1:{streamlit_port}/_stcore/health",
                            f"http://127.0.0.1:{mcp_port}/mcp"):
                    deadline = time.monotonic() + 50
                    while True:
                        self.assertTrue(all(p.poll() is None for p in processes), "Service exited during startup")
                        try:
                            if httpx.get(url, timeout=2).status_code != 503: break
                        except httpx.TransportError:
                            pass
                        if time.monotonic() > deadline: self.fail("Service startup timed out: " + url)
                        time.sleep(0.1)
                result = subprocess.run([bash, "check.sh", base], cwd=workspace, env=env,
                                        capture_output=True, timeout=150)
                output = result.stdout.decode("utf-8", "replace")
                self.assertEqual(result.returncode, 0, output + result.stderr.decode("utf-8", "replace"))
                self.assertIn("FAIL=0  SKIP=0", output)
                print("Live gate:", base, output.splitlines()[-2:])
            finally:
                for process in processes: process.terminate()
                for process in processes: process.wait(timeout=15)
                for log in logs: log.close()


if __name__ == "__main__":
    unittest.main()

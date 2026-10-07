import unittest
from aiplatform import api_server, gateway
from aiplatform.answers import answer_with_client, summarize_result


class AnswerSummaryTests(unittest.TestCase):
    def test_rankings_keep_numeric_values_and_greeting_is_not_missing_data(self):
        self.assertIn("999", summarize_result("收入排名", {"intent": "numeric_rank",
                      "data": [{"name": "fixture", "value": 999}]}))
        self.assertIn("11", summarize_result("机构排名", {"intent": "relation_rank",
                      "data": [{"name": "fixture", "count": 11}]}))
        self.assertIn("你好", summarize_result("你好", {"intent": "greeting", "hint": "你好，可以查询图谱。"}))

    def test_interfaces_explain_ambiguity_and_show_trend_counts(self):
        for module in (api_server, gateway):
            with self.subTest(interface=module.__name__):
                self.assertIn("同名", module._summarize("Example是谁？", {
                    "intent": "ambiguous", "hint": "存在同名实体，请使用 URI 或补充身份信息确认。"}))
                summary = module._summarize("论文趋势", {"intent": "trend", "total": 8,
                            "data": [{"name": "2023", "count": 3}, {"name": "2024", "count": 5}]})
                self.assertIn("2023", summary)
                self.assertIn("3 条", summary)
                self.assertIn("2024", summary)
                self.assertIn("5 条", summary)

    def test_total_is_distinct_from_display_and_empty_answer_falls_back(self):
        result = {"intent": "list_sources", "sources": [f"source{i}" for i in range(19)]}
        summary = summarize_result("有哪些数据源？", result)
        self.assertIn("共查到 19", summary)
        self.assertIn("展示 15", summary)
        picked = {"name": "external fixture", "kind": "api", "key": "fixture"}
        _, trace, answer = answer_with_client("有哪些数据源？", result, picked,
                                              lambda *a, **k: {"content": ""})
        self.assertIn("回退", trace[-1]["answer_source"])
        self.assertIn("本地规则引擎", answer)

    def test_legacy_api_chat_json_is_not_cut_in_middle(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import json
        from fastapi.testclient import TestClient
        result = {"intent": "list_class", "count": 100, "data": [{"name": "fixture" * 30} for _ in range(100)]}
        platform = (None, None, None, SimpleNamespace(ask=lambda q: result), None)
        with patch.object(api_server, "get_platform", return_value=platform), TestClient(api_server.app) as client:
            response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "有哪些公司？"}]})
            self.assertEqual(response.status_code, 200)
            content = response.json()["choices"][0]["message"]["content"]
            self.assertEqual(json.loads(content.split("```json\n")[1].split("\n```")[0]), result)


if __name__ == "__main__":
    unittest.main()

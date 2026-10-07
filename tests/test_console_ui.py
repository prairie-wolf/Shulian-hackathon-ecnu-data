"""Run the console in a disposable workspace, without account or API files."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConsoleUITests(unittest.TestCase):
    def test_navigation_filters_chat_and_private_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            for name in ("aiplatform", "app", "ontology"):
                shutil.copytree(ROOT / name, workspace / name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copy(ROOT / "mappings.json", workspace)
            # Only the public fixtures needed by build(); never copy users,
            # private partitions, uploaded originals, or client configuration.
            fixtures = {
                "processed": ("institutions.csv", "scholars.csv", "publications.csv",
                              "venues.csv", "fields.csv", "affiliation.csv", "author_of.csv",
                              "published_in.csv", "belongs_to_field.csv"),
                "raw": ("companies.csv", "companies_v2.csv", "institution_labels.csv",
                        "scholar_labels.csv", "v2_inst_clean.json", "v2_fields_clean.json",
                        "v2_works_clean.json", "datasets.csv"),
            }
            for folder, names in fixtures.items():
                target = workspace / "data" / folder
                target.mkdir(parents=True)
                for name in names:
                    shutil.copy(ROOT / "data" / folder / name, target)
            script = textwrap.dedent('''
                import contextlib, io, httpx
                from pathlib import Path
                from streamlit.testing.v1 import AppTest
                def offline(*args, **kwargs):
                    raise OSError("UI test is offline")
                httpx.Client.request = offline
                from aiplatform import privates
                from aiplatform import ai_clients
                ai_clients.call_client = lambda *a, **k: {"error": "fixture failure", "content": "stale model answer"}
                app = AppTest.from_file("app/console.py", default_timeout=30)
                def check():
                    assert not app.exception, [e.message for e in app.exception]
                with contextlib.redirect_stdout(io.StringIO()):
                    app.run()
                    check()
                    pages = ["运行总览", "数据资产", "语义问答", "本体与工具", "AI 接入", "系统设置"]
                    for page in pages:
                        app.button(key="topnav_" + page).click().run()
                        assert app.session_state["nav"] == page
                        check()
                    app.button(key="topnav_数据资产").click().run()
                    filters = {t.label: t.value for t in app.toggle}
                    assert filters == {
                        "允许写入 GenericRecord（未识别类别）": False,
                        "保留未识别字段到描述": False,
                        "跳过空行和空主键": True,
                    }, filters
                    limit = next(n for n in app.number_input if n.label == "整个文件最多导入行数")
                    assert limit.value == 5000
                    limit.set_value(1).run()
                    check()
                    app.button(key="topnav_语义问答").click().run()
                    assert app.chat_input, "Default chat must offer a local query input"
                    driver = next(s for s in app.selectbox if s.label == "驱动 AI")
                    driver.select("本地规则引擎（无 AI 兜底）").run()
                    app.chat_input[0].set_value("复旦大学有哪些论文？").run()
                    check()
                    assert app.session_state["last"]["result"]["count"] == 115
                    assert len(app.session_state["messages"]) == 2
                    assert "根据本平台已接入的数据" in app.session_state["messages"][-1]["content"]
                    assert "共查到 115" in app.session_state["messages"][-1]["content"]
                    assert "展示 15" in app.session_state["messages"][-1]["content"]
                    app.run()
                    app.button(key="clear_chat_button").click().run()
                    check()
                    assert app.session_state["messages"] == []
                    assert app.session_state["last"] is None
                    app.chat_input[0].set_value("大语言模型趋势").run()
                    check()
                    assert app.session_state["last"]["result"]["intent"] == "ambiguous"
                    assert "同名" in app.session_state["messages"][-1]["content"]
                    app.chat_input[0].set_value("http://ecnu.edu.cn/resource/f_C137293760 趋势").run()
                    check()
                    trend = app.session_state["last"]["result"]
                    assert trend["intent"] == "trend", trend
                    for row in trend["data"]:
                        assert str(row["name"]) in app.session_state["messages"][-1]["content"]
                        assert f'{row["count"]} 条' in app.session_state["messages"][-1]["content"]
                    external = next(c for c in ai_clients.list_clients() if c["kind"] not in ("local", "platform"))
                    driver = next(s for s in app.selectbox if s.label == "驱动 AI")
                    driver.select(external["name"]).run()
                    app.chat_input[0].set_value("复旦大学有哪些论文？").run()
                    check()
                    assert "本地规则引擎" in app.session_state["messages"][-1]["meta"]
                    assert "外部 AI" in app.session_state["messages"][-1]["content"]
                    assert "stale model answer" not in app.session_state["messages"][-1]["content"]
                    # Use a fixture account and partitions only in this copy.
                    privates.new_partition("ui_fixture", "remove_me")
                    privates.new_partition("ui_fixture", "keep_me")
                    app.session_state["uid"] = "ui_fixture"
                    app.session_state["role"] = "user"
                    app.button(key="topnav_数据资产").click().run()
                    part = app.selectbox(key="part_sel")
                    part.select("remove_me").run()
                    app.button(key="delete_private_remove_me").click().run()
                    assert (Path("data/private/ui_fixture/remove_me.ttl")).exists()
                    app.checkbox(key="confirm_delete_remove_me").check().run()
                    app.button(key="delete_private_remove_me").click().run()
                    check()
                    assert not Path("data/private/ui_fixture/remove_me.ttl").exists()
                    assert Path("data/private/ui_fixture/keep_me.ttl").exists()
                    # A damaged private file is visible as an error, never an empty answer.
                    broken = Path("data/private/ui_fixture/keep_me.ttl")
                    broken.write_text("broken {{{", encoding="utf-8")
                    app.button(key="topnav_语义问答").click().run()
                    app.chat_input[0].set_value("复旦大学有哪些论文？").run()
                    check()
                    assert "私人分区损坏" in app.session_state["last"]["result"]["error"]
                    assert broken.read_text(encoding="utf-8") == "broken {{{"
                print("UI: six pages, filters, local answer, clear chat and private deletion passed")
            ''')
            env = {k: v for k, v in os.environ.items()
                   if k.upper() in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATH",
                                    "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "NUMBER_OF_PROCESSORS")}
            env.update({"TEMP": directory, "TMP": directory, "USERPROFILE": directory})
            result = subprocess.run([sys.executable, "-c", script], cwd=workspace,
                                    env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()

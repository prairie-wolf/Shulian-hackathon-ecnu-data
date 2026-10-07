"""Build/ingest/rebuild must retain uploads, catalog and deletion provenance."""
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, RDF
from aiplatform import build_platform as builder, upload
from aiplatform.core import ONTO, RES, PlatformOntology, SourceCatalog
from aiplatform.public_state import PublicUploadStore


class PublicPersistenceTests(unittest.TestCase):
    def test_restart_and_other_process_view_restore_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "processed").mkdir()
            (root / "raw").mkdir()
            mappings = root / "mappings.json"
            mappings.write_text("{}", encoding="utf-8")
            # Empty mapped fixtures avoid copying any real uploads or private data.
            with patch.object(builder, "PROC", str(root / "processed")), \
                 patch.object(builder, "RAW", str(root / "raw")), \
                 patch.object(builder.json, "load", return_value={}), \
                 patch("aiplatform.core.SourceCatalog.read", return_value=[]), \
                 patch.object(upload, "UPSTREAM_DIR", str(root / "uploads")), \
                 redirect_stdout(StringIO()) as stdout, redirect_stderr(StringIO()):
                onto, catalog, graph = builder.build()
                _, other_catalog, other = builder.build()
                path, display = upload.save_upload_bytes("fixture.csv", b"company_id,company_name\nFIX,Durable fixture\n")
                report = upload.ingest_file(graph, catalog, path, display)
                self.assertNotIn("error", report)
                _, restored_catalog, restored = builder.build()
                self.assertIn((RES.c_FIX, ONTO.name, Literal("Durable fixture")), restored.g)
                self.assertIn(report["source_id"], restored_catalog.sources)
                from aiplatform.tools import PlatformTools
                self.assertEqual(PlatformTools(other, other_catalog).find_entity("Company", "Durable")["total"], 1)
                upload.remove_uploaded_source(restored, restored_catalog, report["source_id"])
                self.assertEqual(PlatformTools(other, other_catalog).find_entity("Company", "Durable")["total"], 0)
                _, final_catalog, final = builder.build()
                self.assertNotIn(report["source_id"], final_catalog.sources)
                self.assertNotIn((RES.c_FIX, ONTO.name, Literal("Durable fixture")), final.g)
                self.assertEqual(stdout.getvalue(), "", "Build must not pollute MCP stdout")
                exported = Graph().parse(root / "processed/platform_graph.ttl", format="turtle")
                self.assertEqual(set(exported), set(final.g))


class PublicStateFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.upload_root = self.root / "uploads"
        self.upload_root.mkdir()
        self.patched = patch.object(upload, "UPSTREAM_DIR", str(self.upload_root))
        self.patched.start()
        self.onto = PlatformOntology(str(Path(__file__).resolve().parents[1] / "ontology/platform.owl"))
        self.base = {(RES.c_C1, RDF.type, ONTO.Company), (RES.c_C1, ONTO.name, Literal("base"))}
        self.path = self.root / "upload_state.json"

    def tearDown(self):
        self.patched.stop()
        self.temp.cleanup()

    def platform(self):
        g = Graph()
        for t in self.base: g.add(t)
        graph = SimpleNamespace(onto=self.onto, g=g)
        catalog = SourceCatalog()
        PublicUploadStore(graph, catalog, self.path)
        return graph, catalog

    def ingest(self, graph, catalog, name="shared"):
        path, display = upload.save_upload_bytes("fixture.csv", f"company_id,company_name\nC1,{name}\n".encode())
        report = upload.ingest_file(graph, catalog, path, display)
        self.assertNotIn("error", report)
        return report["source_id"], Path(path)

    def test_reload_deletion_preserves_base_and_overlapping_sources(self):
        graph, catalog = self.platform()
        first, _ = self.ingest(graph, catalog)
        second, _ = self.ingest(graph, catalog)
        restored, cat = self.platform()
        upload.remove_uploaded_source(restored, cat, first)
        self.assertIn((RES.c_C1, ONTO.name, Literal("shared")), restored.g)
        upload.remove_uploaded_source(restored, cat, second)
        self.assertEqual(set(restored.g), self.base)

    def test_corrupt_and_failed_snapshot_keep_existing_data(self):
        graph, catalog = self.platform()
        first, original = self.ingest(graph, catalog)
        snapshot = self.path.read_bytes()
        before = set(graph.g)
        with patch("aiplatform.public_state.atomic_write", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError): upload.remove_uploaded_source(graph, catalog, first)
        self.assertTrue(original.exists())
        self.assertEqual(self.path.read_bytes(), snapshot)
        self.assertEqual(set(graph.g), before)
        self.path.write_bytes(b"broken {{{")
        with self.assertRaises(ValueError): self.platform()
        with self.assertRaises(ValueError): self.ingest(graph, catalog)
        self.assertEqual(self.path.read_bytes(), b"broken {{{")
        self.assertEqual(set(graph.g), before)

    def test_crash_recovery_before_and_after_delete_commit(self):
        graph, catalog = self.platform()
        sid, original = self.ingest(graph, catalog)
        moved = original.with_name(".deleted-" + original.name)
        os.replace(original, moved)  # Simulate interruption before snapshot commit.
        restored, cat = self.platform()
        self.assertTrue(original.exists())
        self.assertFalse(moved.exists())
        with patch("aiplatform.public_state.os.remove", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(OSError, "撤回已保存"):
                upload.remove_uploaded_source(restored, cat, sid)
        self.assertTrue(moved.exists())
        final, cat = self.platform()  # After commit: cleanup is retried, never resurrected.
        self.assertFalse(moved.exists())
        self.assertNotIn(sid, cat.sources)
        self.assertEqual(set(final.g), self.base)

    def test_concurrent_writers_reload_latest_snapshot(self):
        first, cat1 = self.platform()
        second, cat2 = self.platform()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.ingest, first, cat1, "one"), pool.submit(self.ingest, second, cat2, "two")]
            for future in futures: future.result()
        restored, cat = self.platform()
        self.assertEqual(len(cat.sources), 2)
        self.assertIn((RES.c_C1, ONTO.name, Literal("one")), restored.g)
        self.assertIn((RES.c_C1, ONTO.name, Literal("two")), restored.g)

    def test_separate_process_writers_do_not_lose_uploads(self):
        script = '''
import sys
from pathlib import Path
from types import SimpleNamespace
from rdflib import Graph
from aiplatform import upload
from aiplatform.core import PlatformOntology, SourceCatalog
from aiplatform.public_state import PublicUploadStore
root = Path(sys.argv[1]); upload.UPSTREAM_DIR = str(root / "uploads")
onto = PlatformOntology(sys.argv[2]); graph = SimpleNamespace(onto=onto, g=Graph()); cat = SourceCatalog()
PublicUploadStore(graph, cat, root / "upload_state.json")
path, display = upload.save_upload_bytes("fixture.csv", f"company_id,company_name\\nC1,{sys.argv[3]}\\n".encode())
result = upload.ingest_file(graph, cat, path, display)
assert "error" not in result, result
'''
        ontology = Path(__file__).resolve().parents[1] / "ontology/platform.owl"
        processes = [subprocess.Popen([sys.executable, "-c", script, str(self.root), str(ontology), name],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for name in ("process1", "process2")]
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, (stdout + stderr).decode("utf-8", "replace"))
        restored, cat = self.platform()
        self.assertEqual(len(cat.sources), 2)
        for name in ("process1", "process2"):
            self.assertIn((RES.c_C1, ONTO.name, Literal(name)), restored.g)


if __name__ == "__main__":
    unittest.main()

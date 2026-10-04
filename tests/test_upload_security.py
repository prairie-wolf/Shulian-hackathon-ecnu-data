# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from rdflib import Graph

from aiplatform.upload import ingest_file, save_upload_bytes


class UploadPathTraversalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "uploads"
        self.outside = Path(self._tmp.name) / "ai_clients.json"
        self.outside.write_bytes(b"original")

    def tearDown(self):
        self._tmp.cleanup()

    def assert_confined(self, stored_path, display_name):
        upload_root = os.path.realpath(self.root)
        stored_real = os.path.realpath(stored_path)
        self.assertEqual(os.path.commonpath([upload_root, stored_real]), upload_root)
        self.assertEqual(os.path.dirname(stored_real), upload_root)
        self.assertEqual(display_name, "ai_clients.json")
        self.assertNotIn("/", display_name)
        self.assertNotIn("\\", display_name)
        self.assertEqual(self.outside.read_bytes(), b"original")

    def test_parent_directory_and_absolute_paths_are_confined(self):
        attacks = [
            "../../ai_clients.json",
            "..\\..\\ai_clients.json",
            "C:\\Windows\\Temp\\ai_clients.json",
            str(self.outside),
            "/tmp/ai_clients.json",
            "\\\\server\\share\\ai_clients.json",
        ]
        for attack in attacks:
            with self.subTest(filename=attack):
                stored_path, display_name = save_upload_bytes(
                    attack, b"attack", root=self.root
                )
                self.assert_confined(stored_path, display_name)
                self.assertEqual(Path(stored_path).read_bytes(), b"attack")

    def test_server_generated_names_do_not_overwrite_each_other(self):
        first_path, _ = save_upload_bytes("same.json", b"first", root=self.root)
        second_path, _ = save_upload_bytes("same.json", b"second", root=self.root)

        self.assertNotEqual(first_path, second_path)
        self.assertEqual(Path(first_path).read_bytes(), b"first")
        self.assertEqual(Path(second_path).read_bytes(), b"second")

    def test_saved_bytes_can_still_be_ingested_with_original_name(self):
        stored_path, display_name = save_upload_bytes(
            "..\\..\\季度报告.txt", "测试内容".encode("utf-8"), root=self.root
        )
        graph = SimpleNamespace(g=Graph())

        report = ingest_file(graph, None, stored_path, display_name, register=False)

        self.assertNotIn("error", report)
        self.assertEqual(report["filename"], "季度报告.txt")
        self.assertEqual(report["kind"], "text")
        self.assertTrue(stored_path.endswith(".txt"))

    def test_invalid_names_and_unsupported_types_are_rejected(self):
        for filename in ["", "..", "../", "payload.exe", "payload", "\x00.json"]:
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    save_upload_bytes(filename, b"attack", root=self.root)
        self.assertEqual(self.outside.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()

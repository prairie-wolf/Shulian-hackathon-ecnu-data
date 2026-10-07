import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from rdflib import Graph, Literal
from aiplatform import privates
from aiplatform.core import ONTO, RES


class PrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patched = patch.object(privates, "PRIV_DIR", str(self.root))
        self.patched.start()

    def tearDown(self):
        self.patched.stop()
        self.temp.cleanup()

    def test_non_ascii_users_cannot_share_sanitized_directory(self):
        pid = privates.new_partition("甲", "notes")
        g = Graph()
        g.add((RES.fixture, ONTO.name, Literal("private")))
        privates.save_partition("甲", pid, g)
        _, other = privates.load_user_priv("乙")
        self.assertEqual(len(other), 0)
        self.assertNotEqual(privates.user_priv_dir("甲"), privates.user_priv_dir("乙"))

    def test_case_variants_do_not_share_windows_directory(self):
        pid = privates.new_partition("Alice", "notes")
        g = Graph()
        g.add((RES.fixture, ONTO.name, Literal("private")))
        privates.save_partition("Alice", pid, g)
        _, other = privates.load_user_priv("alice")
        self.assertEqual(len(other), 0)
        self.assertNotEqual(privates.user_priv_dir("Alice"), privates.user_priv_dir("alice"))

    def test_legacy_colliding_directory_requires_explicit_migration(self):
        legacy = self.root / "_"
        legacy.mkdir()
        (legacy / "notes.ttl").write_bytes(b"# legacy private content\n")
        with self.assertRaisesRegex(ValueError, "迁移"):
            privates.load_user_priv("甲")
        self.assertEqual((legacy / "notes.ttl").read_bytes(), b"# legacy private content\n")

    def test_delete_rename_failure_restores_both_files(self):
        pid = privates.new_partition("fixture", "notes")
        directory = Path(privates.user_priv_dir("fixture"))
        ttl, meta = directory / "notes.ttl", directory / "notes.meta.json"
        original = {p: p.read_bytes() for p in (ttl, meta)}
        replace = os.replace
        def fail_metadata(source, destination):
            if str(source).endswith("notes.meta.json"):
                raise PermissionError("metadata locked")
            return replace(source, destination)
        with patch("aiplatform.privates.os.replace", side_effect=fail_metadata):
            with self.assertRaises(PermissionError):
                privates.delete_partition("fixture", pid)
        for path, data in original.items(): self.assertEqual(path.read_bytes(), data)

    def test_interrupted_private_delete_recovers_on_load(self):
        pid = privates.new_partition("fixture", "notes")
        g = Graph()
        g.add((RES.fixture, ONTO.name, Literal("keep")))
        privates.save_partition("fixture", pid, g)
        directory = Path(privates.user_priv_dir("fixture"))
        journal = directory / ".delete_notes.json"
        journal.write_text(json.dumps({"id": pid, "committed": False, "files": ["notes.ttl", "notes.meta.json"]}), encoding="utf-8")
        os.replace(directory / "notes.ttl", directory / ".notes.ttl.deleted")
        self.assertIn((RES.fixture, ONTO.name, Literal("keep")), privates.load_partition_g("fixture", pid))
        self.assertFalse(journal.exists())

    def test_concurrent_read_update_save_keeps_both_contributions(self):
        pid = privates.new_partition("fixture", "notes")
        def edit(name):
            with privates.edit_partition("fixture", pid) as g:
                g.add((RES[name], ONTO.name, Literal(name)))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(edit, ("first", "second")))
        self.assertEqual(len(privates.load_partition_g("fixture", pid)), 2)


if __name__ == "__main__":
    unittest.main()

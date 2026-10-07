"""Upload controls and deletion must preserve other sources and users."""
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from rdflib import Graph, Literal, RDF

from aiplatform import privates
from aiplatform.core import ONTO, RES, PlatformOntology, SourceCatalog
from aiplatform.upload import MAX_UPLOAD_BYTES, ingest_file, remove_uploaded_source, save_upload_bytes

BASE = Path(__file__).resolve().parents[1]


class UploadLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.onto = PlatformOntology(str(BASE / "ontology/platform.owl"))
        self.graph = SimpleNamespace(onto=self.onto, g=Graph())
        self.catalog = SourceCatalog()
        self.upload_root = patch("aiplatform.upload.UPSTREAM_DIR", str(self.root))
        self.upload_root.start()

    def tearDown(self):
        self.upload_root.stop()
        self.tmp.cleanup()

    def upload(self, name="companies.csv", body="company_id,company_name\nC1,ACME\n", **options):
        path, display = save_upload_bytes(name, body.encode("utf-8"))
        result = ingest_file(self.graph, self.catalog, path, display, **options)
        self.assertNotIn("error", result)
        return result, Path(path)

    def workbook(self, sheets, **options):
        path = self.root / "input.xlsx"
        with pd.ExcelWriter(path) as writer:
            for name, rows in sheets.items():
                pd.DataFrame(rows).to_excel(writer, sheet_name=name, index=False)
        return ingest_file(self.graph, self.catalog, str(path), "upload.xlsx", **options)

    def test_deletion_preserves_base_properties_provenance_and_inbound_relation(self):
        ent = RES.c_C1
        original = {
            (ent, RDF.type, ONTO.Company),
            (ent, ONTO.name, Literal("Original company")),
            (ent, ONTO.sourcedFrom, RES.ds_ds_companies),
            (RES.s_S1, ONTO.affiliatedWith, ent),
        }
        for triple in original:
            self.graph.g.add(triple)
        result, path = self.upload()
        remove_uploaded_source(self.graph, self.catalog, result["source_id"])
        self.assertEqual(set(self.graph.g), original)
        self.assertFalse(path.exists())
        self.assertEqual(self.catalog.list(), [])

    def test_overlapping_uploads_and_same_filename_are_independently_removable(self):
        first, path1 = self.upload()
        second, path2 = self.upload()
        self.assertNotEqual(first["source_id"], second["source_id"])
        self.assertEqual(len(self.catalog.list()), 2)
        remove_uploaded_source(self.graph, self.catalog, first["source_id"])
        self.assertIn((RES.c_C1, ONTO.name, Literal("ACME")), self.graph.g)
        self.assertIn((RES.c_C1, ONTO.sourcedFrom, RES["ds_" + second["source_id"]]), self.graph.g)
        self.assertTrue(path2.exists())
        self.assertFalse(path1.exists())
        remove_uploaded_source(self.graph, self.catalog, second["source_id"])
        self.assertEqual(len(self.graph.g), 0)

    def test_relation_targets_are_removed_without_leaving_orphans(self):
        result, _ = self.upload(body="company_id,company_name,industry\nC1,ACME,Logistics\n")
        self.assertIn((RES.ind_Logistics, RDF.type, ONTO.Industry), self.graph.g)
        remove_uploaded_source(self.graph, self.catalog, result["source_id"])
        self.assertEqual(len(self.graph.g), 0)

    def test_concurrent_deletions_preserve_preexisting_triples(self):
        base = (RES.c_C1, ONTO.name, Literal("ACME"))
        self.graph.g.add(base)
        first, _ = self.upload()
        second, _ = self.upload()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda sid: remove_uploaded_source(self.graph, self.catalog, sid),
                          [first["source_id"], second["source_id"]]))
        self.assertEqual(set(self.graph.g), {base})

    def test_missing_provenance_and_builtin_deletion_are_rejected(self):
        self.catalog.sources["ds_upload_legacy"] = {"location": str(self.root / "legacy.csv")}
        self.graph.g.add((RES.c_C1, ONTO.sourcedFrom, RES.ds_ds_upload_legacy))
        original = set(self.graph.g)
        for sid in ("ds_companies", "ds_upload_legacy"):
            with self.assertRaises(ValueError):
                remove_uploaded_source(self.graph, self.catalog, sid)
        self.assertEqual(set(self.graph.g), original)
        self.assertIn("ds_upload_legacy", self.catalog.sources)

    def test_reloaded_graph_without_runtime_provenance_is_not_deleted(self):
        result, path = self.upload()
        restored = SimpleNamespace(onto=self.onto, g=Graph())
        restored.g.parse(data=self.graph.g.serialize(format="turtle"), format="turtle")
        original = set(restored.g)
        with self.assertRaises(ValueError):
            remove_uploaded_source(restored, self.catalog, result["source_id"])
        self.assertEqual(set(restored.g), original)
        self.assertTrue(path.exists())
        self.assertIn(result["source_id"], self.catalog.sources)

    def test_file_failure_does_not_remove_graph_or_catalog(self):
        result, path = self.upload()
        original = set(self.graph.g)
        with patch("aiplatform.upload.os.remove", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                remove_uploaded_source(self.graph, self.catalog, result["source_id"])
        self.assertEqual(set(self.graph.g), original)
        self.assertIn(result["source_id"], self.catalog.sources)
        self.assertTrue(path.exists())

    def test_outside_original_is_rejected_before_mutation(self):
        result, path = self.upload()
        original = set(self.graph.g)
        self.catalog.sources[result["source_id"]]["location"] = str(self.root.parent / "outside.csv")
        with self.assertRaises(ValueError):
            remove_uploaded_source(self.graph, self.catalog, result["source_id"])
        self.assertEqual(set(self.graph.g), original)
        self.assertTrue(path.exists())

    def test_filtered_first_sheet_registers_accepted_second_sheet(self):
        result = self.workbook({
            "Notes": [{"id": "N1", "remarks": "hello"}],
            "Companies": [{"company_id": "C1", "company_name": "ACME"}],
        }, allow_generic=False, max_rows=1)
        self.assertNotIn("error", result)
        self.assertIn(result["source_id"], self.catalog.sources)
        self.assertIn((RES.c_C1, RDF.type, ONTO.Company), self.graph.g)
        self.assertEqual(result["quality"]["rows_used"], 1)
        self.assertEqual(result["quality"]["rows_in"], 2)

    def test_file_limit_across_multiple_sheets(self):
        result = self.workbook({
            "Companies1": [{"company_id": "C1", "company_name": "ACME"}],
            "Companies2": [{"company_id": "C2", "company_name": "ACME2"}],
        }, max_rows=1)
        self.assertNotIn("error", result)
        self.assertEqual(set(self.graph.g.subjects(RDF.type, ONTO.Company)), {RES.c_C1})
        self.assertEqual(result["quality"]["rows_used"], 1)
        self.assertEqual(result["quality"]["rows_truncated"], 1)

    def test_entity_count_is_unique_across_sheets(self):
        result = self.workbook({
            "Companies1": [{"company_id": "C1", "company_name": "ACME"}],
            "Companies2": [{"company_id": "C1", "company_name": "ACME"}],
        })
        self.assertEqual(result["quality"]["rows_used"], 2)
        self.assertEqual(result["report"]["entities"], 1)

    def test_blank_primary_keys_do_not_consume_limit(self):
        result, _ = self.upload(body="company_id,company_name\n,Unidentified\nC1,ACME\nC2,ACME2\n",
                                skip_empty=True, max_rows=1)
        self.assertEqual(set(self.graph.g.subjects(RDF.type, ONTO.Company)), {RES.c_C1})
        self.assertEqual(result["quality"]["rows_used"], 1)
        self.assertEqual(result["quality"]["rows_truncated"], 1)

    def test_blank_keys_can_be_kept_when_filter_is_disabled(self):
        self.upload(body="company_id,company_name\n,Unidentified\n", skip_empty=False)
        self.assertIn((RES.c_auto1, RDF.type, ONTO.Company), self.graph.g)

    def test_text_documents_with_same_name_remain_independent(self):
        first, _ = self.upload("notes.txt", "first document")
        second, _ = self.upload("notes.txt", "second document")
        remove_uploaded_source(self.graph, self.catalog, first["source_id"])
        descriptions = set(map(str, self.graph.g.objects(None, ONTO.description)))
        self.assertEqual(descriptions, {"second document"})
        remove_uploaded_source(self.graph, self.catalog, second["source_id"])
        self.assertEqual(len(self.graph.g), 0)

    def test_size_rejections_create_no_originals(self):
        for body in (b"", b"x" * (MAX_UPLOAD_BYTES + 1)):
            with self.assertRaises(ValueError):
                save_upload_bytes("companies.csv", body)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_private_delete_keeps_other_partitions_and_users(self):
        with patch.object(privates, "PRIV_DIR", str(self.root / "private")):
            privates.new_partition("alice", "p1")
            privates.new_partition("alice", "p2")
            privates.new_partition("bob", "p1")
            removed = privates.delete_partition("alice", "p1")
            self.assertEqual(len(removed), 2)
            self.assertEqual([p["id"] for p in privates.list_partitions("alice")], ["p2"])
            self.assertEqual([p["id"] for p in privates.list_partitions("bob")], ["p1"])


if __name__ == "__main__":
    unittest.main()

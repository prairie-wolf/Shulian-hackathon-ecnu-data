"""Small regressions against disposable graphs and private partitions."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, RDF
from aiplatform import privates
from aiplatform.core import ONTO, RES, SourceCatalog
from aiplatform.tools import PlatformTools


class RemainingRegressions(unittest.TestCase):
    def test_tools_accept_raw_and_wrapped_graph(self):
        g = Graph()
        g.add((RES.fixture, RDF.type, ONTO.Company))
        g.add((RES.fixture, ONTO.name, Literal("Fixture Co")))
        for graph in (g, SimpleNamespace(g=g)):
            with self.subTest(wrapped=graph is not g):
                pt = PlatformTools(graph, SourceCatalog())
                self.assertEqual(pt.list_ontology()["ontology_classes"]["Company"], 1)
                self.assertEqual(pt.semantic_ask("有哪些公司？")["count"], 1)

    def test_duplicate_private_partition_preserves_data(self):
        with tempfile.TemporaryDirectory() as root, patch.object(privates, "PRIV_DIR", root):
            pid = privates.new_partition("fixture", "existing")
            g = Graph()
            g.add((RES.fixture, ONTO.name, Literal("keep")))
            privates.save_partition("fixture", pid, g)
            original = (Path(root) / "fixture" / "existing.ttl").read_bytes()
            with self.assertRaises(FileExistsError):
                privates.new_partition("fixture", "existing")
            self.assertEqual((Path(root) / "fixture" / "existing.ttl").read_bytes(), original)

    def test_corrupt_private_partition_is_reported_and_preserved(self):
        with tempfile.TemporaryDirectory() as root, patch.object(privates, "PRIV_DIR", root):
            path = Path(privates.user_priv_dir("fixture")) / "broken.ttl"
            original = b"not valid turtle {{{"
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                privates.load_partition_g("fixture", "broken")
            with self.assertRaises(ValueError):
                privates.save_partition("fixture", "broken", Graph())
            self.assertEqual(path.read_bytes(), original)

    def test_failed_atomic_save_and_stale_writer_preserve_partition(self):
        with tempfile.TemporaryDirectory() as root, patch.object(privates, "PRIV_DIR", root):
            pid = privates.new_partition("fixture", "existing")
            first = privates.load_partition_g("fixture", pid)
            stale = privates.load_partition_g("fixture", pid)
            first.add((RES.fixture, ONTO.name, Literal("keep")))
            privates.save_partition("fixture", pid, first)
            with self.assertRaises(RuntimeError):
                privates.save_partition("fixture", pid, stale)
            original = (Path(root) / "fixture" / "existing.ttl").read_bytes()
            first.add((RES.other, ONTO.name, Literal("uncommitted")))
            with patch("aiplatform.storage.os.replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    privates.save_partition("fixture", pid, first)
            self.assertEqual((Path(root) / "fixture" / "existing.ttl").read_bytes(), original)

    def test_tools_company_aliases_share_search_detail_and_count(self):
        from tests.test_entity_equivalence import CompanyEvidenceTests
        fixture = CompanyEvidenceTests()
        fixture.setUp()
        g = fixture.graph
        g.add((fixture.right, ONTO.cnLabel, Literal("示例")))
        pt = PlatformTools(g)
        self.assertEqual(pt.explore_class("Company")["instances"], 1)
        self.assertEqual(pt.find_entity("Company", "Example")["total"], 1)
        self.assertEqual(pt.find_entity("Company", "示例")["total"], 1)
        self.assertIn({"property": "cnLabel", "value": "示例"},
                      pt.entity_detail("c_C001")["properties"])

    def test_sparql_ask_and_write_error(self):
        from aiplatform.tools import SPARQLReadOnlyError
        g = Graph()
        g.add((RES.fixture, ONTO.name, Literal("INSERT")))
        pt = PlatformTools(g)
        self.assertTrue(pt.sparql("ASK { ?s ?p ?o }")["boolean"])
        self.assertEqual(pt.sparql('SELECT ?s WHERE {?s onto:name "INSERT"}')["count"], 1)
        with self.assertRaises(SPARQLReadOnlyError):
            pt.sparql("DELETE WHERE {?s ?p ?o}")


if __name__ == "__main__":
    unittest.main()

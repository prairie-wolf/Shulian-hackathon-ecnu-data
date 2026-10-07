import csv
import os
import tempfile
import unittest
from types import SimpleNamespace

from rdflib import Graph

from aiplatform.core import PlatformOntology, SourceCatalog
from aiplatform.upload import ingest_file


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class UploadFilteringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "projects.csv")
        with open(self.path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["project_id", "项目名称", "负责人", "预算"])
            writer.writerow(["P1", "数据平台建设", "张三", "100.5"])
            writer.writerow(["P2", "知识图谱构建", "李四", "200.0"])
        self.onto = PlatformOntology(os.path.join(BASE, "ontology", "platform.owl"))
        self.catalog = SourceCatalog()

    def tearDown(self):
        self.tmp.cleanup()

    def _graph(self):
        return SimpleNamespace(onto=self.onto, g=Graph())

    def test_generic_records_can_be_filtered(self):
        result = ingest_file(self._graph(), self.catalog, self.path, "projects.csv",
                             source_id="filter_test_1", allow_generic=False)
        self.assertIn("error", result)
        self.assertIn("GenericRecord", result["error"])

    def test_row_limit_and_unmapped_field_filter(self):
        graph = self._graph()
        result = ingest_file(graph, self.catalog, self.path, "projects.csv",
                             source_id="filter_test_2", allow_generic=True,
                             max_rows=1, include_unmapped=False)
        self.assertNotIn("error", result)
        self.assertEqual(result["quality"]["rows_in"], 2)
        self.assertEqual(result["quality"]["rows_used"], 1)
        mapping = result["report"]["mappings"][0]
        self.assertEqual(mapping["rows"], 1)
        self.assertEqual(mapping["unmapped_columns"], ["负责人", "预算"])
        self.assertGreater(len(graph.g), 0)


if __name__ == "__main__":
    unittest.main()

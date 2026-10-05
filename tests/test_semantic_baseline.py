"""Read-only public baseline checks; reconstruct mappings without build() exports."""
import csv
import json
import unittest
from pathlib import Path
from rdflib import Graph, RDF, URIRef
from aiplatform.core import ONTO, PlatformOntology, SemanticMapper
from aiplatform.semantic import GenericSemanticQuery

ROOT = Path(__file__).resolve().parents[1]

def public_graph():
    mapper = SemanticMapper(PlatformOntology(str(ROOT / 'ontology/platform.owl')))
    mappings = json.loads((ROOT / 'mappings.json').read_text(encoding='utf-8'))
    files = {
        'ds_institutions': 'processed/institutions.csv', 'ds_scholars': 'processed/scholars.csv',
        'ds_publications': 'processed/publications.csv', 'ds_venues': 'processed/venues.csv',
        'ds_fields': 'processed/fields.csv', 'ds_affiliation': 'processed/affiliation.csv',
        'ds_author_of': 'processed/author_of.csv', 'ds_published_in': 'processed/published_in.csv',
        'ds_belongs_to_field': 'processed/belongs_to_field.csv', 'ds_companies': 'raw/companies.csv',
        'ds_institution_labels': 'raw/institution_labels.csv', 'ds_scholar_labels': 'raw/scholar_labels.csv',
        'ds_v2_inst': 'raw/v2_inst_clean.json', 'ds_v2_fields': 'raw/v2_fields_clean.json',
        'ds_v2_works': 'raw/v2_works_clean.json', 'ds_v2_scholars': 'raw/v2_works_clean.json',
        'ds_v2_author_of': 'raw/v2_works_clean.json', 'ds_v2_datasets': 'raw/datasets.csv',
        'ds_companies_v2': 'raw/companies_v2.csv',
    }
    graph = Graph()
    for sid, filename in files.items():
        path = ROOT / 'data' / filename
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = json.load(stream) if path.suffix == '.json' else list(csv.DictReader(stream))
        for mapping in mappings.get(sid, []):
            for triple in mapper.apply(rows, mapping, sid):
                graph.add(triple)
    return graph

class PublicBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = public_graph()
        cls.query = GenericSemanticQuery(cls.graph)

    def test_fudan_publication_total_from_independent_traversal(self):
        institution = URIRef('http://ecnu.edu.cn/resource/i_I24943067')
        authors = set(self.graph.subjects(ONTO.affiliatedWith, institution))
        expected = {paper for author in authors for paper in self.graph.objects(author, ONTO.authorOf)
                    if (paper, RDF.type, ONTO.Publication) in self.graph}
        result = self.query.ask('复旦大学有哪些论文？')
        self.assertEqual(result['intent'], 'entity_publications')
        self.assertEqual(result['uri'], str(institution))
        self.assertEqual(result['count'], len(expected))
        self.assertEqual(result['returned'], min(30, len(expected)))
        self.assertEqual(result['has_more'], len(expected) > 30)
        self.assertTrue({URIRef(row['uri']) for row in result['data']} <= expected)
        print('Public baseline Fudan publications:', len(expected), 'returned:', result['returned'])

if __name__ == '__main__':
    unittest.main()

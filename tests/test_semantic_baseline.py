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

    def test_chinese_institutions_use_expected_uris(self):
        bit = URIRef('http://ecnu.edu.cn/resource/i_I125839683')
        neu = URIRef('http://ecnu.edu.cn/resource/i_I9224756')
        self.assertEqual(self.query.find_mentioned_entities('北理工有哪些学者？')[0][0], bit)
        self.assertEqual(self.query.find_mentioned_entities('东北大学有哪些学者？')[0][0], neu)
        result = self.query.ask('北理工有哪些学者？')
        expected = {u for u in self.graph.subjects(ONTO.affiliatedWith, bit)
                    if (u, RDF.type, ONTO.Scholar) in self.graph}
        self.assertEqual(result['intent'], 'scholars_filtered')
        self.assertEqual(result['count'], len(expected))
        print('Public baseline BIT associated scholars:', len(expected))

    def test_public_company_evidence_and_deduplicated_listing(self):
        from aiplatform.entity_equivalence import CompanyEquivalence, inferred_company_pairs
        raw = set(self.graph.subjects(RDF.type, ONTO.Company))
        pairs = list(inferred_company_pairs(self.graph))
        view = CompanyEquivalence(self.graph)
        expected = {view.representative(uri) for uri in raw}
        result = self.query.ask('有哪些公司？')
        self.assertEqual(result['count'], len(expected))
        self.assertEqual(result['raw_count'], len(raw))
        self.assertEqual(len(raw) - len(expected), len(pairs))
        self.assertTrue(pairs)
        print('Public baseline companies:', len(raw), 'display entities:', len(expected), 'evidenced pairs:', len(pairs))

if __name__ == '__main__':
    unittest.main()

import unittest
from rdflib import Graph, RDF, Literal
from aiplatform.core import ONTO, RES
from aiplatform.entity_equivalence import CompanyEquivalence, link_company_equivalences

class CompanyEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.left, self.right = RES['c_C001'], RES['c2_V2C021']
        for entity, source, revenue in ((self.left, 'ds_ds_companies', '100.0'),
                                        (self.right, 'ds_ds_companies_v2', '100')):
            for predicate, value in ((RDF.type, ONTO.Company), (ONTO.name, Literal('Example')),
                                     (ONTO.region, Literal('杭州')), (ONTO.revenue, Literal(revenue)),
                                     (ONTO.employees, Literal(20)), (ONTO.sourcedFrom, RES[source])):
                self.graph.add((entity, predicate, value))
    def test_matching_public_source_evidence_and_idempotent_build_hook(self):
        before = set(self.graph)
        view = CompanyEquivalence(self.graph)
        self.assertEqual(view.representative(self.left), view.representative(self.right))
        self.assertEqual(set(self.graph), before)
        self.assertEqual(link_company_equivalences(self.graph), 1)
        self.assertEqual(link_company_equivalences(self.graph), 0)
        self.assertTrue(before <= set(self.graph))
    def test_conflicting_missing_or_untrusted_evidence_is_not_merged(self):
        for predicate, replacement in ((ONTO.region, Literal('北京')), (ONTO.revenue, Literal(101)),
                                        (ONTO.employees, Literal(21)), (ONTO.sourcedFrom, RES['upload'])):
            with self.subTest(predicate=predicate):
                changed = Graph()
                for triple in self.graph:
                    changed.add(triple)
                changed.set((self.right, predicate, replacement))
                view = CompanyEquivalence(changed)
                self.assertNotEqual(view.representative(self.left), view.representative(self.right))
        self.graph.remove((self.right, ONTO.employees, None))
        self.assertEqual(link_company_equivalences(self.graph), 0)
    def test_live_evidence_changes_and_non_company_sameas(self):
        self.assertEqual(CompanyEquivalence(self.graph).representative(self.left),
                         CompanyEquivalence(self.graph).representative(self.right))
        self.graph.set((self.right, ONTO.region, Literal('北京')))
        view = CompanyEquivalence(self.graph)
        self.assertNotEqual(view.representative(self.left), view.representative(self.right))
        scholar = RES['scholar']
        self.graph.add((scholar, RDF.type, ONTO.Scholar))
        self.graph.add((self.left, ONTO.sameAs, scholar))
        self.assertEqual(CompanyEquivalence(self.graph).aliases(scholar), {scholar})

if __name__ == '__main__':
    unittest.main()

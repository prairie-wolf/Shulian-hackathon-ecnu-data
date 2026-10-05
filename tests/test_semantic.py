import unittest
from rdflib import Graph, RDF, Literal, URIRef
from aiplatform.core import ONTO
from aiplatform.semantic import GenericSemanticQuery

class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.g = Graph()
        self.q = GenericSemanticQuery(self.g)
    def entity(self, key, kind, name, **props):
        uri = URIRef('urn:test:' + key)
        self.g.add((uri, RDF.type, ONTO[kind]))
        self.g.add((uri, ONTO.name, Literal(name)))
        for prop, value in props.items():
            self.g.add((uri, ONTO[prop], Literal(value)))
        return uri
    def test_a1_chinese_school_and_unknown_scope(self):
        school = self.entity('bit', 'Institution', 'Beijing Institute of Technology', country='CN')
        scholar = self.entity('a', 'Scholar', 'Alice')
        self.g.add((scholar, ONTO.affiliatedWith, school))
        result = self.q.ask('北理工有哪些学者？')
        self.assertEqual(result['intent'], 'scholars_filtered')
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['data'][0]['name'], 'Alice')
        self.assertEqual(self.q.find_mentioned_entities('北京理工大学有哪些学者？')[0][0], school)
        for question in ('不存在大学有哪些学者？', '张不存在有哪些论文？', '火星领域有哪些论文？'):
            with self.subTest(question=question):
                self.assertEqual(self.q.ask(question)['intent'], 'not_found')
        self.assertEqual(self.q.ask('有哪些学者？')['intent'], 'list_class')

if __name__ == '__main__':
    unittest.main()

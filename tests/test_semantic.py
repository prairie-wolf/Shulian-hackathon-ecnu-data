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

    def test_a2_people_routing_and_unsupported(self):
        field = self.entity('field', 'Field', 'Knowledge Graph', cnLabel='知识图谱')
        author = self.entity('author', 'Scholar', 'Alice')
        work = self.entity('work', 'Publication', 'A paper')
        self.g.add((author, ONTO.authorOf, work))
        self.g.add((work, ONTO.belongsToField, field))
        for question in ('知识图谱领域有哪些学者？', '知识图谱领域的学者是谁？'):
            result = self.q.ask(question)
            self.assertEqual(result['intent'], 'scholars_filtered')
            self.assertEqual(result['data'][0]['name'], 'Alice')
        self.assertEqual(self.q.ask('张不存在是谁？')['intent'], 'not_found')
        for question in ('Alice的生日是什么？', 'Alice的邮箱是什么？'):
            self.assertEqual(self.q.ask(question)['intent'], 'unsupported')
        self.assertEqual(self.q.ask('你好！')['intent'], 'greeting')
        self.assertEqual(self.q.ask('Alice是谁？')['intent'], 'entity_detail')

    def test_a3_publication_totals_and_intersection(self):
        school = self.entity('fudan', 'Institution', 'Fudan University', country='CN')
        field = self.entity('field', 'Field', 'Knowledge Graph', cnLabel='知识图谱')
        a = self.entity('a', 'Scholar', 'Alice')
        b = self.entity('b', 'Scholar', 'Bob')
        for author in (a, b):
            self.g.add((author, ONTO.affiliatedWith, school))
        for i in range(35):
            paper = self.entity('p' + str(i), 'Publication', 'Same title')
            self.g.add((a, ONTO.authorOf, paper))
            self.g.add((b, ONTO.authorOf, paper))
            if i < 4:
                self.g.add((paper, ONTO.belongsToField, field))
        for question in ('复旦有哪些论文？', '复旦有多少论文？', 'Alice发表的论文列表'):
            result = self.q.ask(question)
            self.assertEqual(result['intent'], 'entity_publications')
            self.assertEqual(result['count'], 35)
            self.assertEqual(result['returned'], 30)
            self.assertTrue(result['has_more'])
            self.assertEqual(len({r['uri'] for r in result['data']}), 30)
        result = self.q.ask('复旦知识图谱领域有哪些论文？')
        self.assertEqual((result['count'], result['returned'], result['has_more']), (4, 4, False))

    def test_a4_country_ambiguity_and_live_mutations(self):
        us = self.entity('neu-us', 'Institution', 'Northeastern University', country='US', cnLabel='东北大学')
        self.assertEqual(self.q.ask('东北大学有哪些学者？')['intent'], 'not_found')
        cn = self.entity('neu-cn', 'Institution', 'Northeastern University', country='CN')
        self.assertEqual(self.q.find_mentioned_entities('东北大学有哪些学者？'), [(cn, '东北大学')])
        result = self.q.ask('Northeastern University有哪些学者？')
        self.assertEqual(result['intent'], 'ambiguous')
        self.assertEqual({row['uri'] for row in result['data']}, {str(us), str(cn)})
        self.g.remove((cn, None, None))
        self.assertEqual(self.q.find_mentioned_entities('东北大学有哪些学者？'), [])
        other = self.entity('neu-cn-new', 'Institution', 'Northeastern University', country='CN')
        self.assertEqual(self.q.find_mentioned_entities('东北大学有哪些学者？')[0][0], other)
        a = self.entity('homonym1', 'Scholar', 'Alice')
        b = self.entity('homonym2', 'Scholar', 'Alice')
        self.assertEqual(self.q.ask('Alice是谁？')['intent'], 'ambiguous')
        self.assertEqual(self.q.ask(str(a) + '是谁？')['intent'], 'entity_detail')

if __name__ == '__main__':
    unittest.main()

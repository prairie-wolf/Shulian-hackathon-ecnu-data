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

    def test_a5_alias_only_labels_relations_and_homonym_scholars(self):
        from aiplatform.entity_equivalence import CompanyEquivalence
        canonical = self.entity('company1', 'Company', 'Example')
        alias = self.entity('company2', 'Company', 'Other name', cnLabel='别名独有标签')
        unrelated = self.entity('company3', 'Company', 'Example')
        self.g.add((canonical, ONTO.sameAs, alias))
        industry = self.entity('industry', 'Industry', 'AI', cnLabel='人工智能')
        self.g.add((alias, ONTO.belongsToIndustry, industry))
        self.assertEqual(CompanyEquivalence(self.g).representative(alias), canonical)
        self.assertEqual(CompanyEquivalence(self.g).representative(unrelated), unrelated)
        hits = self.q.search_entities('别名独有标签')
        self.assertEqual(hits[0]['uri'], canonical)
        detail = self.q.ask('别名独有标签的详情')
        self.assertEqual(detail['intent'], 'entity_detail')
        self.assertIn('AI', detail['relations']['所属行业'])
        self.assertIn('别名独有标签', detail['properties']['中文名'])
        result = self.q.ask('人工智能行业有哪些公司？')
        self.assertEqual(self.q.ask('人工智能有哪些公司？')['count'], 1)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['companies'][0]['uri'], str(canonical))
        self.assertEqual(self.q.ask('有哪些公司？')['count'], 2)
        self.assertEqual(self.q.ask('有哪些公司？')['raw_count'], 3)
        self.assertEqual(self.q.ask('别名独有标签有哪些公司？')['intent'], 'unsupported')
        school = self.entity('bit', 'Institution', 'Beijing Institute of Technology', country='CN')
        for key in ('same1', 'same2'):
            scholar = self.entity(key, 'Scholar', 'Same name')
            self.g.add((scholar, ONTO.affiliatedWith, school))
        result = self.q.ask('北理工的校友是谁？')
        self.assertEqual(result['count'], 2)
        self.assertEqual(len({r['uri'] for r in result['data']}), 2)
        self.assertIn('不代表毕业关系', result['hint'])
        self.assertEqual(result['relation'], 'affiliatedWith')

    def test_scope_guards_and_total_before_display(self):
        field = self.entity('field', 'Field', 'Knowledge Graph', cnLabel='知识图谱')
        school = self.entity('bit', 'Institution', 'Beijing Institute of Technology', country='CN')
        self.assertEqual(self.q.ask('未知大学知识图谱领域有哪些学者？')['intent'], 'not_found')
        self.assertEqual(self.q.ask('未知领域论文趋势')['intent'], 'not_found')
        for i in range(35):
            self.entity('author' + str(i), 'Scholar', 'Person ' + str(i))
        for question in ('有哪些学者？', '有多少学者？', '列出所有学者'):
            result = self.q.ask(question)
            self.assertEqual((result['count'], result['returned'], result['has_more']), (35, 25, True))

    def test_scoped_trend_and_paper_ranking_count_unique_papers(self):
        school = self.entity('bit', 'Institution', 'Beijing Institute of Technology', country='CN')
        a = self.entity('a', 'Scholar', 'Alice')
        b = self.entity('b', 'Scholar', 'Bob')
        self.g.add((a, ONTO.affiliatedWith, school))
        self.g.add((b, ONTO.affiliatedWith, school))
        paper = self.entity('paper', 'Publication', 'Shared paper', year=2020)
        self.g.add((a, ONTO.authorOf, paper))
        self.g.add((b, ONTO.authorOf, paper))
        self.entity('unrelated-paper', 'Publication', 'Unrelated paper', year=2021)
        result = self.q.ask('北理工论文趋势')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['data'], [{'name': '2020', 'count': 1}])
        result = self.q.ask('哪个机构论文最多？')
        self.assertEqual(result['metric'], 'publications')
        self.assertEqual(result['data'][0]['count'], 1)
        self.assertEqual(result['data'][0]['uri'], str(school))

if __name__ == '__main__':
    unittest.main()

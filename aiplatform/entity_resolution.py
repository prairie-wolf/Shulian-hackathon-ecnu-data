# -*- coding: utf-8 -*-
"""Graph-backed institution aliases; no stale name cache or first-hit selection."""
import re
from rdflib import RDF
from aiplatform.core import ONTO

INSTITUTION_ALIASES = {
    '清华大学': ('Tsinghua University', 'CN'),
    '北京大学': ('Peking University', 'CN'),
    '浙江大学': ('Zhejiang University', 'CN'),
    '复旦大学': ('Fudan University', 'CN'),
    '上海交通大学': ('Shanghai Jiao Tong University', 'CN'),
    '华东师范大学': ('East China Normal University', 'CN'),
    '南京大学': ('Nanjing University', 'CN'),
    '中国科学技术大学': ('University of Science and Technology of China', 'CN'),
    '华中科技大学': ('Huazhong University of Science and Technology', 'CN'),
    '武汉大学': ('Wuhan University', 'CN'),
    '中山大学': ('Sun Yat-sen University', 'CN'),
    '西安交通大学': ("Xi'an Jiaotong University", 'CN'),
    '哈尔滨工业大学': ('Harbin Institute of Technology', 'CN'),
    '北京理工大学': ('Beijing Institute of Technology', 'CN'),
    '北京航空航天大学': ('Beihang University', 'CN'),
    '同济大学': ('Tongji University', 'CN'),
    '天津大学': ('Tianjin University', 'CN'),
    '南开大学': ('Nankai University', 'CN'),
    '四川大学': ('Sichuan University', 'CN'),
    '山东大学': ('Shandong University', 'CN'),
    '厦门大学': ('Xiamen University', 'CN'),
    '东南大学': ('Southeast University', 'CN'),
    '吉林大学': ('Jilin University', 'CN'),
    '大连理工大学': ('Dalian University of Technology', 'CN'),
    '华南理工大学': ('South China University of Technology', 'CN'),
    '湖南大学': ('Hunan University', 'CN'),
    '中南大学': ('Central South University', 'CN'),
    '重庆大学': ('Chongqing University', 'CN'),
    '兰州大学': ('Lanzhou University', 'CN'),
    '东北大学': ('Northeastern University', 'CN'),
    '西北工业大学': ('Northwestern Polytechnical University', 'CN'),
    '电子科技大学': ('University of Electronic Science and Technology of China', 'CN'),
    '北京邮电大学': ('Beijing University of Posts and Telecommunications', 'CN'),
    '北京师范大学': ('Beijing Normal University', 'CN'),
    '中国人民大学': ('Renmin University of China', 'CN'),
    '上海大学': ('Shanghai University', 'CN'),
    '上海海事大学': ('Shanghai Maritime University', 'CN'),
    '深圳大学': ('Shenzhen University', 'CN'),
    '苏州大学': ('Soochow University', 'CN'),
    '郑州大学': ('Zhengzhou University', 'CN'),
    '香港大学': ('University of Hong Kong', 'HK'),
    '香港中文大学': ('Chinese University of Hong Kong', 'HK'),
    '香港科技大学': ('Hong Kong University of Science and Technology', 'HK'),
    '香港理工大学': ('Hong Kong Polytechnic University', 'HK'),
    '新加坡国立大学': ('National University of Singapore', 'SG'),
    '南洋理工大学': ('Nanyang Technological University', 'SG'),
    '麻省理工学院': ('Massachusetts Institute of Technology', 'US'),
    '斯坦福大学': ('Stanford University', 'US'),
    '哈佛大学': ('Harvard University', 'US'),
    '牛津大学': ('University of Oxford', 'GB'),
    '剑桥大学': ('University of Cambridge', 'GB'),
    '中国科学院': ('Chinese Academy of Sciences', 'CN'),
    '中国社会科学院': ('Chinese Academy of Social Sciences', 'CN'),
}
for short, full in {'北理工': '北京理工大学', '北大': '北京大学', '华东师大': '华东师范大学', '上海交大': '上海交通大学', '复旦': '复旦大学', '浙大': '浙江大学', '清华': '清华大学'}.items():
    INSTITUTION_ALIASES[short] = INSTITUTION_ALIASES[full]

def mentioned_entities(graph, question):
    """Return all candidates for non-overlapping longest mentions, preserving URIs."""
    explicit = [entity for entity in graph.subjects(RDF.type, None) if str(entity) in question]
    if explicit:
        return [(entity, str(entity)) for entity in sorted(set(explicit), key=str)]
    candidates = {}
    for predicate in (ONTO.name, ONTO.cnLabel):
        for entity, _, value in graph.triples((None, predicate, None)):
            label = str(value)
            if len(label) >= 2 and label.casefold() in question.casefold():
                candidates.setdefault(label, set()).add(entity)
    for alias, (english, country) in INSTITUTION_ALIASES.items():
        if alias not in question:
            continue
        matches = {entity for entity in graph.subjects(RDF.type, ONTO.Institution)
                   if any(str(v).casefold() == english.casefold() for v in graph.objects(entity, ONTO.name))
                   and country in {str(v) for v in graph.objects(entity, ONTO.country)}}
        # A Chinese alias must not resolve to a conflicting country even with a bad cnLabel.
        direct = candidates.get(alias, set())
        matches |= {entity for entity in direct if (entity, RDF.type, ONTO.Institution) in graph
                    and country in {str(v) for v in graph.objects(entity, ONTO.country)}}
        candidates[alias] = matches
    accepted, spans, seen = [], [], set()
    for label in sorted(candidates, key=lambda x: (-len(x), x)):
        for match in re.finditer(re.escape(label), question, re.IGNORECASE):
            start, end = match.span()
            if any(start < b and end > a for a, b in spans):
                continue
            spans.append((start, end))
            for entity in sorted(candidates[label], key=str):
                if entity not in seen:
                    accepted.append((entity, label)); seen.add(entity)
    return accepted

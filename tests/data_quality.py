# -*- coding: utf-8 -*-
"""数据质量巡检

检查项：
  1) 同名实体统计（按类给出"多余实体"数量）
  2) Company 精确重复（名称+营收+员工数三项全同）是否都已用 sameAs 建链
  3) sameAs 是否真的被用上（作者本体定义了该关系）
  4) 查询工具是否正确折叠 sameAs 别名

说明：同名不等于重复 —— Institution 的 Ministry of Education 分属 KR/NZ/CL，
Scholar 的同名可能是不同人，因此**不做盲目去重**，只对三项数值全同的 Company 建等价链接。
"""
import os, sys, collections

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)
sys.path.insert(0, REPO)

from aiplatform.build_platform import build
from aiplatform.tools import PlatformTools
from aiplatform.core import ONTO
from rdflib import RDF, URIRef

checks = []
def add(name, ok, detail=""):
    checks.append(ok)
    print(("  PASS " if ok else "  FAIL ") + name + (("  " + str(detail)[:180]) if detail else ""))

onto, cat, graph = build()
g = graph.g
pt = PlatformTools(g, cat)

# ---- 1) 同名实体统计 ----
print("\n[1] 同名实体统计（仅报告，不自动去重）")
by_class = collections.defaultdict(lambda: collections.defaultdict(set))
for s in set(g.subjects(RDF.type, None)):
    if not isinstance(s, URIRef):
        continue
    nm = g.value(s, ONTO.name) or g.value(s, ONTO.cnLabel)
    if not nm:
        continue
    for o in g.objects(s, RDF.type):
        cn = str(o).split("#")[-1]
        by_class[cn][str(nm)].add(str(s))
total_dup = 0
for cn, names in sorted(by_class.items()):
    extra = sum(len(u) - 1 for u in names.values() if len(u) > 1)
    if extra:
        total_dup += extra
        print(f"      {cn:<14} 多余实体 {extra}")
print(f"  同类同名多余实体合计 = {total_dup}（Scholar 同名不等于同一人，属正常）")

# ---- 2) Company 精确重复是否都已建 sameAs ----
print("\n[2] Company 精确重复的 sameAs 覆盖率")
sig = collections.defaultdict(list)
for s in g.subjects(RDF.type, ONTO.Company):
    nm, rev, emp = g.value(s, ONTO.name), g.value(s, ONTO.revenue), g.value(s, ONTO.employees)
    if nm and rev is not None and emp is not None:
        sig[(str(nm), str(rev), str(emp))].append(s)
need = sum(len(v) - 1 for v in sig.values() if len(v) > 1)
have = len(list(g.triples((None, ONTO.sameAs, None))))
add(f"Company 精确重复 {need} 组已全部建 sameAs", have >= need, f"需要 {need}，实有 {have}")

# ---- 3) sameAs 在查询中被折叠 ----
print("\n[3] sameAs 别名折叠")
aliases = pt._alias_uris()
add("别名集合非空", len(aliases) > 0, f"{len(aliases)} 个别名")
for kw in ("比亚迪", "工商银行", "上海机场"):
    hits = pt.search_cross_class(kw)
    add(f"search_cross_class('{kw}') 只返回 1 条", len(hits) == 1, f"实得 {len(hits)} 条")

# ---- 4) 空关键词不再倒全表 ----
print("\n[4] 空关键词防护")
r = pt.find_entity("Scholar", "")
add("find_entity 空关键词返回 0 条并给出 error", r.get("total") == 0 and r.get("error"), r.get("error", ""))

# ---- 5) 总量未因等价链接而虚增 ----
print("\n[5] 计数一致性")
add("实体总数仍为 13934", len(set(g.subjects())) == 13934, len(set(g.subjects())))
add("三元组 = 78971 + sameAs", len(g) == 78971 + have, f"{len(g)} vs {78971 + have}")

ok = sum(1 for c in checks if c)
print(f"\n结果：PASS={ok}  FAIL={len(checks)-ok}")
sys.exit(0 if ok == len(checks) else 1)

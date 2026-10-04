# -*- coding: utf-8 -*-
"""
构建平台实例：加载本体 -> 接入数据源 -> 本体性转化 -> 统一语义图
输出：统一图 (data/processed/platform_graph.ttl) + 转化统计
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aiplatform.core import PlatformOntology, SourceCatalog, UnifiedGraph, ONTO, RDF

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(BASE, "data", "processed")
RAW = os.path.join(BASE, "data", "raw")

def link_duplicates(graph):
    """同一实体从两个数据源各进一次时，用 sameAs 建等价链接。

    背景：companies.csv（30 家）与 companies_v2.csv（44 家）内容大面积重叠，
    两套 ID 前缀（c_ / c2_）让同一家公司进了图谱两次——27 组、名称与营收与员工数三项全同。
    本体里本来就定义了 sameAs，但从未使用。

    策略：只加 sameAs 边，**不删实体、不改任何已有计数**；
    判定条件是「名称 + 营收 + 员工数三项全同」，缺数值一律不判（宁可漏，不可错）。
    Institution 的同名（如 Ministry of Education 分属 KR/NZ/CL）是不同实体，故不处理。
    Scholar 同名不等于同一人，坚决不处理。
    """
    g = graph.g
    buckets = {}
    for s in g.subjects(RDF.type, ONTO.Company):
        nm = g.value(s, ONTO.name)
        rev = g.value(s, ONTO.revenue)
        emp = g.value(s, ONTO.employees)
        if not nm or rev is None or emp is None:
            continue
        buckets.setdefault((str(nm), str(rev), str(emp)), []).append(s)
    n = 0
    for sig, uris in buckets.items():
        if len(uris) < 2:
            continue
        uris = sorted(set(uris), key=lambda u: (len(str(u)), str(u)))  # 短的当规范实体
        canonical = uris[0]
        for alias in uris[1:]:
            g.add((canonical, ONTO.sameAs, alias))
            n += 1
    return n


def build():
    onto = PlatformOntology(os.path.join(BASE, "ontology", "platform.owl"))
    print("== 本体加载 ==", file=sys.stderr)
    print("  类:", onto.classes, file=sys.stderr)
    print("  对象属性:", onto.object_props, file=sys.stderr)
    print("  数据属性:", onto.data_props, file=sys.stderr)

    cat = SourceCatalog()
    # 数据源注册表（来源文件 -> source_id）
    sources = {
        "ds_institutions": ("学术机构", os.path.join(PROC, "institutions.csv")),
        "ds_scholars": ("学者", os.path.join(PROC, "scholars.csv")),
        "ds_publications": ("论文", os.path.join(PROC, "publications.csv")),
        "ds_venues": ("期刊/会议", os.path.join(PROC, "venues.csv")),
        "ds_fields": ("研究领域", os.path.join(PROC, "fields.csv")),
        "ds_affiliation": ("学者-机构归属", os.path.join(PROC, "affiliation.csv")),
        "ds_author_of": ("学者-论文署名", os.path.join(PROC, "author_of.csv")),
        "ds_published_in": ("论文-期刊", os.path.join(PROC, "published_in.csv")),
        "ds_belongs_to_field": ("论文-领域", os.path.join(PROC, "belongs_to_field.csv")),
        "ds_companies": ("上市公司", os.path.join(RAW, "companies.csv")),
        "ds_institution_labels": ("机构中文标签", os.path.join(RAW, "institution_labels.csv")),
        "ds_scholar_labels": ("学者中文标签", os.path.join(RAW, "scholar_labels.csv")),
        # ===== 扩充数据 v2（多机构 / 多领域 / 2213 论文 / 44 企业 / 数据集）=====
        "ds_v2_inst": ("12 所高校", os.path.join(RAW, "v2_inst_clean.json")),
        "ds_v2_fields": ("12 个研究领域", os.path.join(RAW, "v2_fields_clean.json")),
        "ds_v2_works": ("扩充论文库", os.path.join(RAW, "v2_works_clean.json")),
        "ds_v2_scholars": ("扩充学者（论文作者）", os.path.join(RAW, "v2_works_clean.json")),
        "ds_v2_author_of": ("扩充学者-论文署名", os.path.join(RAW, "v2_works_clean.json")),
        "ds_v2_datasets": ("开放数据集", os.path.join(RAW, "datasets.csv")),
        "ds_companies_v2": ("44 家上市公司", os.path.join(RAW, "companies_v2.csv")),
            }

    mappings = json.load(open(os.path.join(BASE, "mappings.json"), encoding="utf-8"))

    graph = UnifiedGraph(onto)
    print("\n== 数据接入 + 本体性转化 ==", file=sys.stderr)
    total_triples = 0
    for sid, (name, loc) in sources.items():
        cat.register(sid, name, loc)
        res = graph.ingest(cat, mappings.get(sid, []), sid)
        total_triples += res["triples_added"]
        print(f"  {name}: {res['rows']} 行 -> {res['triples_added']} 三元组", file=sys.stderr)

    print(f"\n== 统一语义图 ==", file=sys.stderr)
    dup = link_duplicates(graph)
    if dup:
        print(f"已建立 sameAs 等价链接: {dup} 条", file=sys.stderr)
    stats = graph.stats()
    print(f"  总三元组: {stats['triples']}", file=sys.stderr)
    print(f"  总实体: {stats['entities']}", file=sys.stderr)
    for c, n in sorted(stats["classes"].items()):
        print(f"    {c}: {n}", file=sys.stderr)

    out = os.path.join(PROC, "platform_graph.ttl")
    graph.g.serialize(destination=out, format="turtle")
    print(f"\n统一图已保存: {out}", file=sys.stderr)

    # 把上次通过界面上传的公共数据装回来（否则重启就丢）
    from aiplatform.upload import load_public_uploads
    restored = load_public_uploads(graph)
    if restored:
        print(f"已恢复公共上传数据: {restored} 条三元组", file=sys.stderr)
    return onto, cat, graph

if __name__ == "__main__":
    build()

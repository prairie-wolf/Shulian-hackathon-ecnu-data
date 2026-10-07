# -*- coding: utf-8 -*-
"""
构建平台实例：加载本体 -> 接入数据源 -> 本体性转化 -> 统一语义图
输出：统一图 (data/processed/platform_graph.ttl) + 转化统计
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aiplatform.core import PlatformOntology, SourceCatalog, UnifiedGraph
from aiplatform.public_state import PublicUploadStore
from aiplatform.entity_equivalence import link_company_equivalences


def _log(*args):
    print(*args, file=sys.stderr)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(BASE, "data", "processed")
RAW = os.path.join(BASE, "data", "raw")

def build():
    onto = PlatformOntology(os.path.join(BASE, "ontology", "platform.owl"))
    _log("== 本体加载 ==")
    _log("  类:", onto.classes)
    _log("  对象属性:", onto.object_props)
    _log("  数据属性:", onto.data_props)

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

    with open(os.path.join(BASE, "mappings.json"), encoding="utf-8") as stream:
        mappings = json.load(stream)

    graph = UnifiedGraph(onto)
    _log("\n== 数据接入 + 本体性转化 ==")
    total_triples = 0
    for sid, (name, loc) in sources.items():
        cat.register(sid, name, loc)
        res = graph.ingest(cat, mappings.get(sid, []), sid)
        total_triples += res["triples_added"]
        _log(f"  {name}: {res['rows']} 行 -> {res['triples_added']} 三元组")

    link_company_equivalences(graph.g)
    store = PublicUploadStore(graph, cat, os.path.join(PROC, "upload_state.json"))

    _log(f"\n== 统一语义图 ==")
    stats = graph.stats()
    _log(f"  总三元组: {stats['triples']}")
    _log(f"  总实体: {stats['entities']}")
    for c, n in sorted(stats["classes"].items()):
        _log(f"    {c}: {n}")

    out = os.path.join(PROC, "platform_graph.ttl")
    store.export(out)
    _log(f"\n统一图已保存: {out}")
    return onto, cat, graph

if __name__ == "__main__":
    build()

# -*- coding: utf-8 -*-
"""
上传编排：任何文件 -> 摄取 -> 自动推断映射 -> 本体性转化 -> 进统一图 -> AI 立即可查
"""
import hashlib
import os, sys, json, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aiplatform.documents import ingest_any, detect_kind
from aiplatform.auto_map import infer_mapping
from aiplatform.core import SemanticMapper, RES, ONTO, RDF, Literal, XSD

UPSTREAM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "raw", "uploads")
os.makedirs(UPSTREAM_DIR, exist_ok=True)

# 公共上传的持久化文件。
# 修：以前未登录上传只写内存，服务一重启数据就没了（会丢数据）。
# 现在把「本次上传新增的三元组」落盘，build() 时再装回统一图。
PUBLIC_TTL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "processed", "public_uploads.ttl")


def public_snapshot(graph):
    """上传前拍一张快照，用于事后 diff 出本次新增的三元组"""
    try:
        return set(graph.g)
    except Exception:
        return set()


def persist_public(graph, before):
    """把 graph 中比 before 多出来的三元组，追加进公共上传持久化文件。返回新增条数"""
    from rdflib import Graph as RDFGraph
    try:
        new = [t for t in graph.g if t not in before]
    except Exception:
        return 0
    if not new:
        return 0
    store = RDFGraph()
    if os.path.exists(PUBLIC_TTL):
        try:
            store.parse(PUBLIC_TTL, format="turtle")
        except Exception:
            pass
    for t in new:
        store.add(t)
    store.serialize(destination=PUBLIC_TTL, format="turtle")
    return len(new)


def load_public_uploads(graph):
    """启动时把上次持久化的公共上传数据装回统一图。返回装回的条数"""
    if not os.path.exists(PUBLIC_TTL):
        return 0
    try:
        before = len(graph.g)
        with open(PUBLIC_TTL, "rb") as f:
            graph.g.parse(f, format="turtle")
        return len(graph.g) - before
    except Exception:
        return 0


def _rows_from_sheet(columns, rows):
    return [dict(zip(columns, r)) for r in rows]


def _stable_id(text, mod=100000):
    """Stable identifier across Python processes (built-in hash() is salted)."""
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:10]
    return int(digest, 16) % mod


def _make_unique(rows, key_col):
    """若主键重复或为空，生成唯一键"""
    seen = {}
    fixed = []
    for i, r in enumerate(rows):
        v = r.get(key_col)
        if v is None or str(v).strip() == "" or str(v) in seen:
            v = f"auto{i+1}"
        seen[str(v)] = True
        rr = dict(r); rr[key_col] = v
        fixed.append(rr)
    return fixed


def ingest_file(graph, catalog, path, filename=None, source_id=None, register=True):
    """
    上传一个文件，完成全流程。返回结果报告。
    """
    filename = filename or os.path.basename(path)
    kind = detect_kind(filename)
    report = {"filename": filename, "kind": kind, "steps": []}

    if kind is None:
        report["error"] = f"不支持的类型：{os.path.splitext(filename)[1]}"
        return report

    # 1) 摄取
    ing = ingest_any(path, filename)
    report["ingest"] = {k: v for k, v in ing.items() if k not in ("sheets",)}
    report["steps"].append({"step": "① 文档摄取", "kind": kind})

    # 2) 找到可转化的表格
    sheets = ing.get("sheets") or {}
    if not sheets and ing.get("tables"):
        # 文档里的表格
        sheets = {}
        for i, t in enumerate(ing["tables"]):
            sheets[f"table{i+1}"] = (t["header"], t["rows"])

    sid = source_id or f"ds_upload_{_stable_id(filename, 10000)}"

    if not sheets:
        # 没有表格：把文档本身登记成一个实体（文档类）
        if register:
            spec = catalog.register(sid, f"上传:{filename}", path)
        doc_uri = RES[f"doc_{_stable_id(filename)}"]
        graph.g.add((doc_uri, RDF.type, ONTO.Dataset))
        graph.g.add((doc_uri, ONTO.name, Literal(filename)))
        graph.g.add((doc_uri, ONTO.description,
                     Literal((ing.get("text") or ing.get("ocr_text") or "")[:1500])))
        graph.g.add((doc_uri, ONTO.sourcedFrom, RES["ds_" + sid]))
        report["steps"].append({"step": "② 登记为文档实体", "entity": str(doc_uri).split('/')[-1],
                                "chars": ing.get("chars", 0)})
        report["mode"] = "document"
        report["report"] = {"class": "Dataset", "entities": 1, "triples": 4}
        return report

    # 3) 逐表自动映射 + 转化
    total_entities, total_triples, map_summaries = 0, 0, []
    for sheet_name, (columns, rows) in sheets.items():
        if not columns or not rows:
            continue
        dict_rows = _rows_from_sheet(columns, rows)
        inferred = infer_mapping(filename, columns, dict_rows, sheet_name)
        mappings = inferred["mappings"]
        if not mappings:
            continue
        # 主键唯一化
        idcol = inferred["summary"]["id_column"]
        dict_rows = _make_unique(dict_rows, idcol)

        # 注册数据源（用于溯源）
        if register and sheet_name == list(sheets.keys())[0]:
            catalog.register(sid, f"上传:{filename}", path)

        mapper = SemanticMapper(graph.onto)
        added = 0
        for m in mappings:
            try:
                triples = mapper.apply(dict_rows, m, sid)
                for t in triples:
                    graph.g.add(t)
                added += len(triples)
            except Exception as e:
                report.setdefault("warnings", []).append(f"{m.get('class') or m.get('predicate')}: {e}")

        total_triples += added
        total_entities += len({str(t[0]) for t in []} or []) or 0
        map_summaries.append({"sheet": sheet_name, **inferred["summary"], "triples": added})
        report["steps"].append({"step": f"② 自动语义映射 ({sheet_name})",
                                "class": inferred["summary"]["inferred_class"],
                                "properties": inferred["summary"]["properties"],
                                "relations": inferred["summary"]["relations"]})

    report["mode"] = "tabular"
    report["report"] = {"tables": len(map_summaries), "triples": total_triples,
                        "mappings": map_summaries}
    report["steps"].append({"step": "③ 本体性转化完成", "triples": total_triples})

    # 4) 让新数据可被通用语义查询到（图已更新，无需额外动作）
    report["steps"].append({"step": "④ AI 可查（已进统一图）", "ok": True})
    return report


if __name__ == "__main__":
    from aiplatform.build_platform import build
    onto, cat, graph = build()
    # 造一个测试文件
    import pandas as pd
    p = os.path.join(UPSTREAM_DIR, "test_projects.csv")
    pd.DataFrame({"project_id": ["P1", "P2"], "项目名称": ["数据平台建设", "知识图谱构建"],
                  "领域": ["知识图谱", "大模型"], "预算": [100.5, 200.0]}).to_csv(p, index=False, encoding="utf-8-sig")
    r = ingest_file(graph, cat, p, "test_projects.csv")
    print(json.dumps(r, ensure_ascii=False, indent=1))

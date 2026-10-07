# -*- coding: utf-8 -*-
"""
上传编排：任何文件 -> 摄取 -> 自动推断映射 -> 本体性转化 -> 进统一图 -> AI 立即可查
"""
import hashlib
import os, sys, json, shutil
import uuid
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aiplatform.documents import SUPPORTED, ingest_any, detect_kind
from aiplatform.auto_map import infer_mapping
from aiplatform.core import SemanticMapper, RES, ONTO, RDF, Literal, XSD

UPSTREAM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "raw", "uploads")
os.makedirs(UPSTREAM_DIR, exist_ok=True)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_STATE_LOCK = threading.Lock()


def _source_state(g):
    """Runtime provenance for uploads; existing public triples are protected."""
    with _STATE_LOCK:
        if not hasattr(g, "_upload_sources"):
            g._upload_sources = {
                "lock": threading.RLock(), "sources": {},
                "owners": {}, "preexisting": set(),
            }
        return g._upload_sources


def _add_source_triples(g, source_id, triples):
    state = _source_state(g)
    with state["lock"]:
        before = len(g)
        contribution = state["sources"].setdefault(source_id, set())
        for triple in triples:
            if triple not in state["owners"]:
                if triple in g:
                    state["preexisting"].add(triple)
                state["owners"][triple] = set()
            state["owners"][triple].add(source_id)
            contribution.add(triple)
            g.add(triple)
        return len(g) - before


def remove_uploaded_source(graph, catalog, source_id):
    """Withdraw one upload's contribution, preserving all other sources."""
    if not source_id.startswith("ds_upload_"):
        raise ValueError("只能删除上传的公共数据源")
    store = getattr(graph, "_public_store", None)
    if store is not None:
        return store.mutate(lambda working, cat: remove_uploaded_source(working, cat, source_id),
                            source_to_remove=source_id)
    state = _source_state(graph.g)
    with state["lock"]:
        source = catalog.sources.get(source_id)
        if source is None or source_id not in state["sources"]:
            raise ValueError("缺少该上传的三元组溯源记录，无法安全删除")
        location = os.path.realpath(source["location"])
        upload_root = os.path.realpath(UPSTREAM_DIR)
        try:
            inside = os.path.commonpath([upload_root, location]) == upload_root
        except ValueError:
            inside = False
        if not inside or location == upload_root:
            raise ValueError("上传原件路径不在上传目录内，已取消删除")
        # A filesystem failure must leave the graph and catalog intact.
        if os.path.exists(location):
            os.remove(location)
        removed = 0
        for triple in state["sources"].pop(source_id):
            owners = state["owners"][triple]
            owners.remove(source_id)
            if not owners:
                del state["owners"][triple]
                if triple not in state["preexisting"]:
                    removed += int(triple in graph.g)
                    graph.g.remove(triple)
                state["preexisting"].discard(triple)
        catalog.remove(source_id)
        return removed


def safe_upload_name(filename):
    """返回不含目录片段的显示文件名，拒绝空名称和控制字符。"""
    name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    if not name or name in {".", ".."} or "\x00" in name:
        raise ValueError("上传文件名无效")
    return name


def save_upload_bytes(filename, data, root=None):
    """
    使用服务端随机文件名保存上传内容，返回 (存储路径, 原始显示名)。

    上传内容永远写入上传根目录；原始文件名只用于展示、类型识别和映射。
    """
    display_name = safe_upload_name(filename)
    ext = os.path.splitext(display_name)[1].lower()
    if ext not in SUPPORTED:
        raise ValueError(f"不支持的文件类型：{ext or '无扩展名'}")
    if not data:
        raise ValueError("上传文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件过大：最大允许 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    upload_root = os.path.realpath(root or UPSTREAM_DIR)
    os.makedirs(upload_root, exist_ok=True)
    stored_path = os.path.realpath(os.path.join(upload_root, uuid.uuid4().hex + ext))
    try:
        inside_root = os.path.commonpath([upload_root, stored_path]) == upload_root
    except ValueError:
        inside_root = False
    if not inside_root:
        raise ValueError("上传目标越界")

    with open(stored_path, "xb") as fh:
        fh.write(data)
    return stored_path, display_name


def _rows_from_sheet(columns, rows):
    return [dict(zip(columns, r)) for r in rows]


def _stable_id(text, mod=100000):
    """Stable identifier across Python processes (built-in hash() is salted)."""
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:10]
    return int(digest, 16) % mod


def _make_unique(rows, key_col):
    """若主键重复或为空，生成唯一键"""
    seen = set()
    reserved = {str(r.get(key_col)) for r in rows
                if r.get(key_col) is not None and str(r.get(key_col)).strip()}
    fixed = []
    for i, r in enumerate(rows):
        v = r.get(key_col)
        if v is None or str(v).strip() == "" or str(v) in seen:
            v = f"auto{i+1}"
            while v in seen or v in reserved:
                v += "_"
        seen.add(str(v))
        rr = dict(r); rr[key_col] = v
        fixed.append(rr)
    return fixed


def ingest_file(graph, catalog, path, filename=None, source_id=None, register=True,
                allow_generic=True, skip_empty=True, max_rows=5000,
                include_unmapped=False):
    store = getattr(graph, "_public_store", None)
    if store is not None:
        if not register or (source_id is not None and not source_id.startswith("ds_upload_")):
            raise ValueError("公共持久化上传必须登记独立的 ds_upload_ 来源")
        return store.mutate(lambda working, cat: ingest_file(
            working, cat, path, filename, source_id, register,
            allow_generic, skip_empty, max_rows, include_unmapped))
    # Keep registration, all sheets, and deletion in one critical section.
    with _source_state(graph.g)["lock"]:
        return _ingest_file(graph, catalog, path, filename, source_id, register,
                            allow_generic, skip_empty, max_rows, include_unmapped)


def _ingest_file(graph, catalog, path, filename=None, source_id=None, register=True,
                 allow_generic=True, skip_empty=True, max_rows=5000,
                 include_unmapped=False):
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

    if ing.get("error") and not ing.get("sheets") and not ing.get("tables") and not ing.get("text"):
        report["error"] = f"解析失败：{ing['error']}"
        report["mode"] = "failed"
        return report

    # 2) 找到可转化的表格
    sheets = ing.get("sheets") or {}
    if not sheets and ing.get("tables"):
        # 文档里的表格
        sheets = {}
        for i, t in enumerate(ing["tables"]):
            sheets[f"table{i+1}"] = (t["header"], t["rows"])

    sid = source_id or f"ds_upload_{uuid.uuid4().hex}"
    if register and sid in catalog.sources:
        raise ValueError("数据源标识已存在，请为每次上传使用独立标识")
    report["source_id"] = sid

    if not sheets:
        # 没有表格：把文档本身登记成一个实体（文档类）
        if register:
            spec = catalog.register(sid, f"上传:{filename}", path)
        doc_uri = RES[f"doc_{sid}"]
        added = _add_source_triples(graph.g, sid, [
            (doc_uri, RDF.type, ONTO.Dataset),
            (doc_uri, ONTO.name, Literal(filename)),
            (doc_uri, ONTO.description,
             Literal((ing.get("text") or ing.get("ocr_text") or "")[:1500])),
            (doc_uri, ONTO.sourcedFrom, RES["ds_" + sid]),
        ])
        report["steps"].append({"step": "② 登记为文档实体", "entity": str(doc_uri).split('/')[-1],
                                "chars": ing.get("chars", 0)})
        report["mode"] = "document"
        report["report"] = {"class": "Dataset", "entities": 1, "triples": added}
        return report

    # 3) 逐表自动映射 + 转化
    total_triples, map_summaries = 0, []
    all_entity_ids = set()
    rows_in_total, rows_used_total = 0, 0
    filtered_tables = []
    registered = False
    for sheet_name, (columns, rows) in sheets.items():
        if not columns or not rows:
            continue
        dict_rows = _rows_from_sheet(columns, rows)
        rows_in_total += len(dict_rows)
        if skip_empty:
            dict_rows = [row for row in dict_rows
                         if any(value is not None and str(value).strip() for value in row.values())]
        if not dict_rows:
            filtered_tables.append({"sheet": sheet_name, "reason": "没有有效数据行"})
            continue
        inferred = infer_mapping(filename, columns, dict_rows, sheet_name,
                                 include_unmapped=include_unmapped)
        mappings = inferred["mappings"]
        if not mappings:
            continue
        if inferred["summary"]["inferred_class"] == "GenericRecord" and not allow_generic:
            filtered_tables.append({"sheet": sheet_name, "reason": "未识别到本体类别，已按设置跳过 GenericRecord"})
            continue
        # 主键唯一化
        idcol = inferred["summary"]["id_column"]
        if skip_empty:
            dict_rows = [row for row in dict_rows
                         if row.get(idcol) is not None and str(row[idcol]).strip()]
        if max_rows:
            remaining = max(0, max_rows - rows_used_total)
            report["truncated_rows"] = report.get("truncated_rows", 0) + max(0, len(dict_rows) - remaining)
            dict_rows = dict_rows[:remaining]
        if not dict_rows:
            filtered_tables.append({"sheet": sheet_name, "reason": "空主键已过滤或文件行数配额已用完"})
            continue
        dict_rows = _make_unique(dict_rows, idcol)

        mapper = SemanticMapper(graph.onto)
        added = 0
        entity_ids = set()
        table_triples = set()
        for m in mappings:
            try:
                triples = mapper.apply(dict_rows, m, sid)
                table_triples.update(triples)
                entity_ids.update(str(t[0]) for t in triples if t[1] == RDF.type)
            except Exception as e:
                report.setdefault("warnings", []).append(f"{m.get('class') or m.get('predicate')}: {e}")

        if not table_triples:
            filtered_tables.append({"sheet": sheet_name, "reason": "没有生成可写入的三元组"})
            continue
        if register and not registered:
            catalog.register(sid, f"上传:{filename}", path)
            registered = True
        added = _add_source_triples(graph.g, sid, table_triples)
        rows_used_total += len(dict_rows)

        total_triples += added
        all_entity_ids.update(entity_ids)
        map_summaries.append({"sheet": sheet_name, **inferred["summary"],
                              "rows": len(dict_rows), "triples": added})
        report["steps"].append({"step": f"② 自动语义映射 ({sheet_name})",
                                "class": inferred["summary"]["inferred_class"],
                                "properties": inferred["summary"]["properties"],
                                "relations": inferred["summary"]["relations"]})

    if not map_summaries:
        reason = "未识别到可写入本体的字段或类别"
        if filtered_tables:
            reason = "；".join(f"{x['sheet']}：{x['reason']}" for x in filtered_tables[:3])
        report["error"] = f"没有数据被写入：{reason}"
        report["mode"] = "filtered"
        return report

    report["mode"] = "tabular"
    report["report"] = {"tables": len(map_summaries), "triples": total_triples,
                        "graph_delta": total_triples, "entities": len(all_entity_ids),
                        "mappings": map_summaries}
    report["quality"] = {
        "rows_in": rows_in_total,
        "rows_used": rows_used_total,
        "rows_filtered": rows_in_total - rows_used_total,
        "rows_truncated": report.get("truncated_rows", 0),
        "include_unmapped_fields": include_unmapped,
        "allow_generic_record": allow_generic,
        "filtered_tables": filtered_tables,
    }
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

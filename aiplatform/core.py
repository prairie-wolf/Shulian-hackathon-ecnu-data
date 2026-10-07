# -*- coding: utf-8 -*-
"""
平台核心：数据接入 + 本体性转化 + 统一语义图
- PlatformOntology：加载平台本体，做类/属性的校验与枚举（本体的「语义契约」作用）
- SourceCatalog：数据源目录，注册任意 CSV/JSON/Parquet/SQL，自动检测 schema（接入其他数据）
- SemanticMapper：按语义映射把原始行「转化」成本体三元组（本体性转化）
- UnifiedGraph：统一 RDF 图，多源融合 + 溯源(sourcedFrom) + SPARQL
"""
import os, json, csv
from pathlib import Path
import duckdb
from rdflib import Graph, Namespace, URIRef, Literal, RDF, XSD, BNode

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONTO_NS = "http://ecnu.edu.cn/ontology/platform#"
RES_NS = "http://ecnu.edu.cn/resource/"
ONTO = Namespace(ONTO_NS)
RES = Namespace(RES_NS)
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
OWL = Namespace("http://www.w3.org/2002/07/owl#")


def _sql_path(path):
    """Escape single quotes before embedding a filesystem path in DuckDB SQL."""
    return str(path).replace("'", "''")


class PlatformOntology:
    """平台本体的运行时视图：校验映射、枚举类/属性（本体驱动一切）"""
    def __init__(self, owl_path):
        self.g = Graph()
        with open(owl_path, "rb") as f:
            self.g.parse(f, format="xml")
        self.classes = self._collect(OWL.Class)
        self.object_props = self._collect(OWL.ObjectProperty)
        self.data_props = self._collect(OWL.DatatypeProperty)

    def _collect(self, typ):
        out = set()
        for s in self.g.subjects(RDF.type, typ):
            if str(s).startswith(ONTO_NS):
                out.add(str(s).split('#')[-1])
        return out

    def class_uri(self, local): return URIRef(ONTO_NS + local)
    def prop_uri(self, local): return URIRef(ONTO_NS + local)

    def has_class(self, local): return local in self.classes
    def has_prop(self, local): return local in self.object_props or local in self.data_props

    def describe(self):
        return {"classes": sorted(self.classes),
                "object_properties": sorted(self.object_props),
                "data_properties": sorted(self.data_props)}


class SourceCatalog:
    """数据源目录：注册数据源 + 自动 schema 检测（平台可接入任意数据）"""
    def __init__(self):
        self.sources = {}  # source_id -> {name, kind, location, schema, rows}

    def register(self, source_id, name, location, kind=None):
        """注册一个数据源，自动检测 schema 与行数（支持 csv/json/parquet/xlsx 及文档）"""
        kind = kind or self._detect_kind(location)
        schema, nrows = [], 0
        try:
            if kind in ("csv", "json", "parquet"):
                con = duckdb.connect()
                reader = {"csv": "read_csv_auto", "json": "read_json_auto", "parquet": "read_parquet"}[kind]
                con.execute(f"CREATE TABLE t AS SELECT * FROM {reader}('{_sql_path(location)}')")
                schema = con.execute("DESCRIBE t").fetchall()
                nrows = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
                con.close()
            elif kind in ("xlsx", "xls"):
                import pandas as pd
                df = pd.read_excel(location)
                schema = [(c, str(t)) for c, t in zip(df.columns, df.dtypes)]
                nrows = len(df)
            else:
                # 文档类（docx/pdf/图片/txt）：登记元数据，行数按 1 计
                nrows = 1
                schema = [("document", "text")]
        except Exception as e:
            # 任何解析失败都不阻断注册，登记为通用文档源
            schema, nrows = [("unparsed", str(type(e).__name__))], 1
        self.sources[source_id] = {
            "source_id": source_id, "name": name, "kind": kind,
            "location": location, "rows": nrows,
            "schema": [{"column": s[0], "type": str(s[1])} for s in schema],
        }
        return self.sources[source_id]

    def _detect_kind(self, location):
        ext = os.path.splitext(location)[1].lower().lstrip('.')
        return {"csv": "csv", "json": "json", "parquet": "parquet",
                "xlsx": "xlsx", "xls": "xls"}.get(ext, "document")

    def list(self):
        """列出全部数据源（供控制台展示）"""
        return list(self.sources.values())

    def read(self, source_id):
        """按行读取数据源（用于转化）"""
        loc = self.sources[source_id]["location"]
        kind = self.sources[source_id]["kind"]
        con = duckdb.connect()
        con.execute(f"CREATE TABLE t AS SELECT * FROM read_csv_auto('{_sql_path(loc)}')" if kind == "csv"
                    else f"CREATE TABLE t AS SELECT * FROM read_json_auto('{_sql_path(loc)}')" if kind == "json"
                    else f"CREATE TABLE t AS SELECT * FROM read_parquet('{_sql_path(loc)}')")
        cols = [c[0] for c in con.execute("DESCRIBE t").fetchall()]
        rows = con.execute("SELECT * FROM t").fetchall()
        con.close()
        return [dict(zip(cols, r)) for r in rows]

    def list(self):
        return list(self.sources.values())

    def remove(self, source_id):
        """Remove a runtime source registration."""
        return self.sources.pop(source_id, None)


class SemanticMapper:
    """本体性转化引擎：把原始数据行，按语义映射，转化成本体三元组"""
    def __init__(self, ontology):
        self.onto = ontology

    def _fmt(self, template, value):
        return template.format(id=self.slug(value))

    @staticmethod
    def slug(value):
        """把任意值转成合法 URI 片段（中文/空格/特殊字符都安全）"""
        s = str(value).strip()
        if not s:
            return s
        try:
            from urllib.parse import quote
            # 保留 ASCII 字母数字/常见符号，其余（含中文、空格）编码
            out = quote(s, safe="")
            return out
        except Exception:
            return s

    def validate(self, mapping):
        """校验映射：类与属性必须存在于本体"""
        errors = []
        cls = mapping.get("class")
        if cls and not self.onto.has_class(cls):
            errors.append(f"类不存在: {cls}")
        for p in mapping.get("properties", []):
            if not self.onto.has_prop(p["property"]):
                errors.append(f"属性不存在: {p['property']}")
        for r in mapping.get("relations", []):
            if not self.onto.has_prop(r["predicate"]):
                errors.append(f"关系属性不存在: {r['predicate']}")
            if r.get("target_class") and not self.onto.has_class(r["target_class"]):
                errors.append(f"目标类不存在: {r['target_class']}")
        return errors

    def transform_entity(self, rows, mapping, source_id):
        """实体映射：rows -> 某类实体 + 数据属性三元组（支持多值列）"""
        cls = self.onto.class_uri(mapping["class"])
        id_col = mapping["id_column"]
        id_tpl = mapping["id_template"]
        multi = mapping.get("multi_value", False) or mapping.get("subject_multi_value", False)
        triples = []
        for row in rows:
            ids = self._as_list(row.get(id_col))
            if not ids:
                continue
            labels = self._as_list(row.get(mapping.get("label_column"))) if mapping.get("label_column") else []
            for k, raw_id in enumerate(ids):
                raw_id = str(raw_id).strip()
                if not raw_id:
                    continue
                ent = RES[self._fmt(id_tpl, raw_id)]
                triples.append((ent, RDF.type, cls))
                triples.append((ent, ONTO.sourcedFrom, RES["ds_" + source_id]))
                if k < len(labels) and labels[k]:
                    triples.append((ent, ONTO.name, Literal(str(labels[k]))))
                for pm in mapping.get("properties", []):
                    v = row.get(pm["column"])
                    if v is None or v == "":
                        continue
                    if isinstance(v, list):
                        if k >= len(v):
                            continue
                        v = v[k]
                    pred = self.onto.prop_uri(pm["property"])
                    dt = pm.get("datatype")
                    if dt == "integer":
                        try: v = int(float(v))
                        except: continue
                        triples.append((ent, pred, Literal(v, datatype=XSD.integer)))
                    elif dt == "float":
                        try: v = float(v)
                        except: continue
                        triples.append((ent, pred, Literal(v, datatype=XSD.decimal)))
                    else:
                        triples.append((ent, pred, Literal(str(v))))
        return triples

    @staticmethod
    def _as_list(v):
        """把单元格值规整成列表（支持 list / JSON 字符串 / 逗号分隔）"""
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return [x for x in v if x is not None and str(x).strip() != ""]
        s = str(v).strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                import json as _json
                arr = _json.loads(s)
                if isinstance(arr, list):
                    return [x for x in arr if x is not None and str(x).strip() != ""]
            except Exception:
                pass
        if ";" in s:
            return [x.strip() for x in s.split(";") if x.strip()]
        return [s]

    def transform_relation(self, rows, mapping, source_id):
        """关系映射：rows -> (subject, predicate, object) 三元组（支持多值 subject/object）"""
        s_cls = self.onto.class_uri(mapping["subject_class"])
        o_cls = self.onto.class_uri(mapping["object_class"])
        s_idc = mapping["subject_id_column"]; s_tpl = mapping["subject_id_template"]
        pred = self.onto.prop_uri(mapping["predicate"])
        o_idc = mapping["object_id_column"]; o_tpl = mapping["object_id_template"]
        from_value = mapping.get("object_id_from_value", False)
        s_multi = mapping.get("subject_multi_value", False)
        o_multi = mapping.get("object_from_list", False)
        triples = []
        for row in rows:
            svals = self._as_list(row.get(s_idc)) if s_multi else [row.get(s_idc)]
            s_list = []
            for sv in svals:
                if sv is None or str(sv).strip() == "":
                    continue
                s_list.append(RES[self._fmt(s_tpl, str(sv).strip())])
            if not s_list:
                continue
            ov = row.get(o_idc)
            ovals = self._as_list(ov) if (o_multi or isinstance(ov, list)) else [ov]
            for s in s_list:
                for oval in ovals:
                    if oval is None or str(oval).strip() == "":
                        continue
                    if from_value:
                        o = RES[self._fmt(o_tpl, str(oval).strip())]
                        triples.append((o, RDF.type, o_cls))
                        triples.append((o, ONTO.name, Literal(str(oval).strip())))
                    else:
                        o = RES[self._fmt(o_tpl, str(oval).strip())]
                        triples.append((o, RDF.type, o_cls))
                    triples.append((s, pred, o))
        return triples

    def apply(self, rows, mapping, source_id):
        """应用映射（先校验），返回三元组 + 统计"""
        errs = self.validate(mapping)
        if errs:
            raise ValueError(f"映射校验失败: {errs}")
        kind = mapping.get("kind", "entity")
        if kind == "relation":
            return self.transform_relation(rows, mapping, source_id)
        return self.transform_entity(rows, mapping, source_id)


class UnifiedGraph:
    """统一语义图：多源融合 + 溯源 + SPARQL"""
    def __init__(self, ontology):
        self.g = Graph()
        self.g.bind("res", RES)
        self.g.bind("onto", ONTO)
        self.onto = ontology

    def ingest(self, catalog, mappings, source_id):
        """接入一个数据源：注册 -> 按映射转化 -> 灌入统一图"""
        src = catalog.sources[source_id]
        # 数据源实体（治理）
        ds = RES["ds_" + source_id]
        self.g.add((ds, RDF.type, ONTO.DataSource))
        self.g.add((ds, ONTO.name, Literal(src["name"])))
        self.g.add((ds, ONTO.description, Literal(f"{src['kind']} 源, {src['rows']} 行, {src['location']}")))
        total = 0
        mapper = SemanticMapper(self.onto)
        rows = catalog.read(source_id)
        for m in mappings:
            triples = mapper.apply(rows, m, source_id)
            for t in triples:
                self.g.add(t)
            total += len(triples)
        return {"source": src["name"], "rows": src["rows"], "triples_added": total}

    def query(self, sparql):
        return list(self.g.query(sparql))

    def stats(self):
        n_entities = len(set(self.g.subjects(RDF.type, None)))
        n_triples = len(self.g)
        classes = {}
        for s, _, o in self.g.triples((None, RDF.type, None)):
            if str(o).startswith(ONTO_NS):
                c = str(o).split('#')[-1]
                classes[c] = classes.get(c, 0) + 1
        return {"triples": n_triples, "entities": n_entities, "classes": classes}

# -*- coding: utf-8 -*-
"""
本体驱动的工具生成 + 自然语言语义查询
工具不是为某个数据集写死的——它们由「本体」自动生成，任何映射进本体的数据都可用。
这些工具会被 MCP server / REST / 本地智能体以同一套方式调用（AI 无关）。
"""
import json, re
from contextvars import ContextVar
from functools import wraps
from rdflib import RDF, URIRef, Literal, Namespace
from aiplatform.core import ONTO_NS, RES_NS, ONTO, RES, RDFS
from aiplatform.entity_equivalence import CompanyEquivalence


def _read_snapshot(method):
    @wraps(method)
    def read(self, *args, **kwargs):
        if self._view.get() is not None:
            return method(self, *args, **kwargs)
        store = getattr(self._graph, "_public_store", None)
        if store is not None:
            graph, sources = store.snapshot()
        else:
            graph = self.g
            sources = dict(self._catalog.sources) if self._catalog is not None else {}
        token = self._view.set((graph, sources, CompanyEquivalence(graph)))
        try:
            return method(self, *args, **kwargs)
        finally:
            self._view.reset(token)
    return read


class SPARQLReadOnlyError(Exception):
    """SPARQL 写操作被拒（只读平台）。上层应转 403 + problem+json。"""


PREFIXES = """
PREFIX onto: <http://ecnu.edu.cn/ontology/platform#>
PREFIX res: <http://ecnu.edu.cn/resource/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
"""


class PlatformTools:
    def __init__(self, graph, catalog=None):
        self._graph = graph
        self._catalog = catalog
        self._view = ContextVar("platform_tool_view", default=None)

    @property
    def g(self):
        view = self._view.get()
        if view is not None:
            return view[0]
        store = getattr(self._graph, "_public_store", None)
        if store is not None:
            store.refresh()
        return getattr(self._graph, "g", self._graph)

    # ---- 基础工具（本体驱动，通用）----
    @_read_snapshot
    def list_ontology(self):
        """列出平台本体的类、对象属性、数据属性"""
        onto_g = self.g  # 这里传的是数据图，本体类从数据图 type 推断
        classes = {}
        for s, _, o in self.g.triples((None, RDF.type, None)):
            if str(o).startswith(ONTO_NS):
                c = str(o).split('#')[-1]
                classes[c] = classes.get(c, 0) + 1
        raw_classes = dict(classes)
        if "Company" in classes:
            classes["Company"] = len(self._entities("Company"))
        return {"ontology_classes": classes, "raw_classes": raw_classes}

    @_read_snapshot
    def list_sources(self, catalog=None):
        """列出已接入的数据源"""
        self.g  # Refresh the graph and its catalog together.
        catalog = catalog if catalog is not None else self._catalog
        if catalog is None:
            return {"sources": []}
        sources = self._view.get()[1].values() if catalog is self._catalog else catalog.list()
        return {"sources": [{"id": s["source_id"], "name": s["name"],
                             "kind": s["kind"], "rows": s["rows"]} for s in sources]}

    def _entities(self, class_name):
        cls = URIRef(class_name if str(class_name).startswith("http") else ONTO_NS + class_name)
        entities = set(self.g.subjects(RDF.type, cls))
        if cls == ONTO.Company:
            view = self._equivalence()
            entities = {view.representative(e) for e in entities}
        return sorted(entities, key=str)

    def _properties(self, entity):
        return self._equivalence().predicate_objects(entity)

    def _equivalence(self):
        view = self._view.get()
        return view[2] if view is not None else CompanyEquivalence(self.g)

    def _entity_name(self, e):
        names = sorted(str(o) for p, o in self._properties(e) if p == ONTO.name)
        return names[0] if names else str(e).split('/')[-1]

    @_read_snapshot
    def explore_class(self, class_name, limit=5):
        """探索某类实体：实例数 + 样例"""
        cls = URIRef(class_name if str(class_name).startswith("http") else ONTO_NS + class_name)
        ents = self._entities(class_name)
        samples = [self._entity_name(e) for e in ents[:limit]]
        return {"class": class_name, "instances": len(ents),
                "samples": samples, "sample_count": len(samples),
                "raw_instances": len(set(self.g.subjects(RDF.type, cls)))}

    @_read_snapshot
    def find_entity(self, class_name, keyword, limit=10, offset=0):
        """Search canonical entities through all evidenced names, with stable pagination."""
        if limit < 1 or offset < 0:
            raise ValueError("limit 必须为正整数，offset 不能为负数")
        kw = (keyword or "").lower()
        out = []
        for e in self._entities(class_name):
            labels = [(p, str(o)) for p, o in self._properties(e) if p in (ONTO.name, ONTO.cnLabel)]
            if any(kw in value.lower() for _, value in labels):
                cn = sorted(value for p, value in labels if p == ONTO.cnLabel)
                out.append({"id": str(e).split('/')[-1], "name": self._entity_name(e),
                            "cnName": "、".join(cn)})
        page = out[offset:offset + limit]
        return {"class": class_name, "keyword": keyword, "matches": page,
                "total": len(out), "offset": offset, "limit": limit,
                "has_more": offset + len(page) < len(out)}

    @_read_snapshot
    def search_cross_class(self, keyword, limit=20):
        """跨类检索：在全部分类的 name/cnLabel 中模糊匹配，返回带类别与 id。"""
        kw = (keyword or "").lower()
        out, seen = [], set()
        view = self._equivalence()
        for e in sorted({view.representative(e) for e in self.g.subjects(RDF.type, None)}, key=str):
            if e in seen:
                continue
            nm = self._entity_name(e)
            labels = [(p, str(o)) for p, o in self._properties(e) if p in (ONTO.name, ONTO.cnLabel)]
            cn = "、".join(sorted(v for p, v in labels if p == ONTO.cnLabel))
            hit = any(kw in v.lower() for _, v in labels)
            if not hit:
                continue
            seen.add(e)
            cls = None
            for _, _, o in self.g.triples((e, RDF.type, None)):
                if str(o).startswith(ONTO_NS):
                    cls = str(o).split('#')[-1]
                    break
            out.append({"id": str(e).split('/')[-1], "name": nm,
                        "cnName": str(cn) if cn is not None else "",
                        "class": cls or ""})
            if len(out) >= limit:
                break
        return out

    @_read_snapshot
    def entity_detail(self, entity_id):
        """实体的全部属性与关系"""
        e = RES[entity_id]
        props, rels = [], []
        for p, o in self._properties(e):
            pl = str(p).split('#')[-1]
            if isinstance(o, Literal):
                props.append({"property": pl, "value": str(o)})
            elif str(o).startswith(RES_NS):
                rels.append({"relation": pl, "target": self._entity_name(o), "target_id": str(o).split('/')[-1]})
        return {"entity": entity_id, "name": self._entity_name(e), "properties": props, "relations": rels}

    @_read_snapshot
    def query_relation(self, subject_class, relation, object_class, limit=20):
        """查询某类关系 (subject_class -relation-> object_class)"""
        s_cls = URIRef(ONTO_NS + subject_class)
        o_cls = URIRef(ONTO_NS + object_class)
        pred = URIRef(ONTO_NS + relation)
        out = []
        view = self._equivalence()
        pairs = {(view.representative(s), view.representative(o)) for s, o in self.g.subject_objects(pred)}
        for s, o in sorted(pairs, key=lambda pair: tuple(map(str, pair))):
            if (s, RDF.type, s_cls) in self.g and (o, RDF.type, o_cls) in self.g:
                out.append({"subject": self._entity_name(s), "object": self._entity_name(o)})
        return {"relation": f"{subject_class} -{relation}-> {object_class}", "count": len(out), "pairs": out[:limit]}

    @_read_snapshot
    def sparql(self, query):
        """执行原始 SPARQL（高级入口）。只读白名单：拒绝写操作（INSERT/DELETE/DROP/LOAD/CLEAR）。
        拒绝时抛 SPARQLReadOnlyError（上层转 403 problem+json）——不能用 200 承载错误。"""
        from rdflib.plugins.sparql.parser import parseQuery, parseUpdate
        try:
            parseQuery(PREFIXES + query)
        except Exception as query_error:
            try:
                parsed = parseUpdate(PREFIXES + query)
                if not parsed.get("request"):
                    raise ValueError("不是 SPARQL 更新")
            except Exception:
                raise ValueError("无效的 SPARQL 查询") from query_error
            raise SPARQLReadOnlyError("拒绝：SPARQL 只读，不允许更新数据。")
        try:
            result = self.g.query(PREFIXES + query)
            if result.type == "ASK":
                return {"boolean": bool(result), "count": 1}
            if result.type in ("CONSTRUCT", "DESCRIBE"):
                triples = [[str(n) for n in t] for t in result.graph]
                return {"columns": ["subject", "predicate", "object"], "rows": triples, "count": len(triples)}
            rows = list(result)
            if not rows:
                return {"rows": [], "count": 0}
            vars_ = [str(v) for v in rows[0].labels] if rows[0].labels else []
            if vars_:
                data = [[str(r[v]) if r[v] is not None else "" for v in vars_] for r in rows]
            else:
                data = [["result"] for _ in rows]
            return {"columns": vars_, "rows": data, "count": len(data)}
        except Exception as e:
            return {"error": str(e)}

    # ---- 语义查询（自然语言 -> 工具，供智能体调用）----
    @_read_snapshot
    def semantic_ask(self, question, llm_parse=None):
        """自然语言问数入口。走通用语义查询引擎（本体驱动，非写死关键词）。
        llm_parse 可选：外部 LLM 提供的解析结果（当前版本直接走引擎）"""
        from aiplatform.semantic import GenericSemanticQuery
        q = question
        if "数据源" in q and ("哪些" in q or "列出" in q):
            sources = [s["name"] for s in self.list_sources()["sources"]]
            return {"intent": "list_sources",
                    "sources": sources, "count": len(sources)}
        if "本体" in q and ("哪些" in q or "列出" in q or "结构" in q):
            return self.list_ontology()
        return GenericSemanticQuery(self.g).ask(question)

    def _all_industry_names(self):
        out = []
        for s in self.g.subjects(RDF.type, ONTO.Industry):
            v = self.g.value(s, ONTO.name)
            if v:
                out.append(str(v))
        return out

    def _resolve_field_uri(self, term):
        """本体消歧：自然语言词 -> 领域实体 URI（按中英文标签匹配）"""
        t = (term or "").lower()
        for s in self.g.subjects(RDF.type, ONTO.Field):
            nm = self.g.value(s, ONTO.name)
            cn = self.g.value(s, ONTO.cnLabel)
            if (nm and t in str(nm).lower()) or (cn and t in str(cn).lower()):
                return s
        return None

    def _companies_in_industry(self, q):
        ind = None
        for name in self._all_industry_names():
            if name in q:
                ind = name
                break
        if not ind:
            return {"error": "请说明行业，例如'哪些公司在人工智能行业'",
                    "available_industries": self._all_industry_names()}
        fq = f"""
        SELECT ?name ?revenue ?employees WHERE {{
          ?c a onto:Company ; onto:name ?name ; onto:belongsToIndustry ?i .
          ?i onto:name ?iname . FILTER(CONTAINS(LCASE(?iname), LCASE("{ind}")))
          OPTIONAL {{ ?c onto:revenue ?revenue }}
          OPTIONAL {{ ?c onto:employees ?employees }}
        }} ORDER BY DESC(?revenue) LIMIT 20
        """
        rows = self.sparql(fq)
        if "error" in rows:
            return rows
        return {"intent": "companies_in_industry", "industry": ind,
                "companies": [{"name": r[0], "revenue_b": r[1], "employees": r[2]} for r in rows["rows"]]}

    def _scholars_query(self, q):
        inst = None
        for k in ["华东师大", "华东师范大学", "上海交大", "上海交通大学", "复旦", "清华大学", "浙大", "浙江大学"]:
            if k in q:
                inst = k; break
        field = None
        for f in ["知识图谱", "深度学习", "数据挖掘", "大语言模型", "可信数据空间", "语言模型", "knowledge graph", "deep learning", "data mining"]:
            if f.lower() in q.lower():
                field = f; break
        where = "?s a onto:Scholar ; onto:authorOf ?p ; onto:name ?name ."
        if field:
            fu = self._resolve_field_uri(field)
            if fu:
                where += f" ?p onto:belongsToField <{fu}> ."
            else:
                where += f' ?p onto:belongsToField ?f . ?f onto:name ?fname . FILTER(CONTAINS(LCASE(?fname), LCASE("{field}"))).'
        if inst:
            where += (f' ?s onto:affiliatedWith ?i . ?i onto:name ?iname .'
                      f' OPTIONAL {{ ?i onto:cnLabel ?icn }} .'
                      f' FILTER(CONTAINS(LCASE(?iname), LCASE("{inst}")) || (BOUND(?icn) && CONTAINS(LCASE(?icn), LCASE("{inst}")))).')
        rows = self.sparql(f"SELECT DISTINCT ?name WHERE {{ {where} }} LIMIT 15")
        if "error" in rows:
            return rows
        return {"intent": "scholars", "institution": inst, "field": field,
                "scholars": [r[0] for r in rows["rows"]]}

    def _field_ranking(self, q):
        field = None
        for f in ["知识图谱", "深度学习", "数据挖掘", "大语言模型", "可信数据空间"]:
            if f in q:
                field = f; break
        fu = self._resolve_field_uri(field) if field else None
        if not fu:
            return {"error": "请说明领域（知识图谱/深度学习/数据挖掘/大语言模型/可信数据空间）"}
        qq = f"""
        SELECT ?iname (COUNT(DISTINCT ?p) AS ?n) WHERE {{
          ?p a onto:Publication ; onto:belongsToField <{fu}> .
          ?s onto:authorOf ?p . ?s onto:affiliatedWith ?i . ?i onto:name ?iname .
        }} GROUP BY ?iname ORDER BY DESC(?n) LIMIT 10
        """
        rows = self.sparql(qq)
        if "error" in rows:
            return rows
        return {"intent": "field_ranking", "field": field,
                "ranking": [{"institution": r[0], "papers": r[1]} for r in rows["rows"]]}

    def _field_trend(self, q):
        field = None
        for f in ["知识图谱", "深度学习", "数据挖掘", "大语言模型", "可信数据空间"]:
            if f in q:
                field = f; break
        fu = self._resolve_field_uri(field) if field else None
        if not fu:
            return {"error": "请说明领域"}
        qq = f"""
        SELECT ?year (COUNT(DISTINCT ?p) AS ?n) WHERE {{
          ?p a onto:Publication ; onto:belongsToField <{fu}> .
          ?p onto:year ?year .
        }} GROUP BY ?year ORDER BY ASC(?year)
        """
        rows = self.sparql(qq)
        if "error" in rows:
            return rows
        return {"intent": "field_trend", "field": field,
                "trend": [{"year": r[0], "papers": r[1]} for r in rows["rows"]]}


# ============ 工具注册表（MCP / REST / 本地共用）============
def build_tool_registry(platform_tools, catalog):
    pt = platform_tools
    return {
        "list_ontology": {"desc": "列出平台本体覆盖的类及其实例数", "func": pt.list_ontology, "params": {}},
        "list_sources": {"desc": "列出平台已接入的数据源", "func": lambda: pt.list_sources(catalog), "params": {}},
        "explore_class": {"desc": "探索某类实体（实例数+样例）", "func": pt.explore_class,
                          "params": {"class_name": "本体类名"}},
        "find_entity": {"desc": "按名称搜索某类实体", "func": pt.find_entity,
                        "params": {"class_name": "本体类名", "keyword": "关键词"}},
        "entity_detail": {"desc": "查看实体的属性与关系", "func": pt.entity_detail,
                          "params": {"entity_id": "实体ID"}},
        "query_relation": {"desc": "查询某类关系", "func": pt.query_relation,
                           "params": {"subject_class": "主体类", "relation": "关系", "object_class": "客体类"}},
        "sparql": {"desc": "执行原始 SPARQL 查询", "func": pt.sparql, "params": {"query": "SPARQL"}},
        "semantic_ask": {"desc": "自然语言问数（覆盖学术+企业数据）", "func": pt.semantic_ask,
                         "params": {"question": "自然语言问题"}},
    }

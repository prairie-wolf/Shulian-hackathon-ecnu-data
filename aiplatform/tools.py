# -*- coding: utf-8 -*-
"""
本体驱动的工具生成 + 自然语言语义查询
工具不是为某个数据集写死的——它们由「本体」自动生成，任何映射进本体的数据都可用。
这些工具会被 MCP server / REST / 本地智能体以同一套方式调用（AI 无关）。
"""
import json, re
from rdflib import RDF, URIRef, Literal, Namespace
from aiplatform.core import ONTO_NS, RES_NS, ONTO, RES, RDFS


class SPARQLReadOnlyError(Exception):
    """SPARQL 写操作被拒（只读平台）。上层应转 403 + problem+json。"""


PREFIXES = """
PREFIX onto: <http://ecnu.edu.cn/ontology/platform#>
PREFIX res: <http://ecnu.edu.cn/resource/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
"""


class PlatformTools:
    def __init__(self, graph, catalog=None):
        # 归一化：调用方传的对象不一致——api_server 传 UnifiedGraph（真正的图在 .g），
        # gateway / mcp_server 传裸 rdflib Graph。UnifiedGraph 本身没有 triples/subjects/value，
        # 统一取出内层裸图，避免各方法在不同调用路径下随机 AttributeError。
        self.g = getattr(graph, "g", graph)
        self._catalog = catalog

    # ---- 基础工具（本体驱动，通用）----
    def list_ontology(self):
        """列出平台本体的类、对象属性、数据属性"""
        onto_g = self.g  # 这里传的是数据图，本体类从数据图 type 推断
        classes = {}
        for s, _, o in self.g.triples((None, RDF.type, None)):
            if str(o).startswith(ONTO_NS):
                c = str(o).split('#')[-1]
                classes[c] = classes.get(c, 0) + 1
        return {"ontology_classes": classes}

    def list_sources(self, catalog):
        """列出已接入的数据源"""
        return {"sources": [{"id": s["source_id"], "name": s["name"],
                             "kind": s["kind"], "rows": s["rows"]} for s in catalog.list()]}

    def _entity_name(self, e):
        v = self.g.value(e, ONTO.name)
        return str(v) if v else str(e).split('/')[-1]

    def explore_class(self, class_name, limit=5):
        """探索某类实体：实例数 + 样例"""
        cls = URIRef(ONTO_NS + class_name)
        ents = list(self.g.subjects(RDF.type, cls))
        samples = [self._entity_name(e) for e in ents[:limit]]
        return {"class": class_name, "instances": len(ents),
                "samples": samples, "sample_count": len(samples)}

    def _alias_uris(self):
        """被 sameAs 指向的等价实体。展示时隐藏别名、只留规范实体，
        避免同一家公司（来自两个数据源）在结果里出现两次。"""
        out = set()
        for _s, o in self.g.subject_objects(ONTO.sameAs):
            out.add(o)
        return out

    def find_entity(self, class_name, keyword, limit=10, offset=0):
            """按名称搜索某类实体（支持分页 offset）。同时匹配 name 与 cnLabel（中文标签），大小写不敏感。"""
            cls = URIRef(ONTO_NS + class_name) if not str(class_name).startswith("http") else URIRef(class_name)
            kw = (keyword or "").lower()
            # 空关键词会匹配全部实体，等于把整类倒出来 —— 明确报错，别装作查到了
            if not kw.strip():
                return {"class": class_name, "keyword": keyword, "matches": [],
                        "total": 0, "offset": offset, "limit": limit, "has_more": False,
                        "error": "keyword 不能为空：本工具按名称搜索，空关键词会匹配全部实体。"}
            aliases = self._alias_uris()
            out = []
            for e in self.g.subjects(RDF.type, cls):
                if e in aliases:          # sameAs 的别名不重复展示
                    continue
                nm = self._entity_name(e)
                cn = self.g.value(e, ONTO.cnLabel)
                hit = kw in nm.lower()
                if not hit and cn is not None:
                    hit = kw in str(cn).lower()
                if hit:
                    out.append({"id": str(e).split('/')[-1], "name": nm,
                                "cnName": str(cn) if cn is not None else ""})
            page = out[offset:offset + limit]
            return {"class": class_name, "keyword": keyword, "matches": page,
                    "total": len(out), "offset": offset, "limit": limit,
                    "has_more": (offset + len(page)) < len(out)}

    def search_cross_class(self, keyword, limit=20):
        """跨类检索：在全部分类的 name/cnLabel 中模糊匹配，返回带类别与 id。"""
        kw = (keyword or "").lower()
        if not kw.strip():
            return []
        aliases = self._alias_uris()      # sameAs 的别名不重复展示
        out, seen = [], set()
        for e in self.g.subjects(RDF.type, None):
            if e in seen or e in aliases:
                continue
            nm = self._entity_name(e)
            cn = self.g.value(e, ONTO.cnLabel)
            hit = kw in nm.lower() or (cn is not None and kw in str(cn).lower())
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

    def entity_detail(self, entity_id):
        """实体的全部属性与关系"""
        e = RES[entity_id]
        props, rels = [], []
        for p, o in self.g.predicate_objects(e):
            pl = str(p).split('#')[-1]
            if isinstance(o, Literal):
                props.append({"property": pl, "value": str(o)})
            elif str(o).startswith(RES_NS):
                rels.append({"relation": pl, "target": self._entity_name(o), "target_id": str(o).split('/')[-1]})
        return {"entity": entity_id, "name": self._entity_name(e), "properties": props, "relations": rels}

    def query_relation(self, subject_class, relation, object_class, limit=20):
        """查询某类关系 (subject_class -relation-> object_class)"""
        s_cls = URIRef(ONTO_NS + subject_class)
        o_cls = URIRef(ONTO_NS + object_class)
        pred = URIRef(ONTO_NS + relation)
        out = []
        for s, o in self.g.subject_objects(pred):
            if (s, RDF.type, s_cls) in self.g and (o, RDF.type, o_cls) in self.g:
                out.append({"subject": self._entity_name(s), "object": self._entity_name(o)})
        return {"relation": f"{subject_class} -{relation}-> {object_class}", "count": len(out), "pairs": out[:limit]}

    def sparql(self, query):
        """执行原始 SPARQL（高级入口）。只读白名单：拒绝写操作（INSERT/DELETE/DROP/LOAD/CLEAR）。
        拒绝时抛 SPARQLReadOnlyError（上层转 403 problem+json）——不能用 200 承载错误。"""
        banned = ["insert", "delete", "drop ", "load ", "clear ", "create ", "copy ", "move "]
        ql = (" " + query + " ").lower()
        for b in banned:
            if b in ("delete", "drop ", "load ", "clear ", "create ", "copy ", "move "):
                if (" " + b.rstrip() in ql) or (b.strip() in ql and "\n" + b.strip() in ql):
                    raise SPARQLReadOnlyError(f"拒绝：SPARQL 只读，不允许写操作（{b.strip()}）。")
            elif b in ql:
                raise SPARQLReadOnlyError("拒绝：SPARQL 只读，不允许 INSERT。")
        try:
            rows = list(self.g.query(PREFIXES + query))
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
    def semantic_ask(self, question, llm_parse=None):
        """自然语言问数入口。走通用语义查询引擎（本体驱动，非写死关键词）。
        llm_parse 可选：外部 LLM 提供的解析结果（当前版本直接走引擎）"""
        from aiplatform.semantic import GenericSemanticQuery
        q = question
        if "数据源" in q and ("哪些" in q or "列出" in q):
            return {"intent": "list_sources",
                    "sources": [s["name"] for s in self._catalog.list()] if getattr(self, "_catalog", None) else []}
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

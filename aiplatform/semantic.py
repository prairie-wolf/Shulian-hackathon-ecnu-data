# -*- coding: utf-8 -*-
"""
通用语义查询引擎 v2 —— 升级点：
1. 结果带「完整属性 + 关系」，不再只返回一串名字（学者 → 论文数/机构/领域）
2. 支持「趋势 / 逐年 / 排行 / 分布」等聚合问法（之前落 overview 兜底）
3. 关键词检索覆盖全类（论文标题、公司名、领域名、机构名），不再只匹配 name
4. 意图路由增强：实体 + 属性组合、时间维度、比较类问句

设计：本体驱动（类/属性/关系全从 OWL 读），任何映射进本体的数据都自动可查。
"""
import re
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdflib import RDF, URIRef, Literal
from aiplatform.core import ONTO_NS, RES_NS, ONTO, RES


class GenericSemanticQuery:
    """本体驱动的通用查询器"""

    def __init__(self, graph):
        self.g = graph

    # ---------- 本体自省 ----------
    def classes(self):
        out = {}
        for s, _, o in self.g.triples((None, RDF.type, None)):
            n = str(o).split("#")[-1]
            if not n.startswith("http"):
                out[n] = out.get(n, 0) + 1
        # 只保留本体类
        return {k: v for k, v in sorted(out.items(), key=lambda x: -x[1])}

    def _all_entities(self, class_name=None):
        if class_name:
            cls = URIRef(ONTO_NS + class_name)
            return list(self.g.subjects(RDF.type, cls))
        out = []
        for s, _, o in self.g.triples((None, RDF.type, None)):
            n = str(o).split("#")[-1]
            if not n.startswith("http") and not n.startswith("owl"):
                out.append(s)
        return list(set(out))

    def _label(self, e):
        for p in (ONTO.name, ONTO.cnLabel):
            v = self.g.value(e, p)
            if v:
                return str(v)
        return str(e).split("/")[-1]

    def _class_of(self, e):
        for _, _, o in self.g.triples((e, RDF.type, None)):
            n = str(o).split("#")[-1]
            if not n.startswith("http") and not n.startswith("owl"):
                return n
        return "Unknown"

    def _props_of(self, ent):
        """实体的全部数据属性（带人类可读标签）"""
        LABELS = {
            "name": "名称", "cnLabel": "中文名", "year": "年份", "doi": "DOI",
            "cited_by_count": "被引次数", "citedByCount": "被引次数",
            "country": "国家", "region": "地区", "level": "层级",
            "revenue": "营收(亿元)", "employees": "员工数",
            "worksCount": "论文总数", "hIndex": "h指数",
            "publishedAt": "发表期刊", "size": "规模", "license": "许可",
            "url": "链接", "description": "描述", "openalex_id": "OpenAlex ID",
        }
        d = {}
        for p, o in self.g.predicate_objects(ent):
            pn = str(p).split("#")[-1]
            if pn in ("name",) and str(o) == self._label(ent):
                continue
            if pn in LABELS and not str(pn).startswith("http"):
                d[LABELS[pn]] = str(o)
        return d

    # ---------- 匹配：找问题里提到的实体 ----------
    def find_mentioned_entities(self, question):
        from aiplatform.entity_resolution import mentioned_entities
        return [(u, label) for u, label in mentioned_entities(self.g, question)
                if self._class_of(u) != "Unknown"]

    def find_mentioned_classes(self, question):
        """查找问题里提到的本体类（中英文）"""
        CN = {"学者": "Scholar", "老师": "Scholar", "作者": "Scholar", "教授": "Scholar",
              "论文": "Publication", "文献": "Publication", "文章": "Publication",
              "机构": "Institution", "学校": "Institution", "大学": "Institution",
              "公司": "Company", "企业": "Company", "厂商": "Company",
              "行业": "Industry", "产业": "Industry",
              "领域": "Field", "方向": "Field", "学科": "Field",
              "期刊": "Venue", "会议": "Venue",
              "数据集": "Dataset", "资料集": "Dataset", "数据源": "DataSource"}
        found = []
        for cn, en in CN.items():
            if cn in question and en not in found:
                found.append(en)
        return found

    # ---------- 关系遍历 ----------
    def related_to(self, ent):
        """出边关系（带关系中文名）"""
        REL = {"authorOf": "发表论文", "affiliatedWith": "所属机构",
               "belongsToField": "所属领域", "belongsToIndustry": "所属行业",
               "publishedIn": "发表于", "sourcedFrom": "数据来源",
               "coAuthorWith": "合作者", "cites": "引用"}
        out = []
        for p, o in self.g.predicate_objects(ent):
            pn = str(p).split("#")[-1]
            if pn in REL and not isinstance(o, Literal):
                out.append({"relation": REL[pn], "raw": pn, "target": o,
                            "target_name": self._label(o), "target_class": self._class_of(o)})
        return out

    def reverse_related(self, ent, relation):
        """入边关系"""
        pred = URIRef(ONTO_NS + relation) if not str(relation).startswith("http") else relation
        out = []
        for s in self.g.subjects(pred, ent):
            out.append({"id": str(s).split("/")[-1], "name": self._label(s),
                        "uri": s, "class": self._class_of(s)})
        return out

    # ---------- 聚合 ----------
    def count_by_relation(self, subject_class, relation, object_class):
        pred = URIRef(ONTO_NS + relation)
        sc = URIRef(ONTO_NS + subject_class)
        counter = {}
        for s in self.g.subjects(RDF.type, sc):
            for o in self.g.objects(s, pred):
                if (o, RDF.type, URIRef(ONTO_NS + object_class)) in self.g:
                    n = self._label(o)
                    counter[n] = counter.get(n, 0) + 1
        ranked = sorted(counter.items(), key=lambda x: -x[1])
        return [{"name": k, "count": v} for k, v in ranked]

    def numeric_summary(self, class_name, prop, top=20):
        """数值属性汇总排序（按名称去重）"""
        cls = URIRef(ONTO_NS + class_name)
        best = {}
        for e in self.g.subjects(RDF.type, cls):
            v = self.g.value(e, URIRef(ONTO_NS + prop))
            if v is None:
                continue
            try:
                val = float(v)
            except (TypeError, ValueError):
                continue
            name = self._label(e)
            if name not in best or val > best[name]:
                best[name] = val
        rows = [{"name": n, "value": v} for n, v in best.items()]
        rows.sort(key=lambda x: -x["value"])
        return rows[:top]

    def _field_matches(self, fld, term):
        """领域实体的名字是否匹配（同时看 name 与 cnLabel，避免中英/编码不一致）"""
        if fld is None:
            return False
        for p in (ONTO.name, ONTO.cnLabel):
            v = self.g.value(fld, p)
            if v and term in str(v):
                return True
        # 退一步：URI 解码后匹配
        try:
            from urllib.parse import unquote
            if term in unquote(str(fld)):
                return True
        except Exception:
            pass
        return False

    def year_distribution(self, field_name=None, class_name="Publication"):
        """按年份统计（趋势）"""
        cls = URIRef(ONTO_NS + class_name)
        counter = {}
        for e in self.g.subjects(RDF.type, cls):
            if field_name:
                flds = list(self.g.objects(e, ONTO.belongsToField))
                if not any(self._field_matches(f, field_name) for f in flds):
                    continue
            y = self.g.value(e, ONTO.year)
            if y is None:
                continue
            try:
                y = int(str(y))
            except ValueError:
                continue
            counter[y] = counter.get(y, 0) + 1
        return [{"name": str(y), "count": counter[y]} for y in sorted(counter)]

    def entities_of_class(self, class_name, limit=50):
        cls = URIRef(ONTO_NS + class_name)
        ents = list(self.g.subjects(RDF.type, cls))
        return [{"id": str(e).split("/")[-1], "name": self._label(e)} for e in ents[:limit]], len(ents)

    # ---------- 通用检索 ----------
    def search_entities(self, keyword, limit=30):
        """按关键词检索任意实体的名称（全类，含中文标签）"""
        out, seen = [], set()
        for s in self.g.subjects(RDF.type, None):
            if s in seen:
                continue
            for p, o in self.g.predicate_objects(s):
                pn = str(p).split("#")[-1]
                if pn in ("name", "cnLabel") and isinstance(o, Literal) and keyword in str(o):
                    cls = self._class_of(s)
                    if cls != "Unknown":
                        seen.add(s)
                        out.append({"name": str(o), "class": cls, "uri": s})
                    break
        return out[:limit]

    def search_publications(self, keyword, limit=20):
        """论文标题模糊检索"""
        out = []
        for s in self.g.subjects(RDF.type, URIRef(ONTO_NS + "Publication")):
            t = self.g.value(s, ONTO.name)
            if t and keyword.lower() in str(t).lower():
                out.append({"name": str(t), "uri": s})
        return out[:limit]

    # ---------- 智能入口 ----------
    def ask(self, question):
        """通用自然语言问数"""
        question = question.strip()
        if re.fullmatch(r"(你好|您好|嗨|hello|hi|谢谢|感谢)[！!。？?\s]*", question, re.IGNORECASE):
            return {"intent": "greeting", "hint": "你好，可以查询图谱中的实体、论文、关联学者或趋势。"}
        if any(word in question for word in ("生日", "出生", "年龄", "电话", "邮箱", "住址", "校长", "毕业时间", "毕业年份")):
            return {"intent": "unsupported", "question": question,
                    "hint": "图谱未收录此字段，无法据此回答。"}
        LISTING = ("有哪些", "有什么", "列出", "所有", "全部", "清单", "列表")
        is_listing = any(k in question for k in LISTING)
        cls_list = self.find_mentioned_classes(question)
        ents = self.find_mentioned_entities(question)

        if not ents and any(word in question for word in ("是谁", "介绍", "详情", "校友", "毕业生")):
            return {"intent": "not_found", "question": question, "count": 0, "data": [],
                    "hint": "未找到指定实体，请提供名称或标识。"}

        # 1) 趋势 / 逐年 / 分布
        if any(k in question for k in ("趋势", "逐年", "每年", "增长", "分布", "按年")):
            # 先看是否指定了领域
            fname = None
            for u, l in ents:
                if self._class_of(u) == "Field":
                    fname = l
                    break
            if not fname:
                for c in ["大语言模型", "可信数据空间", "知识图谱", "深度学习", "数据挖掘",
                          "联邦学习", "图神经网络", "语义网", "数据治理", "供应链管理", "物流优化"]:
                    if c in question:
                        fname = c
                        break
            data = self.year_distribution(fname)
            return {"intent": "trend", "field": fname or "全部论文", "data": data,
                    "total": sum(x["count"] for x in data)}

        # 7) 某机构 + 某领域 的学者（返回带论文数的完整信息，而不是一串名字）
        if any(k in question for k in ("学者", "老师", "教授", "校友", "毕业生", "教师", "成员")) and ents:
            inst_uri = next((u for u, l in ents if self._class_of(u) == "Institution"), None)
            field_uri = next((u for u, l in ents if self._class_of(u) == "Field"), None)
            if inst_uri or field_uri:
                # 收集学者 URI（保留 URI 才能取论文数/中文名）
                if inst_uri:
                    uris = {m["uri"] for m in self.reverse_related(inst_uri, "affiliatedWith")}
                else:
                    uris = set()
                if field_uri:
                    works = list(self.g.subjects(ONTO.belongsToField, field_uri))
                    furi = set()
                    for w in works:
                        for a in self.g.subjects(ONTO.authorOf, w):
                            furi.add(a)
                    uris = uris & furi if inst_uri else furi

                rows = []
                seen_names = set()
                for u in uris:
                    n_works = len(list(self.g.objects(u, ONTO.authorOf)))
                    cn = self.g.value(u, ONTO.cnLabel)
                    en = self.g.value(u, ONTO.name)
                    nm = str(cn) if cn else str(en)
                    if nm in seen_names:  # 去重（同一学者多 URI 别名）
                        continue
                    seen_names.add(nm)
                    insts = [self._label(i) for i in self.g.objects(u, ONTO.affiliatedWith)]
                    rows.append({
                        "name": nm,
                        "en_name": str(en) if en else "",
                        "papers": n_works,
                        "institution": (insts[0] if insts else ""),
                    })
                rows.sort(key=lambda x: -x["papers"])
                top = rows[:30]
                return {"intent": "scholars_filtered",
                        "scope": (self._label(inst_uri) if inst_uri else "") +
                                 ((" · " + self._label(field_uri)) if field_uri else ""),
                        "count": len(rows), "scholars": [r["name"] for r in top],
                        "data": top, "returned": len(top), "has_more": len(rows) > len(top),
                        "relation": "affiliatedWith" if inst_uri else "authorOf",
                        "hint": "这里只列出平台记录的关联学者，机构关联不代表毕业关系。" if any(w in question for w in ("校友", "毕业生")) else ""}

        # 2) 实体详情（列举型问句不走这里）
        if (not is_listing) and ents and any(
                k in question for k in ("谁", "什么", "详情", "介绍", "研究", "合作", "相关", "多少")):
            uri, label = ents[0]
            cls = self._class_of(uri)
            props = self._props_of(uri)
            rels = self.related_to(uri)
            fwd = {}
            for r in rels:
                fwd.setdefault(r["relation"], []).append(r["target_name"])
            limits = {}
            for k, v in fwd.items():
                limits[k] = v[:20]
                if len(v) > 20:
                    limits[k + "（总数）"] = len(v)
            return {"intent": "entity_detail", "entity": label, "class": cls,
                    "properties": props, "relations": limits}

        # 3) 排名 / 最多 / 最高
        if any(k in question for k in ("排名", "最多", "最高", "排行", "最大", "top", "Top")):
            if "营收" in question or "收入" in question:
                return {"intent": "numeric_rank", "label": "营收(亿元)",
                        "data": self.numeric_summary("Company", "revenue")}
            if "员工" in question or "人数" in question:
                return {"intent": "numeric_rank", "label": "员工数",
                        "data": self.numeric_summary("Company", "employees")}
            if "引用" in question or "被引" in question:
                return {"intent": "numeric_rank", "label": "被引次数",
                        "data": self.numeric_summary("Publication", "cited_by_count")}
            if "h指数" in question or "h_index" in question:
                return {"intent": "numeric_rank", "label": "h指数",
                        "data": self.numeric_summary("Institution", "hIndex")}
            for rc in ("Institution", "Company", "Field"):
                if rc in cls_list:
                    d = (self.count_by_relation("Scholar", "affiliatedWith", "Institution")
                         if rc == "Institution" else
                         self.count_by_relation("Company", "belongsToIndustry", "Industry")
                         if rc == "Company" else
                         self.count_by_relation("Publication", "belongsToField", "Field"))
                    return {"intent": "relation_rank", "subject": rc, "data": d}
            # 默认机构论文排名
            return {"intent": "relation_rank", "subject": "Institution",
                    "data": self.count_by_relation("Scholar", "affiliatedWith", "Institution")}

        # 4) 机构学者（带论文数）
        if "机构" in question and any(k in question for k in ("学者", "老师", "谁", "教授")):
            for uri, label in ents:
                if self._class_of(uri) == "Institution":
                    members = self.reverse_related(uri, "affiliatedWith")
                    rows = []
                    seen_names = set()
                    for m in members:
                        n_works = len(list(self.g.objects(m["uri"], ONTO.authorOf)))
                        cn = self.g.value(m["uri"], ONTO.cnLabel)
                        nm = str(cn) if cn else m["name"]
                        if nm in seen_names:  # 去重（同一学者多 URI 别名）
                            continue
                        seen_names.add(nm)
                        rows.append({"name": nm,
                                     "en_name": m["name"], "papers": n_works})
                    rows.sort(key=lambda x: -x["papers"])
                    return {"intent": "institution_members", "institution": label,
                            "count": len(members), "scholars": [r["name"] for r in rows[:30]],
                            "data": rows[:30]}

# 5) 行业公司
        if ("行业" in question or "产业" in question) and ("公司" in question or "企业" in question):
            for uri, label in ents:
                if self._class_of(uri) == "Industry":
                    members = self.reverse_related(uri, "belongsToIndustry")
                    seen, rows = set(), []
                    for m in members:
                        if m["name"] in seen:
                            continue
                        seen.add(m["name"])
                        rev = self.g.value(m["uri"], ONTO.revenue)
                        emp = self.g.value(m["uri"], ONTO.employees)
                        rows.append({"name": m["name"],
                                     "营收(亿元)": round(float(rev), 2) if rev else None,
                                     "员工数": int(float(emp)) if emp else None})
                    rows.sort(key=lambda x: -(x["营收(亿元)"] or 0))
                    return {"intent": "industry_companies", "industry": label,
                            "count": len(rows), "companies": rows}

        # 6) 领域下的实体
        if "领域" in question or ("研究" in question and "方向" in question):
            for uri, label in ents:
                if self._class_of(uri) == "Field":
                    members = self.reverse_related(uri, "belongsToField")
                    rows = [{"name": m["name"], "class": m["class"]} for m in members[:30]]
                    return {"intent": "field_entities", "field": label,
                            "count": len(members), "entities": [r["name"] for r in rows],
                            "data": rows}

        # 8) 论文标题关键词检索
        kw = self._extract_keyword(question)
        if kw and ("论文" in question or "文献" in question or "研究" in question):
            pubs = self.search_publications(kw, 25)
            if pubs:
                return {"intent": "search_publications", "keyword": kw,
                        "count": len(pubs), "entities": [p["name"] for p in pubs],
                        "data": pubs}

        # A scoped question must never degrade to a whole-class listing.
        generic = re.sub(r"有哪些|有什么|列出|所有|全部|清单|列表|多少|几个|哪些|平台|收录|请问|的|[？?。\s]", "", question)
        for word in ("学者", "老师", "教授", "作者", "论文", "文献", "文章", "机构", "学校", "大学", "公司", "企业", "厂商", "行业", "产业", "领域", "方向", "学科", "期刊", "会议", "数据集", "资料集", "数据源"):
            generic = generic.replace(word, "")
        if cls_list and generic and not ents:
            return {"intent": "not_found", "question": question, "count": 0,
                    "data": [], "hint": "未找到问句中的实体或范围，请提供名称或标识。"}

        # 9) 按类列举
        if cls_list:
            c = cls_list[0]
            items, total = self.entities_of_class(c, limit=25)
            return {"intent": "list_class", "class": c, "count": total,
                    "samples": items, "returned": len(items), "has_more": total > len(items)}

        # 10) 关键词模糊匹配（全类）
        if kw:
            hits = self.search_entities(kw, limit=30)
            if hits:
                return {"intent": "search_entities", "keyword": kw, "count": len(hits),
                        "entities": hits}

        # 11) 兜底：概览
        return {"intent": "overview", "classes": self.classes(),
                "hint": "可以问：华东师大有哪些学者 / 知识图谱领域有哪些论文 / "
                        "大语言模型趋势 / 人工智能行业有哪些公司 / 营收最高的公司"}

    # ---------- 辅助 ----------
    def _extract_keyword(self, question):
        q = question
        for w in ("有哪些", "有什么", "哪些", "是什么", "多少", "请问", "的", "？", "?", "。",
                  "列出", "告诉我", "帮我", "查一下", "查查", "相关", "关于", "研究"):
            q = q.replace(w, " ")
        parts = [p.strip() for p in re.split(r"[\s,，、]+", q) if len(p.strip()) >= 2]
        return parts[0] if parts else None


if __name__ == "__main__":
    from aiplatform.build_platform import build
    import json
    onto, cat, graph = build()
    q = GenericSemanticQuery(graph.g)
    tests = [
        "华东师范大学有哪些学者？", "大语言模型趋势", "知识图谱领域有哪些论文？",
        "人工智能行业有哪些公司？", "营收最高的公司", "有哪些数据集？",
        "哪个机构论文最多？", "供应链管理趋势", "兰曼是谁",
    ]
    print("\n" + "=" * 70)
    for t in tests:
        r = q.ask(t)
        print(f"\nQ: {t}\n  intent={r.get('intent')}")
        print("  " + json.dumps(r, ensure_ascii=False)[:260])

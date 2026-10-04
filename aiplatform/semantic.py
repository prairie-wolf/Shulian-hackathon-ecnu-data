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

    # 中文机构名 → 图谱里的英文名。
    # 背景：960 所机构中只有 12 所有人工中文标签（data/raw/institution_labels.csv），
    # 其余 948 所只有英文名。用户用中文校名提问时实体完全认不出来，语义层会退化成
    # 「列出全部 960 所机构」。这里补一张常见高校别名表兜底。
    CN_ALIASES = {
        "清华大学": "Tsinghua University", "北京大学": "Peking University",
        "浙江大学": "Zhejiang University", "复旦大学": "Fudan University",
        "上海交通大学": "Shanghai Jiao Tong University",
        "华东师范大学": "East China Normal University", "南京大学": "Nanjing University",
        "中国科学技术大学": "University of Science and Technology of China",
        "华中科技大学": "Huazhong University of Science and Technology",
        "武汉大学": "Wuhan University", "中山大学": "Sun Yat-sen University",
        "西安交通大学": "Xi'an Jiaotong University",
        "哈尔滨工业大学": "Harbin Institute of Technology",
        "北京理工大学": "Beijing Institute of Technology",
        "北京航空航天大学": "Beihang University", "同济大学": "Tongji University",
        "天津大学": "Tianjin University", "南开大学": "Nankai University",
        "四川大学": "Sichuan University", "山东大学": "Shandong University",
        "厦门大学": "Xiamen University", "东南大学": "Southeast University",
        "吉林大学": "Jilin University", "大连理工大学": "Dalian University of Technology",
        "华南理工大学": "South China University of Technology",
        "湖南大学": "Hunan University", "中南大学": "Central South University",
        "重庆大学": "Chongqing University", "兰州大学": "Lanzhou University",
        "东北大学": "Northeastern University",
        "西北工业大学": "Northwestern Polytechnical University",
        "电子科技大学": "University of Electronic Science and Technology of China",
        "北京邮电大学": "Beijing University of Posts and Telecommunications",
        "北京师范大学": "Beijing Normal University",
        "中国人民大学": "Renmin University of China", "上海大学": "Shanghai University",
        "上海海事大学": "Shanghai Maritime University", "深圳大学": "Shenzhen University",
        "苏州大学": "Soochow University", "郑州大学": "Zhengzhou University",
        "香港大学": "University of Hong Kong",
        "香港中文大学": "Chinese University of Hong Kong",
        "香港科技大学": "Hong Kong University of Science and Technology",
        "香港理工大学": "Hong Kong Polytechnic University",
        "新加坡国立大学": "National University of Singapore",
        "南洋理工大学": "Nanyang Technological University",
        "麻省理工学院": "Massachusetts Institute of Technology",
        "斯坦福大学": "Stanford University", "哈佛大学": "Harvard University",
        "牛津大学": "University of Oxford", "剑桥大学": "University of Cambridge",
        "中国科学院": "Chinese Academy of Sciences",
        "中国社会科学院": "Chinese Academy of Social Sciences",
    }

    # 本体类的中文说法（find_mentioned_classes 使用）
    CN_CLASSES = {
        "学者": "Scholar", "老师": "Scholar", "作者": "Scholar", "教授": "Scholar",
        "论文": "Publication", "文献": "Publication", "文章": "Publication",
        "机构": "Institution", "学校": "Institution", "大学": "Institution",
        "公司": "Company", "企业": "Company", "厂商": "Company",
        "行业": "Industry", "产业": "Industry",
        "领域": "Field", "方向": "Field", "学科": "Field",
        "期刊": "Venue", "会议": "Venue",
        "数据集": "Dataset", "资料集": "Dataset", "数据源": "DataSource",
    }

    # 表达「问某人/某群体」的词：出现它们时，"某机构的X是谁"应该列出人，
    # 而不是返回机构自身的属性
    PEOPLE_WORDS = ("校友", "学者", "老师", "教授", "教师", "成员", "毕业生")

    def _name_index(self):
        """英文名 → 实体 的缓存索引（避免逐个中文别名扫全图）"""
        idx = getattr(self, "_nidx", None)
        if idx is None:
            idx = {}
            for s, p, o in self.g.triples((None, ONTO.name, None)):
                idx.setdefault(str(o), s)
            self._nidx = idx
        return idx

    def _resolve_alias(self, en_name):
        """先精确匹配英文名，再退化为包含匹配（取最短者，避免误配更长名字）"""
        idx = self._name_index()
        if en_name in idx:
            return idx[en_name]
        low = en_name.lower()
        cands = [k for k in idx if low in k.lower()]
        return idx[min(cands, key=len)] if cands else None

    def _unresolved_subject(self, question):
        """问句里有没有「某个具体名字 + 的」结构，而这个名字我们没认出来。
        例：「北京理工大学的校友是谁」→ 返回「北京理工大学」。
        用于避免在实体没认出来时把整个类倒出来（问北理工却列出 960 所机构）。"""
        for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z0-9]{2,20})的", question):
            name = m.group(1)
            for w in self.CN_CLASSES:  # 剥掉"大学/机构/公司"这类类名后缀
                name = name.replace(w, "")
            if len(name) >= 2:
                return m.group(1)
        return None

    def _not_found(self, kw, class_name, items):
        """关键词没匹配到任何实体时的诚实回答：不猜、不硬答、给候选"""
        CN = {"Scholar": "学者", "Publication": "论文", "Institution": "机构",
              "Company": "公司", "Industry": "行业", "Field": "领域",
              "Venue": "期刊/会议", "Dataset": "数据集", "DataSource": "数据源"}
        what = CN.get(class_name) or "实体"
        cands = "、".join(str(x.get("name") if isinstance(x, dict) else x) for x in items[:8])
        hint = (f"平台里没有找到名为「{kw}」的{what}，所以这次没法作答（不猜）。"
                f"图谱中多数机构只登记了英文名，换成英文名通常能问到。")
        if cands:
            hint += f"也可以参考这些已接入的：{cands}。"
        return {"intent": "not_found", "keyword": kw, "class": class_name,
                "count": 0, "hint": hint}

    def _resolvable(self, name):
        """这个名字在图谱里到底认不认得出来？认得出就不该报"没找到"。
        修：复旦大学的论文 曾因只看句式、不检查主体是否已解析，被误报成"没找到复旦大学"。"""
        if not name:
            return False
        try:
            return bool(self.find_mentioned_entities(str(name)))
        except Exception:
            return False

    # 列举类问句里的"装饰词"：剥掉它们后若只剩类名，说明用户是想"列举某一类"，
    # 而不是在指名道姓
    LIST_FILLERS = ("所有", "全部", "一些", "有些", "哪些", "什么", "搜索", "查找",
                    "查询", "查一下", "查查", "找出", "找", "看看", "列出", "显示",
                    "给我", "一下子", "一下", "是谁", "是什么", "是啥", "的介绍")

    # 图谱里确实没有收录的人事/联系方式类字段。
    # 用户问了不该假装答上，也不该退化成"列出全部同类"。
    UNSUPPORTED_ATTRS = ("校长", "院长", "系主任", "主任", "书记", "电话", "邮箱", "地址", "邮编")

    def _strip_decorations(self, text):
        """剥掉动词/数量词与类名，留下"实体样"的残余"""
        for w in self.LIST_FILLERS:
            text = text.replace(w, "")
        for w in self.CN_CLASSES:
            text = text.replace(w, "")
        return text.strip()

    def _is_class_listing(self, kw):
        """kw 剥掉装饰词后是不是纯类名？是 → 用户在列举某一类，不该报"没找到" """
        k = kw
        for w in self.LIST_FILLERS:
            k = k.replace(w, "")
        return k in self.CN_CLASSES

    def _unknown_subject(self, question, kw):
        """这次是不是"用户指了个具体名字、但我们没认出来"？返回那个名字，否则 None"""
        subj = self._unresolved_subject(question)          # 优先「X的」结构
        if subj:
            # 用完整名字判断可否解析："复旦大学" 认得出来，"复旦" 反而认不出
            return None if self._resolvable(subj) else subj
        if not kw or self._is_class_listing(kw):
            return None
        rest = self._strip_decorations(kw)
        if len(rest) < 2:
            return None
        # 用剥离后的名字判断："搜索张三学者" → "张三"，避开"学者"这类数据源标签的误命中
        return None if self._resolvable(rest) else rest

    # 问候/寒暄：别让"你好"掉进"抱歉我没理解清楚"
    SMALLTALK = {
        "你好": "你好！我是这个数据平台的语义问答助手。可以问我学者、论文、机构、企业、行业、领域相关的问题，例如「华东师范大学有哪些学者」「人工智能行业有哪些公司」。",
        "您好": "您好！我是这个数据平台的语义问答助手，随时可以帮你查学者、论文、机构、企业和行业数据。",
        "嗨": "嗨！想查点什么数据？学者、论文、机构、企业、行业都能问。",
        "hello": "Hello! 我是这个数据平台的语义问答助手，可以帮你查学者、论文、机构、企业和行业数据。",
        "hi": "Hi! 想查哪方面的数据？",
        "谢谢": "不客气！还想查点什么？",
        "多谢": "不客气！还想查点什么？",
        "你是谁": "我是这个数据平台的语义问答助手：先在本体语义层查询统一知识图谱，再给出可核验的答案。",
        "你能做什么": "我能查平台已接入的学术与企业数据：学者、论文、机构、领域、上市公司、行业。比如「清华大学的校友是谁」「大语言模型趋势」「物流快递行业有哪些公司」。",
    }

    # ---------- 匹配：找问题里提到的实体 ----------
    def find_mentioned_entities(self, question):
        """在问题中查找提到的实体（按 name/cnLabel 匹配，2字以上）"""
        hits = []
        seen = set()
        for s, p, o in self.g.triples((None, ONTO.name, None)):
            lbl = str(o)
            if len(lbl) >= 2 and lbl in question and s not in seen:
                cls = self._class_of(s)
                if cls != "Unknown":
                    hits.append((s, lbl)); seen.add(s)
        for s, p, o in self.g.triples((None, ONTO.cnLabel, None)):
            lbl = str(o)
            if len(lbl) >= 2 and lbl in question and s not in seen:
                cls = self._class_of(s)
                if cls != "Unknown":
                    hits.append((s, lbl)); seen.add(s)
        # 中文别名 → 英文名 → 实体（补齐图谱里缺失的中文标签）
        for cn, en in self.CN_ALIASES.items():
            if len(cn) >= 2 and cn in question:
                s = self._resolve_alias(en)
                if s is not None and s not in seen and self._class_of(s) != "Unknown":
                    hits.append((s, cn)); seen.add(s)
        # 长名优先（避免"上海"盖过"上海交通大学"）
        hits.sort(key=lambda x: -len(x[1]))
        return hits

    def find_mentioned_classes(self, question):
        """查找问题里提到的本体类（中英文）"""
        CN = self.CN_CLASSES
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
        _key = str(question).strip().strip("！!。.？?~,， ").lower()
        if _key in self.SMALLTALK:
            return {"intent": "smalltalk", "count": 0, "hint": self.SMALLTALK[_key]}
        LISTING = ("有哪些", "有什么", "列出", "所有", "全部", "清单", "列表")
        is_listing = any(k in question for k in LISTING)
        cls_list = self.find_mentioned_classes(question)
        ents = self.find_mentioned_entities(question)

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

        # 1.5) 问的是图谱根本没收录的人事/联系字段（校长、院长、电话…）
        #      → 直说没有。既不能假装答上（"详情：国家：CN"），也不能倒全类清单。
        for attr in self.UNSUPPORTED_ATTRS:
            if attr in question:
                who = next((l for u, l in ents
                            if self._class_of(u) in ("Institution", "Company", "Scholar")), None)
                if who:
                    return {"intent": "unsupported_attribute", "attribute": attr,
                            "entity": who, "count": 0,
                            "hint": (f"平台没有收录「{who}」的{attr}信息，所以这个问题答不了"
                                     f"（不编造）。平台现有的是学者、论文、机构、企业、领域等"
                                     f"结构化数据，暂无人事任免与联系方式字段。")}

        # 2) 实体详情（列举型问句不走这里；"某机构的校友/学者是谁"也不走，
        #    交给第 7 条按机构列人，否则只会吐出机构自己的属性而答非所问）
        _people_ask = any(k in question for k in self.PEOPLE_WORDS)
        # "哪些学者研究知识图谱"问的是人，不是这个领域的详情 → 也要跳过实体详情
        _subject_is_group = any(self._class_of(u) in ("Institution", "Field") for u, _ in ents)
        if (not is_listing) and ents and not (_people_ask and _subject_is_group) and any(
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

        # 7) 某机构 + 某领域 的学者（返回带论文数的完整信息，而不是一串名字）
        if any(k in question for k in ("学者", "老师", "教授", "校友", "教师", "成员", "毕业生")) and ents:
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
                        "data": top}

        # 7.5) 某实体的论文列表（"复旦大学的论文"、"兰曼的论文"）
        if ents and any(k in question for k in ("论文", "文献", "文章")):
            uri, label = ents[0]
            cls = self._class_of(uri)
            if cls == "Institution":
                authors = [m["uri"] for m in self.reverse_related(uri, "affiliatedWith")]
            elif cls == "Scholar":
                authors = [uri]
            else:
                authors = []
            if authors:
                seen, rows = set(), []
                for a in authors:
                    for w in self.g.objects(a, ONTO.authorOf):
                        if w in seen:
                            continue
                        seen.add(w)
                        rows.append({"name": self._label(w),
                                     "year": str(self.g.value(w, ONTO.year) or ""),
                                     "cited": str(self.g.value(w, ONTO.cited_by_count) or "")})
                        if len(rows) >= 30:
                            break
                    if len(rows) >= 30:
                        break
                if rows:
                    return {"intent": "entity_publications", "entity": label,
                            "count": len(rows), "data": rows,
                            "entities": [r["name"] for r in rows]}

        # 8) 论文标题关键词检索
        kw = self._extract_keyword(question)
        if kw and ("论文" in question or "文献" in question or "研究" in question):
            pubs = self.search_publications(kw, 25)
            if pubs:
                return {"intent": "search_publications", "keyword": kw,
                        "count": len(pubs), "entities": [p["name"] for p in pubs],
                        "data": pubs}

        # 9) 按类列举
        if cls_list:
            c = cls_list[0]
            items, total = self.entities_of_class(c)
            filtered = [x for x in items if kw in str(x)] if kw else items
            if filtered:
                return {"intent": "list_class", "class": c, "count": len(filtered),
                        "samples": filtered[:25]}
            if items:
                # 关键词没匹配上、且这是个大类 → 多半是"没认出来的实体名"。
                # 此时不能退化成"把整个类倒出来"（问北理工却列出 960 所机构），
                # 而要诚实地说没找到。
                if total > 30:
                    subj = self._unknown_subject(question, kw)
                    if subj:
                        return self._not_found(subj, c, items)
                return {"intent": "list_class", "class": c, "count": total, "samples": items[:25]}

        # 10) 关键词模糊匹配（全类）
        if kw:
            hits = self.search_entities(kw, limit=30)
            if hits:
                return {"intent": "search_entities", "keyword": kw, "count": len(hits),
                        "entities": hits}

        # 11) "X是谁 / X是什么" 却什么都没匹配上 → 诚实说没找到，别含糊兜底
        #     （修：杨卓是谁 以前掉进 overview，界面回"抱歉我没理解清楚"）
        #     只在明确的"问某个东西"句式上触发，避免把闲聊/常识问题也说成"没找到"
        if kw and any(k in question for k in ("是谁", "是什么", "是啥", "的介绍")):
            subj = self._unknown_subject(question, kw)
            if subj:
                return self._not_found(subj, None, [])

        # 12) 兜底：概览
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

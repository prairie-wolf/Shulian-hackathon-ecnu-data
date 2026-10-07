# -*- coding: utf-8 -*-
"""
自动语义映射推断 —— 上传的数据「即刻可用」的关键
输入：任意表格（列名 + 样例值）
输出：符合平台本体的映射（类 / 属性 / 关系），写入映射后立即可被 AI 查询
规则：列名关键词 -> 本体属性；识别主键；识别外键型列（关系）；推断实体类
"""
import re

# 列名 -> (本体属性, 数据类型, 同义关键词)
PROP_RULES = [
    ("name",        "str",   ["name", "名称", "名字", "标题", "title", "公司", "机构", "学者", "论文", "领域", "行业"]),
    ("year",        "int",   ["year", "年份", "年度", "年"]),
    ("cited_by_count", "int", ["cited", "引用", "被引"]),
    ("doi",         "str",   ["doi"]),
    ("country",     "str",   ["country", "国家", "国别"]),
    ("region",      "str",   ["region", "地区", "区域", "省份", "城市", "所在地"]),
    ("revenue",     "float", ["revenue", "营收", "收入", "营业额", "销售额"]),
    ("employees",   "int",   ["employee", "员工", "人数", "职工"]),
    ("level",       "int",   ["level", "层级", "级别"]),
    ("cnLabel",     "str",   ["cn", "中文", "中文名"]),
    ("description", "str",   ["desc", "描述", "说明", "简介", "摘要"]),
]

# 列名/表名 -> 本体类推断
CLASS_RULES = [
    ("Scholar",     ["scholar", "author", "学者", "作者", "教师", "老师", "researcher", "研究人员"]),
    ("Publication", ["publication", "paper", "论文", "文献", "文章", "work", "成果"]),
    ("Institution", ["institution", "机构", "学校", "大学", "university", "院所"]),
    ("Company",     ["company", "corp", "公司", "企业", "厂商", "上市公司"]),
    ("Industry",    ["industry", "行业", "产业"]),
    ("Field",       ["field", "领域", "concept", "概念", "方向", "学科"]),
    ("Venue",       ["venue", "journal", "期刊", "会议", "刊物"]),
]

ID_HINT = ["id", "编号", "标识", "code", "key", "代码"]


def _norm(s):
    return str(s).strip().lower()


def infer_class(*texts):
    """推断本体类：优先匹配已知类；匹配不到则用通用类"""
    blob = " ".join(_norm(t) for t in texts if t)
    for cls, kws in CLASS_RULES:
        for kw in kws:
            if kw in blob:
                return cls
    return "GenericRecord"  # 未知类型 -> 通用记录类（仍可被查询）


def infer_id_column(columns):
    """找主键列"""
    for c in columns:
        if _norm(c) in ("id", "openalex_id"):
            return c
    for c in columns:
        if any(h in _norm(c) for h in ID_HINT):
            return c
    return columns[0] if columns else None


def infer_property(col):
    """列名 -> 本体属性（返回 None 表示不映射）"""
    c = _norm(col)
    for prop, dtype, kws in PROP_RULES:
        for kw in kws:
            if kw in c:
                return prop, dtype
    return None, None


def guess_relation(col, value_samples, known_entity_cols):
    """
    判断某列是否是「关系」（指向另一个实体）。
    典型：scholar_id / pub_id / inst_id / industry / field_id
    """
    c = _norm(col)
    if not c.endswith("_id") and c not in ("industry", "field", "venue", "institution", "company"):
        return None
    base = c.replace("_id", "")
    if base in ("scholar", "author"):
        return {"predicate": "authorOf", "object_class": "Publication", "invert": True}
    if base in ("pub", "publication", "paper", "work"):
        return {"predicate": "authorOf", "object_class": "Publication", "invert": False}
    if base in ("inst", "institution"):
        return {"predicate": "affiliatedWith", "object_class": "Institution", "invert": False}
    if base in ("field", "concept"):
        return {"predicate": "belongsToField", "object_class": "Field", "invert": False}
    if base == "industry":
        return {"predicate": "belongsToIndustry", "object_class": "Industry", "invert": False,
                "from_value": True}
    if base in ("venue", "journal"):
        return {"predicate": "publishedIn", "object_class": "Venue", "invert": False}
    return None


def infer_mapping(filename, columns, sample_rows, sheet_name=None, include_unmapped=False):
    """
    核心：为上传的表推断一份平台可用的语义映射
    返回 {"mappings": [...], "summary": {...}}
    """
    columns = [str(c) for c in columns]
    id_col = infer_id_column(columns)
    cls = infer_class(filename, sheet_name, " ".join(columns))

    # 主键模板前缀
    prefix = {"Scholar": "s", "Publication": "p", "Institution": "i", "Company": "c",
              "Field": "f", "Venue": "v", "Industry": "ind"}.get(cls, "e")

    tpl = f"{prefix}_{{id}}"

    # 值派生型关系列：列名表示"分类"，值才是实体名（行业/领域/地区等）
    VALUE_RELATION_RULES = [
        (["行业", "产业", "industry"], "belongsToIndustry", "Industry", "ind"),
        (["领域", "方向", "学科", "field", "concept"], "belongsToField", "Field", "f"),
        (["期刊", "会议", "刊物", "venue", "journal"], "publishedIn", "Venue", "v"),
    ]

    properties = []
    relation_cols = []
    unmapped_columns = []
    for c in columns:
        if c == id_col:
            continue
        cn = _norm(c)
        # 1) 值派生关系（行业/领域列）
        matched_value_rel = None
        for kws, pred, ocls, oprefix in VALUE_RELATION_RULES:
            if any(k in cn for k in kws):
                matched_value_rel = (pred, ocls, oprefix)
                break
        if matched_value_rel:
            pred, ocls, oprefix = matched_value_rel
            relation_cols.append({
                "kind": "relation",
                "subject_class": cls, "subject_id_column": id_col, "subject_id_template": tpl,
                "predicate": pred,
                "object_class": ocls, "object_id_column": c, "object_id_template": f"{oprefix}_{{id}}",
                "object_id_from_value": True,
            })
            continue
        # 2) 外键型关系（xxx_id）
        if cn.endswith("_id"):
            g = guess_relation(c, None, [])
            if g:
                relation_cols.append({
                    "kind": "relation",
                    "subject_class": g.get("object_class", "Publication") if g.get("invert") else cls,
                    "subject_id_column": c,
                    "subject_id_template": {"Scholar": "s", "Publication": "p", "Institution": "i",
                                            "Field": "f", "Venue": "v"}.get(g.get("object_class"), "e") + "_{id}",
                    "predicate": g["predicate"],
                    "object_class": cls if g.get("invert") else g["object_class"],
                    "object_id_column": id_col,
                    "object_id_template": tpl,
                })
                continue
        # 3) 普通属性
        prop, dtype = infer_property(c)
        if prop:
            properties.append({"column": c, "property": prop, "datatype": dtype})
        else:
            unmapped_columns.append(c)
            if include_unmapped:
                properties.append({"column": c, "property": "description", "datatype": "str"})

    # 名称列：优先映射为 label（精确匹配优先，避免被"id/编号/维度"类列抢走）
    label_col = None
    NAME_EXACT = {"name", "名称", "名字", "title", "标题", "公司", "公司名称", "机构", "机构名称",
                  "论文", "论文标题", "学者", "学者姓名", "项目名称", "产品名称"}
    DIMENSION_WORDS = ("领域", "行业", "地区", "区域", "国家", "类别", "类型", "分类")
    for c in columns:
        if _norm(c) in {x.lower() for x in NAME_EXACT}:
            label_col = c
            break
    if label_col is None:
        for c in columns:
            cn = _norm(c)
            if any(k in cn for k in ("name", "名称", "标题", "title")) and "id" not in cn:
                label_col = c
                break
    if label_col is None:
        # 避开 id 与维度列
        for c in columns:
            cn = _norm(c)
            if c != id_col and not any(d in cn for d in DIMENSION_WORDS) and not cn.endswith("_id"):
                label_col = c
                break
    if label_col is None and columns:
        for c in columns:
            if c != id_col:
                label_col = c
                break

    all_mappings = [{
        "kind": "entity", "class": cls,
        "id_column": id_col, "id_template": tpl,
        "label_column": label_col,
        "properties": properties,
    }] + relation_cols

    return {
        "mappings": all_mappings,
        "summary": {
            "inferred_class": cls, "id_column": id_col, "id_template": tpl,
            "label_column": label_col, "properties": len(properties),
            "relations": [r["predicate"] for r in relation_cols],
            "unmapped_columns": unmapped_columns,
        },
    }


if __name__ == "__main__":
    demo_cols = ["company_id", "公司名称", "行业", "地区", "营收", "员工人数"]
    rows = [["C001", "阿里巴巴", "电商", "杭州", 9411.68, 204891]]
    print(__import__("json").dumps(infer_mapping("companies.csv", demo_cols, rows), ensure_ascii=False, indent=1))

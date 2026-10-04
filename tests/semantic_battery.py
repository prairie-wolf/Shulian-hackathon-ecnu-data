# -*- coding: utf-8 -*-
"""探针 7：问句轰炸 —— 扫出"答案荒谬 / 答非所问 / 该答没答"的残留问题"""
import os, sys, json

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)
sys.path.insert(0, REPO)

from aiplatform.build_platform import build
from aiplatform.semantic import GenericSemanticQuery
onto, cat, graph = build()
q = GenericSemanticQuery(graph.g)

QUESTIONS = [
    "哪些公司在人工智能行业", "人工智能行业有哪些公司", "营收最高的公司", "员工最多的公司",
    "物流快递行业有哪些公司", "哪些学者研究知识图谱", "知识图谱领域有哪些论文",
    "华东师范大学有哪些学者", "清华大学的校友是谁", "北京大学有哪些学者",
    "哪个机构论文最多", "被引最多的论文", "h指数最高的机构",
    "大语言模型趋势", "深度学习趋势", "供应链管理趋势",
    "有哪些数据集", "平台有哪些数据源", "平台覆盖哪些类",
    "兰曼是谁", "周傲英是谁", "OpenAlex 学术知识图谱是什么",
    "上海有哪些公司", "北京有哪些大学", "复旦大学的论文",
    "你好", "谢谢", "今天天气怎么样", "帮我写首诗", "1+1等于几",
    "搜索张三学者", "杨卓是谁", "北京理工大学的校长是谁", "麻省理工学院的学者",
    "南京大学有哪些学者", "香港中文大学的校友是谁",
]

print(f"{'问题':<28} {'intent':<24} {'count':>6}  摘要")
print("-" * 110)
for question in QUESTIONS:
    try:
        r = q.ask(question)
    except Exception as e:
        print(f"{question:<28} EXCEPTION {type(e).__name__}: {e}")
        continue
    intent = str(r.get("intent"))
    count = r.get("count", "")
    if intent == "not_found":
        brief = "没找到「" + str(r.get("keyword")) + "」"
    elif intent == "unsupported_attribute":
        brief = "无收录字段:" + str(r.get("attribute"))
    elif intent == "overview":
        brief = "兜底概览"
    elif intent == "list_class":
        brief = f"列出全类({r.get('class')})"
    elif intent == "entity_detail":
        brief = str(r.get("entity"))
    elif intent == "trend":
        brief = str(r.get("field"))
    elif intent in ("scholars_filtered", "industry_companies"):
        brief = str(r.get("scope") or r.get("industry"))
    elif intent == "relation_rank":
        brief = str(r.get("subject"))
    else:
        brief = str(r.get("keyword") or r.get("entity") or "")
    print(f"{question:<28} {intent:<24} {str(count):>6}  {brief}")

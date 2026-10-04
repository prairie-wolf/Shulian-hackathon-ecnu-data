# -*- coding: utf-8 -*-
"""
私人数据分区（对外产品核心）：
- 每个登录用户有一个独立的本体化知识图谱，存 data/private/<uid>/user_<n>.ttl
- 上传数据进「当前用户的私有图」，不污染公共图
- 查询时合并 = 公共图(所有人共享) + 该用户私有图(仅本人可见)
生产可把 data/private 换成数据库；这里用文件系统便于演示与审查。
"""
import os, re, glob, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdflib import Graph

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIV_DIR = os.path.join(BASE, "data", "private")
os.makedirs(PRIV_DIR, exist_ok=True)


def safe_uid(uid):
    """把用户名规范成安全的目录名（防路径注入）"""
    return re.sub(r"[^0-9A-Za-z_-]", "_", str(uid or "guest")) or "user"


def user_priv_dir(uid):
    d = os.path.join(PRIV_DIR, safe_uid(uid))
    os.makedirs(d, exist_ok=True)
    return d


def load_user_priv(uid):
    """加载某个用户的私有图（rdflib.Graph），没有则空图"""
    g = Graph()
    d = user_priv_dir(uid)
    for ttl in sorted(glob.glob(os.path.join(d, "*.ttl"))):
        try:
            with open(ttl, "rb") as f:
                g.parse(f, format="turtle")
        except Exception as e:
            print(f"[private] parse {ttl}: {e}")
    return d, g


def save_user_priv(uid, g, idx=0):
    """把用户私有图落盘（idx 用于分文件，见 list_partitions）"""
    d = user_priv_dir(uid)
    out = os.path.join(d, f"user_{idx}.ttl")
    g.serialize(destination=out, format="turtle")   # 传路径而非文件对象（见 new_partition 注释）
    return out


# ========== 私人库分区（partition） ==========
# 一个分区 = 一个独立 .ttl/.json 文件；文件名即分区名（安全化）。
# 兼容旧数据：老式 user_0.ttl / user_<n>.ttl 归入「默认分区」。
# 设计：分区用 <safe>.ttl 存图 + <safe>.meta.json 存分区名/创建时间。
# 旧 user_*.ttl 无 meta，映射到分区名 user_<n>.

def _partition_meta(d, name_stem):
    meta_p = os.path.join(d, name_stem + ".meta.json")
    if os.path.exists(meta_p):
        try:
            with open(meta_p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _valid_name(name):
    n = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "_", str(name).strip())
    if not n:
        n = "默认分区"
    return n[:40]


def list_partitions(uid):
    """返回 [(分区id, 分区名, 三元组数, 数据源数)] 有序；至少含「默认分区」"""
    d = user_priv_dir(uid)
    out = []
    seen = set()
    for f in sorted(glob.glob(os.path.join(d, "*.ttl"))):
        stem = os.path.splitext(os.path.basename(f))[0]
        if stem in seen:
            continue
        seen.add(stem)
        meta = _partition_meta(d, stem)
        pname = meta.get("name") or stem
        # 三元组数
        cnt = 0
        try:
            with open(f, "rb") as fh:
                g = Graph(); g.parse(fh, format="turtle")
                cnt = len(g)
        except Exception:
            cnt = 0
        out.append({"id": stem, "name": pname, "triples": cnt})
    if not out:
        out.append({"id": "user_0", "name": "默认分区", "triples": 0})
    # 让「默认分区」优先
    out.sort(key=lambda x: 0 if x["id"] == "user_0" else 1)
    return out


def new_partition(uid, name):
    """新建一个分区，返回 partition id（安全文件名）"""
    stem = _valid_name(name)
    d = user_priv_dir(uid)
    path = os.path.join(d, stem + ".ttl")
    # 直接传路径字符串。传文件对象时 rdflib 会拿 Windows 路径当 base URI，
    # 触发 "does not look like a valid URI, trying to serialize this will break."
    Graph().serialize(destination=path, format="turtle")
    with open(os.path.join(d, stem + ".meta.json"), "w", encoding="utf-8") as f:
        json.dump({"name": name, "created": time.strftime("%Y-%m-%d")}, f, ensure_ascii=False)
    return stem


def load_partition_g(uid, pid):
    """加载某个分区的图"""
    d = user_priv_dir(uid)
    g = Graph()
    path = os.path.join(d, _valid_name(pid) + ".ttl")
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                g.parse(f, format="turtle")
        except Exception as e:
            print(f"[private] load_partition {path}: {e}")
    return g


def save_partition(uid, pid, g):
    """把分区图落盘（覆盖该分区文件）"""
    d = user_priv_dir(uid)
    stem = _valid_name(pid)
    out = os.path.join(d, stem + ".ttl")
    g.serialize(destination=out, format="turtle")   # 传路径而非文件对象（见 new_partition 注释）
    return out


def merge_public_user(public_g, priv_g):
    """合并公共图 + 用户私有图 → 仅用于查询（不落盘，不改公共图）"""
    m = Graph()
    for ns, uri in public_g.namespaces():
        m.bind(ns, uri)
    for t in public_g:
        m.add(t)
    for ns, uri in priv_g.namespaces():
        m.bind(ns, uri)
    for t in priv_g:
        m.add(t)
    return m


class UserGraphProxy:
    """给 upload.ingest_file 使用的带 onto + g 的私有图包装（不重灌公共数据）"""

    def __init__(self, onto, g):
        self.onto = onto
        self.g = g
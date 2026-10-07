# -*- coding: utf-8 -*-
"""
私人数据分区（对外产品核心）：
- 每个登录用户有一个独立的本体化知识图谱，存 data/private/<uid>/user_<n>.ttl
- 上传数据进「当前用户的私有图」，不污染公共图
- 查询时合并 = 公共图(所有人共享) + 该用户私有图(仅本人可见)
生产可把 data/private 换成数据库；这里用文件系统便于演示与审查。
"""
import os, re, glob, sys, json, time, hashlib
from pathlib import Path
from contextlib import contextmanager
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdflib import Graph
from aiplatform.storage import atomic_write, file_lock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIV_DIR = os.path.join(BASE, "data", "private")
os.makedirs(PRIV_DIR, exist_ok=True)


def safe_uid(uid):
    """把用户名规范成安全的目录名（防路径注入）"""
    raw = str(uid or "guest")
    # Preserve existing ASCII accounts; reserve ~uid- for hashed non-ASCII IDs.
    if re.fullmatch(r"[0-9A-Za-z_-]+", raw) and raw == raw.lower():
        return raw
    return "~uid-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def user_priv_dir(uid):
    raw = str(uid or "guest")
    legacy_stem = re.sub(r"[^0-9A-Za-z_-]", "_", raw) or "user"
    legacy = Path(PRIV_DIR) / legacy_stem
    marker = legacy / ".owner.json"
    legacy_data = list(legacy.glob("*.ttl")) + list(legacy.glob("*.meta.json"))
    if legacy_data and not marker.exists():
        ambiguous = safe_uid(uid) != legacy_stem or "_" in legacy_stem
        actual_names = [p.name for p in Path(PRIV_DIR).iterdir() if p.name.casefold() == legacy_stem.casefold()]
        if ambiguous or actual_names != [raw]:
            raise ValueError("旧用户名目录可能由多个账号共用；原文件已保留，需确认归属后人工迁移")
    d = os.path.join(PRIV_DIR, safe_uid(uid))
    os.makedirs(d, exist_ok=True)
    with file_lock(os.path.join(d, ".partitions.lock")):
        owner = Path(d) / ".owner.json"
        if owner.exists():
            try:
                identity = json.loads(owner.read_bytes())["uid"]
            except Exception as exc:
                raise ValueError("私人目录归属记录损坏，原文件已保留") from exc
            if identity != raw:
                raise ValueError("私人目录归属不匹配，已拒绝读取")
        elif not list(Path(d).glob("*.ttl")) and not list(Path(d).glob("*.meta.json")):
            atomic_write(owner, json.dumps({"uid": raw}, ensure_ascii=False).encode("utf-8"))
        _recover_deletions(d)
    return d


def _recover_deletions(directory):
    for journal in Path(directory).glob(".delete_*.json"):
        try:
            data = json.loads(journal.read_bytes())
            stem = data["id"]
            allowed = {stem + ".ttl", stem + ".meta.json"}
            if stem != _valid_name(stem) or journal.name != ".delete_" + stem + ".json":
                raise ValueError("无效分区标识")
            if not isinstance(data["committed"], bool) or not set(data["files"]) <= allowed:
                raise ValueError("无效删除日志")
        except Exception as exc:
            raise ValueError("私人分区删除日志损坏，原文件已保留") from exc
        for filename in data["files"]:
            original = Path(directory) / filename
            moved = original.with_name("." + filename + ".deleted")
            if not moved.exists():
                continue
            if data["committed"]:
                os.remove(moved)
            elif not original.exists():
                os.replace(moved, original)
            else:
                raise ValueError("私人分区恢复时发现重名文件，已保留两份文件")
        os.remove(journal)


def load_user_priv(uid):
    """加载某个用户的私有图（rdflib.Graph），没有则空图"""
    g = Graph()
    d = user_priv_dir(uid)
    with file_lock(os.path.join(d, ".partitions.lock")):
        for ttl in sorted(glob.glob(os.path.join(d, "*.ttl"))):
            for triple in _read_graph(ttl):
                g.add(triple)
    return d, g


def save_user_priv(uid, g, idx=0):
    """把用户私有图落盘（idx 用于分文件，见 list_partitions）"""
    return save_partition(uid, f"user_{idx}", g)


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
                meta = json.load(f)
            if not isinstance(meta, dict):
                raise ValueError("元数据必须是对象")
            return meta
        except Exception as exc:
            raise ValueError(f"私人分区元数据损坏：{name_stem}，原文件已保留") from exc
    return {}


def _valid_name(name):
    n = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "_", str(name).strip())
    if not n:
        n = "默认分区"
    return n[:40]


def list_partitions(uid):
    """返回 [(分区id, 分区名, 三元组数, 数据源数)] 有序；至少含「默认分区」"""
    d = user_priv_dir(uid)
    with file_lock(os.path.join(d, ".partitions.lock")):
        return _list_partitions(d)


def _list_partitions(d):
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
        error = None
        try:
            cnt = len(_read_graph(f))
        except ValueError as exc:
            cnt = None
            error = str(exc)
        out.append({"id": stem, "name": pname, "triples": cnt, "error": error})
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
    meta = os.path.join(d, stem + ".meta.json")
    with file_lock(os.path.join(d, ".partitions.lock")):
        if os.path.exists(path) or os.path.exists(meta):
            raise FileExistsError("同名或规范化后同名的分区已存在，请使用其他名称")
        # A crash before metadata leaves a valid empty partition with its stem as name.
        atomic_write(path, Graph().serialize(format="turtle", encoding="utf-8"))
        try:
            atomic_write(meta, json.dumps({"name": name, "created": time.strftime("%Y-%m-%d")},
                                         ensure_ascii=False).encode("utf-8"))
        except Exception:
            os.remove(path)
            raise
    return stem


def load_partition_g(uid, pid):
    """加载某个分区的图"""
    d = user_priv_dir(uid)
    path = os.path.join(d, _valid_name(pid) + ".ttl")
    with file_lock(os.path.join(d, ".partitions.lock")):
        return _read_graph(path)


def _read_graph(path):
    g = Graph()
    data = Path(path).read_bytes() if os.path.exists(path) else None
    if data is not None:
        try:
            g.parse(data=data, format="turtle", publicID=Path(path).resolve().as_uri())
        except Exception as exc:
            raise ValueError(f"私人分区损坏：{Path(path).stem}，原文件已保留，禁止覆盖") from exc
    g._storage_revision = hashlib.sha256(data).hexdigest() if data is not None else None
    g._storage_path = str(Path(path).resolve())
    return g


def save_partition(uid, pid, g):
    """把分区图落盘（覆盖该分区文件）"""
    d = user_priv_dir(uid)
    stem = _valid_name(pid)
    out = os.path.join(d, stem + ".ttl")
    with file_lock(os.path.join(d, ".partitions.lock")):
        current = _read_graph(out)  # Never replace a file that cannot be parsed.
        if getattr(g, "_storage_path", None) == str(Path(out).resolve()):
            if g._storage_revision != current._storage_revision:
                raise RuntimeError("私人分区已被其他操作更新，请重新加载后重试")
        data = g.serialize(format="turtle", encoding="utf-8")
        atomic_write(out, data)
        g._storage_revision = hashlib.sha256(data).hexdigest()
        g._storage_path = str(Path(out).resolve())
    return out


@contextmanager
def edit_partition(uid, pid):
    """Serialize the whole read/update/save transaction across processes."""
    d = user_priv_dir(uid)
    with file_lock(os.path.join(d, ".partitions.lock")):
        g = load_partition_g(uid, pid)
        yield g
        save_partition(uid, pid, g)


def delete_partition(uid, pid):
    """删除指定私人分区及其元数据，返回实际删除的路径。"""
    d = user_priv_dir(uid)
    stem = _valid_name(pid)
    with file_lock(os.path.join(d, ".partitions.lock")):
        paths = [Path(d) / (stem + suffix) for suffix in (".ttl", ".meta.json")]
        paths = [p for p in paths if p.exists()]
        if not paths:
            return []
        journal = Path(d) / (".delete_" + stem + ".json")
        data = {"id": stem, "committed": False, "files": [p.name for p in paths]}
        atomic_write(journal, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        try:
            for path in paths:
                os.replace(path, path.with_name("." + path.name + ".deleted"))
            data["committed"] = True
            atomic_write(journal, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        except BaseException:
            _recover_deletions(d)
            raise
        try:
            _recover_deletions(d)
        except OSError as exc:
            raise OSError("分区删除已提交，但文件清理失败；下次加载会重试") from exc
    return [str(p) for p in paths]


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

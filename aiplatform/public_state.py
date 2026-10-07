"""Durable public upload contributions; built-in data remains rebuildable."""
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
import uuid

from rdflib import Graph, URIRef, Literal, BNode
from aiplatform.core import SourceCatalog
from aiplatform.storage import atomic_write, file_lock


def _encode(node):
    if isinstance(node, Literal):
        return ["literal", str(node), str(node.datatype) if node.datatype else None, node.language]
    return ["blank" if isinstance(node, BNode) else "uri", str(node)]


def _decode(value):
    if not isinstance(value, list) or len(value) < 2 or not isinstance(value[1], str):
        raise ValueError("无效 RDF 节点")
    if value[0] == "literal" and len(value) == 4:
        return Literal(value[1], datatype=URIRef(value[2]) if value[2] else None, lang=value[3])
    if len(value) == 2 and value[0] in ("uri", "blank"):
        return (URIRef if value[0] == "uri" else BNode)(value[1])
    raise ValueError("无效 RDF 节点类型")


def _catalog_spec(entry):
    from aiplatform.upload import UPSTREAM_DIR
    spec = dict(entry["catalog"])
    if spec.pop("relative_to_uploads", False):
        filename = spec["location"]
        if not isinstance(filename, str) or not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]+", filename):
            raise ValueError("无效上传存储名")
        spec["location"] = str(Path(UPSTREAM_DIR).resolve() / filename)
    return spec


class PublicUploadStore:
    def __init__(self, graph, catalog, path):
        self.graph, self.catalog = graph, catalog
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(".lock")
        self.base = Graph()
        for namespace, uri in graph.g.namespaces():
            self.base.bind(namespace, uri)
        for triple in graph.g:
            self.base.add(triple)
        self.base_catalog = dict(catalog.sources)
        self.signature = None
        graph._public_store = self
        self.refresh(force=True)

    def _signature(self):
        try:
            stat = self.path.stat()
            return (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            return None

    def _read(self):
        if not self.path.exists():
            return {"version": 1, "revision": None, "sources": {}}
        try:
            data = json.loads(self.path.read_bytes())
            if data["version"] != 1 or not isinstance(data["sources"], dict):
                raise ValueError("快照版本或目录无效")
            return data
        except Exception as exc:
            raise ValueError("公共上传快照损坏或版本不支持，原文件已保留，禁止覆盖") from exc

    def _assemble(self, data):
        from aiplatform.upload import _add_source_triples
        g = Graph()
        for namespace, uri in self.base.namespaces():
            g.bind(namespace, uri)
        for triple in self.base:
            g.add(triple)
        catalog = SourceCatalog()
        catalog.sources = dict(self.base_catalog)
        try:
            for sid, entry in sorted(data["sources"].items()):
                spec = _catalog_spec(entry)
                if not sid.startswith("ds_upload_") or spec["source_id"] != sid:
                    raise ValueError("无效上传来源")
                for key in ("name", "kind", "location", "rows", "schema"):
                    spec[key]
                triples = {tuple(_decode(node) for node in triple) for triple in entry["triples"]}
                if any(len(t) != 3 or not isinstance(t[1], URIRef) or isinstance(t[0], Literal)
                       for t in triples):
                    raise ValueError("无效三元组")
                _add_source_triples(g, sid, triples)
                catalog.sources[sid] = spec
        except Exception as exc:
            raise ValueError("公共上传快照内容无效，原文件已保留，禁止覆盖") from exc
        return SimpleNamespace(onto=self.graph.onto, g=g), catalog

    def _recover_deletions(self, data):
        """A crash between rename and commit restores the still-registered original."""
        from aiplatform.upload import UPSTREAM_DIR
        root = Path(UPSTREAM_DIR).resolve()
        registered = {str(Path(_catalog_spec(entry)["location"]).resolve())
                      for entry in data["sources"].values()}
        for moved in root.glob(".deleted-*"):
            if not re.fullmatch(r"\.deleted-[0-9a-f]{32}\.[a-z0-9]+", moved.name):
                continue
            original = moved.with_name(moved.name[len(".deleted-"):])
            if str(original) in registered and not original.exists():
                os.replace(moved, original)
            else:
                os.remove(moved)

    def _publish(self, graph, catalog):
        # Swap complete snapshots so concurrent readers never see a half-built graph.
        self.graph.g = graph.g
        self.catalog.sources = catalog.sources
        self.signature = self._signature()

    def refresh(self, force=False):
        if not force and self.signature == self._signature():
            return False
        with file_lock(self.lock_path):
            if not force and self.signature == self._signature():
                return False
            data = self._read()
            graph, catalog = self._assemble(data)
            self._recover_deletions(data)
            self._publish(graph, catalog)
        return True

    def snapshot(self):
        with file_lock(self.lock_path):
            self.refresh()
            return self.graph.g, dict(self.catalog.sources)

    def mutate(self, operation, source_to_remove=None):
        """Reload, modify an isolated copy, persist, then publish under one lock."""
        from aiplatform.upload import _source_state, UPSTREAM_DIR
        with file_lock(self.lock_path):
            data = self._read()
            graph, catalog = self._assemble(data)
            self._recover_deletions(data)
            moved = None
            if source_to_remove is not None:
                spec = catalog.sources.get(source_to_remove)
                if spec is None:
                    raise ValueError("上传来源不存在")
                original = Path(spec["location"]).resolve()
                root = Path(UPSTREAM_DIR).resolve()
                if original == root or root not in original.parents:
                    raise ValueError("上传原件路径不在上传目录内，已取消删除")
                if original.exists():
                    moved = original.with_name(".deleted-" + original.name)
                    os.replace(original, moved)
            try:
                result = operation(graph, catalog)
                if isinstance(result, dict) and result.get("error"):
                    return result
                state = _source_state(graph.g)
                persisted_catalog = {}
                root = Path(UPSTREAM_DIR).resolve()
                for sid in state["sources"]:
                    spec = dict(catalog.sources[sid])
                    location = Path(spec["location"]).resolve()
                    if location.parent != root or not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]+", location.name):
                        raise ValueError("公共上传原件必须经 save_upload_bytes 保存到上传目录")
                    spec["location"] = location.name
                    spec["relative_to_uploads"] = True
                    persisted_catalog[sid] = spec
                data = {"version": 1, "revision": uuid.uuid4().hex, "sources": {
                    sid: {"catalog": persisted_catalog[sid], "triples": [
                        [_encode(node) for node in triple]
                        for triple in sorted(triples, key=lambda t: tuple(n.n3() for n in t))]}
                    for sid, triples in state["sources"].items()}}
                atomic_write(self.path, json.dumps(data, ensure_ascii=False).encode("utf-8"))
            except BaseException:
                if moved is not None:
                    os.replace(moved, original)
                raise
            self._publish(graph, catalog)
            if moved is not None:
                try:
                    os.remove(moved)
                except OSError as exc:
                    raise OSError("来源撤回已保存，但原件清理失败；待清理文件：" + str(moved)) from exc
            return result

    def export(self, path):
        with file_lock(self.lock_path):
            self.refresh(force=True)
            atomic_write(path, self.graph.g.serialize(format="turtle", encoding="utf-8"))

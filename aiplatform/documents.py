# -*- coding: utf-8 -*-
"""
多格式文档摄取：docx / pdf / xlsx / csv / 图片(OCR)
- 结构化文件（xlsx/csv）：直接转成表格行 -> 本体性转化
- 文档（docx/pdf）：抽正文 + 表格 -> 实体抽取 -> 本体性转化
- 图片：若装了 OCR 则提取文字，否则登记为文档类实体（含元数据）
"""
import os, sys, io, json, re, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SUPPORTED = {
    ".csv": "csv", ".xlsx": "xlsx", ".xls": "xlsx",
    ".docx": "docx", ".doc": "docx",
    ".pdf": "pdf", ".json": "json",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".txt": "text", ".md": "text",
}


def detect_kind(filename):
    ext = os.path.splitext(filename)[1].lower()
    return SUPPORTED.get(ext)


def read_tabular(path):
    """xlsx / csv -> (列名, 行列表, 引擎) """
    import pandas as pd
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        sheets = pd.read_excel(path, sheet_name=None)
        out = {}
        for name, df in sheets.items():
            out[name] = (list(df.columns), df.astype(object).where(df.notna(), None).values.tolist())
        return {"sheets": out, "engine": "openpyxl"}
    if ext == ".csv":
        df = pd.read_csv(path)
        return {"sheets": {"default": (list(df.columns), df.astype(object).where(df.notna(), None).values.tolist())},
                "engine": "pandas"}
    if ext == ".json":
        import pandas as pd
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            df = pd.DataFrame(data)
            return {"sheets": {"default": (list(df.columns), df.astype(object).where(df.notna(), None).values.tolist())},
                    "engine": "json"}
        return {"sheets": {}, "raw": data, "engine": "json"}
    return {"sheets": {}, "engine": None}


def read_document(path):
    """docx / pdf -> 正文 + 表格"""
    ext = os.path.splitext(path)[1].lower()
    text, tables = "", []
    if ext == ".docx":
        from docx import Document
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        for t in doc.tables:
            rows = [[c.text.strip() for c in r.cells] for r in t.rows]
            if len(rows) > 1:
                tables.append({"header": rows[0], "rows": rows[1:]})
    elif ext == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                pages = []
                for pg in pdf.pages:
                    pages.append(pg.extract_text() or "")
                    for tb in (pg.extract_tables() or []):
                        if len(tb) > 1:
                            tables.append({"header": tb[0], "rows": tb[1:]})
                text = "\n".join(pages)
        except ImportError:
            try:
                from pypdf import PdfReader
                text = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
            except ImportError:
                text = ""
    elif ext in (".txt", ".md"):
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    return {"text": text, "tables": tables, "chars": len(text)}


def read_image(path):
    """图片：OCR（若可用）+ 基本元数据"""
    info = {"ocr_text": "", "engine": None}
    try:
        from PIL import Image
        im = Image.open(path)
        info["size"] = im.size
        info["mode"] = im.mode
    except Exception:
        pass
    # 尝试 OCR
    try:
        import pytesseract
        from PIL import Image
        info["ocr_text"] = pytesseract.image_to_string(Image.open(path), lang="chi_sim+eng")
        info["engine"] = "pytesseract"
    except Exception as e:
        info["ocr_note"] = f"OCR 不可用（{type(e).__name__}），已登记为文档类实体"
    return info


def ingest_any(path, filename=None):
    """统一入口：任何文件 -> 摄取结果"""
    filename = filename or os.path.basename(path)
    kind = detect_kind(filename)
    result = {"filename": filename, "kind": kind, "path": path}
    if kind is None:
        result["error"] = f"不支持的文件类型：{os.path.splitext(filename)[1]}"
        return result
    try:
        if kind in ("csv", "xlsx", "json"):
            result.update(read_tabular(path))
        elif kind in ("docx", "pdf", "text"):
            result.update(read_document(path))
        elif kind == "image":
            result.update(read_image(path))
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = ingest_any(p)
        print(json.dumps({k: (v if not isinstance(v, dict) else list(v.keys()) or v)
                          for k, v in r.items() if k != "sheets"}, ensure_ascii=False)[:400])

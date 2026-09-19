"""
文档加载模块
================
作用：根据文件扩展名，选择对应的加载器，把文档读成文本。

技术栈：文档加载器
- TextLoader：读 .txt（中文必须显式指定 utf-8 编码）
- PyPDFLoader：读 .pdf
- python-docx：读 .docx（不依赖 docx2txt，环境更稳）
- CSVLoader：读 .csv（每行一个 document）
- openpyxl：读 .xlsx（每个 sheet 的单元格拼成文本，简单表格知识库）

返回的是 LangChain 的 Document 对象列表（List[Document]），
每个对象有 page_content（正文）和 metadata（元信息）。
"""
from pathlib import Path
import sys

from langchain_community.document_loaders import (
    TextLoader,
    PyPDFLoader,
    CSVLoader,
)
from langchain_core.documents import Document

MARKITDOWN_EXTENSIONS = {".txt", ".pdf", ".docx", ".csv", ".xlsx", ".xls"}


def _with_original_source(docs: list[Document], filename: str) -> list[Document]:
    """统一把 loader 写入的落盘路径改回用户上传的原始文件名。"""
    for doc in docs:
        metadata = dict(doc.metadata or {})
        metadata["source"] = filename
        doc.metadata = metadata
    return docs


def _with_parser_metadata(
    docs: list[Document],
    filename: str,
    parser_name: str,
    parser_version: str = "",
) -> list[Document]:
    """统一写入前端可展示的解析器信息。"""
    docs = _with_original_source(docs, filename)
    for doc in docs:
        metadata = dict(doc.metadata or {})
        metadata["parser_name"] = parser_name
        metadata["parser_version"] = parser_version
        doc.metadata = metadata
    return docs


def _markitdown_version() -> str:
    try:
        from importlib.metadata import version

        return version("markitdown")
    except Exception:
        return "local"


def _import_markitdown():
    """优先导入已安装包；本地开发时兼容 markitdown-main 源码目录。"""
    try:
        from markitdown import MarkItDown

        return MarkItDown
    except Exception:
        local_pkg = Path(__file__).resolve().parents[3] / "markitdown-main" / "packages" / "markitdown" / "src"
        if local_pkg.exists() and str(local_pkg) not in sys.path:
            sys.path.insert(0, str(local_pkg))
        from markitdown import MarkItDown

        return MarkItDown


def _convert_with_markitdown(file_path: str, filename: str) -> str:
    """用 Microsoft MarkItDown 把复杂文档先统一转成 Markdown。"""
    MarkItDown = _import_markitdown()
    converter = MarkItDown(enable_plugins=False)
    if hasattr(converter, "convert_local"):
        result = converter.convert_local(file_path)
    else:
        result = converter.convert(file_path)
    markdown = getattr(result, "markdown", "") or getattr(result, "text_content", "") or str(result or "")
    return markdown.strip()


def _load_with_markitdown(file_path: str, filename: str) -> list[Document]:
    markdown = _convert_with_markitdown(file_path, filename)
    if not markdown:
        return []
    return _with_parser_metadata(
        [Document(page_content=markdown, metadata={"source": filename})],
        filename,
        "markitdown",
        _markitdown_version(),
    )


def _load_docx(file_path: str, filename: str) -> list[Document]:
    """用 python-docx 读 .docx：段落 + 表格文本，返回 Document 列表。

    不用 Docx2txtLoader（依赖 docx2txt 库，环境可能没装），
    用 python-docx（项目里已有）实现，更稳定。
    """
    import docx

    d = docx.Document(file_path)
    parts = []
    for p in d.paragraphs:
        if p.text.strip():
            parts.append(p.text.strip())
    # 表格内容也读出来（"表头：值"形式）
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append("，".join(cells))
    text = "\n".join(parts)
    return _with_parser_metadata([Document(page_content=text, metadata={"source": filename})], filename, "python-docx")


def _load_pdf(file_path: str, filename: str) -> list[Document]:
    """读 .pdf：优先 pypdf 提取文本；无文本层（如"打印到 PDF"把文字转成
    矢量曲线的扫描件/特殊导出件）或 pypdf 解析超慢时，降级到 OCR
    （渲染 + tesseract）还原。

    为什么加超时：某些异常 PDF 的 pypdf 逐页提取极慢，
    而 OCR 只要几秒；用线程超时兜底，保证上传不卡"解析中"。
    """
    # 1. pypdf 提取（限时 5s，超时或为空则走 OCR）
    docs = _load_pdf_with_timeout(file_path)
    if docs is None:
        docs = _ocr_fallback(file_path)
    elif docs and all(not (d.page_content or "").strip() for d in docs):
        ocr_docs = _ocr_fallback(file_path)
        if ocr_docs:
            docs = ocr_docs
    elif not docs:
        docs = _ocr_fallback(file_path)
    if not docs:
        if not _pdf_ocr_available():
            raise ValueError(
                "PDF 解析失败：该文件没有可提取文本，可能是扫描版 PDF 或特殊导出 PDF；"
                "当前服务器未安装 OCR 依赖。请在服务器执行 "
                "apt install -y tesseract-ocr tesseract-ocr-chi-sim 后重启后端，再重新上传。"
            )
        raise ValueError(
            "PDF 解析失败：未提取到有效文本。该文件可能是扫描版 PDF、加密 PDF、图片质量过低，"
            "或 OCR 未识别出中文内容；请尝试重新导出为可复制文本的 PDF，或转为 .docx/.txt 后上传。"
        )
    return _with_parser_metadata(docs, filename, "pypdf+ocr")


def _load_pdf_with_timeout(file_path: str, timeout: float = 5.0):
    """线程超时包裹 pypdf 提取。超时返回 None（触发 OCR 降级）。"""
    import threading

    result = {}

    def worker():
        try:
            result["docs"] = PyPDFLoader(file_path).load()
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None  # pypdf 卡住，放弃
    if "docs" in result:
        return result["docs"]
    return None


def _ocr_fallback(file_path: str) -> list:
    """OCR 兜底：渲染 + tesseract 还原文本，返回 Document 列表（可能空）。"""
    try:
        from app.core.pdf_ocr import ocr_pdf

        text = ocr_pdf(file_path)
        if text:
            return [Document(page_content=text, metadata={"source": Path(file_path).name})]
    except Exception as exc:
        print(f"PDF OCR fallback failed for {file_path}: {exc}", flush=True)
    return []


def _pdf_ocr_available() -> bool:
    """是否具备 PDF OCR 能力。单独封装，便于测试和部署诊断。"""
    try:
        from app.core.pdf_ocr import pdf_ocr_available

        return pdf_ocr_available()
    except Exception:
        return False


def _load_excel(file_path: str, filename: str) -> list[Document]:
    """用 openpyxl 读 .xlsx：每个 sheet 的每行拼成一行文本，返回 Document 列表。

    简单实现：把"表头：值"拼成一段，适合企业表格（人员名册、费用明细、库存表等）。
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True)
    docs = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = rows[0]  # 首行当表头
        for row in rows[1:]:
            # 跳过全空行
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            parts = []
            for col_idx, cell in enumerate(row):
                if cell is None or str(cell).strip() == "":
                    continue
                # 表头有值就拼 "表头: 值"，否则只放值
                key = str(header[col_idx]).strip() if col_idx < len(header) and header[col_idx] else ""
                val = str(cell).strip()
                parts.append(f"{key}：{val}" if key and key != "None" else val)
            text = f"[工作表：{sheet}]\n" + "\n".join(parts)
            docs.append(Document(page_content=text, metadata={"source": filename}))
    return _with_parser_metadata(docs, filename, "openpyxl")


def load_document(file_path: str, filename: str):
    """按扩展名分派加载器，把文档读成 Document 列表。

    参数：
        file_path：落盘后的文件绝对路径
        filename：原始文件名（用于判断扩展名）
    返回：
        List[Document]：LangChain 文档对象列表
    抛出：
        ValueError：文件类型不在支持列表里
    """
    # 取小写扩展名：'手册.txt' -> '.txt'
    ext = Path(filename).suffix.lower()
    if ext in MARKITDOWN_EXTENSIONS:
        try:
            docs = _load_with_markitdown(file_path, filename)
            if docs and any((d.page_content or "").strip() for d in docs):
                return docs
        except Exception as exc:
            print(f"MarkItDown parse failed for {filename}, fallback to legacy loader: {exc}", flush=True)

    if ext == ".txt":
        # 中文 TXT 不指定 encoding 在 Windows 下可能乱码，必须写 utf-8。
        # 用 utf-8-sig：自动剥离文件头 BOM（﻿），避免首字符污染 chunk
        loader = TextLoader(file_path, encoding="utf-8-sig")
    elif ext == ".pdf":
        return _load_pdf(file_path, filename)
    elif ext == ".docx":
        return _load_docx(file_path, filename)
    elif ext == ".csv":
        # CSVLoader：默认逗号分隔；utf-8-sig 剥离 BOM，否则首格带
        loader = CSVLoader(file_path, encoding="utf-8-sig")
    elif ext in (".xlsx", ".xls"):
        return _load_excel(file_path, filename)
    else:
        # 明确拒绝不支持的类型，而不是静默返回空结果
        raise ValueError(
            f"不支持的文件类型: {ext}，仅支持 .txt / .pdf / .docx / .csv / .xlsx / .xls"
        )

    return _with_parser_metadata(loader.load(), filename, f"{ext.lstrip('.')}-loader")

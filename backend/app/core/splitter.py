"""
文本切分模块
================
作用：把一篇长文档按语义边界切成多个 chunk（块），方便向量化和检索。

策略：按"章节标题"整章切分（cut_by_heading）——本项目的文档都是企业制度
（极短，681B~3.6KB），每章（如"七、福利待遇"）是一个完整语义单元。
整章独立成块比 RecursiveCharacterTextSplitter 自然切分更优：
- 自然切分会把 2-3 个不相关章节拼进同一块（旧病根），嵌入相似度被稀释、
  BM25 被无关关键词污染，导致"年假答案被埋"、检索命中率低。
- 整章独立成块后，块语义纯、标题保留，标题关键词参与向量化 + BM25 打分，
  大幅提升中文短查询召回（离线实测混合检索 13/14 命中）。

标题识别两种格式：
- Markdown 标题：`## 章节名`（13 份 txt 都这么写，`# ` 是文档名）
- 中文序号标题：`一、章节名`（docx 用这种，如"一、办公用品管理"）

注意：NUMBERED_RE 中文序号标题**只在整篇没有 `## ` 标题时启用**，
防止正文里的"一、我们主张"等句子被误切。
"""
import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import CHUNK_OVERLAP, CHUNK_SIZE

# 单个章节超过该字符数时，用 RecursiveCharacterTextSplitter 兜底再切
# （制度文档正文一般 <500 字符，1200 足够容纳极少数长章）
MAX_CHAPTER_LEN = 1200

# Markdown 标题行：`# 文档名` / `## 章节名` / `### 小节`
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# 中文序号标题行：`一、章节名` / `二. 章节名`（docx 用），只在无 ## 时启用
_NUMBERED_RE = re.compile(r"^[一二三四五六七八九十]+[、.．]\s*(.+?)\s*$")
# 裸标题行（OCR 文本等无 markdown 标记的章节名，如"密码管理"）：
# 长度 ≤ 10 且几乎全中文（允许少量空格/数字），且不以标点结尾。
# 用于把"打印到 PDF"经 OCR 还原的无标记文本也按章节切块。
_BARE_HEADING_RE = re.compile(
    r"^[一-鿿0-9\s]{2,10}$"
)
_CLAUSE_RE = re.compile(r"\b[A-Z]{2,}-\d{2}-\d{3}\b")
_SECTION_FIELD_RE = re.compile(r"章节[：:]\s*(.+?)\s*$")
_PAGE_SECTION_RE = re.compile(r"第\d{1,2}页\s+.+?\s+-\s*(.+?)\s*$")


def build_splitter() -> RecursiveCharacterTextSplitter:
    """创建兜底切分器实例（配置参数来自 .env / config.py）。

    返回值每次新建，因为切分器是有状态的（切分完内部会记录剩余文本）。
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,      # 每块目标字符数（默认 350）
        chunk_overlap=CHUNK_OVERLAP,  # 相邻块重叠字符数（默认 80）
        separators=[               # 切分优先级：从上到下依次尝试
            "\n==============================\n",
            "\n\n",                # 空行（段落边界）
            "\n",                  # 单换行
            "。",                  # 中文句号
            "！",                  # 中文感叹号
            "？",                  # 中文问号
            " ",                   # 空格
            "",                    # 兜底：按字符硬切
        ],
    )


def _extract_doc_title(lines: list) -> str:
    """从文档开头提取一级标题（`# 文档名`）作为文档名；没有则返回空串。"""
    for ln in lines[:5]:
        m = _HEADING_RE.match(ln.strip())
        if m and len(m.group(1)) == 1:  # 只有 `# ` 才算文档名，`## ` 是章节
            return m.group(2).strip()
    return ""


def _split_sections(lines: list) -> list:
    """按章节标题把文档拆成 [(heading, body_lines)]。

    - `## ` 开新节；`# ` 只作文档名（由调用方提取）不单独成节。
    - 整篇没有 `## ` 时，启用中文序号标题 `一、` 开新节（docx 场景）。
    - 无任何标题的文档返回 []，由调用方整篇作为一块。
    """
    has_md = any(_HEADING_RE.match(ln.strip()) for ln in lines)
    sections = []
    cur_heading = ""
    cur_body = []

    def flush():
        nonlocal cur_heading, cur_body
        if cur_body:  # 跳过纯标题空块（根治"万能候选"块）
            sections.append((cur_heading, cur_body))
        cur_heading = ""
        cur_body = []

    for ln in lines:
        s = ln.strip()
        m = _HEADING_RE.match(s)
        if m and len(m.group(1)) >= 2:  # `## ` 及以上是章节
            flush()
            cur_heading = m.group(2).strip()
            continue
        if m and len(m.group(1)) == 1:
            # `# 文档名` 是文档名（已由 doc_title 记录），跳过，不进正文，
            # 否则会产生"文档名\n# 文档名"这种无正文价值的空块
            continue
        if not has_md:
            n = _NUMBERED_RE.match(s)
            if n:
                flush()
                cur_heading = n.group(1).strip()
                continue
            # 裸标题：短行且非标点结尾（OCR 文本的章节名）
            if _BARE_HEADING_RE.match(s):
                flush()
                cur_heading = s
                continue
        cur_body.append(ln)
    flush()
    return sections


def _guess_section_from_lines(lines: list[str]) -> str:
    """从表格/CSV/PDF页文本中兜底提取章节名。"""
    for ln in lines[:12]:
        s = ln.strip()
        m = _SECTION_FIELD_RE.search(s)
        if m:
            return m.group(1).strip()
        m = _PAGE_SECTION_RE.search(s)
        if m:
            return m.group(1).strip()
    return ""


def _split_clause_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """把一个章节继续按条款编号拆块。

    返回 [(clause_id, lines)]；无条款号的前置说明保留为 clause_id="" 的块。
    """
    blocks: list[tuple[str, list[str]]] = []
    cur_clause = ""
    cur_lines: list[str] = []

    def flush():
        nonlocal cur_clause, cur_lines
        if cur_lines:
            blocks.append((cur_clause, cur_lines))
        cur_clause = ""
        cur_lines = []

    for ln in lines:
        match = _CLAUSE_RE.search(ln)
        if match:
            flush()
            cur_clause = match.group(0)
        cur_lines.append(ln)
    flush()
    return blocks


def _structured_prefix(source: str, doc_title: str, heading: str, clause_id: str) -> str:
    """统一 chunk 结构头，让标题、章节、条款号进入向量和 BM25。"""
    source_name = source or doc_title or "未知来源"
    section = heading or doc_title or ""
    parts = [source_name]
    if section:
        parts.append(section)
    if clause_id:
        parts.append(clause_id)
    return "来源：" + " > ".join(parts)


def cut_by_heading(documents) -> list:
    """按章节标题整章切分（本项目主切分策略）。

    每块 page_content = "文档名 章节标题\n正文"：
    - 标题拼进 text → BM25 在 entity["text"] 上打分时自动吃到标题关键词
      （如"年假"命中"福利待遇"标题、"餐补"命中"食堂管理"标题），
      这是召回提升的关键。
    - 前端/模型展示时 text 自带"文档名 章节"，引用溯源更清晰，零前端改动。
    """
    out = []
    for doc in documents:
        source = (doc.metadata or {}).get("source", "")
        doc_title = _extract_doc_title(doc.page_content.splitlines()) or source
        sections = _split_sections(doc.page_content.splitlines())
        if not sections:
            sections = [("", doc.page_content.splitlines())]
        for heading, body_lines in sections:
            guessed_heading = _guess_section_from_lines(body_lines)
            source_stem = source.rsplit(".", 1)[0] if source else ""
            if guessed_heading and (not heading or heading == doc_title or heading == source_stem):
                effective_heading = guessed_heading
            else:
                effective_heading = heading or guessed_heading
            for clause_id, clause_lines in _split_clause_blocks(body_lines):
                body = "\n".join(clause_lines).strip()
                if not body:
                    continue
                # 过滤噪声空块：正文过短（OCR 噪声如"aes tr"、孤立标题残留），
                # 无检索价值，直接跳过，避免产生"万能候选"垃圾块。
                # 正常章节正文一般 >30 字；20 字阈值平衡"滤噪声"与"保短章"。
                if len(body) < 20:
                    continue
                prefix = _structured_prefix(source, doc_title, effective_heading, clause_id)
                if len(body) > MAX_CHAPTER_LEN:
                    # 超长条款/章节：用 RecursiveCharacterTextSplitter 兜底切，
                    # 每段都带结构头，保证标题和条款关键词仍参与打分
                    for sub in build_splitter().split_text(body):
                        sub = sub.strip()
                        if sub:
                            out.append(Document(page_content=f"{prefix}\n{sub}"))
                else:
                    out.append(Document(page_content=f"{prefix}\n{body}"))
    return out


def split_documents(documents) -> list:
    """把加载器产出的 Document 列表切成小块。

    参数：documents —— load_document() 返回的 List[Document]
    返回：切分后的 Document 列表（每块 page_content 是被切出的文本）

    说明：默认走"按标题整章切分"（cut_by_heading）。
    早期版本用 RecursiveCharacterTextSplitter 自然切分，短文档下会把
    多个不相关章节拼进一块，实测命中率低；本版改为整章独立成块。
    """
    return cut_by_heading(documents)

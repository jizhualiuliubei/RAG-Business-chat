"""
文本切分模块
================
作用：把一篇长文档按语义边界切成多个 chunk（块），方便向量化和检索。

策略：按"章节标题"整章切分（cut_by_heading）——本项目的文档都是企业制度
（篇幅短、按章节编写），每章（如"七、福利待遇"）是一个完整语义单元。
整章独立成块比 RecursiveCharacterTextSplitter 自然切分更优：
- 自然切分会把 2-3 个不相关章节拼进同一块（旧病根），嵌入相似度被稀释、
  BM25 被无关关键词污染，导致"年假答案被埋"、检索命中率低。
- 整章独立成块后，块语义纯、标题保留，标题关键词参与向量化 + BM25 打分，
  明显提升中文短查询召回。

标题识别两种格式：
- Markdown 标题：`## 章节名`（TXT 语料都这么写，`# ` 是文档名）
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
_CLAUSE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,}-\d{2}-\d{3}(?![A-Za-z0-9])")
_AUX_ROW_CODE_RE = re.compile(r"\b[A-Za-z0-9\u4e00-\u9fff]+-P\d{2}-S\d{2}\b")
# 「章节：X」字段：X 一直取到字段分隔符为止，不能取到行尾 ——
# 表格行展开后同一行里还有很多别的列（`…；章节：采购审批；条款编号：PUR-03-002；…`），
# 取到行尾会把整个表格行当成章节名。
_SECTION_FIELD_RE = re.compile(r"章节[：:]\s*([^；;，,\n|]+)")
_PAGE_SECTION_RE = re.compile(r"第\d{1,2}页\s+.+?\s+-\s*([^；;，,\n|]+?)\s*$")
_MD_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")

# 零宽字符与 BOM。真实语料里它们很常见：CSV 逐行带 BOM（utf-8-sig 只剥文件开头那一个）、
# PDF 抽出的文本夹零宽空格。混进正文和结构头会污染 BM25 的 bigram 与 embedding 文本，
# 也会让「文档」这类字段名永远差一个不可见字符。切片前统一剥掉。
# 用码点构造，源码里不放不可见字符 —— 否则编辑器或换行转换可能把它们悄悄吃掉。
_ZERO_WIDTH_CODES = (0xFEFF, 0x200B, 0x200C, 0x200D)
_INVISIBLE_RE = re.compile("[" + "".join(map(chr, _ZERO_WIDTH_CODES)) + "]")

# 兜底找章节名时向下扫多少行（见 _guess_section_from_lines）：
# 表格类资料的「章节：X」在第 4~6 个表格行，太小会漏、太大可能串到下一页
_SECTION_SCAN_LINES = 24


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


def _split_sections(lines: list, doc_title: str = "") -> list:
    """按章节标题把文档拆成 [(heading, body_lines)]。

    - `## ` 开新节；`# ` 只作文档名（由调用方提取）不单独成节 ——
      但仅限于与 doc_title 相同的那一行。MarkItDown 转 docx/PDF 时会用
      `# 第01页 …` 这类一级标题分页，那不是文档名，必须当章节标题用；
      否则整篇塌成一个章节，每页的块都挂着一个错误的章节名。
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
            title = m.group(2).strip()
            if not doc_title or title == doc_title:
                # `# 文档名` 已由 doc_title 记录，不进正文，
                # 否则会产生"文档名\n# 文档名"这种无正文价值的空块
                continue
            flush()
            cur_heading = title
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


def _is_markdown_table_row(line: str) -> bool:
    return "|" in line and line.strip().startswith("|") and line.strip().endswith("|")


def _split_markdown_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _expand_markdown_tables(lines: list[str]) -> list[str]:
    """把 Markdown 表格改写为带表头语义的行文本，提升表格 RAG 可读性。"""
    out: list[str] = []
    i = 0
    while i < len(lines):
        if (
            i + 2 < len(lines)
            and _is_markdown_table_row(lines[i])
            and _MD_TABLE_SEPARATOR_RE.match(lines[i + 1].strip())
        ):
            headers = _split_markdown_table_cells(lines[i])
            i += 2
            # MarkItDown 转 Word/Excel 表格时，有时会先写一个全空的首行，
            # 真正的表头落在分隔行之后：
            #     |  |  |  |  |
            #     | --- | --- | --- | --- |
            #     | 控制点 | 责任人 | 证据 | 风险提示 |
            # 这种形态下必须把分隔行后的第一行当表头，否则每一列都会退化成
            # "列1/列2/列3"，表头语义全丢 —— 表头（控制点/责任人/证据/风险提示）
            # 恰恰是 BM25 和向量能利用的关键词。
            if not any(h.strip() for h in headers) and _is_markdown_table_row(lines[i]):
                headers = _split_markdown_table_cells(lines[i])
                i += 1
            while i < len(lines) and _is_markdown_table_row(lines[i]):
                cells = _split_markdown_table_cells(lines[i])
                parts = []
                for idx, value in enumerate(cells):
                    if not value:
                        continue
                    header = headers[idx] if idx < len(headers) and headers[idx] else f"列{idx + 1}"
                    parts.append(f"{header}：{value}")
                if parts:
                    out.append("表格行：" + "；".join(parts))
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return out


def _guess_section_from_lines(lines: list[str]) -> str:
    """从表格/CSV/PDF页文本中兜底提取章节名。

    扫一段而不是只看第一行：表格类资料里「章节：X」往往在第 4~6 个表格行，
    窗口太小会漏掉，于是章节名退化成载体名（工作表名、页码标题）。
    窗口按"一页"的量级取，页面级切分下不会串到别的章节。
    """
    for ln in lines[:_SECTION_SCAN_LINES]:
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
        stripped = ln.strip()
        match = _CLAUSE_RE.search(ln)
        if match:
            flush()
            cur_clause = match.group(0)
            cur_lines.append(ln)
            continue

        # MarkItDown 会把 Word/Excel 表格展开成连续的 Markdown 表格行。
        # 如果某个表格行紧跟在 HR-xx-xxx 条款之后，但自身并不包含该条款，
        # 继续继承上一条款号会把“整改要求”等辅助行误标成 HR-01-004。
        # 这里在遇到表格/辅助编号行时切回章节级块，避免条款号污染后续 chunk。
        if cur_clause and (
            stripped.startswith("表格行：")
            or _AUX_ROW_CODE_RE.search(stripped)
        ):
            flush()
        cur_lines.append(ln)
    flush()
    return blocks


def _structured_prefix(source: str, doc_title: str, heading: str, clause_id: str) -> str:
    """统一 chunk 结构头，让标题、章节、条款号进入向量和 BM25。

    章节名与文档名相同就不写：docx/PDF 里常常取不到 `# 文档名`，
    doc_title 于是退化成文件名，写出来变成「来源：X.docx > X.docx」，
    白占一份 BM25 的权重，还让所有块长得更像。
    """
    source_name = source or doc_title or "未知来源"
    section = heading or doc_title or ""
    if section and section in (source_name, source_name.rsplit(".", 1)[0]):
        section = ""
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
        # 先剥掉 BOM / 零宽字符再切：CSV 常常逐行带 BOM（utf-8-sig 只剥文件开头那一个），
        # 不剥的话它们会混进结构头和正文，污染 BM25 的 bigram 与 embedding 文本。
        raw = _INVISIBLE_RE.sub("", doc.page_content or "")
        lines = _expand_markdown_tables(raw.splitlines())
        doc_title = _extract_doc_title(lines) or source
        sections = _split_sections(lines, doc_title)
        if not sections:
            sections = [("", raw.splitlines())]
        for heading, body_lines in sections:
            # 内容里的「章节：X」字段比标题本身更权威；标题是页码型时从尾部抽出章节名。
            # 标题常常来自载体而不是业务章节 —— 同一批资料里，xlsx 的章节被写成
            # 工作表名「评测明细」，txt 的章节被写成「第01页 … - 故障响应」，
            # 这两种都让章节名失去检索价值。
            guessed_heading = _guess_section_from_lines(body_lines)
            if not guessed_heading and heading:
                m = _PAGE_SECTION_RE.match(heading)
                if m:
                    guessed_heading = m.group(1).strip()
            effective_heading = guessed_heading or heading or ""
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
    多个不相关章节拼进一块，检索命中率低；本版改为整章独立成块。
    """
    return cut_by_heading(documents)

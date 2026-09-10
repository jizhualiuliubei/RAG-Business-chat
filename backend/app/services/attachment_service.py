"""
会话附件业务逻辑
================
作用：管理"对话中上传的附件"（会话级临时上下文）。

- 上传：校验格式 → 按会话落盘 → 解析文本 → 存 DB
- 列表/删除：按会话隔离
- 注入：把某会话所有附件文本拼成 prompt 片段（用于问答时注入）

设计：
- 附件属于会话（conversation_id），不同会话互相隔离
- 附件不入知识库，只作为"当前对话的临时上下文"
- 持久化：元数据存 DB，文件存 ATTACHMENT_DIR/{conversation_id}/
"""
from pathlib import Path
import json
import math
import re

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ATTACHMENT_DIR, MAX_FILE_SIZE
from app.core.loader import load_document
from app.core.splitter import split_documents
from app.models.attachment import ConversationAttachment, ConversationAttachmentChunk

# 附件允许的格式（复用文档解析器支持的格式）
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx", ".csv", ".xlsx", ".xls"}

# 单个附件注入的最大字符数（防止超大文件把 prompt 撑爆）
MAX_CONTENT_CHARS = 6000
MAX_ATTACHMENT_CONTEXT_CHARS = 4500


def _get_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def _validate_ext(filename: str) -> None:
    ext = _get_ext(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"不支持的附件类型 {ext}，仅支持 .txt / .pdf / .docx / .csv / .xlsx"
        )


def _validate_size(file_size: int) -> None:
    """校验附件大小是否超限（超大文件解析/注入 prompt 会卡住或撑爆）。"""
    if file_size > MAX_FILE_SIZE:
        limit_mb = MAX_FILE_SIZE // (1024 * 1024)
        raise ValueError(f"附件超过大小限制 {limit_mb}MB，请拆分后上传")


def create_attachment(
    db: Session,
    conversation_id: int,
    file: UploadFile,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> ConversationAttachment:
    """上传附件：校验 → 落盘 → 解析 → 存 DB。返回附件记录。"""
    _validate_ext(file.filename)
    file_size = getattr(file, "size", 0) or 0
    _validate_size(file_size)

    # 按会话建目录：data/attachments/{conversation_id}/
    conv_dir = ATTACHMENT_DIR / str(conversation_id)
    conv_dir.mkdir(parents=True, exist_ok=True)

    # 用时间戳 + 原文件名落盘，避免重名覆盖
    import time
    safe_name = f"{int(time.time())}_{Path(file.filename).name}"
    stored_path = conv_dir / safe_name
    stored_path.write_bytes(file.file.read())

    status = "done"
    failure_reason = ""
    try:
        docs = load_document(str(stored_path), file.filename)
        chunks = split_documents(docs)
        if not chunks:
            chunks = docs
        content = "\n\n".join(d.page_content for d in docs).strip()
    except Exception as exc:
        docs = []
        chunks = []
        content = ""
        status = "failed"
        failure_reason = str(exc)[:1000] or "附件解析失败"

    # 截断超大内容（防止注入 prompt 过大）
    content = content[:MAX_CONTENT_CHARS]
    summary = _summarize_content(content, file.filename, status, failure_reason)

    att = ConversationAttachment(
        enterprise_id=enterprise_id,
        user_id=user_id,
        conversation_id=conversation_id,
        filename=file.filename,
        stored_path=str(stored_path),
        content=content,
        status=status,
        failure_reason=failure_reason,
        summary=summary,
        chunk_count=len(chunks),
        content_chars=len(content),
        file_size=file_size,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    _replace_attachment_chunks(
        db,
        att,
        chunks,
        enterprise_id=enterprise_id,
        user_id=user_id,
    )
    return att


def list_attachments(
    db: Session,
    conversation_id: int,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> list[ConversationAttachment]:
    """某会话的全部附件（按时间正序）。"""
    stmt = (
        select(ConversationAttachment)
        .where(ConversationAttachment.conversation_id == conversation_id)
        .order_by(ConversationAttachment.id.asc())
    )
    if enterprise_id is not None:
        stmt = stmt.where(ConversationAttachment.enterprise_id == enterprise_id)
    if user_id is not None:
        stmt = stmt.where(ConversationAttachment.user_id == user_id)
    return db.execute(stmt).scalars().all()


def delete_attachment(
    db: Session,
    conversation_id: int,
    attachment_id: int,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> bool:
    """删除附件（DB 记录 + 落盘文件）。"""
    query = (
        db.query(ConversationAttachment)
        .filter(
            ConversationAttachment.id == attachment_id,
            ConversationAttachment.conversation_id == conversation_id,
        )
    )
    if enterprise_id is not None:
        query = query.filter(ConversationAttachment.enterprise_id == enterprise_id)
    if user_id is not None:
        query = query.filter(ConversationAttachment.user_id == user_id)
    att = query.first()
    if not att:
        return False
    # 删文件（忽略失败，文件缺失不影响）
    try:
        Path(att.stored_path).unlink(missing_ok=True)
    except Exception:
        pass
    db.query(ConversationAttachmentChunk).filter(
        ConversationAttachmentChunk.attachment_id == attachment_id,
        ConversationAttachmentChunk.conversation_id == conversation_id,
    ).delete()
    db.delete(att)
    db.commit()
    return True


def build_attachments_context(
    db: Session,
    conversation_id: int,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """把某会话的附件文本拼成 prompt 注入片段。

    返回如：
    【附件内容】
    [附件1] 员工名册.csv
    姓名：张三 部门：技术部 ...

    [附件2] 产品价格表.xlsx
    ...
    """
    atts = list_attachments(db, conversation_id, enterprise_id=enterprise_id, user_id=user_id)
    if not atts:
        return ""
    ready = [att for att in atts if att.status == "done" and (att.summary or att.content)]
    if not ready:
        return ""
    blocks = []
    for i, att in enumerate(ready, 1):
        text = att.summary or att.content[:500]
        blocks.append(f"[附件{i}] {att.filename}\n摘要：{text}")
    return _attachment_context_header() + "\n" + "\n\n".join(blocks)


def retrieve_attachment_sources(
    db: Session,
    conversation_id: int,
    question: str,
    enterprise_id: int | None = None,
    user_id: int | None = None,
    top_k: int = 3,
) -> list[dict]:
    """从当前会话附件切片中召回相关片段。"""
    stmt = (
        select(ConversationAttachmentChunk)
        .where(ConversationAttachmentChunk.conversation_id == conversation_id)
        .order_by(ConversationAttachmentChunk.id.asc())
    )
    if enterprise_id is not None:
        stmt = stmt.where(ConversationAttachmentChunk.enterprise_id == enterprise_id)
    if user_id is not None:
        stmt = stmt.where(ConversationAttachmentChunk.user_id == user_id)
    chunks = db.execute(stmt).scalars().all()
    if not chunks:
        return []
    query_terms = _terms(question)
    ranked = []
    for chunk in chunks:
        score = _score(query_terms, chunk.text, chunk.source)
        if score <= 0:
            continue
        meta = _loads(chunk.metadata_json)
        ranked.append({
            "source_type": "attachment",
            "attachment_id": chunk.attachment_id,
            "source": chunk.source,
            "chunk_id": chunk.chunk_id,
            "score": score,
            "text": chunk.text,
            "kb_name": "会话附件",
            "metadata": meta,
            "locator": meta.get("locator", ""),
        })
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:top_k]


def build_attachment_rag_context(sources: list[dict]) -> str:
    if not sources:
        return ""
    blocks = []
    total = 0
    for i, src in enumerate(sources, 1):
        text = src.get("text", "")
        remaining = MAX_ATTACHMENT_CONTEXT_CHARS - total
        if remaining <= 0:
            break
        clipped = text[:remaining]
        total += len(clipped)
        locator = f" · {src.get('locator')}" if src.get("locator") else ""
        blocks.append(f"[附件来源{i}] {src.get('source', '未知附件')}{locator}\n{clipped}")
    return _attachment_context_header() + "\n" + "\n\n".join(blocks)


def serialize_attachment(att: ConversationAttachment) -> dict:
    return {
        "id": att.id,
        "filename": att.filename,
        "created_at": att.created_at.isoformat(),
        "status": att.status,
        "failure_reason": att.failure_reason or "",
        "summary": att.summary or "",
        "chunk_count": att.chunk_count or 0,
        "content_chars": att.content_chars or len(att.content or ""),
        "file_size": att.file_size or 0,
    }


def _replace_attachment_chunks(
    db: Session,
    att: ConversationAttachment,
    chunks: list,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> None:
    db.query(ConversationAttachmentChunk).filter(
        ConversationAttachmentChunk.attachment_id == att.id,
    ).delete()
    for index, chunk in enumerate(chunks, 1):
        text = (getattr(chunk, "page_content", "") or "").strip()
        if not text:
            continue
        metadata = getattr(chunk, "metadata", {}) or {}
        locator = _guess_locator(text, metadata)
        db.add(ConversationAttachmentChunk(
            enterprise_id=enterprise_id,
            user_id=user_id,
            conversation_id=att.conversation_id,
            attachment_id=att.id,
            chunk_id=index,
            source=att.filename,
            text=text[:MAX_CONTENT_CHARS],
            metadata_json=json.dumps({"locator": locator, **metadata}, ensure_ascii=False),
        ))
    db.commit()


def _summarize_content(content: str, filename: str, status: str, failure_reason: str) -> str:
    if status == "failed":
        return f"附件解析失败：{failure_reason}"
    clean = re.sub(r"\s+", " ", content or "").strip()
    if not clean:
        return f"{filename} 未解析出有效文本"
    return clean[:240]


def _attachment_context_header() -> str:
    return (
        "【会话附件】以下内容来自用户在当前会话上传的临时材料，仅当前账号当前会话有效。\n"
        "回答时必须把会话附件和企业知识库区分开；如果两者存在差异，需明确说明："
        "知识库依据是…；附件显示是…；两者存在差异，不能直接合并为确定结论。"
    )


def _terms(text: str) -> list[str]:
    raw = text or ""
    terms: list[str] = []
    terms.extend(re.findall(r"[A-Z]{2,4}-\d{2}-\d{3}|[A-Za-z0-9]{2,}", raw))
    chinese_runs = re.findall(r"[\u4e00-\u9fa5]+", raw)
    for run in chinese_runs:
        if len(run) <= 3:
            terms.append(run)
            continue
        for size in (2, 3):
            terms.extend(run[i : i + size] for i in range(0, len(run) - size + 1))
    seen = set()
    deduped = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


def _score(query_terms: list[str], text: str, source: str) -> float:
    haystack = f"{source}\n{text}"
    if not query_terms:
        return 0.0
    hits = 0
    weighted = 0.0
    for term in query_terms:
        if term and term in haystack:
            hits += 1
            weighted += 2.0 if re.fullmatch(r"[A-Z]{2,4}-\d{2}-\d{3}", term) else 1.0
    if hits == 0:
        return 0.0
    return min(1.0, weighted / math.sqrt(max(len(query_terms), 1)) / 2.0)


def _guess_locator(text: str, metadata: dict) -> str:
    if metadata.get("page"):
        return f"第 {metadata['page']} 页"
    first_line = (text or "").splitlines()[0] if text else ""
    if first_line.startswith("[工作表："):
        return first_line.strip("[]")
    if "来源：" in first_line and ">" in first_line:
        return first_line.replace("来源：", "").strip()
    return ""


def _loads(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}

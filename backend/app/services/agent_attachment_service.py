"""Agent 任务级附件：租户隔离落盘、解析摘要与哈希。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import select

from app.config import AGENT_ATTACHMENT_DIR, MAX_FILE_SIZE
from app.core.loader import load_document
from app.models.agent import AgentApproval, AgentBusinessRecord, AgentTask, AgentTaskAttachment


ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx", ".csv", ".xlsx", ".xls"}
MAX_SUMMARY_CHARS = 6000


def serialize_attachment(item: AgentTaskAttachment) -> dict:
    return {
        "id": item.id,
        "task_id": item.task_id,
        "filename": item.filename,
        "file_hash": item.file_hash,
        "file_size": item.file_size,
        "status": item.status,
        "summary": item.summary,
        "failure_reason": item.failure_reason,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def create_task_attachment(db, task: AgentTask, user_id: int, file: UploadFile) -> AgentTaskAttachment:
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("不支持的附件类型，仅支持 TXT、PDF、DOCX、CSV、XLSX/XLS")

    content = file.file.read(MAX_FILE_SIZE + 1)
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"附件超过大小限制 {MAX_FILE_SIZE // (1024 * 1024)}MB")
    if not content:
        raise ValueError("附件内容为空")
    if task.status not in {"waiting_input", "failed"}:
        raise ValueError("仅待确认、待补充信息或失败任务可上传；审批中请先退回补充材料")

    task_dir = AGENT_ATTACHMENT_DIR / str(task.enterprise_id) / str(task.id)
    task_dir.mkdir(parents=True, exist_ok=True)
    stored_path = task_dir / f"{uuid4().hex}{extension}"
    stored_path.write_bytes(content)

    status = "done"
    failure_reason = ""
    summary = ""
    try:
        docs = load_document(str(stored_path), filename)
        summary = "\n\n".join((doc.page_content or "").strip() for doc in docs).strip()
        summary = summary[:MAX_SUMMARY_CHARS]
        if not summary:
            raise ValueError("附件未解析出有效文本")
    except Exception as exc:
        status = "failed"
        failure_reason = str(exc)[:1000] or "附件解析失败"

    item = AgentTaskAttachment(
        enterprise_id=task.enterprise_id,
        user_id=user_id,
        task_id=task.id,
        filename=filename,
        stored_path=str(stored_path),
        file_hash=hashlib.sha256(content).hexdigest(),
        file_size=len(content),
        status=status,
        summary=summary,
        failure_reason=failure_reason,
    )
    try:
        task = db.execute(select(AgentTask).where(AgentTask.id == task.id)
                          .with_for_update().execution_options(populate_existing=True)).scalar_one()
        if task.status not in {"waiting_input", "failed"}:
            raise ValueError("任务状态已变更，不能继续上传材料")
        if db.execute(select(AgentBusinessRecord.id).where(
                AgentBusinessRecord.task_id == task.id).limit(1)).first():
            raise ValueError("正式记录已生成，不能变更执行材料")
        for approval in db.execute(select(AgentApproval).where(
                AgentApproval.task_id == task.id, AgentApproval.status.in_(["pending", "approved"]))).scalars():
            approval.status = "superseded"
            approval.comment = "任务材料已变更，原审批不能用于新申请"
        if task.status == "failed":
            task.current_node = "materials_updated"
        task.version += 1
        db.add(item)
        db.commit()
    except Exception:
        db.rollback()
        stored_path.unlink(missing_ok=True)
        raise
    db.refresh(item)
    return item


def list_task_attachments(db, task: AgentTask) -> list[AgentTaskAttachment]:
    return list(
        db.execute(
            select(AgentTaskAttachment)
            .where(
                AgentTaskAttachment.task_id == task.id,
                AgentTaskAttachment.enterprise_id == task.enterprise_id,
            )
            .order_by(AgentTaskAttachment.id.asc())
        ).scalars().all()
    )

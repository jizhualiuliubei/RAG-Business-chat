"""
管理员后台服务
================
聚合用户、知识库、文档和审计数据，供独立 /admin 页面使用。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message
from app.models.user import User
from app.services import audit_service, auth_service, enterprise_service, knowledge_base_service
from app.models.enterprise import Enterprise


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else None


def serialize_user(user: User, enterprise: Enterprise | None = None) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "enterprise_id": user.enterprise_id,
        "enterprise_name": enterprise.name if enterprise else "",
        "enterprise_code": enterprise.code if enterprise else "",
        "created_at": _iso(user.created_at),
        "last_login_at": _iso(user.last_login_at),
    }


def get_overview(db: Session, enterprise_id: int | None = None) -> dict:
    def scoped(stmt, model):
        if enterprise_id is not None and hasattr(model, "enterprise_id"):
            return stmt.where(model.enterprise_id == enterprise_id)
        return stmt

    return {
        "user_count": db.execute(scoped(select(func.count()).select_from(User), User)).scalar_one(),
        "active_user_count": db.execute(scoped(select(func.count()).select_from(User).where(User.status == "active"), User)).scalar_one(),
        "kb_count": db.execute(scoped(
            select(func.count()).select_from(KnowledgeBase).where(knowledge_base_service.valid_name_filter()),
            KnowledgeBase,
        )).scalar_one(),
        "doc_count": db.execute(scoped(select(func.count()).select_from(Document), Document)).scalar_one(),
        "chunk_count": db.execute(scoped(select(func.coalesce(func.sum(Document.chunk_count), 0)), Document)).scalar_one(),
        "conversation_count": db.execute(scoped(select(func.count()).select_from(Conversation), Conversation)).scalar_one(),
        "message_count": db.execute(scoped(select(func.count()).select_from(Message), Message)).scalar_one(),
        "audit_count": db.execute(scoped(select(func.count()).select_from(AuditLog), AuditLog)).scalar_one(),
        "recent_audits": audit_service.list_audit_logs(db, limit=6, enterprise_id=enterprise_id),
    }


def list_users(db: Session, enterprise_id: int | None = None) -> list[dict]:
    stmt = select(User).where(User.status != "deleted").order_by(User.id.asc())
    if enterprise_id is not None:
        stmt = stmt.where(User.enterprise_id == enterprise_id)
    users = db.execute(stmt).scalars().all()
    enterprise_ids = {user.enterprise_id for user in users if user.enterprise_id is not None}
    enterprises = {}
    if enterprise_ids:
        rows = db.execute(select(Enterprise).where(Enterprise.id.in_(enterprise_ids))).scalars().all()
        enterprises = {item.id: item for item in rows}
    return [serialize_user(user, enterprises.get(user.enterprise_id)) for user in users]


def _actor_can_manage_user(actor: dict, user: User) -> bool:
    actor_role = actor.get("role")
    if actor_role in {"admin", "system_admin"}:
        return True
    actor_enterprise_id = int(actor["enterprise_id"]) if actor.get("enterprise_id") is not None else None
    return actor_role == "enterprise_admin" and user.enterprise_id == actor_enterprise_id


def _ensure_employee_manageable(actor: dict, user: User) -> None:
    if not _actor_can_manage_user(actor, user):
        raise LookupError("用户不存在")
    if user.role != "employee":
        raise ValueError("只能管理员工账号")


def _validate_username(db: Session, *, enterprise_id: int | None, username: str, user_id: int | None = None) -> None:
    if len(username) < 3:
        raise ValueError("用户名至少需要 3 个字符")
    stmt = select(User).where(User.enterprise_id == enterprise_id, User.username == username, User.status != "deleted")
    if user_id is not None:
        stmt = stmt.where(User.id != user_id)
    exists = db.execute(stmt).scalar_one_or_none()
    if exists is not None:
        raise ValueError("用户名已存在")


def create_employee(
    db: Session,
    *,
    username: str,
    password: str,
    display_name: str,
    enterprise_code: str | None = None,
    actor: dict,
) -> dict:
    username = username.strip()
    display_name = display_name.strip() or username
    if len(username) < 3:
        raise ValueError("用户名至少需要 3 个字符")
    if len(password) < 6:
        raise ValueError("密码至少需要 6 个字符")
    actor_enterprise_id = int(actor["enterprise_id"]) if actor.get("enterprise_id") is not None else None
    enterprise_id = actor_enterprise_id
    actor_role = actor.get("role")
    target_code = (enterprise_code or "").strip()
    if target_code:
        target_enterprise = enterprise_service.get_enterprise_by_code(db, target_code)
        if target_enterprise is None:
            raise ValueError("目标企业不存在")
        if target_enterprise.status != "active":
            raise ValueError("只能为已启用企业创建员工")
        if actor_role not in {"admin", "system_admin"} and target_enterprise.id != actor_enterprise_id:
            raise ValueError("不能为其他企业创建员工")
        enterprise_id = target_enterprise.id
    _validate_username(db, enterprise_id=enterprise_id, username=username)

    user = User(
        enterprise_id=enterprise_id,
        username=username,
        password_hash=auth_service.hash_password(password),
        display_name=display_name,
        role="employee",
        status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit_service.record_audit(
        db,
        actor_id=int(actor["sub"]),
        actor_name=actor["display_name"],
        action="create_user",
        target_type="user",
        target_name=username,
        enterprise_id=enterprise_id,
    )
    enterprise = db.get(Enterprise, enterprise_id) if enterprise_id is not None else None
    return serialize_user(user, enterprise)


def update_user(
    db: Session,
    *,
    user_id: int,
    username: str | None,
    display_name: str | None,
    password: str | None,
    actor: dict,
) -> dict:
    user = db.get(User, user_id)
    if user is None or user.status == "deleted":
        raise LookupError("用户不存在")
    _ensure_employee_manageable(actor, user)

    if username is not None:
        next_username = username.strip()
        _validate_username(db, enterprise_id=user.enterprise_id, username=next_username, user_id=user.id)
        user.username = next_username
    if display_name is not None:
        user.display_name = display_name.strip() or user.username
    if password:
        if len(password) < 6:
            raise ValueError("密码至少需要 6 个字符")
        user.password_hash = auth_service.hash_password(password)
    db.commit()
    db.refresh(user)
    audit_service.record_audit(
        db,
        actor_id=int(actor["sub"]),
        actor_name=actor["display_name"],
        action="update_user",
        target_type="user",
        target_name=user.username,
        enterprise_id=user.enterprise_id,
    )
    enterprise = db.get(Enterprise, user.enterprise_id) if user.enterprise_id is not None else None
    return serialize_user(user, enterprise)


def delete_user(db: Session, *, user_id: int, actor: dict) -> dict:
    user = db.get(User, user_id)
    if user is None or user.status == "deleted":
        raise LookupError("用户不存在")
    _ensure_employee_manageable(actor, user)
    user.status = "deleted"
    db.commit()
    db.refresh(user)
    audit_service.record_audit(
        db,
        actor_id=int(actor["sub"]),
        actor_name=actor["display_name"],
        action="delete_user",
        target_type="user",
        target_name=user.username,
        enterprise_id=user.enterprise_id,
    )
    enterprise = db.get(Enterprise, user.enterprise_id) if user.enterprise_id is not None else None
    return serialize_user(user, enterprise)


def update_user_status(db: Session, *, user_id: int, status: str, actor: dict) -> dict:
    if status not in {"active", "disabled"}:
        raise ValueError("账号状态只能是 active 或 disabled")
    user = db.get(User, user_id)
    if user is None:
        raise LookupError("用户不存在")
    actor_role = actor.get("role")
    actor_enterprise_id = int(actor["enterprise_id"]) if actor.get("enterprise_id") is not None else None
    if user.status == "deleted":
        raise LookupError("用户不存在")
    if actor_role not in {"admin", "system_admin"} and user.enterprise_id != actor_enterprise_id:
        raise LookupError("用户不存在")
    if user.role != "employee":
        raise ValueError("只能管理员工账号")
    user.status = status
    db.commit()
    db.refresh(user)
    audit_service.record_audit(
        db,
        actor_id=int(actor["sub"]),
        actor_name=actor["display_name"],
        action="update_user_status",
        target_type="user",
        target_name=user.username,
        enterprise_id=user.enterprise_id,
    )
    enterprise = db.get(Enterprise, user.enterprise_id) if user.enterprise_id is not None else None
    return serialize_user(user, enterprise)


def get_knowledge_overview(db: Session, enterprise_id: int | None = None) -> dict:
    kb_stmt = select(KnowledgeBase).where(knowledge_base_service.valid_name_filter()).order_by(KnowledgeBase.id.asc())
    doc_stmt = select(Document).order_by(Document.created_at.desc())
    if enterprise_id is not None:
        kb_stmt = kb_stmt.where(KnowledgeBase.enterprise_id == enterprise_id)
        doc_stmt = doc_stmt.where(Document.enterprise_id == enterprise_id)
    kb_rows = db.execute(kb_stmt).scalars().all()
    docs = db.execute(doc_stmt).scalars().all()
    result = []
    for kb in kb_rows:
        kb_docs = [doc for doc in docs if doc.kb_id == kb.id]
        status_counts = {"done": 0, "processing": 0, "failed": 0}
        for doc in kb_docs:
            if doc.status in status_counts:
                status_counts[doc.status] += 1
        last_upload = max((doc.created_at for doc in kb_docs), default=None)
        result.append({
            "id": kb.id,
            "name": kb.name,
            "doc_count": len(kb_docs),
            "chunk_count": sum(doc.chunk_count or 0 for doc in kb_docs),
            "status_counts": status_counts,
            "last_upload_at": _iso(last_upload),
            "created_at": _iso(kb.created_at),
            "documents": [
                {
                    "id": doc.id,
                    "filename": doc.filename,
                    "file_size": doc.file_size,
                    "status": doc.status,
                    "failure_reason": doc.failure_reason,
                    "parser_name": doc.parser_name,
                    "parser_version": doc.parser_version,
                    "parsed_chars": doc.parsed_chars,
                    "chunk_count": doc.chunk_count,
                    "created_at": _iso(doc.created_at),
                }
                for doc in kb_docs
            ],
        })
    return {"knowledge_bases": result}

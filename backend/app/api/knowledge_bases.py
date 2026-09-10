"""
知识库管理路由
================
对应知识点：FastAPI 路由 + 依赖注入
提供：知识库列表 / 新建 / 删除（多知识库的入口）
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.dependencies import current_enterprise_id, require_admin
from app.services import audit_service, knowledge_base_service

router = APIRouter(prefix="/api/knowledge-bases", tags=["知识库管理"])


class CreateRequest(BaseModel):
    """新建知识库请求"""
    name: str


def _kb_to_dict(kb):
    return {
        "id": kb.id,
        "name": kb.name,
        "created_at": kb.created_at,
        "updated_at": getattr(kb, "updated_at", None),
        "enterprise_id": kb.enterprise_id,
    }


@router.get("")
def list_knowledge_bases(request: Request, db: Session = Depends(get_db)):
    """知识库列表"""
    return [
        _kb_to_dict(kb)
        for kb in knowledge_base_service.list_knowledge_bases(db, current_enterprise_id(request))
    ]


@router.post("")
def create_knowledge_base(
    body: CreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """新建知识库（name 唯一）"""
    try:
        enterprise_id = current_enterprise_id(request)
        kb = knowledge_base_service.create_knowledge_base(db, body.name, enterprise_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="create_knowledge_base",
        target_type="knowledge_base",
        target_name=kb.name,
        enterprise_id=enterprise_id,
    )
    return _kb_to_dict(kb)


@router.delete("/{kb_id}")
def delete_knowledge_base(
    kb_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """删除知识库（会删除其下文档和向量数据）"""
    enterprise_id = current_enterprise_id(request)
    target = knowledge_base_service.get_knowledge_base(db, kb_id, enterprise_id)
    target_name = target.name if target else str(kb_id)
    deleted = knowledge_base_service.delete_knowledge_base(db, kb_id, enterprise_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="知识库不存在")
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="delete_knowledge_base",
        target_type="knowledge_base",
        target_name=target_name,
        enterprise_id=enterprise_id,
    )
    return {"message": "删除成功"}

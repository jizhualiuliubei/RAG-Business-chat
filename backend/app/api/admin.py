"""
管理员后台接口
================
为独立 /admin 管理台提供用户、知识治理和审计数据。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import current_enterprise_id, require_admin, require_full_access
from app.database import get_db
from app.services import admin_service, audit_service, knowledge_base_service

router = APIRouter(
    prefix="/api/admin",
    tags=["管理员后台"],
    dependencies=[Depends(require_admin)],
)


class CreateUserRequest(BaseModel):
    username: str
    password: str
    display_name: str
    enterprise_code: str | None = None


class UpdateUserStatusRequest(BaseModel):
    status: str


class UpdateUserRequest(BaseModel):
    username: str | None = None
    display_name: str | None = None
    password: str | None = None


class UpdateKnowledgeBaseRequest(BaseModel):
    name: str


@router.get("/overview")
def get_admin_overview(request: Request, db: Session = Depends(get_db)):
    return admin_service.get_overview(db, current_enterprise_id(request))


@router.get("/users")
def get_admin_users(request: Request, db: Session = Depends(get_db)):
    user = request.state.auth_user
    enterprise_id = None if user.get("role") in {"admin", "system_admin"} else current_enterprise_id(request)
    return admin_service.list_users(db, enterprise_id)


@router.post("/users")
def create_admin_user(
    body: CreateUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    # 体验账号不能建子员工。注意前端「创建员工」按钮走的是这个接口，
    # 不是 /api/enterprise/users —— 两个入口都要拦，漏一个就白做。
    _full=Depends(require_full_access),
):
    try:
        return admin_service.create_employee(
            db,
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            enterprise_code=body.enterprise_code,
            actor=request.state.auth_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/users/{user_id}/status")
def update_admin_user_status(
    user_id: int,
    body: UpdateUserStatusRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return admin_service.update_user_status(
            db,
            user_id=user_id,
            status=body.status,
            actor=request.state.auth_user,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/users/{user_id}")
def update_admin_user(
    user_id: int,
    body: UpdateUserRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return admin_service.update_user(
            db,
            user_id=user_id,
            username=body.username,
            display_name=body.display_name,
            password=body.password,
            actor=request.state.auth_user,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/users/{user_id}")
def delete_admin_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return admin_service.delete_user(db, user_id=user_id, actor=request.state.auth_user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/knowledge-overview")
def get_admin_knowledge_overview(request: Request, db: Session = Depends(get_db)):
    return admin_service.get_knowledge_overview(db, current_enterprise_id(request))


@router.patch("/knowledge-bases/{kb_id}")
def update_admin_knowledge_base(
    kb_id: int,
    body: UpdateKnowledgeBaseRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        kb = knowledge_base_service.update_knowledge_base(db, kb_id, body.name, current_enterprise_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="update_knowledge_base",
        target_type="knowledge_base",
        target_name=kb.name,
        enterprise_id=current_enterprise_id(request),
    )
    return {"id": kb.id, "name": kb.name, "created_at": kb.created_at}


@router.get("/audit-logs")
def get_admin_audit_logs(request: Request, db: Session = Depends(get_db)):
    user = request.state.auth_user
    enterprise_id = None if user.get("role") in {"admin", "system_admin"} else current_enterprise_id(request)
    return {"items": audit_service.list_audit_logs(db, enterprise_id=enterprise_id)}

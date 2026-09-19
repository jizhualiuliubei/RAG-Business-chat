"""系统管理员租户治理接口。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import require_system_admin
from app.database import get_db
from app.services import audit_service, auth_service, enterprise_service

router = APIRouter(prefix="/api/system", tags=["系统管理"])


class RejectRequest(BaseModel):
    reason: str = ""


class StatusRequest(BaseModel):
    status: str


class EnterpriseCreateRequest(BaseModel):
    name: str
    code: str
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    logo_url: str | None = None
    status: str | None = None
    admin_username: str | None = None
    admin_display_name: str | None = None
    admin_password: str | None = None


class EnterpriseUpdateRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    logo_url: str | None = None


@router.get("/enterprises")
def list_enterprises(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin=Depends(require_system_admin),
):
    return {
        "items": [
            enterprise_service.enterprise_to_dict(item)
            for item in enterprise_service.list_enterprises(db, status=status, q=q)
        ]
    }


@router.post("/enterprises")
def create_enterprise(
    body: EnterpriseCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_system_admin),
):
    try:
        payload = body.model_dump(exclude_none=True)
        admin_username = (payload.pop("admin_username", "") or "").strip()
        admin_display_name = (payload.pop("admin_display_name", "") or "").strip()
        admin_password = (payload.pop("admin_password", "") or "").strip()
        enterprise = enterprise_service.create_enterprise(db, payload)
        admin_user = None
        initial_password = None
        if admin_username:
            initial_password = admin_password or enterprise_service.generate_initial_password()
            admin_user = enterprise_service.create_enterprise_admin(
                db,
                enterprise=enterprise,
                username=admin_username,
                display_name=admin_display_name or f"{enterprise.name}管理员",
                password=initial_password,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="create_enterprise",
        target_type="enterprise",
        target_id=enterprise.id,
        target_name=enterprise.name,
        enterprise_id=enterprise.id,
    )
    result = enterprise_service.enterprise_to_dict(enterprise)
    if admin_user is not None:
        result["admin_user"] = enterprise_service.user_to_dict(admin_user, enterprise)
        result["initial_password"] = initial_password
    return result


@router.get("/enterprises/{enterprise_id}")
def get_enterprise(
    enterprise_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_system_admin),
):
    enterprise = enterprise_service.get_enterprise(db, enterprise_id)
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    return enterprise_service.enterprise_to_dict(enterprise)


@router.patch("/enterprises/{enterprise_id}")
def update_enterprise(
    enterprise_id: int,
    body: EnterpriseUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_system_admin),
):
    try:
        enterprise = enterprise_service.update_enterprise(db, enterprise_id, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="update_enterprise",
        target_type="enterprise",
        target_id=enterprise.id,
        target_name=enterprise.name,
        enterprise_id=enterprise.id,
    )
    return enterprise_service.enterprise_to_dict(enterprise)


@router.post("/enterprises/{enterprise_id}/approve")
def approve_enterprise(
    enterprise_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_system_admin),
):
    enterprise = enterprise_service.approve_enterprise(db, enterprise_id, int(request.state.auth_user["sub"]))
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="approve_enterprise",
        target_type="enterprise",
        target_id=enterprise.id,
        target_name=enterprise.name,
        enterprise_id=enterprise.id,
    )
    return enterprise_service.enterprise_to_dict(enterprise)


@router.post("/enterprises/{enterprise_id}/reject")
def reject_enterprise(
    enterprise_id: int,
    body: RejectRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_system_admin),
):
    enterprise = enterprise_service.reject_enterprise(db, enterprise_id, int(request.state.auth_user["sub"]), body.reason)
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="reject_enterprise",
        target_type="enterprise",
        target_id=enterprise.id,
        target_name=enterprise.name,
        enterprise_id=enterprise.id,
    )
    return enterprise_service.enterprise_to_dict(enterprise)


@router.patch("/enterprises/{enterprise_id}/status")
def update_enterprise_status(
    enterprise_id: int,
    body: StatusRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_system_admin),
):
    try:
        enterprise = enterprise_service.update_enterprise_status(db, enterprise_id, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="update_enterprise_status",
        target_type="enterprise",
        target_id=enterprise.id,
        target_name=enterprise.name,
        enterprise_id=enterprise.id,
    )
    return enterprise_service.enterprise_to_dict(enterprise)


@router.delete("/enterprises/{enterprise_id}")
def delete_enterprise(
    enterprise_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_system_admin),
):
    try:
        enterprise = enterprise_service.soft_delete_enterprise(db, enterprise_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="delete_enterprise",
        target_type="enterprise",
        target_id=enterprise.id,
        target_name=enterprise.name,
        enterprise_id=enterprise.id,
    )
    return enterprise_service.enterprise_to_dict(enterprise)


@router.post("/enterprises/{enterprise_id}/impersonate")
def impersonate_enterprise(
    enterprise_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_system_admin),
):
    enterprise = enterprise_service.get_enterprise(db, enterprise_id)
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    if enterprise.status != "active":
        raise HTTPException(status_code=400, detail="只能代管已启用企业")
    user = enterprise_service.get_user(db, int(request.state.auth_user["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="当前用户不存在")
    token = auth_service.create_access_token(user, enterprise, enterprise_id_override=enterprise.id)
    audit_service.record_audit(
        db,
        actor_id=user.id,
        actor_name=user.display_name,
        action="impersonate_enterprise",
        target_type="enterprise",
        target_id=enterprise.id,
        target_name=enterprise.name,
        enterprise_id=enterprise.id,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "enterprise": enterprise_service.enterprise_to_dict(enterprise),
        "role": user.role,
    }

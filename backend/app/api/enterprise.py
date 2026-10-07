"""企业管理员接口。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import current_enterprise_id, require_admin, require_enterprise_admin, require_full_access
from app.database import get_db
from app.models.enterprise import Enterprise
from app.services import audit_service, enterprise_service

router = APIRouter(prefix="/api/enterprise", tags=["企业管理"])


class EmployeeCreateRequest(BaseModel):
    username: str
    display_name: str
    password: str


class ModelKeysRequest(BaseModel):
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    deepseek_model: str | None = None
    siliconflow_api_key: str | None = None
    siliconflow_base_url: str | None = None
    embed_model_name: str | None = None


@router.get("/settings")
def get_enterprise_settings(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    enterprise_id = current_enterprise_id(request)
    enterprise = db.get(Enterprise, enterprise_id)
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    keys = enterprise_service.get_model_keys(db, enterprise_id)
    return {
        "enterprise": enterprise_service.enterprise_to_dict(enterprise),
        "model_keys": enterprise_service.model_key_to_dict(keys),
        "uses_env_fallback": enterprise.code == enterprise_service.SYSTEM_ENTERPRISE_CODE and (
            keys is None or not keys.deepseek_api_key_encrypted or not keys.siliconflow_api_key_encrypted
        ),
    }


@router.post("/users")
def create_employee(
    body: EmployeeCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_enterprise_admin),
    # 体验账号不能建子员工。这是两个创建入口之一，
    # 另一个是 POST /api/admin/users（前端实际走那条）。
    _full=Depends(require_full_access),
):
    enterprise_id = current_enterprise_id(request)
    try:
        user = enterprise_service.create_employee(
            db, enterprise_id, body.username, body.display_name, body.password
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    enterprise = db.get(Enterprise, enterprise_id)
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="create_employee",
        target_type="user",
        target_name=user.username,
        enterprise_id=enterprise_id,
    )
    return enterprise_service.user_to_dict(user, enterprise)


@router.put("/model-keys")
def save_model_keys(
    body: ModelKeysRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    enterprise_id = current_enterprise_id(request)
    item = enterprise_service.save_model_keys(
        db,
        enterprise_id,
        body.model_dump(exclude_none=True),
        int(request.state.auth_user["sub"]),
    )
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="update_model_keys",
        target_type="enterprise",
        target_name=request.state.auth_user.get("enterprise_name", ""),
        enterprise_id=enterprise_id,
    )
    return enterprise_service.model_key_to_dict(item)


@router.post("/model-keys/test")
def test_model_keys(
    body: ModelKeysRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    enterprise_id = current_enterprise_id(request)
    return enterprise_service.test_model_keys(db, enterprise_id, body.model_dump(exclude_none=True))

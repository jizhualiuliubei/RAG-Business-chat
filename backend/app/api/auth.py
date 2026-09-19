"""
认证路由
================
提供算术验证码、登录、当前用户、退出接口。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.enterprise import Enterprise
from app.schemas.auth import CaptchaOut, EnterpriseRegisterRequest, LoginOut, LoginRequest, UserOut
from app.services import audit_service, auth_service, enterprise_service

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.get("/captcha", response_model=CaptchaOut)
def get_captcha():
    return auth_service.create_captcha()


@router.post("/login", response_model=LoginOut)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    if not auth_service.verify_captcha(body.captcha_id, body.captcha_answer):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    user = auth_service.authenticate_user(db, body.username, body.password, body.enterprise_code)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误，或账号已禁用")
    enterprise = db.get(Enterprise, user.enterprise_id) if user.enterprise_id else None
    audit_service.record_audit(
        db,
        actor_id=user.id,
        actor_name=user.display_name,
        action="login",
        target_type="user",
        target_name=user.username,
        enterprise_id=user.enterprise_id,
    )
    return {
        "access_token": auth_service.create_access_token(user, enterprise),
        "expires_in": auth_service.token_expires_in_seconds(),
        "user": enterprise_service.user_to_dict(user, enterprise),
    }


@router.get("/me", response_model=UserOut)
def me(request: Request):
    user = request.state.auth_user
    return {
        "id": int(user["sub"]),
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "status": user["status"],
        "enterprise_id": user.get("enterprise_id"),
        "enterprise_name": user.get("enterprise_name") or "",
        "enterprise_code": user.get("enterprise_code") or "",
        "brand": user.get("brand") or {"logo_url": None, "theme_color": "#2563eb"},
    }


@router.post("/enterprise-register")
def enterprise_register(body: EnterpriseRegisterRequest, db: Session = Depends(get_db)):
    try:
        enterprise, user = enterprise_service.register_enterprise_admin(db, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "enterprise": enterprise_service.enterprise_to_dict(enterprise),
        "user": enterprise_service.user_to_dict(user, enterprise),
    }


@router.post("/logout")
def logout():
    return {"message": "已退出登录"}

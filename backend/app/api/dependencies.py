"""通用接口依赖。"""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db


def require_admin(request: Request):
    """要求当前登录用户为任一后台管理员。"""
    user = getattr(request.state, "auth_user", None) or {}
    if user.get("role") not in {"admin", "system_admin", "enterprise_admin"}:
        raise HTTPException(status_code=403, detail="当前账号无权限访问该功能")
    return user


def require_system_admin(request: Request):
    """要求系统管理员权限。"""
    user = getattr(request.state, "auth_user", None) or {}
    if user.get("role") not in {"admin", "system_admin"}:
        raise HTTPException(status_code=403, detail="当前账号无权限访问该功能")
    return user


def require_enterprise_admin(request: Request):
    """要求企业管理员权限。"""
    user = getattr(request.state, "auth_user", None) or {}
    if user.get("role") != "enterprise_admin":
        raise HTTPException(status_code=403, detail="当前账号无权限访问该功能")
    return user


def current_enterprise_id(request: Request) -> int:
    user = getattr(request.state, "auth_user", None) or {}
    enterprise_id = user.get("enterprise_id")
    if enterprise_id is None:
        raise HTTPException(status_code=403, detail="当前账号缺少企业归属")
    return int(enterprise_id)


def current_user_id(request: Request) -> int:
    user = getattr(request.state, "auth_user", None) or {}
    user_id = user.get("sub")
    if user_id is None:
        raise HTTPException(status_code=403, detail="当前账号身份无效")
    return int(user_id)


def require_full_access(request: Request, db: Session = Depends(get_db)):
    """要求当前企业不是「体验企业」。

    体验账号（登录页免审核注册的那种）不能创建子员工账号、不能用 RAG 评测，
    这两项要等系统管理员在后台「转为正式」才开放。系统管理员本身不受限。

    为什么每次查库而不是把标记写进 token：转正要立刻生效。
    写进 token 的话，旧 token 会一直带着"体验"标记，直到过期（7 天）为止。
    """
    from app.services import enterprise_service

    user = getattr(request.state, "auth_user", None) or {}
    if user.get("role") in {"admin", "system_admin"}:
        return user
    if enterprise_service.is_trial_enterprise(db, user.get("enterprise_id")):
        raise HTTPException(
            status_code=403,
            detail="体验账号暂不支持该功能，需由平台管理员开通后使用",
        )
    return user

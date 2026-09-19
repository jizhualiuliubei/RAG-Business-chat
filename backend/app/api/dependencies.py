"""通用接口依赖。"""
from fastapi import HTTPException, Request


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

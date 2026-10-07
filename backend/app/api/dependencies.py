"""通用接口依赖。"""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db


def validate_current_identity(request: Request, db: Session = Depends(get_db)):
    """Token signatures do not authorize disabled accounts or stale roles."""
    identity = getattr(request.state, "auth_user", None)
    if not identity:
        return
    from app.models.enterprise import Enterprise
    from app.models.user import User
    from app.services import enterprise_service

    user = db.get(User, current_user_id(request))
    enterprise = db.get(Enterprise, current_enterprise_id(request))
    if user is None or user.status != "active" or user.role != identity.get("role"):
        raise HTTPException(status_code=401, detail="账号状态或权限已变更，请重新登录")
    if enterprise is None or enterprise.status != "active":
        raise HTTPException(status_code=403, detail="企业当前不可使用工作台")
    if user.enterprise_id != enterprise.id:
        # Preserve the explicit platform-management token, never extend it to employee tasks.
        home = db.get(Enterprise, user.enterprise_id)
        delegated = user.role in {"admin", "system_admin"} and home and home.code == "system" and home.status == "active"
        if not delegated:
            raise HTTPException(status_code=401, detail="账号企业归属已变更，请重新登录")
        if request.url.path.startswith("/api/agent/"):
            raise HTTPException(status_code=403, detail="平台代管不能访问其他企业事务任务")
    request.state.auth_user = {**identity, **enterprise_service.user_to_dict(user, enterprise), "enterprise_id": enterprise.id}


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

    体验账号不能创建子员工、使用 RAG 评测、创建合规任务或上传任务材料，
    需要系统管理员在后台「转为正式」。Agent 组件评测另走管理员门禁。

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

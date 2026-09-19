"""
认证服务
================
不引入新依赖，使用标准库完成：
- PBKDF2-HMAC-SHA256 密码哈希
- HMAC-SHA256 签名 token
- 内存算术验证码
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AUTH_SECRET_KEY, AUTH_TOKEN_EXPIRE_DAYS
from app.models.enterprise import Enterprise
from app.models.user import User

_CAPTCHA_STORE: dict[str, dict[str, object]] = {}
_CAPTCHA_TTL_SECONDS = 300
_PBKDF2_ITERATIONS = 120_000


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${_b64url(salt)}${_b64url(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = _b64url_decode(salt_b64)
        expected = _b64url_decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def init_default_users(db: Session) -> None:
    from app.services.enterprise_service import SYSTEM_ENTERPRISE_CODE, ensure_system_enterprise

    system_enterprise = ensure_system_enterprise(db)
    # 体验账号：只读工作台，密码公开在 README 里，不带任何后台权限。
    defaults = [
        {"username": "employee", "password": "employee123", "display_name": "员工用户", "role": "employee"},
    ]
    # 系统管理员密码不写进代码：由 ADMIN_INITIAL_PASSWORD 注入，未配置就不自动创建，
    # 避免把后台口令留在仓库里。已存在的账号不会被覆盖，改密码要单独在库里处理。
    admin_password = os.getenv("ADMIN_INITIAL_PASSWORD", "").strip()
    if admin_password:
        defaults.insert(0, {
            "username": os.getenv("ADMIN_USERNAME", "admin").strip() or "admin",
            "password": admin_password,
            "display_name": "系统管理员",
            "role": "system_admin",
        })
    for item in defaults:
        exists = db.execute(
            select(User).where(User.enterprise_id == system_enterprise.id, User.username == item["username"])
        ).scalar_one_or_none()
        if exists is not None:
            if exists.role == "admin":
                exists.role = "system_admin"
            if exists.enterprise_id is None:
                exists.enterprise_id = system_enterprise.id
            continue
        db.add(User(
            enterprise_id=system_enterprise.id,
            username=item["username"],
            password_hash=hash_password(item["password"]),
            display_name=item["display_name"],
            role=item["role"],
            status="active",
        ))
    db.execute(
        User.__table__.update().where(User.enterprise_id.is_(None)).values(enterprise_id=system_enterprise.id)
    )
    db.commit()


def create_captcha() -> dict[str, str]:
    left = secrets.randbelow(8) + 2
    right = secrets.randbelow(8) + 1
    captcha_id = secrets.token_urlsafe(18)
    _CAPTCHA_STORE[captcha_id] = {
        "answer": str(left + right),
        "expires_at": _utc_now() + timedelta(seconds=_CAPTCHA_TTL_SECONDS),
    }
    return {"captcha_id": captcha_id, "question": f"{left} + {right} = ?"}


def verify_captcha(captcha_id: str, answer: str) -> bool:
    item = _CAPTCHA_STORE.pop(captcha_id, None)
    if not item:
        return False
    if item["expires_at"] < _utc_now():
        return False
    return str(item["answer"]) == str(answer).strip()


def authenticate_user(db: Session, username: str, password: str, enterprise_code: str = "system") -> User | None:
    from app.services.enterprise_service import ACTIVE, SYSTEM_ENTERPRISE_CODE

    code = (enterprise_code or SYSTEM_ENTERPRISE_CODE).strip().lower()
    enterprise = db.execute(select(Enterprise).where(Enterprise.code == code)).scalar_one_or_none()
    if enterprise is None or enterprise.status != ACTIVE:
        return None
    user = db.execute(
        select(User).where(User.enterprise_id == enterprise.id, User.username == username.strip())
    ).scalar_one_or_none()
    if user is None or user.status != "active":
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now()
    db.commit()
    db.refresh(user)
    return user


def _sign(message: str) -> str:
    digest = hmac.new(AUTH_SECRET_KEY.encode("utf-8"), message.encode("ascii"), hashlib.sha256).digest()
    return _b64url(digest)


def create_access_token(
    user: User,
    enterprise: Enterprise | None = None,
    enterprise_id_override: int | None = None,
) -> str:
    now = _utc_now()
    exp = now + timedelta(days=AUTH_TOKEN_EXPIRE_DAYS)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "enterprise_id": enterprise_id_override if enterprise_id_override is not None else user.enterprise_id,
        "enterprise_code": enterprise.code if enterprise else "",
        "enterprise_name": enterprise.name if enterprise else "",
        "brand": {
            "logo_url": enterprise.logo_url if enterprise else None,
            "theme_color": enterprise.theme_color if enterprise else "#2563eb",
        },
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    head = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(f"{head}.{body}")
    return f"{head}.{body}.{signature}"


def decode_access_token(token: str) -> dict[str, object] | None:
    try:
        head, body, signature = token.split(".", 2)
        expected = _sign(f"{head}.{body}")
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(_utc_now().timestamp()):
            return None
        if payload.get("status") != "active":
            return None
        return payload
    except Exception:
        return None


def token_expires_in_seconds() -> int:
    return AUTH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

"""多企业租户服务。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import (
    AUTH_SECRET_KEY,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    EMBED_MODEL_NAME,
    SILICONFLOW_API_KEY,
    SILICONFLOW_BASE_URL,
)
from app.models.enterprise import Enterprise, EnterpriseModelKey
from app.models.user import User
from app.services.auth_service import hash_password

SYSTEM_ENTERPRISE_CODE = "system"
SYSTEM_ENTERPRISE_NAME = "系统平台资产"
ACTIVE = "active"
PENDING = "pending_review"
REJECTED = "rejected"
DISABLED = "disabled"
DELETED = "deleted"

CODE_RE = re.compile(r"^[a-z0-9_-]{2,60}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _normalize_code(code: str) -> str:
    value = (code or "").strip().lower()
    if not value:
        raise ValueError("企业代码不能为空")
    if not CODE_RE.fullmatch(value):
        raise ValueError("企业代码只能包含小写字母、数字、横线或下划线，长度 2-60")
    return value


def _clean_optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _validate_enterprise_fields(payload: dict, *, require_name: bool = False, require_code: bool = False) -> None:
    if require_name and not str(payload.get("name") or "").strip():
        raise ValueError("企业名称不能为空")
    if require_code and not str(payload.get("code") or "").strip():
        raise ValueError("企业代码不能为空")
    email = _clean_optional(payload.get("contact_email"))
    if email and not EMAIL_RE.fullmatch(email):
        raise ValueError("联系人邮箱格式不正确")
    phone = _clean_optional(payload.get("contact_phone"))
    if phone and not PHONE_RE.fullmatch(phone):
        raise ValueError("联系人手机号格式不正确")
    logo_url = _clean_optional(payload.get("logo_url"))
    if logo_url and not (logo_url.startswith("http://") or logo_url.startswith("https://")):
        raise ValueError("Logo URL 必须以 http:// 或 https:// 开头")
    theme_color = _clean_optional(payload.get("theme_color"))
    if theme_color and not HEX_COLOR_RE.fullmatch(theme_color):
        raise ValueError("主题色必须是 #RRGGBB 格式")


def validate_enterprise_admin_payload(payload: dict) -> None:
    enterprise_name = str(payload.get("enterprise_name") or "").strip()
    username = str(payload.get("username") or "").strip()
    display_name = str(payload.get("display_name") or "").strip()
    password = str(payload.get("password") or "")
    if not enterprise_name:
        raise ValueError("企业名称不能为空")
    _normalize_code(str(payload.get("enterprise_code") or ""))
    if len(username) < 3:
        raise ValueError("管理员用户名至少需要 3 个字符")
    if not display_name:
        raise ValueError("管理员显示名不能为空")
    if len(password) < 6:
        raise ValueError("密码至少需要 6 位")
    _validate_enterprise_fields(
        {
            "name": enterprise_name,
            "code": payload.get("enterprise_code"),
            "contact_email": payload.get("contact_email"),
            "contact_phone": payload.get("contact_phone"),
        },
        require_name=True,
        require_code=True,
    )


def generate_initial_password() -> str:
    return "Kb@" + secrets.token_urlsafe(8).replace("-", "A").replace("_", "B")[:10]


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(item ^ key[index % len(key)] for index, item in enumerate(data))


def encrypt_secret(secret: str) -> str:
    if not secret:
        return ""
    key = hashlib.sha256(AUTH_SECRET_KEY.encode("utf-8")).digest()
    mac = hmac.new(key, secret.encode("utf-8"), hashlib.sha256).digest()[:8]
    payload = mac + secret.encode("utf-8")
    return base64.urlsafe_b64encode(_xor(payload, key)).decode("ascii")


def decrypt_secret(secret: str | None) -> str:
    if not secret:
        return ""
    key = hashlib.sha256(AUTH_SECRET_KEY.encode("utf-8")).digest()
    payload = _xor(base64.urlsafe_b64decode(secret.encode("ascii")), key)
    mac, raw = payload[:8], payload[8:]
    expected = hmac.new(key, raw, hashlib.sha256).digest()[:8]
    if not hmac.compare_digest(mac, expected):
        raise ValueError("模型 API Key 解密校验失败")
    return raw.decode("utf-8")


def mask_secret(last4: str | None) -> str:
    return f"************{last4}" if last4 else ""


def normalize_provider_base_url(provider: str, base_url: str | None) -> str:
    value = (base_url or "").strip().rstrip("/")
    if not value:
        return DEEPSEEK_BASE_URL if provider == "deepseek" else SILICONFLOW_BASE_URL
    known_hosts = {
        "deepseek": "https://api.deepseek.com",
        "siliconflow": "https://api.siliconflow.cn",
    }
    if value == known_hosts.get(provider):
        return f"{value}/v1"
    return value


def enterprise_to_dict(enterprise: Enterprise) -> dict:
    return {
        "id": enterprise.id,
        "name": enterprise.name,
        "code": enterprise.code,
        "status": enterprise.status,
        "logo_url": enterprise.logo_url,
        "theme_color": enterprise.theme_color,
        "contact_name": enterprise.contact_name,
        "contact_email": enterprise.contact_email,
        "contact_phone": enterprise.contact_phone,
        "reviewed_at": enterprise.reviewed_at.isoformat() if enterprise.reviewed_at else None,
        "reject_reason": enterprise.reject_reason,
        "created_at": enterprise.created_at.isoformat() if enterprise.created_at else None,
    }


def user_to_dict(user: User, enterprise: Enterprise | None = None) -> dict:
    brand = {
        "logo_url": enterprise.logo_url if enterprise else None,
        "theme_color": enterprise.theme_color if enterprise else "#2563eb",
    }
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "enterprise_id": user.enterprise_id,
        "enterprise_name": enterprise.name if enterprise else "",
        "enterprise_code": enterprise.code if enterprise else "",
        "brand": brand,
    }


def ensure_system_enterprise(db: Session) -> Enterprise:
    enterprise = db.execute(
        select(Enterprise).where(Enterprise.code == SYSTEM_ENTERPRISE_CODE)
    ).scalar_one_or_none()
    if enterprise is None:
        enterprise = Enterprise(
            name=SYSTEM_ENTERPRISE_NAME,
            code=SYSTEM_ENTERPRISE_CODE,
            status=ACTIVE,
            contact_name="系统管理员",
        )
        db.add(enterprise)
        db.commit()
        db.refresh(enterprise)
    return enterprise


def migrate_legacy_rows_to_system(db: Session) -> None:
    """把旧单租户数据归属到 system 平台资产。"""
    from app.models.attachment import ConversationAttachment
    from app.models.audit_log import AuditLog
    from app.models.conversation import Conversation
    from app.models.document import Document
    from app.models.evaluation import EvaluationCaseResult, EvaluationDataset, EvaluationDatasetCase, EvaluationRun
    from app.models.knowledge_base import KnowledgeBase
    from app.models.message import Message

    system_enterprise = ensure_system_enterprise(db)
    for model in (
        User,
        KnowledgeBase,
        Document,
        Conversation,
        Message,
        ConversationAttachment,
        EvaluationRun,
        EvaluationDataset,
        EvaluationDatasetCase,
        EvaluationCaseResult,
        AuditLog,
    ):
        db.execute(
            model.__table__.update()
            .where(model.__table__.c.enterprise_id.is_(None))
            .values(enterprise_id=system_enterprise.id)
        )
    db.commit()


def migrate_legacy_conversations_to_enterprise_admin(db: Session) -> None:
    """把旧会话归属到所属企业管理员，避免升级后继续企业内共享会话。"""
    from app.models.attachment import ConversationAttachment
    from app.models.conversation import Conversation
    from app.models.message import Message

    enterprise_ids = [
        row[0]
        for row in db.execute(
            select(Conversation.enterprise_id)
            .where(Conversation.user_id.is_(None), Conversation.enterprise_id.is_not(None))
            .distinct()
        ).all()
    ]
    for enterprise_id in enterprise_ids:
        owner = db.execute(
            select(User)
            .where(
                User.enterprise_id == enterprise_id,
                User.status != "deleted",
                User.role == "enterprise_admin",
            )
            .order_by(User.id.asc())
        ).scalar_one_or_none()
        if owner is None:
            owner = db.execute(
                select(User)
                .where(
                    User.enterprise_id == enterprise_id,
                    User.status != "deleted",
                    User.role.in_(["system_admin", "admin"]),
                )
                .order_by(User.id.asc())
            ).scalar_one_or_none()
        if owner is None:
            owner = db.execute(
                select(User)
                .where(User.enterprise_id == enterprise_id, User.status != "deleted")
                .order_by(User.id.asc())
            ).scalar_one_or_none()
        if owner is None:
            continue

        conversation_ids = [
            row[0]
            for row in db.execute(
                select(Conversation.id).where(
                    Conversation.enterprise_id == enterprise_id,
                    Conversation.user_id.is_(None),
                )
            ).all()
        ]
        if not conversation_ids:
            continue
        db.execute(
            Conversation.__table__.update()
            .where(Conversation.id.in_(conversation_ids))
            .values(user_id=owner.id)
        )
        db.execute(
            Message.__table__.update()
            .where(Message.conversation_id.in_(conversation_ids), Message.user_id.is_(None))
            .values(user_id=owner.id)
        )
        db.execute(
            ConversationAttachment.__table__.update()
            .where(
                ConversationAttachment.conversation_id.in_(conversation_ids),
                ConversationAttachment.user_id.is_(None),
            )
            .values(user_id=owner.id)
        )
    db.commit()


def get_enterprise_by_code(db: Session, code: str) -> Enterprise | None:
    return db.execute(select(Enterprise).where(Enterprise.code == _normalize_code(code))).scalar_one_or_none()


def get_enterprise(db: Session, enterprise_id: int) -> Enterprise | None:
    return db.get(Enterprise, enterprise_id)


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def is_system_enterprise(db: Session, enterprise_id: int | None) -> bool:
    enterprise = db.get(Enterprise, enterprise_id) if enterprise_id else None
    return bool(enterprise and enterprise.code == SYSTEM_ENTERPRISE_CODE)


def get_active_enterprise_or_error(db: Session, enterprise_id: int | None) -> Enterprise:
    enterprise = db.get(Enterprise, enterprise_id) if enterprise_id else None
    if enterprise is None:
        raise ValueError("企业不存在")
    if enterprise.status != ACTIVE:
        if enterprise.status == PENDING:
            raise ValueError("企业尚未审核通过")
        if enterprise.status == DISABLED:
            raise ValueError("企业已被系统管理员禁用")
        if enterprise.status == DELETED:
            raise ValueError("企业已被删除")
        raise ValueError("企业账号不可用")
    return enterprise


def register_enterprise_admin(db: Session, payload: dict) -> tuple[Enterprise, User]:
    validate_enterprise_admin_payload(payload)
    code = _normalize_code(payload["enterprise_code"])
    if code == SYSTEM_ENTERPRISE_CODE:
        raise ValueError("企业代码 system 已被平台保留")
    if get_enterprise_by_code(db, code) is not None:
        raise ValueError(f"企业代码已存在: {code}")

    def clean(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None

    enterprise = Enterprise(
        name=payload["enterprise_name"].strip(),
        code=code,
        status=PENDING,
        contact_name=clean(payload.get("contact_name")),
        contact_email=clean(payload.get("contact_email")),
        contact_phone=clean(payload.get("contact_phone")),
    )
    db.add(enterprise)
    db.flush()
    user = User(
        enterprise_id=enterprise.id,
        username=payload["username"].strip(),
        password_hash=hash_password(payload["password"]),
        display_name=payload["display_name"].strip(),
        role="enterprise_admin",
        status=PENDING,
    )
    db.add(user)
    db.commit()
    db.refresh(enterprise)
    db.refresh(user)
    return enterprise, user


def create_enterprise_admin(
    db: Session,
    *,
    enterprise: Enterprise,
    username: str,
    display_name: str,
    password: str,
) -> User:
    username = username.strip()
    display_name = display_name.strip() or username
    if len(username) < 3:
        raise ValueError("管理员用户名至少需要 3 个字符")
    if len(password) < 6:
        raise ValueError("密码至少需要 6 位")
    exists = db.execute(
        select(User).where(User.enterprise_id == enterprise.id, User.username == username)
    ).scalar_one_or_none()
    if exists is not None:
        raise ValueError("管理员用户名已存在")
    user = User(
        enterprise_id=enterprise.id,
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        role="enterprise_admin",
        status=ACTIVE if enterprise.status == ACTIVE else PENDING,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_enterprises(db: Session, status: str | None = None, q: str | None = None) -> list[Enterprise]:
    stmt = select(Enterprise).order_by(Enterprise.created_at.desc(), Enterprise.id.desc())
    if status:
        stmt = stmt.where(Enterprise.status == status)
    else:
        stmt = stmt.where(Enterprise.status != DELETED)
    keyword = (q or "").strip()
    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                Enterprise.name.like(pattern),
                Enterprise.code.like(pattern),
                Enterprise.contact_name.like(pattern),
                Enterprise.contact_email.like(pattern),
                Enterprise.contact_phone.like(pattern),
            )
        )
    return db.execute(stmt).scalars().all()


def create_enterprise(db: Session, payload: dict) -> Enterprise:
    _validate_enterprise_fields(payload, require_name=True, require_code=True)
    code = _normalize_code(payload["code"])
    if get_enterprise_by_code(db, code) is not None:
        raise ValueError(f"企业代码已存在: {code}")
    enterprise = Enterprise(
        name=payload["name"].strip(),
        code=code,
        status=payload.get("status") or ACTIVE,
        logo_url=_clean_optional(payload.get("logo_url")),
        theme_color=_clean_optional(payload.get("theme_color")) or "#2563eb",
        contact_name=_clean_optional(payload.get("contact_name")),
        contact_email=_clean_optional(payload.get("contact_email")),
        contact_phone=_clean_optional(payload.get("contact_phone")),
    )
    if enterprise.status not in {PENDING, ACTIVE, REJECTED, DISABLED}:
        raise ValueError("企业状态不合法")
    db.add(enterprise)
    db.commit()
    db.refresh(enterprise)
    return enterprise


def update_enterprise(db: Session, enterprise_id: int, payload: dict) -> Enterprise | None:
    enterprise = db.get(Enterprise, enterprise_id)
    if not enterprise:
        return None
    if enterprise.status == DELETED:
        raise ValueError("已删除企业不能编辑")
    _validate_enterprise_fields(payload)
    if "code" in payload and payload["code"] is not None:
        code = _normalize_code(payload["code"])
        if enterprise.code == SYSTEM_ENTERPRISE_CODE and code != SYSTEM_ENTERPRISE_CODE:
            raise ValueError("系统平台企业代码不能修改")
        exists = get_enterprise_by_code(db, code)
        if exists is not None and exists.id != enterprise.id:
            raise ValueError(f"企业代码已存在: {code}")
        enterprise.code = code
    for key in ("name", "logo_url", "theme_color", "contact_name", "contact_email", "contact_phone"):
        if key in payload and payload[key] is not None:
            setattr(enterprise, key, _clean_optional(payload[key]))
    if not enterprise.name:
        raise ValueError("企业名称不能为空")
    if not enterprise.theme_color:
        enterprise.theme_color = "#2563eb"
    db.commit()
    db.refresh(enterprise)
    return enterprise


def soft_delete_enterprise(db: Session, enterprise_id: int) -> Enterprise | None:
    enterprise = db.get(Enterprise, enterprise_id)
    if not enterprise:
        return None
    if enterprise.code == SYSTEM_ENTERPRISE_CODE:
        raise ValueError("系统平台资产不能删除")
    enterprise.status = DELETED
    users = db.execute(select(User).where(User.enterprise_id == enterprise.id)).scalars().all()
    for user in users:
        user.status = DISABLED
    db.commit()
    db.refresh(enterprise)
    return enterprise


def approve_enterprise(db: Session, enterprise_id: int, reviewer_id: int) -> Enterprise | None:
    enterprise = db.get(Enterprise, enterprise_id)
    if not enterprise:
        return None
    enterprise.status = ACTIVE
    enterprise.reviewed_by = reviewer_id
    enterprise.reviewed_at = datetime.now()
    enterprise.reject_reason = None
    users = db.execute(select(User).where(User.enterprise_id == enterprise.id)).scalars().all()
    for user in users:
        if user.status == PENDING:
            user.status = ACTIVE
    db.commit()
    db.refresh(enterprise)
    return enterprise


def reject_enterprise(db: Session, enterprise_id: int, reviewer_id: int, reason: str) -> Enterprise | None:
    enterprise = db.get(Enterprise, enterprise_id)
    if not enterprise:
        return None
    enterprise.status = REJECTED
    enterprise.reviewed_by = reviewer_id
    enterprise.reviewed_at = datetime.now()
    enterprise.reject_reason = reason.strip()
    users = db.execute(select(User).where(User.enterprise_id == enterprise.id)).scalars().all()
    for user in users:
        user.status = REJECTED
    db.commit()
    db.refresh(enterprise)
    return enterprise


def update_enterprise_status(db: Session, enterprise_id: int, status: str) -> Enterprise | None:
    enterprise = db.get(Enterprise, enterprise_id)
    if not enterprise:
        return None
    if enterprise.status == DELETED:
        raise ValueError("已删除企业不能启用或禁用")
    if status not in {ACTIVE, DISABLED}:
        raise ValueError("企业状态只能设置为 active 或 disabled")
    enterprise.status = status
    users = db.execute(select(User).where(User.enterprise_id == enterprise.id)).scalars().all()
    for user in users:
        if user.role != "system_admin":
            user.status = ACTIVE if status == ACTIVE else DISABLED
    db.commit()
    db.refresh(enterprise)
    return enterprise


def create_employee(db: Session, enterprise_id: int, username: str, display_name: str, password: str) -> User:
    exists = db.execute(
        select(User).where(User.enterprise_id == enterprise_id, User.username == username.strip())
    ).scalar_one_or_none()
    if exists is not None:
        raise ValueError(f"用户名已存在: {username}")
    user = User(
        enterprise_id=enterprise_id,
        username=username.strip(),
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role="employee",
        status=ACTIVE,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def save_model_keys(db: Session, enterprise_id: int, payload: dict, updated_by: int) -> EnterpriseModelKey:
    item = db.execute(
        select(EnterpriseModelKey).where(EnterpriseModelKey.enterprise_id == enterprise_id)
    ).scalar_one_or_none()
    if item is None:
        item = EnterpriseModelKey(enterprise_id=enterprise_id)
        db.add(item)
    deepseek_key = payload.get("deepseek_api_key")
    if deepseek_key:
        item.deepseek_api_key_encrypted = encrypt_secret(deepseek_key)
        item.deepseek_key_last4 = deepseek_key[-4:]
    siliconflow_key = payload.get("siliconflow_api_key")
    if siliconflow_key:
        item.siliconflow_api_key_encrypted = encrypt_secret(siliconflow_key)
        item.siliconflow_key_last4 = siliconflow_key[-4:]
    item.deepseek_base_url = normalize_provider_base_url(
        "deepseek", payload.get("deepseek_base_url") or item.deepseek_base_url or DEEPSEEK_BASE_URL
    )
    item.deepseek_model = payload.get("deepseek_model") or item.deepseek_model or "deepseek-v4-flash"
    item.siliconflow_base_url = normalize_provider_base_url(
        "siliconflow", payload.get("siliconflow_base_url") or item.siliconflow_base_url or SILICONFLOW_BASE_URL
    )
    item.embed_model_name = payload.get("embed_model_name") or item.embed_model_name or EMBED_MODEL_NAME
    item.updated_by = updated_by
    db.commit()
    db.refresh(item)
    return item


def model_key_to_dict(item: EnterpriseModelKey | None) -> dict:
    return {
        "deepseek": {
            "configured": bool(item and item.deepseek_key_last4),
            "masked": mask_secret(item.deepseek_key_last4 if item else None),
            "base_url": normalize_provider_base_url("deepseek", item.deepseek_base_url if item else DEEPSEEK_BASE_URL),
            "model": item.deepseek_model if item else "deepseek-v4-flash",
        },
        "siliconflow": {
            "configured": bool(item and item.siliconflow_key_last4),
            "masked": mask_secret(item.siliconflow_key_last4 if item else None),
            "base_url": normalize_provider_base_url("siliconflow", item.siliconflow_base_url if item else SILICONFLOW_BASE_URL),
            "model": item.embed_model_name if item else EMBED_MODEL_NAME,
        },
    }


def get_model_keys(db: Session, enterprise_id: int) -> EnterpriseModelKey | None:
    return db.execute(
        select(EnterpriseModelKey).where(EnterpriseModelKey.enterprise_id == enterprise_id)
    ).scalar_one_or_none()


def _provider_error_message(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    lower = text.lower()
    if "401" in text or "unauthorized" in lower or "authentication" in lower:
        return "鉴权失败，请检查 API Key"
    if (
        "404" in text
        or "not found" in lower
        or "not_found" in lower
        or "notfound" in lower
        or ("model" in lower and "does not exist" in lower)
    ):
        return "模型或接口不存在，请检查 Base URL 和模型名称"
    if "429" in text or "rate limit" in lower or "quota" in lower or "insufficient" in lower:
        return "额度不足或触发限流"
    if "timeout" in lower or "timed out" in lower:
        return "网络超时，请稍后重试"
    if "connection" in lower or "connect" in lower:
        return "网络连接失败，请检查 Base URL"
    return f"未知错误：{text[:160]}"


def _check_provider_connection(provider: str, key: str, base_url: str | None, model: str | None) -> dict:
    started = time.perf_counter()
    try:
        if not key:
            return {"ok": False, "message": "未配置 API Key", "latency_ms": 0}
        if provider == "deepseek":
            from app.core.llm import get_model_for_config

            llm = get_model_for_config(key, base_url, model)
            llm.invoke("请只回复 ok")
        else:
            from app.core.embeddings import get_embed_model_for_config

            embedding = get_embed_model_for_config(key, base_url, model).embed_query("test")
            if not embedding:
                raise ValueError("embedding 返回为空")
        return {
            "ok": True,
            "message": "连接正常",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:  # pragma: no cover - 真实网络错误在单元测试中用 monkeypatch 覆盖
        return {
            "ok": False,
            "message": _provider_error_message(exc),
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }


def test_model_keys(db: Session, enterprise_id: int, payload: dict) -> dict:
    enterprise = db.get(Enterprise, enterprise_id)
    item = get_model_keys(db, enterprise_id)

    deepseek_key = payload.get("deepseek_api_key") or (
        decrypt_secret(item.deepseek_api_key_encrypted) if item and item.deepseek_api_key_encrypted else ""
    )
    siliconflow_key = payload.get("siliconflow_api_key") or (
        decrypt_secret(item.siliconflow_api_key_encrypted) if item and item.siliconflow_api_key_encrypted else ""
    )
    if enterprise and enterprise.code == SYSTEM_ENTERPRISE_CODE:
        deepseek_key = deepseek_key or DEEPSEEK_API_KEY
        siliconflow_key = siliconflow_key or SILICONFLOW_API_KEY

    deepseek_base_url = normalize_provider_base_url(
        "deepseek", payload.get("deepseek_base_url") or (item.deepseek_base_url if item else None) or DEEPSEEK_BASE_URL
    )
    deepseek_model = payload.get("deepseek_model") or (item.deepseek_model if item else None) or "deepseek-v4-flash"
    siliconflow_base_url = normalize_provider_base_url(
        "siliconflow", payload.get("siliconflow_base_url") or (item.siliconflow_base_url if item else None) or SILICONFLOW_BASE_URL
    )
    embed_model_name = payload.get("embed_model_name") or (item.embed_model_name if item else None) or EMBED_MODEL_NAME

    return {
        "deepseek": {
            **_check_provider_connection("deepseek", deepseek_key, deepseek_base_url, deepseek_model),
            "provider": "DeepSeek 对话模型",
            "base_url": deepseek_base_url,
            "model": deepseek_model,
        },
        "siliconflow": {
            **_check_provider_connection("siliconflow", siliconflow_key, siliconflow_base_url, embed_model_name),
            "provider": "SiliconFlow 嵌入模型",
            "base_url": siliconflow_base_url,
            "model": embed_model_name,
        },
    }


def require_enterprise_deepseek_key(db: Session, enterprise_id: int) -> dict:
    enterprise = db.get(Enterprise, enterprise_id)
    item = get_model_keys(db, enterprise_id)
    if enterprise and enterprise.code == SYSTEM_ENTERPRISE_CODE and (item is None or not item.deepseek_api_key_encrypted):
        return {"platform": True, "env_fallback": True}
    if item is None or not item.deepseek_api_key_encrypted:
        raise ValueError("企业尚未配置模型 API Key，请联系企业管理员")
    return {
        "api_key": decrypt_secret(item.deepseek_api_key_encrypted),
        "base_url": item.deepseek_base_url,
        "model": item.deepseek_model,
    }


def require_enterprise_siliconflow_key(db: Session, enterprise_id: int) -> dict:
    enterprise = db.get(Enterprise, enterprise_id)
    item = get_model_keys(db, enterprise_id)
    if enterprise and enterprise.code == SYSTEM_ENTERPRISE_CODE and (item is None or not item.siliconflow_api_key_encrypted):
        return {"platform": True, "env_fallback": True}
    if item is None or not item.siliconflow_api_key_encrypted:
        raise ValueError("企业尚未配置嵌入模型 API Key，请联系企业管理员")
    return {
        "api_key": decrypt_secret(item.siliconflow_api_key_encrypted),
        "base_url": item.siliconflow_base_url,
        "model": item.embed_model_name,
    }

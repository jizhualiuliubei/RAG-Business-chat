"""企业租户与企业模型配置。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Enterprise(Base):
    __tablename__ = "enterprise"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending_review")
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    theme_color: Mapped[str] = mapped_column(String(30), default="#2563eb")
    contact_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 体验企业：免审核注册、注册即可用，但功能受限（不能建子员工、不能用评测），
    # 需由系统管理员「转为正式」才解除。判断走数据库而不是写进 token，
    # 所以转正后立刻生效，体验账号不必重新登录。
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class EnterpriseModelKey(Base):
    __tablename__ = "enterprise_model_key"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), unique=True, nullable=False)
    deepseek_api_key_encrypted: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    deepseek_base_url: Mapped[str] = mapped_column(String(500), default="https://api.deepseek.com/v1")
    deepseek_model: Mapped[str] = mapped_column(String(100), default="deepseek-v4-flash")
    deepseek_key_last4: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    siliconflow_api_key_encrypted: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    siliconflow_base_url: Mapped[str] = mapped_column(String(500), default="https://api.siliconflow.cn/v1")
    embed_model_name: Mapped[str] = mapped_column(String(100), default="BAAI/bge-m3")
    siliconflow_key_last4: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

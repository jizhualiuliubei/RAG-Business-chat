"""
操作审计日志
================
记录管理员后台和登录等关键动作，便于后续展示系统治理能力。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_name: Mapped[str] = mapped_column(String(120), default="系统", nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    result: Mapped[str] = mapped_column(String(30), default="success", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

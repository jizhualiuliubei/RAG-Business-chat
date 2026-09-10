"""
会话表模型
================
一个会话 = 一次连续的多轮对话（比如"和 AI 讨论报销制度"这件事）。
title 取该会话第一条用户消息的前若干字，方便在历史会话列表里展示。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    # onupdate=datetime.now：每次更新这条记录时，自动刷新"更新时间"
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

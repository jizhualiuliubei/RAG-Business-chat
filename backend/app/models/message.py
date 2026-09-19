"""
消息表模型
================
记录每个会话里的每一轮问答（一条用户消息 + 一条 AI 回复 = 两条记录）。

sources 字段存 AI 回复的"引用来源"（JSON 字符串），例如：
    [{"source": "企业员工手册.txt", "score": 0.82, "text": "..."}]
用 Text 类型存，因为片段内容可能比较长。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user / assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 引用来源 JSON：只有 assistant 消息才有，user 消息为 None
    sources: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 模型名 + token 用量 JSON（assistant 消息）：前端展示"模型 xxx，消耗 token xxx"
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    usage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

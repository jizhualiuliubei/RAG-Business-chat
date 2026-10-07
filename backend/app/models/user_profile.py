"""
用户档案表模型
================
作用：存储"长期记忆"——用户主动提供的信息（姓名、偏好等）。

为什么需要它（解决"AI 刷新后失忆"的根本）：
- DeepSeek 是无状态的，每次请求都"重新看"历史，靠模型从对话推断用户信息不稳定
- 历史窗口还会把早先的关键信息挤出
- 正确做法：把用户主动说出的信息（如"我叫张三"）显式存到这张表，
  每次问答时注入 prompt —— 无论历史怎么变、窗口怎么裁，用户信息始终在场

设计：一个会话一个档案（key 用 conversation_id），fields 存 JSON 字典，
如 {"name": "张三"}。未来可扩展更多字段（部门、偏好等）。
"""
import json
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 一个会话对应一份用户档案（同一会话内持续记忆）
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    # JSON 字典：{"name": "张三", ...}，后续可扩展偏好/部门等
    fields: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    def get_fields(self) -> dict:
        try:
            return json.loads(self.fields or "{}")
        except json.JSONDecodeError:
            return {}

    def set_fields(self, data: dict) -> None:
        self.fields = json.dumps(data, ensure_ascii=False)

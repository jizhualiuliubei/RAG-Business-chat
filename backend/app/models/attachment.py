"""
对话附件表模型
================
作用：存储"会话级临时上下文"附件（对话中上传，只在当前会话有效）。

设计（对标 ChatChat 的文件对话）：
- 一个附件属于一个会话（conversation_id）
- 附件文本按会话隔离：提问时只注入当前会话的附件
- 持久化：附件元数据存 DB，文件内容存文件系统（data/attachments/{conversation_id}/）
- 不入知识库：附件是临时上下文，不影响向量检索
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConversationAttachment(Base):
    __tablename__ = "conversation_attachment"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)  # 原始文件名
    # 落盘相对路径（如 conversations/1/xxx.txt），配合 ATTACHMENT_DIR 使用
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # 解析出的文本内容（注入 prompt 用），可能很大
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="done")
    failure_reason: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ConversationAttachmentChunk(Base):
    __tablename__ = "conversation_attachment_chunk"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    attachment_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    chunk_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="attachment")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

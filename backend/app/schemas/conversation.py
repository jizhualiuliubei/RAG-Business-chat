"""
会话相关的 Pydantic 请求/响应模型
================
对应知识点：FastAPI + Pydantic 数据校验
"""
from datetime import datetime

from pydantic import BaseModel


class ConversationOut(BaseModel):
    """会话列表项 / 新建会话的响应"""
    id: int
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}  # 允许直接从 ORM 对象构造


class SourceItem(BaseModel):
    """引用来源片段"""
    text: str
    source: str
    chunk_id: int
    score: float
    kb_name: str = ""  # 所属知识库名（历史会话刷新后仍保留多库来源标注）


class MessageOut(BaseModel):
    """单条消息（含解析后的引用来源）"""
    id: int
    role: str
    content: str
    sources: list[SourceItem]
    model_name: str = ""          # 模型名（assistant 消息）
    usage: dict = {}              # token 用量（assistant 消息）
    created_at: datetime


class ConversationDetail(BaseModel):
    """会话详情：会话信息 + 完整消息列表"""
    id: int
    title: str
    messages: list[MessageOut]

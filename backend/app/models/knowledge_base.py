"""
知识库表模型（预留多知识库）
================
对应知识点：SQLAlchemy ORM 模型定义
- 每个类对应数据库里的一张表
- mapped_column 用来定义字段（列）
- Mapped[...] 是类型注解，告诉 SQLAlchemy 这个字段是什么类型

设计思路（为什么 P0 只用一个知识库，却要单独建这张表）：
- 需求里 P1 要支持"多知识库"，如果现在不建表、以后再加，
  就得改表结构 + 做数据迁移，成本高；
- 从第一天就建好独立表，P0 阶段只放一条"默认知识库"记录，
  P1 阶段直接加新记录即可，零迁移。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    __table_args__ = (UniqueConstraint("enterprise_id", "name", name="uq_kb_enterprise_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

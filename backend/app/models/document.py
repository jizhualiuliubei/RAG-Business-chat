"""
文档表模型
================
记录用户上传的每个文档的元信息，以及解析/入库的状态。

status 字段三种取值（对应需求里的"解析中 / 已完成 / 失败"三态）：
- processing：上传后，后台正在解析 + 向量化 + 入库
- done      ：解析、向量化、入库全部完成
- failed    ：解析过程中出错
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Document(Base):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    # 所属知识库 ID：预留多库，P0 阶段都指向"默认知识库"
    kb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)  # 字节数
    status: Mapped[str] = mapped_column(String(20), default="processing")
    failure_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    parser_name: Mapped[str] = mapped_column(String(80), default="")
    parser_version: Mapped[str] = mapped_column(String(80), default="")
    parsed_chars: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)  # 切分成多少块
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

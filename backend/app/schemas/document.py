"""
文档相关的 Pydantic 请求/响应模型
================
对应知识点：FastAPI + Pydantic 数据校验
- Pydantic 模型负责"接口进出数据的格式定义和校验"
- 后端给前端返回哪些字段，由这里的响应模型决定
"""
from datetime import datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    """文档列表项 / 状态查询的响应结构"""
    id: int
    kb_id: int
    filename: str
    file_size: int
    status: str          # processing / done / failed
    failure_reason: str | None = None
    parser_name: str = ""
    parser_version: str = ""
    parsed_chars: int = 0
    chunk_count: int
    created_at: datetime

    # from_attributes=True：允许直接从 SQLAlchemy ORM 对象构造
    # （Pydantic v1 时代叫 orm_mode=True，v2 改成这个写法）
    model_config = {"from_attributes": True}


class UploadOut(BaseModel):
    """上传接口的响应：告诉前端新建的文档 ID 和当前状态"""
    id: int
    filename: str
    status: str
    message: str = "上传成功，后台正在解析入库"

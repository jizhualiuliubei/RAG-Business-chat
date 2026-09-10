"""
问答相关的 Pydantic 请求/响应模型
================
对应知识点：FastAPI + Pydantic 数据校验
"""
from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """问答请求体"""
    conversation_id: int | None = Field(default=None, description="会话id，None表示不记入会话")
    question: str = Field(..., description="用户问题")
    kb_id: int | list[int] = Field(default=1, description="知识库id；传数组表示多库联合检索")


class SourceItem(BaseModel):
    """引用来源的单个片段"""
    text: str
    source: str
    chunk_id: int
    score: float
    kb_name: str = ""  # 所属知识库名（多库检索标注来源库）
    source_type: str = "knowledge_base"
    attachment_id: int | None = None
    locator: str = ""
    metadata: dict = Field(default_factory=dict)


class AskResponse(BaseModel):
    """问答响应体：回答 + 引用来源 + 是否命中"""
    answer: str
    sources: list[SourceItem]
    knowledge_base_sources: list[SourceItem] = Field(default_factory=list)
    attachment_sources: list[SourceItem] = Field(default_factory=list)
    hit: bool
    usage: dict = Field(default_factory=dict)          # token 用量 {input_tokens, output_tokens, total_tokens}
    model_name: str = ""      # 对话模型名

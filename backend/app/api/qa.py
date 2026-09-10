"""
问答路由
================
对应知识点：FastAPI 路由
路由层很薄：接收请求 → 调 service → 返回结果。
落库消息（记录到历史会话）在这里做，P0 阶段：前端传了 conversation_id 才落库。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.mcp_agent import ask_mcp_agent
from app.core.rag_agent import ask_agent_rag
from app.api.dependencies import current_user_id
from app.database import get_db
from app.schemas.qa import AskRequest, AskResponse
from app.services import conversation_service, enterprise_service, qa_service

router = APIRouter(prefix="/api/qa", tags=["问答"])


class AgentAskRequest(BaseModel):
    """工具化 Agent 问答请求"""
    question: str
    conversation_id: int | None = None   # 可选：用于注入用户档案（长期记忆）
    kb_id: int = 1                       # 知识库 id（Agent 检索当前知识库）


class AgentAskResponse(BaseModel):
    """工具化 Agent 问答响应"""
    answer: str


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request, db: Session = Depends(get_db)):
    """问答：检索 → 过滤 → 生成 → 返回带引用来源的回答"""
    # 核心问答流程（不走会话记忆，P0 阶段直接问答）
    try:
        enterprise_id = int(request.state.auth_user["enterprise_id"])
        user_id = current_user_id(request)
        if body.conversation_id is not None:
            conv = conversation_service.get_conversation(db, body.conversation_id, enterprise_id, user_id)
            if not conv:
                raise HTTPException(status_code=404, detail="会话不存在")
        result = qa_service.answer_question(
            body.question,
            kb_id=body.kb_id,
            db=db,
            enterprise_id=enterprise_id,
            conversation_id=body.conversation_id,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 如果前端带了会话id，把这一问一答落库（历史会话功能）
    if body.conversation_id is not None:
        qa_service.save_message(
            db,
            body.conversation_id,
            "user",
            body.question,
            sources=None,
            enterprise_id=enterprise_id,
            user_id=user_id,
        )
        qa_service.save_message(
            db,
            body.conversation_id,
            "assistant",
            result["answer"],
            sources=result["sources"],
            enterprise_id=enterprise_id,
            user_id=user_id,
        )

    return result


@router.post("/agent-ask", response_model=AgentAskResponse)
def agent_ask(body: AgentAskRequest, request: Request, db: Session = Depends(get_db)):
    """工具化 Agent 问答：Agent 自主决定调用哪些工具（检索/时间/用户档案）。

    与 /ask（手动检索）的对比：
    - /ask         ：代码控制"检索→拼上下文→生成"，来源可靠
    - /agent-ask   ：Agent 拿到多个工具自主决策，更灵活但依赖模型纪律

    body 里的 conversation_id 可选：用于注入用户档案（长期记忆）。
    """
    try:
        enterprise_id = int(request.state.auth_user["enterprise_id"])
        user_id = current_user_id(request)
        model_config = enterprise_service.require_enterprise_deepseek_key(db, enterprise_id)
        embed_config = enterprise_service.require_enterprise_siliconflow_key(db, enterprise_id)
        if body.conversation_id is not None:
            conv = conversation_service.get_conversation(db, body.conversation_id, enterprise_id, user_id)
            if not conv:
                raise HTTPException(status_code=404, detail="会话不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    from app.services.user_profile_service import get_profile_text
    profile_text = ""
    if body.conversation_id is not None:
        profile_text = get_profile_text(db, body.conversation_id)
    answer = ask_agent_rag(
        body.question,
        profile_text=profile_text,
        kb_id=body.kb_id,
        enterprise_id=enterprise_id,
        model_config=model_config,
        embed_config=embed_config,
        allowed_doc_ids=qa_service.active_document_ids_for_scope(db, body.kb_id, enterprise_id),
    )
    return {"answer": answer}


def sse_stream(body: AskRequest, db: Session, enterprise_id: int, user_id: int):
    """SSE 生成器：把 stream_answer 的事件逐个转成 SSE 格式。

    SSE 协议：每条事件用 "data: <json>\n\n" 分隔（\n\n 是事件边界）。
    """
    for event in qa_service.stream_answer(
        body.question,
        kb_id=body.kb_id,
        conversation_id=body.conversation_id,
        db=db,
        enterprise_id=enterprise_id,
        user_id=user_id,
    ):
        # 每条事件一行 data:，用空行结束
        yield f"data: {event}\n\n"


@router.post("/ask-stream")
def ask_stream(body: AskRequest, request: Request, db: Session = Depends(get_db)):
    """流式问答：SSE（Server-Sent Events）逐字输出。

    返回的事件类型：
    - {"type": "sources", "sources": [...], "hit": bool}  引用来源
    - {"type": "text", "content": "..."}                  回答文本片段

    前端用 fetch + ReadableStream 逐块读取，实现打字机效果。
    """
    enterprise_id = int(request.state.auth_user["enterprise_id"])
    user_id = current_user_id(request)
    if body.conversation_id is not None:
        conv = conversation_service.get_conversation(db, body.conversation_id, enterprise_id, user_id)
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")
    return StreamingResponse(
        sse_stream(body, db, enterprise_id, user_id),
        media_type="text/event-stream",  # SSE 的 MIME 类型
        headers={
            "Cache-Control": "no-cache",        # 禁止缓存（流式必须）
            "X-Accel-Buffering": "no",          # 禁用代理缓冲（Nginx 等）
        },
    )


@router.post("/mcp-ask", response_model=AgentAskResponse)
def mcp_ask(body: AgentAskRequest):
    """MCP 工具问答：Agent 通过 MCP 协议调用计算器/日期时间工具。"""
    answer = ask_mcp_agent(body.question)
    return {"answer": answer}

"""
会话管理路由
================
对应知识点：FastAPI 路由 + 依赖注入
提供：会话列表 / 新建 / 详情（含消息）/ 删除 / 会话内提问（带多轮记忆）
"""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import current_enterprise_id, current_user_id
from app.database import get_db
from app.schemas.conversation import ConversationDetail, ConversationOut, MessageOut
from app.services import conversation_service, attachment_service

router = APIRouter(prefix="/api/conversations", tags=["会话管理"])


class CreateRequest(BaseModel):
    """新建会话请求：可选传标题，不传用默认"新会话" """
    title: str = "新会话"


class AskRequest(BaseModel):
    """会话内提问请求"""
    question: str
    kb_id: int = 1


@router.get("", response_model=list[ConversationOut])
def list_conversations(request: Request, db: Session = Depends(get_db)):
    """会话列表"""
    return conversation_service.list_conversations(db, current_enterprise_id(request), current_user_id(request))


@router.post("", response_model=ConversationOut)
def create_conversation(body: CreateRequest, request: Request, db: Session = Depends(get_db)):
    """新建会话"""
    return conversation_service.create_conversation(
        db,
        body.title,
        current_enterprise_id(request),
        current_user_id(request),
    )


@router.get("/{conversation_id}/messages", response_model=ConversationDetail)
def get_messages(conversation_id: int, request: Request, db: Session = Depends(get_db)):
    """查看某会话的完整对话（含引用来源）"""
    enterprise_id = current_enterprise_id(request)
    user_id = current_user_id(request)
    conv = conversation_service.get_conversation(db, conversation_id, enterprise_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = conversation_service.get_messages_with_sources(db, conversation_id, enterprise_id, user_id)
    return {"id": conv.id, "title": conv.title, "messages": messages}


@router.post("/{conversation_id}/ask", response_model=ConversationDetail)
def ask_in_conversation(
    conversation_id: int, body: AskRequest, request: Request, db: Session = Depends(get_db)
):
    """在会话内提问：带多轮记忆的问答，返回更新后的完整会话（含新增消息）"""
    enterprise_id = current_enterprise_id(request)
    user_id = current_user_id(request)
    conv = conversation_service.get_conversation(db, conversation_id, enterprise_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 带历史问答：内部会落库本轮问答
    conversation_service.ask_with_history(
        db,
        conversation_id,
        body.question,
        kb_id=body.kb_id,
        enterprise_id=enterprise_id,
        user_id=user_id,
    )

    # 返回更新后的完整会话（前端可直接用返回内容刷新对话区）
    messages = conversation_service.get_messages_with_sources(db, conversation_id, enterprise_id, user_id)
    return {"id": conv.id, "title": conv.title, "messages": messages}


class SaveTurnRequest(BaseModel):
    """仅保存一轮问答（不重新生成）——前端流式已生成好回答，这里只持久化"""
    question: str
    answer: str
    kb_id: int = 1
    sources: list = []
    model_name: str = ""
    usage: dict = {}


@router.post("/{conversation_id}/save-turn", response_model=ConversationOut)
def save_turn(conversation_id: int, body: SaveTurnRequest, request: Request, db: Session = Depends(get_db)):
    """保存一轮问答到会话历史（不重新生成回答）。

    前端流式问答完成后调用：把【流式生成的回答】存库，
    保证历史会话和用户看到的回答一致（避免双生成不一致导致刷新后"失忆"）。
    """
    enterprise_id = current_enterprise_id(request)
    user_id = current_user_id(request)
    conv = conversation_service.get_conversation(db, conversation_id, enterprise_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    result = conversation_service.save_turn(
        db, conversation_id, body.question, body.answer,
        sources=body.sources, model_name=body.model_name, usage=body.usage,
        enterprise_id=enterprise_id, user_id=user_id,
    )
    conv = conversation_service.get_conversation(db, conversation_id, enterprise_id, user_id)
    return conv


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """删除会话（连同其全部消息 + 附件）

    任何登录用户都可以删**自己的**会话 —— 会话本来就是按 user_id 隔离的：
    下面的 get_conversation 会同时校验 enterprise_id 和 user_id，别人的会话查不到、
    直接返回 404。所以这里不需要管理员权限。
    早期这里挂了 require_admin，导致员工和企业管理员根本看不到删除入口
    （前端也据此用 v-if 隐藏了按钮）。
    """
    deleted = conversation_service.delete_conversation(
        db,
        conversation_id,
        current_enterprise_id(request),
        current_user_id(request),
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "删除成功"}


# ============ 会话附件（对话中上传，会话级临时上下文） ============

class AttachmentOut(BaseModel):
    """附件响应"""
    id: int
    filename: str
    created_at: object
    status: str = "done"
    failure_reason: str = ""
    summary: str = ""
    chunk_count: int = 0
    content_chars: int = 0
    file_size: int = 0


@router.post("/{conversation_id}/attachments")
def upload_attachment(
    conversation_id: int,
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
):
    """上传附件到会话（文档类，只在当前会话有效）。"""
    enterprise_id = current_enterprise_id(request)
    user_id = current_user_id(request)
    conv = conversation_service.get_conversation(db, conversation_id, enterprise_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        att = attachment_service.create_attachment(
            db,
            conversation_id,
            file,
            enterprise_id=enterprise_id,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    data = attachment_service.serialize_attachment(att)
    data["message"] = "附件已上传" if att.status == "done" else "附件已上传，但解析失败"
    return data


@router.get("/{conversation_id}/attachments")
def list_attachments(conversation_id: int, request: Request, db: Session = Depends(get_db)):
    """某会话的附件列表。"""
    enterprise_id = current_enterprise_id(request)
    user_id = current_user_id(request)
    if not conversation_service.get_conversation(db, conversation_id, enterprise_id, user_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    atts = attachment_service.list_attachments(db, conversation_id, enterprise_id=enterprise_id, user_id=user_id)
    return [attachment_service.serialize_attachment(a) for a in atts]


@router.delete("/{conversation_id}/attachments/{attachment_id}")
def delete_attachment(conversation_id: int, attachment_id: int, request: Request, db: Session = Depends(get_db)):
    """删除某会话的附件。"""
    enterprise_id = current_enterprise_id(request)
    user_id = current_user_id(request)
    ok = attachment_service.delete_attachment(
        db,
        conversation_id,
        attachment_id,
        enterprise_id=enterprise_id,
        user_id=user_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="附件不存在")
    return {"message": "删除成功"}

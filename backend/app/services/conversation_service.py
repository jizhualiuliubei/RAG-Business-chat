"""
会话业务逻辑层
================
作用：会话和消息的 CRUD + "带历史的多轮问答"编排。

核心设计（记忆重建机制）：
- 多轮记忆：用 InMemorySaver（内存）实现，thread_id = 会话id
- 历史持久化：每轮问答落 SQLite（conversation / message 表）
- 恢复会话：从 SQLite 读出历史消息，作为 messages 传给 Agent，
  即"用历史重建记忆"（因为 InMemorySaver 重启即丢，靠落盘历史兜底）
"""
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import rag
from app.models.attachment import ConversationAttachment, ConversationAttachmentChunk
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user_profile import UserProfile
from app.services import attachment_service, enterprise_service


# ============ 会话 CRUD ============

def create_conversation(
    db: Session,
    title: str = "新会话",
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> Conversation:
    """新建一个会话（前端"新建会话"按钮调用）。"""
    conv = Conversation(title=title, enterprise_id=enterprise_id, user_id=user_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def list_conversations(
    db: Session,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> list[Conversation]:
    """会话列表：按最后活动时间倒序（最近聊的排前面）"""
    stmt = select(Conversation).order_by(Conversation.updated_at.desc())
    if enterprise_id is not None:
        stmt = stmt.where(Conversation.enterprise_id == enterprise_id)
    if user_id is not None:
        stmt = stmt.where(Conversation.user_id == user_id)
    return db.execute(stmt).scalars().all()


def get_conversation(
    db: Session,
    conversation_id: int,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> Conversation | None:
    """按 id 查会话，查不到返回 None"""
    conv = db.get(Conversation, conversation_id)
    if conv and enterprise_id is not None and conv.enterprise_id != enterprise_id:
        return None
    if conv and user_id is not None and conv.user_id != user_id:
        return None
    return conv


def delete_conversation(
    db: Session,
    conversation_id: int,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> bool:
    """删除会话：同步清理消息、附件文件、附件记录和用户档案。"""
    conv = get_conversation(db, conversation_id, enterprise_id, user_id)
    if not conv:
        return False

    # 附件属于会话级临时上下文；删除会话时必须同时删文件和 DB 记录，
    # 否则会留下孤儿附件，后续同 id 查询仍能看到已删除会话的附件。
    attachments = (
        db.query(ConversationAttachment)
        .filter(ConversationAttachment.conversation_id == conversation_id)
        .all()
    )
    for att in attachments:
        try:
            Path(att.stored_path).unlink(missing_ok=True)
        except Exception:
            pass
        db.delete(att)
    db.query(ConversationAttachmentChunk).filter(
        ConversationAttachmentChunk.conversation_id == conversation_id
    ).delete()

    # 删消息和用户档案（都以 conversation_id 做隔离，避免留下孤儿数据）
    db.query(Message).filter(Message.conversation_id == conversation_id).delete()
    db.query(UserProfile).filter(UserProfile.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return True


# ============ 消息 CRUD ============

def list_messages(
    db: Session,
    conversation_id: int,
    limit_rounds: int | None = None,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> list[Message]:
    """查询某会话的消息（按时间正序，即对话顺序）。

    limit_rounds：可选，只取最近 N 轮对话（每轮 = 1 条 user + 1 条 assistant）。
    用于控制"上下文记忆窗口"——避免历史无限累积导致 prompt 过长、成本过高、超上下文上限。
    实现：按时间倒序取，再反转为正序返回（无论是否限制，都保证正序）。
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    if enterprise_id is not None:
        stmt = stmt.where(Message.enterprise_id == enterprise_id)
    if user_id is not None:
        stmt = stmt.where(Message.user_id == user_id)
    msgs = db.execute(stmt).scalars().all()
    if limit_rounds:
        # 每轮 2 条（user + assistant），取最近 limit_rounds 轮
        msgs = msgs[: limit_rounds * 2]
    # 反转为时间正序（对话顺序）——无论是否 limit_rounds 都保证正序
    msgs = list(reversed(msgs))
    return msgs


def _save_message(
    db: Session, conversation_id: int, role: str, content: str,
    sources=None, model_name: str = "", usage: dict | None = None,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> Message:
    """保存一条消息（落 SQLite），sources 序列化成 JSON 字符串。"""
    msg = Message(
        conversation_id=conversation_id,
        enterprise_id=enterprise_id,
        user_id=user_id,
        role=role,
        content=content,
        sources=json.dumps(sources or [], ensure_ascii=False),
        model_name=model_name or None,
        usage=json.dumps(usage, ensure_ascii=False) if usage else None,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def _parse_sources(sources_json: str | None) -> list:
    """把落库的 JSON 字符串解析回列表（前端要展示引用来源）。"""
    if not sources_json:
        return []
    try:
        return json.loads(sources_json)
    except json.JSONDecodeError:
        return []


def _parse_usage(usage_json: str | None) -> dict:
    """把落库的 usage JSON 字符串解析回 dict（前端展示 token 用量）。"""
    if not usage_json:
        return {}
    try:
        data = json.loads(usage_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _history_to_dicts(messages: list[Message]) -> list[dict]:
    """把 Message 对象列表转成 Agent 需要的消息字典列表（只留 role/content）。"""
    return [{"role": m.role, "content": m.content} for m in messages]


# ============ 带历史的多轮问答 ============

def ask_with_history(
    db: Session,
    conversation_id: int,
    question: str,
    kb_id: int = 1,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """在一个会话内提问：检索 → 阈值过滤 → 带历史生成 → 落库。

    返回：{answer, sources, hit}，与模块 4 的无记忆问答结构一致，前端可统一渲染。
    """
    # 1. 检索 + 阈值过滤（多轮时依然只基于"当前问题"检索，历史不用来检索）
    model_config = embed_config = None
    if enterprise_id is not None:
        model_config = enterprise_service.require_enterprise_deepseek_key(db, enterprise_id)
        embed_config = enterprise_service.require_enterprise_siliconflow_key(db, enterprise_id)

    _, contexts = rag.retrieve_contexts(
        question,
        kb_id=kb_id,
        enterprise_id=enterprise_id,
        model_config=model_config,
        embed_config=embed_config,
    )
    knowledge_base_sources = [
        {**ctx, "source_type": ctx.get("source_type") or "knowledge_base"}
        for ctx in contexts
    ]
    attachment_sources = attachment_service.retrieve_attachment_sources(
        db,
        conversation_id,
        question,
        enterprise_id=enterprise_id,
        user_id=user_id,
    )
    attachments_context = attachment_service.build_attachment_rag_context(attachment_sources)
    if not attachments_context:
        attachments_context = attachment_service.build_attachments_context(
            db,
            conversation_id,
            enterprise_id=enterprise_id,
            user_id=user_id,
        )
    combined_sources = knowledge_base_sources + attachment_sources
    hit = len(combined_sources) > 0

    # 2. 取该会话的历史消息（用于记忆重建）
    history_msgs = list_messages(db, conversation_id, enterprise_id=enterprise_id, user_id=user_id)
    history_dicts = _history_to_dicts(history_msgs)

    # 3. 生成回答
    usage = None
    model_name = ""
    if hit:
        result = rag.chat_with_history(
            question=question,
            context_blocks=knowledge_base_sources,
            history=history_dicts,
            thread_id=str(conversation_id),
            model_config=model_config,
            attachments_context=attachments_context,
        )
        answer = result["answer"]
        usage = result["usage"]
        model_name = result["model_name"]
    else:
        # 未命中知识库：模型先标注"没有相关内容"，再基于通用知识回答
        result = rag.generate_general_answer(
            question,
            model_config=model_config,
            attachments_context=attachments_context,
        )
        answer = result["answer"]
        usage = result["usage"]
        model_name = result["model_name"]

    # 4. 落库：用户问题 + AI 回答（AI 回答带上引用来源 + 模型信息）
    _save_message(db, conversation_id, "user", question, enterprise_id=enterprise_id, user_id=user_id)
    _save_message(
        db, conversation_id, "assistant", answer,
        sources=combined_sources, model_name=model_name, usage=usage,
        enterprise_id=enterprise_id, user_id=user_id,
    )

    # 5. 会话标题：第一次提问时，用问题前 20 个字当标题
    conv = get_conversation(db, conversation_id, enterprise_id, user_id)
    if conv and conv.title == "新会话":
        conv.title = question[:20]
        db.commit()

    return {"answer": answer, "sources": combined_sources, "hit": hit}


def save_turn(
    db: Session, conversation_id: int, question: str, answer: str,
    sources: list | None = None, model_name: str = "", usage: dict | None = None,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """只保存一轮问答到会话（不重新生成）。

    用途：前端流式问答已生成好回答，这里只做持久化——
    避免"流式回答（记得上文）"和"落库回答（不记得上文）"两次生成不一致，
    导致刷新后历史会话显示的是不记得上文的回答。
    """
    _save_message(db, conversation_id, "user", question, enterprise_id=enterprise_id, user_id=user_id)
    _save_message(
        db, conversation_id, "assistant", answer,
        sources=sources or [], model_name=model_name, usage=usage,
        enterprise_id=enterprise_id, user_id=user_id,
    )
    # 会话标题：第一次提问时，用问题前 20 个字当标题
    conv = get_conversation(db, conversation_id, enterprise_id, user_id)
    if conv and conv.title == "新会话":
        conv.title = question[:20]
        db.commit()
    return {"id": conversation_id, "title": conv.title if conv else "新会话"}


# ============ 消息查询（含引用来源解析） ============

def get_messages_with_sources(
    db: Session,
    conversation_id: int,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> list[dict]:
    """查询会话消息，并把 assistant 消息的 sources/usage 解析成可展示的结构。"""
    msgs = list_messages(db, conversation_id, enterprise_id=enterprise_id, user_id=user_id)
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "sources": _parse_sources(m.sources),
            "model_name": m.model_name or "",
            "usage": _parse_usage(m.usage),
            "created_at": m.created_at,
        }
        for m in msgs
    ]

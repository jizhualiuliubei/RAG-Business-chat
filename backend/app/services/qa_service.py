"""
问答业务逻辑层
================
作用：编排"一次提问"的完整流程：
    检索 → 阈值过滤 → 组装引用来源 → 生成回答 → 返回结构化结果。

对应知识点：
- 手动 RAG 编排（课程 course_rag_full.py）：检索→拼上下文→生成
- 引用溯源（参考 ChatChat 产品设计）：回答必须附带命中的文档名和片段内容
"""
from concurrent.futures import ThreadPoolExecutor

from app.core import rag
from app.models.document import Document
from app.models.message import Message
from app.services import conversation_service  # 读历史消息做上下文记忆
from app.services import user_profile_service  # 长期记忆：用户档案
from app.services import attachment_service     # 会话附件：临时上下文
from app.services import enterprise_service
from sqlalchemy import or_, select

# 历史摘要和检索互不依赖，用这个池把摘要挪到后台线程，和检索并行跑。
# 本地实测：摘要约 4 秒、检索约 5-10 秒，串行 9-14 秒；并行后首字节省下摘要那一份。
# 池大小给 4：一次提问只占 1 个线程，其余留给并发提问。
_SUMMARY_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hist-summary")


def _kb_ids(kb_id: int | list[int]) -> list[int]:
    if isinstance(kb_id, list):
        return [int(item) for item in kb_id]
    return [int(kb_id)]


def active_document_ids_for_scope(db, kb_id: int | list[int], enterprise_id: int | None = None) -> list[int] | None:
    """返回当前租户、当前知识库范围内仍有效的文档 id。

    这是防止“数据库文档已删但 Zilliz 残留向量仍被召回”的最终保险。
    """
    if db is None:
        return None

    stmt = select(Document.id).where(
        Document.kb_id.in_(_kb_ids(kb_id)),
        Document.status == "done",
    )
    if enterprise_id is not None:
        if enterprise_service.is_system_enterprise(db, enterprise_id):
            stmt = stmt.where(or_(Document.enterprise_id == enterprise_id, Document.enterprise_id.is_(None)))
        else:
            stmt = stmt.where(Document.enterprise_id == enterprise_id)
    return [int(row[0]) for row in db.execute(stmt).all()]


def answer_question(
    question: str,
    kb_id: int = 1,
    history: list | None = None,
    db=None,
    enterprise_id: int | None = None,
    conversation_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """处理一次提问，返回 {answer, sources, hit}。

    参数：
        question：用户问题
        kb_id   ：知识库 id（P0 阶段固定默认库，P1 多库时传真实 id）
        history ：历史消息列表（模块 5 接入记忆后传入；P0 阶段先传 None）

    返回：
        answer  —— 模型生成的回答文本
        sources —— 命中的来源片段列表（[{text, source, chunk_id, score}]）
        hit     —— 是否检索到相关内容（用于前端提示"没有相关内容"）
    """
    model_config = embed_config = None
    if db is not None and enterprise_id is not None:
        model_config = enterprise_service.require_enterprise_deepseek_key(db, enterprise_id)
        embed_config = enterprise_service.require_enterprise_siliconflow_key(db, enterprise_id)
    allowed_doc_ids = active_document_ids_for_scope(db, kb_id, enterprise_id)

    attachment_sources = []
    attachments_context = ""
    if db is not None and conversation_id is not None:
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

    # 第1步：检索 + 阈值过滤（低于阈值说明知识库没有相关内容）
    _, contexts = rag.retrieve_contexts(
        question,
        kb_id=kb_id,
        enterprise_id=enterprise_id,
        model_config=model_config,
        embed_config=embed_config,
        allowed_doc_ids=allowed_doc_ids,
    )

    # 第2步：组织引用来源（无论有没有命中，都返回给前端）
    knowledge_base_sources = [
        {**ctx, "source_type": ctx.get("source_type") or "knowledge_base"}
        for ctx in contexts
    ]
    sources = knowledge_base_sources + attachment_sources
    hit = len(sources) > 0

    # 第3步：生成回答
    if hit:
        result = rag.generate_answer(
            question,
            knowledge_base_sources,
            model_config=model_config,
            attachments_context=attachments_context,
        )
        answer = result["answer"]
        usage = result["usage"]
        model_name = result["model_name"]
    else:
        # 未命中知识库：不再返回固定文案，而是让模型先标注"没有相关内容"再基于通用知识回答
        result = rag.generate_general_answer(
            question,
            model_config=model_config,
            attachments_context=attachments_context,
        )
        answer = result["answer"]
        usage = result["usage"]
        model_name = result["model_name"]

    return {
        "answer": answer,
        "sources": sources,
        "knowledge_base_sources": knowledge_base_sources,
        "attachment_sources": attachment_sources,
        "hit": hit,
        "usage": usage,
        "model_name": model_name,
    }


def stream_answer(
    question: str,
    kb_id: int = 1,
    conversation_id: int | None = None,
    db=None,
    enterprise_id: int | None = None,
    user_id: int | None = None,
):
    """流式问答编排（生成器）：读历史记忆 → 检索 → 过滤 → 流式生成。

    返回的生成器逐个 yield 事件，前端按 SSE 协议解析：
    1. 先 yield 一个 {"type": "sources", "sources": [...], "hit": bool} —— 引用来源元数据
    2. 再逐个 yield {"type": "text", "content": "..."} —— 模型逐块输出

    为什么先发 sources：前端要先知道引用来源，才能渲染"引用来源"折叠区，
    然后 AI 回答逐字出现（打字机效果）。

    conversation_id：会话 id。传入时从 SQLite 读历史消息 + 用户档案，
    拼进流式 prompt —— 让打字机回答本身就"记得"上文（同一会话的上下文记忆）。
    """
    # 第0步：读历史记忆 + 用户档案 + 会话附件
    history = None
    history_future = None    # 并行跑的历史摘要，检索之后再取回
    profile_text = ""
    attachments_context = ""
    attachment_sources = []
    model_config = embed_config = None
    if db is not None and enterprise_id is not None:
        model_config = enterprise_service.require_enterprise_deepseek_key(db, enterprise_id)
        embed_config = enterprise_service.require_enterprise_siliconflow_key(db, enterprise_id)
    allowed_doc_ids = active_document_ids_for_scope(db, kb_id, enterprise_id)

    if conversation_id and db:
        # 提取用户主动提供的信息（如"我叫张三"）存入档案
        user_profile_service.extract_and_save(db, conversation_id, question)
        # 读档案注入文本（如"用户姓名：张三"）——长期记忆，刷新/重启不丢
        profile_text = user_profile_service.get_profile_text(db, conversation_id)
        raw_history = conversation_service._history_to_dicts(
            conversation_service.list_messages(
                db,
                conversation_id,
                enterprise_id=enterprise_id,
                user_id=user_id,
            )
        )
        # 主流式链路不经过 Agent middleware，这里显式做“旧历史摘要 + 最近原文”。
        # 摘要是纯 LLM 调用（不碰 db），和检索互不依赖，所以丢到后台线程并行跑，
        # 检索结束后再取回 —— 实测能省下摘要那一份耗时（本地约 4 秒）。
        history_future = _SUMMARY_POOL.submit(
            rag.build_memory_history, raw_history, model_config=model_config
        )
        # 会话附件是当前账号当前会话内的临时 RAG：先检索相关切片，
        # 没命中时只注入摘要，避免把整份附件硬塞进 prompt。
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

    # 第1步：检索 + 阈值过滤
    _, contexts = rag.retrieve_contexts(
        question,
        kb_id=kb_id,
        enterprise_id=enterprise_id,
        model_config=model_config,
        embed_config=embed_config,
        allowed_doc_ids=allowed_doc_ids,
    )

    # 取回并行跑的历史摘要：此时它通常已经完成，几乎不额外等待。
    # 万一线程里抛了异常，退化成“本轮没有历史记忆”，不影响回答本身。
    if history_future is not None:
        try:
            history = history_future.result()
        except Exception:
            history = None

    knowledge_base_sources = [
        {**ctx, "source_type": ctx.get("source_type") or "knowledge_base"}
        for ctx in contexts
    ]
    combined_sources = knowledge_base_sources + attachment_sources
    hit = len(combined_sources) > 0

    # 第2步：先发引用来源元数据
    import json
    yield json.dumps(
        {
            "type": "sources",
            "sources": combined_sources,
            "knowledge_base_sources": knowledge_base_sources,
            "attachment_sources": attachment_sources,
            "hit": hit,
        },
        ensure_ascii=False,
    )

    # 第3步：流式生成回答（带历史记忆 + 用户档案 + 会话附件）
    if hit:
        # 逐块 yield 模型输出的文本片段
        for text_chunk in rag.stream_generate_answer(
            question, knowledge_base_sources, history=history,
            profile_text=profile_text, attachments_context=attachments_context,
            model_config=model_config,
        ):
            yield json.dumps({"type": "text", "content": text_chunk}, ensure_ascii=False)
    else:
        # 未命中知识库：模型先标注"没有相关内容"，再基于通用知识流式回答
        for text_chunk in rag.stream_generate_general_answer(
            question, history=history,
            profile_text=profile_text, attachments_context=attachments_context,
            model_config=model_config,
        ):
            yield json.dumps({"type": "text", "content": text_chunk}, ensure_ascii=False)


def save_message(
    db,
    conversation_id: int,
    role: str,
    content: str,
    sources: list | None = None,
    enterprise_id: int | None = None,
    user_id: int | None = None,
) -> Message:
    """把一轮问答落库（模块 5 历史会话用；sources 序列化成 JSON 字符串存储）。"""
    import json

    msg = Message(
        conversation_id=conversation_id,
        enterprise_id=enterprise_id,
        user_id=user_id,
        role=role,
        content=content,
        sources=json.dumps(sources or [], ensure_ascii=False),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg

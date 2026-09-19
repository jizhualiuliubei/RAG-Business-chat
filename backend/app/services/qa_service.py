"""
问答业务逻辑层
================
作用：编排"一次提问"的完整流程：
    检索 → 阈值过滤 → 组装引用来源 → 生成回答 → 返回结构化结果。

对应知识点：
- 手动 RAG 编排：检索→拼上下文→生成
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
# 两者串行时首字延迟是两段之和；并行后只等检索那一段。
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
    # 先把两个模型配置置空。后面只有拿到企业身份才会去查 Key；
    # 查不到就保持 None，让下游回退到环境变量里的兜底模型（本地开发、平台账号用）

    if db is not None and enterprise_id is not None:
        # 两个前提都满足才去查：db 是查库的前提，enterprise_id 是"按企业取 Key"的前提

        model_config = enterprise_service.require_enterprise_deepseek_key(db, enterprise_id)
        # 取【该企业的对话模型配置】（Key + base_url + 模型名）。
        # 用 require_ 而不是 get_：企业没配 Key 就直接抛错、提示去后台配置，
        # 不静默回退——否则会拿别人的 Key 跑，既串了账也说不清成本算谁的

        embed_config = enterprise_service.require_enterprise_siliconflow_key(db, enterprise_id)
        # 同理取【该企业的嵌入模型配置】（BGE-M3）。向量化也要走企业自己的 Key，
        # 所以嵌入模型同样按企业路由，而不是全局一个

    allowed_doc_ids = active_document_ids_for_scope(db, kb_id, enterprise_id)
    # 实时从数据库查【本企业 + 本知识库 + 状态有效】的文档 id 白名单，两个作用：
    #   ① 多租户隔离 —— 检索时只可能捞到本企业的文档
    #   ② 兜脏数据 —— 文档记录删了但向量还残留在 Milvus 里，白名单一卡就捞不出来
    # 这个列表会一路传到 milvus_store.search 的 doc_ids 参数

    attachment_sources = []
    # 会话附件命中的片段，先占位成空列表。
    # 这样"没有会话"和"有会话但没命中"返回的结构一致，前端不用写分支判断

    attachments_context = ""
    # 附件拼成的提示词文本，同样先占位

    if db is not None and conversation_id is not None:
        # 只有【有数据库】且【有会话 id】才做附件检索——附件是按会话存的，没会话就没附件

        attachment_sources = attachment_service.retrieve_attachment_sources(
            db,
            conversation_id,
            question,
            enterprise_id=enterprise_id,
            user_id=user_id,
        )
        # 拿【当前问题】去该会话的附件里检索相关片段。
        # 注意这里是 MySQL + 词项重合打分，不走向量库——附件量小，不值得为它建索引。
        # enterprise_id 和 user_id 都传：附件是"当前账号 + 当前会话"双边界，换人或换会话都查不到

        attachments_context = attachment_service.build_attachment_rag_context(attachment_sources)
        # 把命中的附件片段拼成一段【带编号的提示词文本】，准备塞进模型上下文

        if not attachments_context:
            # 片段一个都没命中，退一步走兜底

            attachments_context = attachment_service.build_attachments_context(
                db,
                conversation_id,
                enterprise_id=enterprise_id,
                user_id=user_id,
            )
            # 没命中时只注入【附件摘要】（有哪些附件、多大、什么类型），
            # 而不是把整份附件硬塞进 prompt。
            # 好处：控 token，同时让模型知道"这个会话里确实传过东西"，
            # 不会答成"我没看到你上传的附件"

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
        # 检索结束后再取回 —— 省下摘要那一份等待。
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

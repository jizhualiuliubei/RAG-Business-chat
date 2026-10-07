"""
概览统计路由
================
作用：提供 Dashboard 概览页（现代 AI 工作台）所需的统计数据。

返回结构：
- 基础计数：知识库/文档/会话/消息 数量 + 向量片段(chunk)总数
- kb_stats：各知识库详情（文档数 / chunk 数 / 状态分布 / 最近上传时间）
- recent_conversations：最近 N 个会话（标题 / 消息数 / 最后活动时间）
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import current_enterprise_id, current_user_id, require_admin
from app.database import get_db
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message

router = APIRouter(prefix="/api/stats", tags=["概览统计"])

# 概览页展示的最近会话数量
RECENT_CONV_LIMIT = 6


@router.get("/summary")
def get_summary(request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """概览统计：基础计数 + 各知识库分布 + 最近会话。

    全部用 SQL 聚合，一次路由返回，无额外表。
    """
    enterprise_id = current_enterprise_id(request)
    user_id = current_user_id(request)

    # ========== 1. 基础计数 ==========
    kb_count = db.execute(
        select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.enterprise_id == enterprise_id)
    ).scalar_one()
    doc_count = db.execute(
        select(func.count()).select_from(Document).where(Document.enterprise_id == enterprise_id)
    ).scalar_one()
    conv_count = db.execute(
        select(func.count()).select_from(Conversation).where(
            Conversation.enterprise_id == enterprise_id,
            Conversation.user_id == user_id,
        )
    ).scalar_one()
    msg_count = db.execute(
        select(func.count()).select_from(Message).where(
            Message.enterprise_id == enterprise_id,
            Message.user_id == user_id,
        )
    ).scalar_one()
    # 向量片段总数（各文档 chunk 数之和，体现"向量化"能力）
    chunk_total = db.execute(
        select(func.coalesce(func.sum(Document.chunk_count), 0)).where(Document.enterprise_id == enterprise_id)
    ).scalar_one()

    def _iso(dt):
        if dt is None:
            return None
        if isinstance(dt, datetime):
            return dt.strftime("%Y-%m-%d %H:%M")
        return str(dt)

    # ========== 2. 各知识库文档分布 ==========
    # 按 kb_id 分组聚合文档：数量 / chunk 总和 / 各状态计数 / 最近上传时间
    kb_doc_rows = db.execute(
        select(
            Document.kb_id,
            func.count(Document.id).label("doc_count"),
            func.coalesce(func.sum(Document.chunk_count), 0).label("chunk_count"),
            func.max(Document.created_at).label("last_upload_at"),
        )
        .where(Document.enterprise_id == enterprise_id)
        .group_by(Document.kb_id)
    ).all()
    # 各知识库的状态分布（done/processing/failed）
    status_rows = db.execute(
        select(
            Document.kb_id, Document.status, func.count(Document.id)
        )
        .where(Document.enterprise_id == enterprise_id)
        .group_by(Document.kb_id, Document.status)
    ).all()

    kb_stats_map = {
        r.kb_id: {
            "id": r.kb_id,
            "name": "",
            "doc_count": r.doc_count,
            "chunk_count": r.chunk_count,
            "status_counts": {"done": 0, "processing": 0, "failed": 0},
            "last_upload_at": r.last_upload_at,
        }
        for r in kb_doc_rows
    }
    for kb_id, status, cnt in status_rows:
        if kb_id in kb_stats_map and status in kb_stats_map[kb_id]["status_counts"]:
            kb_stats_map[kb_id]["status_counts"][status] = cnt

    # 补库名（即使某库无文档也出现在列表里）
    kb_names = db.execute(
        select(KnowledgeBase.id, KnowledgeBase.name).where(KnowledgeBase.enterprise_id == enterprise_id)
    ).all()
    for kb_id, name in kb_names:
        if kb_id not in kb_stats_map:
            kb_stats_map[kb_id] = {
                "id": kb_id, "name": name, "doc_count": 0, "chunk_count": 0,
                "status_counts": {"done": 0, "processing": 0, "failed": 0},
                "last_upload_at": None,
            }
        else:
            kb_stats_map[kb_id]["name"] = name

    # 每个知识库的文档列表（概览下钻：文件名/大小/状态/chunk数/时间）
    docs = db.execute(
        select(Document).where(Document.enterprise_id == enterprise_id).order_by(Document.created_at.desc())
    ).scalars().all()
    for kb_id, info in kb_stats_map.items():
        info["documents"] = [
            {
                "id": d.id,
                "filename": d.filename,
                "file_size": d.file_size,
                "status": d.status,
                "chunk_count": d.chunk_count,
                "created_at": _iso(d.created_at),
            }
            for d in docs
            if d.kb_id == kb_id
        ]

    kb_stats = sorted(kb_stats_map.values(), key=lambda k: k["id"])

    # ========== 3. 最近会话（按最后活动倒序）==========
    recent_rows = db.execute(
        select(Conversation, func.count(Message.id).label("msg_count"))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.enterprise_id == enterprise_id, Conversation.user_id == user_id)
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc())
        .limit(RECENT_CONV_LIMIT)
    ).all()

    recent_conversations = [
        {
            "id": conv.id,
            "title": conv.title,
            "message_count": msg_count,
            "updated_at": _iso(conv.updated_at),
        }
        for conv, msg_count in recent_rows
    ]

    return {
        "kb_count": kb_count,
        "doc_count": doc_count,
        "conv_count": conv_count,
        "msg_count": msg_count,
        "chunk_count": chunk_total,
        "kb_stats": kb_stats,
        "recent_conversations": recent_conversations,
    }

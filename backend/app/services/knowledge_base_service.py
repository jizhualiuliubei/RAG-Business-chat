"""
知识库业务逻辑层
================
作用：知识库的 CRUD。P0 阶段只有一个"默认知识库"，
模块 9 把它升级成真正可管理多个知识库。

核心：每个知识库一个 id，文档挂 kb_id，检索按 kb_id 过滤（已预留字段）。
"""
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import UPLOAD_DIR
from app.core import milvus_store
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services import enterprise_service


def normalize_knowledge_base_name(name: str | None) -> str:
    """统一知识库名称入口，避免空白名称进入系统。"""
    normalized = (name or "").strip()
    if not normalized:
        raise ValueError("知识库名称不能为空")
    return normalized


def valid_name_filter():
    """过滤历史脏数据：旧库可能存在空字符串或纯空白名称。"""
    return and_(KnowledgeBase.name.is_not(None), func.length(func.trim(KnowledgeBase.name)) > 0)


def list_knowledge_bases(db: Session, enterprise_id: int | None = None) -> list[KnowledgeBase]:
    """知识库列表（按 id 升序，默认知识库在第一个）"""
    stmt = select(KnowledgeBase).where(valid_name_filter()).order_by(KnowledgeBase.id.asc())
    if enterprise_id is not None:
        if enterprise_service.is_system_enterprise(db, enterprise_id):
            stmt = select(KnowledgeBase).where(
                valid_name_filter(),
                or_(KnowledgeBase.enterprise_id == enterprise_id, KnowledgeBase.enterprise_id.is_(None)),
            ).order_by(KnowledgeBase.id.asc())
        else:
            stmt = select(KnowledgeBase).where(
                valid_name_filter(),
                KnowledgeBase.enterprise_id == enterprise_id,
            ).order_by(KnowledgeBase.id.asc())
    return db.execute(stmt).scalars().all()


def get_knowledge_base(db: Session, kb_id: int, enterprise_id: int | None = None) -> KnowledgeBase | None:
    """按 id 查知识库，查不到返回 None"""
    kb = db.get(KnowledgeBase, kb_id)
    if (
        kb
        and enterprise_id is not None
        and kb.enterprise_id != enterprise_id
        and not (kb.enterprise_id is None and enterprise_service.is_system_enterprise(db, enterprise_id))
    ):
        return None
    return kb


def create_knowledge_base(db: Session, name: str, enterprise_id: int | None = None) -> KnowledgeBase:
    """新建知识库（name 唯一，重名抛 ValueError）"""
    name = normalize_knowledge_base_name(name)
    # 检查重名：知识库名应该是唯一的
    stmt = select(KnowledgeBase).where(func.trim(KnowledgeBase.name) == name)
    if enterprise_id is not None:
        stmt = stmt.where(KnowledgeBase.enterprise_id == enterprise_id)
    if db.execute(stmt).scalar_one_or_none() is not None:
        raise ValueError(f"知识库名称已存在: {name}")

    kb = KnowledgeBase(enterprise_id=enterprise_id, name=name)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


def update_knowledge_base(db: Session, kb_id: int, name: str, enterprise_id: int | None = None) -> KnowledgeBase | None:
    """更新知识库名称（name 唯一）。"""
    kb = db.get(KnowledgeBase, kb_id)
    if not kb or (enterprise_id is not None and kb.enterprise_id != enterprise_id):
        return None

    name = normalize_knowledge_base_name(name)
    stmt = select(KnowledgeBase).where(func.trim(KnowledgeBase.name) == name, KnowledgeBase.id != kb_id)
    if enterprise_id is not None:
        stmt = stmt.where(KnowledgeBase.enterprise_id == enterprise_id)
    if db.execute(stmt).scalar_one_or_none() is not None:
        raise ValueError(f"知识库名称已存在: {name}")

    kb.name = name
    db.commit()
    db.refresh(kb)
    return kb


def delete_knowledge_base(db: Session, kb_id: int, enterprise_id: int | None = None) -> bool:
    """删除知识库：级联删除其下所有文档 + 向量数据。

    步骤：
    1. 找出该知识库下的所有文档 id（它们的向量也要删）
    2. 逐个删除向量（按 doc_id 过滤）
    3. 删除文档记录 + 知识库记录
    """
    kb = db.get(KnowledgeBase, kb_id)
    if not kb:
        return False
    if (
        enterprise_id is not None
        and kb.enterprise_id != enterprise_id
        and not (kb.enterprise_id is None and enterprise_service.is_system_enterprise(db, enterprise_id))
    ):
        return False

    # 1. 找出该库下的文档
    stmt = select(Document).where(Document.kb_id == kb_id)
    if enterprise_id is not None:
        if enterprise_service.is_system_enterprise(db, enterprise_id):
            stmt = stmt.where(or_(Document.enterprise_id == enterprise_id, Document.enterprise_id.is_(None)))
        else:
            stmt = stmt.where(Document.enterprise_id == enterprise_id)
    docs = db.execute(stmt).scalars().all()

    # 2. 批量删向量（一次请求 + 一次 flush，避免知识库文档多时删除很慢）
    doc_ids = [doc.id for doc in docs]
    try:
        try:
            if enterprise_id is None:
                milvus_store.delete_by_doc_ids(doc_ids)
            else:
                milvus_store.delete_by_doc_ids(doc_ids, enterprise_id=enterprise_id)
        except TypeError as exc:
            if "enterprise_id" not in str(exc):
                raise
            milvus_store.delete_by_doc_ids(doc_ids)
    except Exception as exc:
        print(f"[警告] 删除知识库时批量删向量失败 doc_ids={doc_ids}: {exc}")

    for doc in docs:
        ext = "." + doc.filename.rsplit(".", 1)[-1].lower() if "." in doc.filename else ""
        stored_path = UPLOAD_DIR / f"doc_{doc.id}{ext}"
        try:
            stored_path.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[警告] 删除知识库时删文件失败 doc_id={doc.id}: {exc}")

    # 3. 删文档记录 + 知识库记录
    for doc in docs:
        db.delete(doc)
    db.delete(kb)
    db.commit()
    return True

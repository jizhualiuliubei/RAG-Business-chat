"""
文档业务逻辑层
================
作用：把"路由层"的请求，翻译成"具体的业务操作"。
这一层负责：
- 上传：校验 + 建记录 + 落盘
- 列表 / 状态查询 / 删除
- 后台处理函数（解析文本，更新状态）
"""
from pathlib import Path
import queue
import threading
import traceback

from fastapi import UploadFile
from sqlalchemy import or_, select

from app.config import DOCUMENT_PROCESS_CONCURRENCY, MAX_FILE_SIZE, UPLOAD_DIR
from app.core import milvus_store
from app.core.embeddings import embed_documents
from app.core.loader import load_document
from app.core.splitter import split_documents
from app.database import SessionLocal
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services import enterprise_service

# 允许上传的扩展名白名单（和 core/loader.py 支持的保持一致）
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx", ".csv", ".xlsx", ".xls"}

# 线上 2核4G 机器不适合同时解析/向量化多个文档。这里用进程内信号量
# 让 FastAPI BackgroundTasks 排队执行，避免 MySQL 连接池被批量上传打满。
_PROCESS_SEMAPHORE = threading.BoundedSemaphore(max(DOCUMENT_PROCESS_CONCURRENCY, 1))
VECTOR_DELETE_TIMEOUT_SECONDS = 8


def _get_ext(filename: str) -> str:
    """取小写扩展名，例如 '手册.txt' -> '.txt'"""
    return Path(filename).suffix.lower()


def _validate_ext(filename: str) -> None:
    """校验扩展名是否在白名单里，不在则抛 ValueError（由路由层转成 400 响应）"""
    ext = _get_ext(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型 {ext}，仅支持 .txt / .pdf / .docx / .csv / .xlsx")


def _validate_size(file_size: int) -> None:
    """校验文件大小是否超限（超大文件后台解析/向量化会长时间卡住）。"""
    if file_size > MAX_FILE_SIZE:
        limit_mb = MAX_FILE_SIZE // (1024 * 1024)
        raise ValueError(f"文件超过大小限制 {limit_mb}MB，请拆分后上传")


def create_document(db, file: UploadFile, kb_id: int, enterprise_id: int | None = None) -> dict:
    """上传核心逻辑：
    1. 校验扩展名
    2. 先在数据库建一条 processing 状态的记录（拿到自增 id）
    3. 用"文档 id"作为存储文件名落盘（避免用户文件名里的特殊字符 / 路径穿越问题）
    4. 返回文档 id 和落盘路径，由路由层触发后台任务
    """
    _validate_ext(file.filename)

    # file.size 是上传文件的大小（字节）；getattr 兜底防止个别版本没有该属性
    file_size = getattr(file, "size", 0) or 0
    _validate_size(file_size)

    with db:
        if enterprise_id is not None:
            kb = db.get(KnowledgeBase, kb_id)
            if not kb:
                raise ValueError("知识库不存在或无权限访问")
            if (
                kb.enterprise_id != enterprise_id
                and not (kb.enterprise_id is None and enterprise_service.is_system_enterprise(db, enterprise_id))
            ):
                raise ValueError("知识库不存在或无权限访问")
        doc = Document(
            enterprise_id=enterprise_id,
            kb_id=kb_id,
            filename=file.filename,
            file_size=file_size,
            status="processing",
            failure_reason=None,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)  # 刷新：让 doc.id 拿到数据库自增后的值
        doc_id = doc.id

    # 用 id 命名存储文件，避免中文 / 特殊字符造成路径问题
    ext = _get_ext(file.filename)
    stored_path = UPLOAD_DIR / f"doc_{doc_id}{ext}"

    # 把上传的二进制内容写入磁盘（file.file 是底层文件对象）
    with open(stored_path, "wb") as f:
        f.write(file.file.read())

    return {"doc_id": doc_id, "stored_path": stored_path}


def list_documents(db, kb_id: int, enterprise_id: int | None = None):
    """查询某知识库下的文档列表，按上传时间倒序（新的在前）"""
    stmt = (
        select(Document)
        .where(Document.kb_id == kb_id)
        .order_by(Document.created_at.desc())
    )
    if enterprise_id is not None:
        if enterprise_service.is_system_enterprise(db, enterprise_id):
            stmt = stmt.where(or_(Document.enterprise_id == enterprise_id, Document.enterprise_id.is_(None)))
        else:
            stmt = stmt.where(Document.enterprise_id == enterprise_id)
    return db.execute(stmt).scalars().all()


def get_document(db, doc_id: int, enterprise_id: int | None = None):
    """按 id 查单个文档，查不到返回 None"""
    doc = db.get(Document, doc_id)
    if (
        doc
        and enterprise_id is not None
        and doc.enterprise_id != enterprise_id
        and not (doc.enterprise_id is None and enterprise_service.is_system_enterprise(db, enterprise_id))
    ):
        return None
    return doc


def delete_document(db, doc_id: int, enterprise_id: int | None = None) -> bool:
    """删除文档：删除向量库数据 + 数据库记录 + 落盘的物理文件。

    顺序说明：先删向量库，再删 DB 记录。这样即使 DB 删除失败，
    向量数据也不会残留成"幽灵数据"；反过来如果向量删除失败会抛异常，
    整个删除请求失败，由调用方决定是否重试。
    """
    doc = get_document(db, doc_id, enterprise_id)
    if not doc:
        return False

    # 1. 删除 Milvus 里该文档的全部向量。云向量库偶发慢响应时不阻塞用户删除。
    _delete_vectors_best_effort([doc_id], enterprise_id=enterprise_id)

    filename = doc.filename  # 先取出文件名，commit 后对象属性会失效
    db.delete(doc)
    db.commit()

    # 2. 删除物理文件：按同样的命名规则构造路径
    ext = _get_ext(filename)
    stored_path = UPLOAD_DIR / f"doc_{doc_id}{ext}"
    if stored_path.exists():
        stored_path.unlink()

    return True


def delete_documents(db, doc_ids: list[int], enterprise_id: int | None = None) -> int:
    """批量删除文档：一次性删除向量，再删除数据库记录和本地文件。"""
    unique_ids = list(dict.fromkeys(int(doc_id) for doc_id in doc_ids))
    if not unique_ids:
        return 0

    stmt = select(Document).where(Document.id.in_(unique_ids))
    if enterprise_id is not None:
        if enterprise_service.is_system_enterprise(db, enterprise_id):
            stmt = stmt.where(or_(Document.enterprise_id == enterprise_id, Document.enterprise_id.is_(None)))
        else:
            stmt = stmt.where(Document.enterprise_id == enterprise_id)
    docs = db.execute(stmt).scalars().all()
    if not docs:
        return 0

    _delete_vectors_best_effort([doc.id for doc in docs], enterprise_id=enterprise_id)

    for doc in docs:
        filename = doc.filename
        db.delete(doc)
        ext = _get_ext(filename)
        stored_path = UPLOAD_DIR / f"doc_{doc.id}{ext}"
        try:
            stored_path.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[警告] 批量删除物理文件失败 doc_id={doc.id}: {exc}", flush=True)
    db.commit()
    return len(docs)


def _delete_vectors_best_effort(doc_ids: list[int], enterprise_id: int | None = None) -> None:
    """尽力删除云向量，超时或失败不阻塞文档治理操作。

    Zilliz delete/flush 属外部网络调用，若同步等待会导致前端批量删除按钮长时间 loading。
    文档 DB 记录删除后，检索侧还会按企业与知识库过滤；残留向量后续可由维护任务清理。
    """
    ids = list(dict.fromkeys(int(doc_id) for doc_id in doc_ids))
    if not ids:
        return
    result_queue: queue.Queue[Exception | None] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            try:
                if enterprise_id is None:
                    milvus_store.delete_by_doc_ids(ids)
                else:
                    milvus_store.delete_by_doc_ids(ids, enterprise_id=enterprise_id)
            except TypeError as exc:
                if "enterprise_id" not in str(exc):
                    raise
                milvus_store.delete_by_doc_ids(ids)
            result_queue.put(None)
        except Exception as exc:
            result_queue.put(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(VECTOR_DELETE_TIMEOUT_SECONDS)
    if thread.is_alive():
        print(f"[警告] 删除向量超时，已转为后台尽力清理 doc_ids={ids}", flush=True)
        return
    try:
        exc = result_queue.get_nowait()
    except queue.Empty:
        return
    if exc:
        print(f"[警告] 删除向量失败 doc_ids={ids}: {exc}", flush=True)


def process_document_with_limit(doc_id: int, stored_path: str, filename: str, enterprise_id: int | None = None) -> None:
    """带并发限制的后台处理入口，供上传接口注册 BackgroundTasks 使用。"""
    print(f"[解析队列] 等待处理 doc_id={doc_id} filename={filename}", flush=True)
    with _PROCESS_SEMAPHORE:
        print(f"[解析队列] 开始处理 doc_id={doc_id} filename={filename}", flush=True)
        if enterprise_id is None:
            process_document(doc_id, stored_path, filename)
        else:
            process_document(doc_id, stored_path, filename, enterprise_id=enterprise_id)
        print(f"[解析队列] 处理结束 doc_id={doc_id} filename={filename}", flush=True)


def process_document(doc_id: int, stored_path: str, filename: str, enterprise_id: int | None = None) -> None:
    """后台处理函数（响应返回后由 FastAPI 执行）：
    1. 加载文档为文本（TextLoader / PyPDFLoader / Docx2txtLoader）
    2. 切分成长度合适的 chunk（RecursiveCharacterTextSplitter）
    3. 批量向量化（BGE-M3 embed_documents）
    4. 写入 Milvus（upsert + flush）
    5. 更新状态为 done + 记录 chunk 数

    对应课程 course_rag_full.py 的完整入库流程：
    加载 → 切分 → 向量化 → 存储。

    注意：这里必须自己开 SessionLocal，
    因为后台任务执行时，请求的数据库会话已经关闭了。
    """
    try:
        # 第1步：加载文档为 Document 列表
        import time
        _t0 = time.time()
        docs = load_document(stored_path, filename)
        print(f"[解析] {filename} load耗时={time.time()-_t0:.1f}s docs={len(docs)}", flush=True)

        # 判空：某些 PDF（扫描版/损坏/加密）pypdf 解析出空文本，
        # 若继续入库会得到无意义 chunk，导致检索命中率骤降且不自知。
        # 这里主动抛异常，被下方 except 捕获 → 状态置 failed，前端可见。
        if not docs or all(not (d.page_content or "").strip() for d in docs):
            raise ValueError("文档解析为空文本（可能是扫描版 PDF 或文件损坏）")

        # 第2步：切分（chunk_size, overlap 由 config 配置）
        chunks = split_documents(docs)
        print(f"[解析] {filename} 切分={len(chunks)}块", flush=True)

        # 第3步：读取文档所属企业和知识库，用于企业级 Key 路由与向量隔离。
        # kb_id 不能写死为 1：必须从数据库读该文档所属的知识库 id，
        # 否则多知识库时，文档会全部进默认库，别的库检索不到。
        with SessionLocal() as db:
            doc = db.get(Document, doc_id)
            kb_id = doc.kb_id if doc else 1
            enterprise_id = enterprise_id if enterprise_id is not None else (doc.enterprise_id if doc else None)
            embed_config = (
                enterprise_service.require_enterprise_siliconflow_key(db, int(enterprise_id))
                if enterprise_id is not None
                else None
            )

        # 第4步：批量向量化（取出每个 chunk 的正文文本）
        texts = [c.page_content for c in chunks]
        vectors = embed_documents(texts, model_config=embed_config)
        print(f"[解析] {filename} 向量化={len(vectors)}", flush=True)

        # 第5步：写入 Milvus。
        milvus_store.add_chunks(
            chunks=chunks,
            vectors=vectors,
            doc_id=doc_id,
            kb_id=kb_id,
            source=filename,
            enterprise_id=enterprise_id,
        )

        # 第6步：更新文档状态 + 记录切分块数
        _update_doc_status(doc_id, status="done", chunk_count=len(chunks), failure_reason=None)
    except Exception as exc:
        # 解析失败：状态置为 failed，方便前端展示错误
        print(f"[解析失败] doc_id={doc_id} filename={filename}: {exc}", flush=True)
        traceback.print_exc()
        _safe_update_doc_status(doc_id, status="failed", failure_reason=_format_failure_reason(exc))


def _format_failure_reason(exc: Exception) -> str:
    """把底层异常转成管理员能看懂的失败原因。"""
    raw = str(exc).strip() or exc.__class__.__name__
    lowered = raw.lower()
    if "pdf 解析失败" in raw:
        reason = raw
    elif "no module named 'pil'" in lowered:
        reason = "PDF 解析失败：后端 Python 环境缺少 Pillow，请执行 pip install -r requirements.txt 后重启后端。"
    elif "tesseract" in lowered or "ocr" in lowered:
        reason = "PDF 解析失败：OCR 依赖不可用，请检查 tesseract-ocr 和中文语言包 chi_sim。"
    elif "文档解析为空文本" in raw:
        reason = "文档解析为空文本：可能是扫描版 PDF、加密 PDF、图片质量过低，或文件内容无法提取。"
    elif "embedding" in lowered or "embed" in lowered or "siliconflow" in lowered:
        reason = "嵌入模型调用失败：请检查硅基流动 API Key、余额、网络或模型服务状态。"
    elif "嵌入模型 api key" in lowered:
        reason = raw
    elif "milvus" in lowered or "zilliz" in lowered or "vector" in lowered:
        reason = "向量库写入失败：请检查 Zilliz Cloud Endpoint、Token、Collection 和网络连接。"
    elif "timeout" in lowered or "timed out" in lowered:
        reason = "处理超时：文件可能较大或外部模型/向量库响应较慢，请稍后重试或拆分文件。"
    else:
        reason = raw
    return reason[:1000]


def _update_doc_status(
    doc_id: int,
    status: str,
    chunk_count: int = 0,
    failure_reason: str | None = None,
) -> None:
    """更新文档状态（独立小函数，供后台任务调用）"""
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc:
            doc.status = status
            doc.chunk_count = chunk_count
            doc.failure_reason = failure_reason
            db.commit()


def _safe_update_doc_status(
    doc_id: int,
    status: str,
    chunk_count: int = 0,
    failure_reason: str | None = None,
) -> None:
    """失败路径兜底更新状态，避免状态更新异常掩盖真正的解析/入库错误。"""
    try:
        _update_doc_status(
            doc_id,
            status=status,
            chunk_count=chunk_count,
            failure_reason=failure_reason,
        )
    except Exception as exc:
        print(f"[状态更新失败] doc_id={doc_id} status={status}: {exc}", flush=True)
        traceback.print_exc()

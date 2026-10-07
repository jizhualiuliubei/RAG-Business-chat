"""
知识库重建脚本
================
作用：RAG 检索改造后（整章切分 + 标题拼进 text），把 MySQL 里全部文档
用新逻辑重新切分 + 向量化 + 入库，替换 Milvus 里的旧 chunk。

用法（必须在 backend 目录下运行，保证 app 可导入）：
    python scripts/rebuild_kb.py

为什么不用 BackgroundTasks：process_document 走 FastAPI 上传流程（需 UploadFile），
这里是离线重建，直接复用 loader → splitter → embeddings → milvus_store 全链路。

注意：
- 只遍历数据库 document 表的记录，不处理 uploads 里的孤儿文件。
- 每个文档先删 Milvus 旧 chunk 再写入（幂等，重复执行安全）。
- 解析为空文本的文档（例如无文本层的扫描版 PDF）置 failed，不写入向量。
"""
import sys
import time
from pathlib import Path

# 让 app 包可导入（脚本放在 backend/scripts/ 下，backend 是项目根）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import UPLOAD_DIR
from app.core import milvus_store
from app.core.embeddings import embed_documents
from app.core.loader import load_document
from app.core.splitter import split_documents
from app.database import SessionLocal
from app.models.document import Document


def _update_doc_status(doc_id: int, status: str, chunk_count: int = 0) -> None:
    """更新文档状态（独立小函数，与 document_service 保持一致）。"""
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc:
            doc.status = status
            doc.chunk_count = chunk_count
            db.commit()


def rebuild_one(doc: Document) -> int:
    """重建单个文档，返回写入的 chunk 数（0 表示解析为空/失败）。"""
    ext = Path(doc.filename).suffix.lower()
    stored_path = UPLOAD_DIR / f"doc_{doc.id}{ext}"
    if not stored_path.exists():
        print(f"  [SKIP] {doc.id} 文件缺失: {stored_path}")
        _update_doc_status(doc.id, "failed")
        return 0

    # 1. 加载（loader 已内置 BOM 清洗）
    docs = load_document(str(stored_path), doc.filename)
    if not docs or all(not (d.page_content or "").strip() for d in docs):
        print(f"  [FAIL] {doc.id} 解析为空文本（扫描版 PDF 或文件损坏）")
        _update_doc_status(doc.id, "failed")
        return 0

    # 2. 整章切分（标题拼进 text）
    chunks = split_documents(docs)
    if not chunks:
        _update_doc_status(doc.id, "failed")
        return 0

    # 3. 向量化（硅基流动 BGE-M3）
    vectors = embed_documents([c.page_content for c in chunks])

    # 4. 删旧 chunk + 写入（幂等）
    milvus_store.delete_by_doc_id(doc.id)
    milvus_store.add_chunks(
        chunks=chunks,
        vectors=vectors,
        doc_id=doc.id,
        kb_id=doc.kb_id,
        source=doc.filename,
    )

    _update_doc_status(doc.id, "done", chunk_count=len(chunks))
    return len(chunks)


def main():
    with SessionLocal() as db:
        docs = db.query(Document).order_by(Document.id.asc()).all()
    print(f"待重建文档 {len(docs)} 条")

    total_chunks = 0
    ok = fail = 0
    for i, doc in enumerate(docs, 1):
        name = doc.filename
        print(f"[{i}/{len(docs)}] {doc.id} kb{doc.kb_id} {name}")
        try:
            n = rebuild_one(doc)
            if n > 0:
                total_chunks += n
                ok += 1
            else:
                fail += 1
            print(f"     -> {n} chunks")
        except Exception as exc:
            print(f"     -> [ERROR] {type(exc).__name__}: {exc}")
            fail += 1
            _update_doc_status(doc.id, "failed")
        time.sleep(0.2)  # 给嵌入 API 留一点间隔，防限流

    print(f"\n重建完成: 成功 {ok} / 失败 {fail}，共 {total_chunks} chunks")

    # 核对 Milvus 里的实际 chunk 数
    try:
        cnt = milvus_store.get_client().query(
            collection_name=milvus_store.MILVUS_COLLECTION,
            filter="",
            output_fields=["count(*)"],
        )
        print(f"Milvus 当前总 chunk 数: {cnt}")
    except Exception as exc:
        print(f"核对 chunk 数失败: {exc}")


if __name__ == "__main__":
    main()

"""
文档管理路由
================
对应知识点：FastAPI 路由 + BackgroundTasks（后台任务）
- 路由层只做"接参数 + 调 service + 转 HTTP 错误"，不写业务逻辑
- BackgroundTasks：注册一个"响应返回后才执行"的函数，
  用来做文档解析这类耗时操作，避免用户一直等响应
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import current_enterprise_id, require_admin
from app.database import get_db
from app.schemas import document
from app.services import audit_service, document_service

# prefix：所有接口统一挂在 /api/documents 下；tags：Swagger 文档里的分组名
router = APIRouter(
    prefix="/api/documents",
    tags=["文档管理"],
    dependencies=[Depends(require_admin)],
)


class BatchDeleteRequest(BaseModel):
    document_ids: list[int]


@router.post("/upload", response_model=document.UploadOut)
def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    request: Request,
    # 目标知识库，由前端传入；缺省用默认知识库（id=1）
    kb_id: int = 1,
    db: Session = Depends(get_db),
):
    """上传文档：先落盘 + 建记录，然后后台异步解析入库"""
    try:
        enterprise_id = current_enterprise_id(request)
        result = document_service.create_document(db, file, kb_id, enterprise_id=enterprise_id)
    except ValueError as exc:
        # 不支持的文件类型等业务错误 -> 400
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="upload_document",
        target_type="document",
        target_name=file.filename,
        enterprise_id=enterprise_id,
    )

    # 注册后台任务：响应返回后执行"解析文档"
    # 注意参数在这里就取出来传入，后台执行时 UploadFile 对象可能已关闭
    background_tasks.add_task(
        document_service.process_document_with_limit,
        result["doc_id"],
        str(result["stored_path"]),
        file.filename,
        enterprise_id,
    )

    return {
        "id": result["doc_id"],
        "filename": file.filename,
        "status": "processing",
        "message": "上传成功，后台正在解析入库",
    }


@router.get("", response_model=list[document.DocumentOut])
def list_documents(request: Request, kb_id: int = 1, db: Session = Depends(get_db)):
    """文档列表：文件名 / 大小 / 状态 / 时间"""
    return document_service.list_documents(db, kb_id, current_enterprise_id(request))


@router.get("/{doc_id}/status", response_model=document.DocumentOut)
def get_document_status(doc_id: int, request: Request, db: Session = Depends(get_db)):
    """查询单个文档的解析状态"""
    doc = document_service.get_document(db, doc_id, current_enterprise_id(request))
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.get("/{doc_id}/chunks")
def get_document_chunks(doc_id: int, request: Request, db: Session = Depends(get_db)):
    """查询某文档的全部 chunk（概览页文档下钻）。

    从 Milvus 按 doc_id 拉该文档的向量片段，返回每个 chunk 的编号 +
    所属章节标题 + 正文预览。
    """
    enterprise_id = current_enterprise_id(request)
    doc = document_service.get_document(db, doc_id, enterprise_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    from app.core import milvus_store

    chunks = milvus_store.list_chunks_by_doc(doc_id, enterprise_id=enterprise_id)
    result = []
    for c in chunks:
        text = c["text"]
        # 整章切分时第一行是"文档名 章节标题"，作为所属章节
        first_line = text.split("\n", 1)[0].strip() if text else ""
        # 正文预览：去掉首行标题后取前 60 字
        body = text.split("\n", 1)[1].strip() if "\n" in text else text
        preview = body[:60] if body else text[:60]
        result.append({
            "chunk_id": c["chunk_id"],
            "section_title": first_line,
            "text_preview": preview,
            "full_text": text,
        })
    return result


@router.delete("/batch")
def delete_documents_batch(body: BatchDeleteRequest, request: Request, db: Session = Depends(get_db)):
    """批量删除文档，减少云向量库删除请求次数。"""
    enterprise_id = current_enterprise_id(request)
    try:
        deleted_count = document_service.delete_documents(db, body.document_ids, enterprise_id)
    except document_service.VectorCleanupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="delete_document",
        target_type="document",
        target_name=f"批量删除 {deleted_count} 个文档",
        enterprise_id=enterprise_id,
    )
    return {"message": "删除成功", "deleted_count": deleted_count}


@router.delete("/{doc_id}")
def delete_document(doc_id: int, request: Request, db: Session = Depends(get_db)):
    """删除文档（同步删除数据库记录、落盘文件与向量；向量清理失败时返回 409）"""
    enterprise_id = current_enterprise_id(request)
    target = document_service.get_document(db, doc_id, enterprise_id)
    target_name = target.filename if target else str(doc_id)
    try:
        deleted = document_service.delete_document(db, doc_id, enterprise_id)
    except document_service.VectorCleanupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="文档不存在")
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="delete_document",
        target_type="document",
        target_name=target_name,
        enterprise_id=enterprise_id,
    )
    return {"message": "删除成功"}

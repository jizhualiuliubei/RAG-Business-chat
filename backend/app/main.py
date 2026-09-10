"""
FastAPI 应用入口
================
作用：
1. 创建 FastAPI 应用实例
2. 配置 CORS（跨域资源共享），让前端（Vue，5173 端口）能访问后端（8000 端口）
3. 应用启动时：自动建表 + 初始化"默认知识库"
4. 提供健康检查接口

对应知识点：FastAPI 基础 + lifespan 生命周期
- FastAPI() 创建应用
- @app.get("/路径") 声明一个 GET 接口
- lifespan 是"应用启动/关闭时执行一段逻辑"的钩子
"""
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import models  # 必须导入：触发 models/__init__.py，把 4 个模型注册进去
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.enterprise import router as enterprise_router
from app.api.evaluation_runs import router as evaluation_runs_router
from app.api.evaluations import router as evaluations_router
from app.api.knowledge_bases import router as knowledge_bases_router
from app.api.qa import router as qa_router
from app.api.stats import router as stats_router
from app.api.system import router as system_router
from app.core import milvus_store
from app.database import (
    Base,
    SessionLocal,
    engine,
    ensure_document_failure_reason_column,
    ensure_attachment_rag_columns,
    ensure_evaluation_trust_columns,
    ensure_saas_columns,
)
from app.models.knowledge_base import KnowledgeBase
from app.services import auth_service, enterprise_service, evaluation_run_service
from app.services.enterprise_evaluation_dataset import ENTERPRISE_KB_NAME


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建表 + 初始化默认知识库 + 确保 Milvus 集合存在"""
    # create_all：根据所有"已注册模型"创建对应的表（表已存在则自动跳过）
    Base.metadata.create_all(bind=engine)
    ensure_document_failure_reason_column()
    ensure_saas_columns()
    ensure_attachment_rag_columns()
    ensure_evaluation_trust_columns()
    _migrate_legacy_rows_to_system()
    _init_default_kb()
    _init_enterprise_eval_kb()
    _init_default_users()
    _migrate_legacy_conversations_to_enterprise_admin()
    _mark_stale_evaluation_runs_interrupted()
    # 启动时尽量预热向量库；若本地网络或 Zilliz 暂时不可用，不能阻塞
    # 健康检查、登录和后台页面。真正上传/检索时仍会在操作入口自愈连接。
    _warm_up_vector_store()
    yield  # yield 之前的代码在"启动时"执行；之后的代码在"关闭时"执行


def _warm_up_vector_store(timeout_seconds: int = 5) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        executor.submit(milvus_store.ensure_collection).result(timeout=timeout_seconds)
    except TimeoutError:
        print(f"[启动警告] 向量库预热超过 {timeout_seconds}s，后续RAG操作会再次尝试连接")
    except Exception as exc:
        print(f"[启动警告] 向量库预热失败，后续RAG操作会再次尝试连接: {exc}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _init_default_users():
    """初始化企业内部登录账号。"""
    with SessionLocal() as db:
        auth_service.init_default_users(db)


def _migrate_legacy_rows_to_system():
    """旧单租户数据归属到系统平台资产。"""
    with SessionLocal() as db:
        enterprise_service.migrate_legacy_rows_to_system(db)


def _migrate_legacy_conversations_to_enterprise_admin():
    """旧会话归属到所属企业管理员，避免企业内账号共享历史。"""
    with SessionLocal() as db:
        enterprise_service.migrate_legacy_conversations_to_enterprise_admin(db)


def _mark_stale_evaluation_runs_interrupted():
    """服务重启后清理遗留运行态评测，避免前端一直显示运行中。"""
    with SessionLocal() as db:
        count = evaluation_run_service.mark_stale_running_runs_interrupted(db)
        if count:
            print(f"[启动修复] 已将 {count} 个遗留评测任务标记为已中断，请重新评测")


def _init_default_kb():
    """初始化"默认知识库"。
    P0 阶段只有一个知识库，所有文档都挂在它下面；
    单独建表并预置一条记录，是为了 P1 支持多知识库时不用改表结构。
    """
    with SessionLocal() as db:
        system_enterprise = enterprise_service.ensure_system_enterprise(db)
        # 用 select 查询（SQLAlchemy 2.0 推荐写法，替代旧式 db.query）
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.enterprise_id == system_enterprise.id,
            KnowledgeBase.name == "默认知识库",
        )
        exists = db.execute(stmt).scalar_one_or_none()
        if exists is None:  # 不存在才创建，避免每次启动重复插入
            db.add(KnowledgeBase(enterprise_id=system_enterprise.id, name="默认知识库"))
            db.commit()


def _init_enterprise_eval_kb():
    """初始化独立的企业规模评测库，不影响原有短文档演示库。"""
    with SessionLocal() as db:
        system_enterprise = enterprise_service.ensure_system_enterprise(db)
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.enterprise_id == system_enterprise.id,
            KnowledgeBase.name == ENTERPRISE_KB_NAME,
        )
        exists = db.execute(stmt).scalar_one_or_none()
        if exists is None:
            db.add(KnowledgeBase(enterprise_id=system_enterprise.id, name=ENTERPRISE_KB_NAME))
            db.commit()


# 创建 FastAPI 应用，并传入生命周期钩子
app = FastAPI(title="企业知识库问答系统", lifespan=lifespan)

# CORS 中间件：解决"浏览器同源策略"导致的跨域问题
# 前端（localhost:5173）请求后端（localhost:8000）属于跨域，必须放开
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # 开发阶段放开所有来源；上线时应改成具体前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/captcha",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/enterprise-register",
}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """统一保护业务 API，公开健康检查和登录相关接口。"""
    path = request.url.path.rstrip("/") or request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api") or path in PUBLIC_API_PATHS:
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return JSONResponse(status_code=401, content={"detail": "请先登录"})

    user = auth_service.decode_access_token(token)
    if user is None:
        return JSONResponse(status_code=401, content={"detail": "登录已过期，请重新登录"})
    request.state.auth_user = user
    return await call_next(request)

# 注册业务路由：把所有 /api/... 接口挂到应用上
app.include_router(auth_router)
app.include_router(system_router)
app.include_router(enterprise_router)
app.include_router(admin_router)
app.include_router(documents_router)
app.include_router(qa_router)
app.include_router(conversations_router)
app.include_router(knowledge_bases_router)
app.include_router(stats_router)
app.include_router(evaluations_router)
app.include_router(evaluation_runs_router)


@app.get("/api/health")
def health():
    """健康检查接口：返回一个固定 JSON，用于确认服务已正常启动"""
    return {"status": "ok", "message": "企业知识库问答系统运行中"}

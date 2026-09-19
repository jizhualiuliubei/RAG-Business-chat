"""
数据库模块
================
作用：创建 SQLAlchemy 的"引擎"和"会话工厂"，并定义所有 ORM 模型的公共基类。

对应知识点：SQLAlchemy 2.0 声明式 ORM
- engine（引擎）：负责真正连接数据库
- sessionmaker（会话工厂）：每次请求用它生成一个独立的"会话"，用完自动关闭
- DeclarativeBase：所有模型类都继承它，从而被 SQLAlchemy 识别为"一张表"
"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_SIZE, DB_POOL_TIMEOUT

# 数据库引擎：根据连接串判断 SQLite / MySQL，使用不同的连接参数
if DATABASE_URL.startswith("sqlite"):
    # check_same_thread=False 是关键坑点：
    # FastAPI 是多线程的，不同请求可能在不同线程里访问 SQLite。
    # SQLite 默认禁止同一个连接被多个线程使用，必须关掉这个限制，
    # 否则运行时会报 "SQLite objects created in a thread..." 之类的错误。
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    # MySQL：连接池 + utf8mb4（已含在 URL charset）
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,       # 取连接前先 ping，避免 MySQL 空闲断连
        pool_recycle=3600,        # 连接 1 小时回收
        pool_size=DB_POOL_SIZE,   # 连接池大小
        max_overflow=DB_MAX_OVERFLOW,  # 峰值额外连接
        pool_timeout=DB_POOL_TIMEOUT,  # 等待连接的最长秒数
    )

# 会话工厂：之后业务代码里用 with SessionLocal() as db: 来拿一个会话
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# 所有 ORM 模型的公共基类
class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：为每个请求提供一个数据库会话。

    用法：路由函数参数里写 db: Session = Depends(get_db)，
    FastAPI 会帮你创建会话，并在请求结束后自动执行 finally 里的关闭逻辑。
    这样能保证"每个请求一个会话、用完必关"，不会泄漏数据库连接。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_document_failure_reason_column() -> None:
    """兼容旧线上数据库：为 document 表补充失败原因字段。

    Base.metadata.create_all() 不会修改已存在的表；线上 MySQL 已经有 document
    表时，需要在应用启动时做一次幂等轻量迁移，避免手动 ALTER TABLE。
    """
    inspector = inspect(engine)
    if "document" not in inspector.get_table_names():
        return
    column_names = {column["name"] for column in inspector.get_columns("document")}
    with engine.begin() as conn:
        if "failure_reason" not in column_names:
            conn.execute(text("ALTER TABLE document ADD COLUMN failure_reason VARCHAR(1000) NULL"))
        if "parser_name" not in column_names:
            conn.execute(text("ALTER TABLE document ADD COLUMN parser_name VARCHAR(80) NOT NULL DEFAULT ''"))
        if "parser_version" not in column_names:
            conn.execute(text("ALTER TABLE document ADD COLUMN parser_version VARCHAR(80) NOT NULL DEFAULT ''"))
        if "parsed_chars" not in column_names:
            conn.execute(text("ALTER TABLE document ADD COLUMN parsed_chars INTEGER NOT NULL DEFAULT 0"))
        conn.execute(text(
            """
            UPDATE document
            SET parser_name = CASE
                WHEN LOWER(filename) LIKE '%.docx' THEN 'python-docx'
                WHEN LOWER(filename) LIKE '%.pdf' THEN 'pypdf+ocr'
                WHEN LOWER(filename) LIKE '%.xlsx' THEN 'openpyxl'
                WHEN LOWER(filename) LIKE '%.xls' THEN 'openpyxl'
                WHEN LOWER(filename) LIKE '%.csv' THEN 'csv-loader'
                WHEN LOWER(filename) LIKE '%.txt' THEN 'txt-loader'
                ELSE 'legacy-loader'
            END
            WHERE status = 'done'
              AND (parser_name IS NULL OR TRIM(parser_name) = '')
            """
        ))


def ensure_saas_columns() -> None:
    """兼容旧数据库：补充多企业 SaaS 归属字段。

    这是轻量、幂等的启动迁移，避免线上已有表需要手动逐个 ALTER。
    复杂索引变更后续可用正式迁移工具接管。
    """
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    enterprise_tables = {
        "users": "INTEGER NULL",
        "knowledge_base": "INTEGER NULL",
        "document": "INTEGER NULL",
        "conversation": "INTEGER NULL",
        "message": "INTEGER NULL",
        "conversation_attachment": "INTEGER NULL",
        "evaluation_run": "INTEGER NULL",
        "evaluation_dataset": "INTEGER NULL",
        "evaluation_dataset_case": "INTEGER NULL",
        "evaluation_case_result": "INTEGER NULL",
        "audit_logs": "INTEGER NULL",
    }
    with engine.begin() as conn:
        for table, sql_type in enterprise_tables.items():
            if table not in table_names:
                continue
            column_names = {column["name"] for column in inspector.get_columns(table)}
            if "enterprise_id" not in column_names:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN enterprise_id {sql_type}"))
            if table in {"conversation", "message", "conversation_attachment"} and "user_id" not in column_names:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NULL"))


def ensure_evaluation_trust_columns() -> None:
    """兼容旧数据库：补充 AI 辅助题集的审核与证据快照字段。"""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    additions = {
        "evaluation_dataset": {
            "reviewed_by": "VARCHAR(100) NOT NULL DEFAULT ''",
            "reviewed_at": "DATETIME NULL",
            "review_notes": "TEXT NULL",
            "generation_policy": "TEXT NULL",
        },
        "evaluation_dataset_case": {
            "evidence_text": "TEXT NULL",
            "evidence_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "source_chunk_id": "INTEGER NOT NULL DEFAULT 0",
            "generation_confidence": "INTEGER NOT NULL DEFAULT 0",
            "review_status": "VARCHAR(30) NOT NULL DEFAULT 'pending_review'",
        },
        "evaluation_case_result": {
            "ragas_scores_json": "TEXT NULL",
            "ragas_passed": "INTEGER NOT NULL DEFAULT 0",
            "ragas_skip_reason": "VARCHAR(255) NOT NULL DEFAULT ''",
            "ragas_warning_reason": "VARCHAR(255) NOT NULL DEFAULT ''",
            "needs_review": "INTEGER NOT NULL DEFAULT 0",
            # 补召回之前的真实检索表现；旧数据留 NULL，不要回填成 0。
            "retrieval_top1_hit": "INTEGER NULL",
            "retrieval_top3_hit": "INTEGER NULL",
            "retrieval_top5_hit": "INTEGER NULL",
            "retrieval_section_hit": "INTEGER NULL",
            # 端到端判定（不补召回、不做过度拒答修复）= 用户真实路径拿到什么；
            # 旧数据留 NULL，前端显示"未统计"，不要回填成 0。
            "e2e_passed": "INTEGER NULL",
            "e2e_answer_coverage": "INTEGER NULL",
            "e2e_refusal_passed": "INTEGER NULL",
            "e2e_failure_reason": "VARCHAR(255) NULL",
        },
    }
    with engine.begin() as conn:
        for table, columns in additions.items():
            if table not in table_names:
                continue
            column_names = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in column_names:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def ensure_attachment_rag_columns() -> None:
    """兼容旧数据库：补充会话附件 RAG 所需元数据字段。"""
    inspector = inspect(engine)
    if "conversation_attachment" not in inspector.get_table_names():
        return
    additions = {
        "status": "VARCHAR(20) NOT NULL DEFAULT 'done'",
        "failure_reason": "VARCHAR(1000) NOT NULL DEFAULT ''",
        "summary": "TEXT NULL",
        "chunk_count": "INTEGER NOT NULL DEFAULT 0",
        "content_chars": "INTEGER NOT NULL DEFAULT 0",
        "file_size": "INTEGER NOT NULL DEFAULT 0",
    }
    with engine.begin() as conn:
        column_names = {column["name"] for column in inspector.get_columns("conversation_attachment")}
        for name, sql_type in additions.items():
            if name not in column_names:
                conn.execute(text(f"ALTER TABLE conversation_attachment ADD COLUMN {name} {sql_type}"))

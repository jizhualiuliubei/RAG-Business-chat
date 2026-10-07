"""
数据库模块
================
作用：创建 SQLAlchemy 的"引擎"和"会话工厂"，并定义所有 ORM 模型的公共基类。

对应知识点：SQLAlchemy 2.0 声明式 ORM
- engine（引擎）：负责真正连接数据库
- sessionmaker（会话工厂）：每次请求用它生成一个独立的"会话"，用完自动关闭
- DeclarativeBase：所有模型类都继承它，从而被 SQLAlchemy 识别为"一张表"
"""
from sqlalchemy import Index, MetaData, Table, create_engine, inspect, text
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


def ensure_user_tenant_unique_index() -> None:
    """旧库用户名全局唯一改为企业内唯一，不修改用户数据。"""
    with engine.begin() as conn:
        inspector = inspect(conn)
        if "users" not in inspector.get_table_names():
            return
        indexes = inspector.get_indexes("users", **({"include_auto_indexes": True} if conn.dialect.name == "sqlite" else {}))
        constraints = inspector.get_unique_constraints("users")
        unique = [item for item in indexes if item.get("unique")] + constraints
        if conn.dialect.name == "sqlite" and any(
            item["column_names"] == ["username"] and (
                not item.get("name") or item["name"].startswith("sqlite_autoindex")
                or item in constraints
            ) for item in unique
        ):
            # SQLite 内联 UNIQUE 不能单独删除；为改索引重建带外键的用户表风险太大。
            # 这里只告警、不抛异常：本函数在 lifespan 里执行，抛出去会让整个应用
            # （含问答、知识库、评测）都起不来 —— 代价远大于「跨企业同名用户」这一项功能。
            # 需要该功能时先备份数据库再手动迁移；MySQL 部署不走这个分支。
            print(
                "[启动警告] users 表含 SQLite 内联用户名唯一约束，已跳过「企业内唯一」索引迁移；"
                "该库上跨企业同名用户不可用",
                flush=True,
            )
            return
        table = Table("users", MetaData(), autoload_with=conn, resolve_fks=False)
        if not any(set(item["column_names"]) == {"enterprise_id", "username"} for item in unique):
            # 先建立企业内约束再删除旧索引，失败时不放松原来的唯一性保护。
            Index("uq_users_enterprise_username", table.c.enterprise_id, table.c.username, unique=True).create(conn)
        legacy_names = {
            item["name"] for item in unique
            if item["column_names"] == ["username"] and item.get("name")
        }
        for name in sorted(legacy_names):
            Index(name, table.c.username, unique=True).drop(conn)


def ensure_knowledge_base_tenant_unique_index() -> None:
    """将旧库知识库名称的全局唯一索引迁移为企业内唯一，不修改数据。"""
    with engine.begin() as conn:
        inspector = inspect(conn)
        if "knowledge_base" not in inspector.get_table_names():
            return
        options = {"include_auto_indexes": True} if conn.dialect.name == "sqlite" else {}
        indexes = inspector.get_indexes("knowledge_base", **options)
        constraints = inspector.get_unique_constraints("knowledge_base")
        unique = [item for item in indexes if item.get("unique")] + constraints
        legacy = [item for item in unique if item["column_names"] == ["name"]]
        if conn.dialect.name == "sqlite" and any(
            not item.get("name") or item["name"].startswith("sqlite_autoindex")
            or item in constraints for item in legacy
        ):
            # Avoid rebuilding a legacy SQLite table referenced by documents.
            print(
                "[启动警告] knowledge_base 表含 SQLite 内联名称唯一约束，已跳过企业内唯一索引迁移；"
                "跨企业同名知识库需备份后手动迁移",
                flush=True,
            )
            return
        table = Table("knowledge_base", MetaData(), autoload_with=conn, resolve_fks=False)
        if not any(set(item["column_names"]) == {"enterprise_id", "name"} for item in unique):
            # Build the replacement first so a failed migration keeps the old guard.
            Index("uq_kb_enterprise_name", table.c.enterprise_id, table.c.name, unique=True).create(conn)
        for name in sorted({item["name"] for item in legacy if item.get("name")}):
            Index(name, table.c.name, unique=True).drop(conn)


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


def ensure_trial_column() -> None:
    """兼容旧数据库：给企业表补「体验企业」标记。

    体验企业 = 免审核注册、注册即可用，但功能受限（不能建子员工、不能用评测），
    需系统管理员「转为正式」才解除。旧库没有这一列，补一个默认 false。
    """
    inspector = inspect(engine)
    if "enterprise" not in inspector.get_table_names():
        return
    with engine.begin() as conn:
        column_names = {column["name"] for column in inspector.get_columns("enterprise")}
        if "is_trial" not in column_names:
            conn.execute(text("ALTER TABLE enterprise ADD COLUMN is_trial BOOLEAN NOT NULL DEFAULT 0"))


def ensure_agent_risk_policy_columns() -> None:
    """兼容旧数据库：补齐 Agent 企业级确定性风险策略字段。"""
    inspector = inspect(engine)
    if "agent_risk_policy" not in inspector.get_table_names():
        return
    additions = {
        "procurement_requires_quotation": "BOOLEAN NOT NULL DEFAULT 1",
        "procurement_sensitive_data_requires_approval": "BOOLEAN NOT NULL DEFAULT 1",
        "access_high_risk_classification": "VARCHAR(20) NOT NULL DEFAULT 'secret'",
        "access_max_duration_days": "INTEGER NOT NULL DEFAULT 30",
    }
    column_names = {
        column["name"] for column in inspector.get_columns("agent_risk_policy")
    }
    with engine.begin() as conn:
        for name, sql_type in additions.items():
            if name not in column_names:
                conn.execute(
                    text(f"ALTER TABLE agent_risk_policy ADD COLUMN {name} {sql_type}")
                )


def ensure_agent_task_columns() -> None:
    """幂等补齐任务风险快照及申请人撤回、逻辑删除时间。"""
    inspector = inspect(engine)
    if "agent_task" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("agent_task")}
    if "risk_json" not in columns:
        with engine.begin() as conn:
            # MySQL older versions do not support a default value on TEXT.
            conn.execute(text("ALTER TABLE agent_task ADD COLUMN risk_json TEXT NULL"))
            conn.execute(text("UPDATE agent_task SET risk_json = '{}' WHERE risk_json IS NULL"))
    for column in ("withdrawn_at", "deleted_at"):
        if column not in columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE agent_task ADD COLUMN {column} DATETIME NULL"))

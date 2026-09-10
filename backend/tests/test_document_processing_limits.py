import inspect
import threading
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.document import Document
from app.api import documents as documents_api
from app.services import document_service


def test_document_background_task_uses_limited_processor():
    source = inspect.getsource(documents_api.upload_document)

    assert "process_document_with_limit" in source
    assert "background_tasks.add_task" in source


def test_document_processing_wrapper_serializes_background_tasks(monkeypatch):
    monkeypatch.setattr(
        document_service,
        "_PROCESS_SEMAPHORE",
        threading.BoundedSemaphore(1),
        raising=False,
    )

    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_process_document(doc_id: int, stored_path: str, filename: str) -> None:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1

    monkeypatch.setattr(document_service, "process_document", fake_process_document)

    threads = [
        threading.Thread(
            target=document_service.process_document_with_limit,
            args=(idx, f"doc_{idx}.txt", f"doc_{idx}.txt"),
        )
        for idx in range(4)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert max_active == 1


def test_database_pool_settings_are_environment_driven():
    backend_root = Path(__file__).resolve().parents[1]
    config_source = (backend_root / "app" / "config.py").read_text(encoding="utf-8")
    database_source = (backend_root / "app" / "database.py").read_text(encoding="utf-8")

    assert "DOCUMENT_PROCESS_CONCURRENCY" in config_source
    assert "DB_POOL_SIZE" in config_source
    assert "DB_MAX_OVERFLOW" in config_source
    assert "DB_POOL_TIMEOUT" in config_source
    assert "pool_size=DB_POOL_SIZE" in database_source
    assert "max_overflow=DB_MAX_OVERFLOW" in database_source
    assert "pool_timeout=DB_POOL_TIMEOUT" in database_source


def test_document_failures_print_traceback_and_preserve_failure_status():
    source = inspect.getsource(document_service.process_document)

    assert "traceback.print_exc()" in source
    assert "_safe_update_doc_status" in source


def test_document_failure_reason_is_persisted(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with TestingSessionLocal() as db:
        db.add(Document(kb_id=1, filename="bad.pdf", file_size=1, status="processing"))
        db.commit()

    monkeypatch.setattr(document_service, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        document_service,
        "load_document",
        lambda stored_path, filename: (_ for _ in ()).throw(ValueError("PDF 解析失败：测试错误")),
    )

    document_service.process_document(1, "bad.pdf", "bad.pdf")

    with TestingSessionLocal() as db:
        doc = db.get(Document, 1)
        assert doc.status == "failed"
        assert doc.failure_reason == "PDF 解析失败：测试错误"

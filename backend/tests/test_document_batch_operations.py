from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services import auth_service, knowledge_base_service


def _make_client(monkeypatch):
    import app.main as main
    from app.core import milvus_store

    monkeypatch.setattr(main.milvus_store, "ensure_collection", lambda: None)
    deleted_doc_ids = []
    monkeypatch.setattr(milvus_store, "delete_by_doc_ids", lambda ids: deleted_doc_ids.extend(ids))
    monkeypatch.setattr(milvus_store, "delete_by_doc_id", lambda doc_id: deleted_doc_ids.append(doc_id))

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with TestingSessionLocal() as db:
        auth_service.init_default_users(db)
        db.add(KnowledgeBase(name="默认知识库"))
        db.flush()
        db.add_all([
            Document(kb_id=1, filename="a.txt", file_size=1, status="done", chunk_count=1),
            Document(kb_id=1, filename="b.txt", file_size=1, status="processing", chunk_count=0),
            Document(kb_id=1, filename="c.txt", file_size=1, status="failed", chunk_count=0),
        ])
        db.commit()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSessionLocal, deleted_doc_ids


def _captcha_payload(client, answer="3"):
    captcha = client.get("/api/auth/captcha").json()
    auth_service._CAPTCHA_STORE[captcha["captcha_id"]]["answer"] = answer
    return {"captcha_id": captcha["captcha_id"], "captcha_answer": answer}


def _login_token(client, username="admin", password="admin123"):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password, **_captcha_payload(client)},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_admin_can_batch_delete_documents_and_vectors_once(monkeypatch):
    client, SessionLocal, deleted_doc_ids = _make_client(monkeypatch)
    try:
        token = _login_token(client)
        response = client.request(
            "DELETE",
            "/api/documents/batch",
            headers={"Authorization": f"Bearer {token}"},
            json={"document_ids": [1, 2]},
        )

        assert response.status_code == 200
        assert response.json()["deleted_count"] == 2
        assert deleted_doc_ids == [1, 2]
        with SessionLocal() as db:
            filenames = [doc.filename for doc in db.execute(select(Document)).scalars().all()]
            assert filenames == ["c.txt"]
    finally:
        app.dependency_overrides.clear()


def test_batch_delete_returns_when_vector_store_is_slow(monkeypatch):
    import time
    from app.services import document_service

    client, SessionLocal, _ = _make_client(monkeypatch)

    def slow_delete(_ids, **_kwargs):
        time.sleep(1)

    monkeypatch.setattr(document_service.milvus_store, "delete_by_doc_ids", slow_delete)
    monkeypatch.setattr(document_service, "VECTOR_DELETE_TIMEOUT_SECONDS", 0.01)
    try:
        token = _login_token(client)
        start = time.perf_counter()
        response = client.request(
            "DELETE",
            "/api/documents/batch",
            headers={"Authorization": f"Bearer {token}"},
            json={"document_ids": [1, 2]},
        )
        elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert response.json()["deleted_count"] == 2
        assert elapsed < 0.5
        with SessionLocal() as db:
            filenames = [doc.filename for doc in db.execute(select(Document)).scalars().all()]
            assert filenames == ["c.txt"]
    finally:
        app.dependency_overrides.clear()


def test_employee_cannot_batch_delete_documents(monkeypatch):
    client, _, _ = _make_client(monkeypatch)
    try:
        token = _login_token(client, "employee", "employee123")
        response = client.request(
            "DELETE",
            "/api/documents/batch",
            headers={"Authorization": f"Bearer {token}"},
            json={"document_ids": [1, 2]},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "当前账号无权限访问该功能"
    finally:
        app.dependency_overrides.clear()


def test_knowledge_base_delete_uses_bulk_vector_delete(monkeypatch):
    client, SessionLocal, deleted_doc_ids = _make_client(monkeypatch)
    try:
        with SessionLocal() as db:
            assert knowledge_base_service.delete_knowledge_base(db, 1) is True

        assert deleted_doc_ids == [1, 2, 3]
    finally:
        app.dependency_overrides.clear()

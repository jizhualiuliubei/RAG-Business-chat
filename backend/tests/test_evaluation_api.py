from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.services import auth_service


def _test_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = TestingSessionLocal()
    try:
        auth_service.init_default_users(db)
        yield db
    finally:
        db.close()


def _client(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.milvus_store, "ensure_collection", lambda: None)

    def override_get_db():
        yield from _test_db()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _captcha_payload(client, answer="3"):
    captcha = client.get("/api/auth/captcha").json()
    auth_service._CAPTCHA_STORE[captcha["captcha_id"]]["answer"] = answer
    return {"captcha_id": captcha["captcha_id"], "captcha_answer": answer}


def _login_token(client, username, password):
    payload = {"username": username, "password": password, **_captcha_payload(client)}
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 200
    return response.json()["access_token"]


def test_evaluation_catalog_requires_login(monkeypatch):
    client = _client(monkeypatch)
    try:
        response = client.get("/api/evaluations/catalog")
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_evaluation_catalog_requires_admin(monkeypatch):
    client = _client(monkeypatch)
    try:
        employee_token = _login_token(client, "employee", "employee123")
        response = client.get(
            "/api/evaluations/catalog",
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "当前账号无权限访问该功能"
    finally:
        app.dependency_overrides.clear()


def test_admin_can_read_evaluation_catalog(monkeypatch):
    client = _client(monkeypatch)
    try:
        admin_token = _login_token(client, "admin", "admin123")
        response = client.get(
            "/api/evaluations/catalog",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = response.json()

        assert response.status_code == 200
        assert data["case_count"] == 52
        assert data["categories"]["A"]["label"] == "直接事实检索"
        assert data["categories"]["E"]["case_count"] == 10
    finally:
        app.dependency_overrides.clear()


def test_admin_can_read_evaluation_metric_definitions(monkeypatch):
    client = _client(monkeypatch)
    try:
        admin_token = _login_token(client, "admin", "admin123")
        response = client.get(
            "/api/evaluations/metrics-definition",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = response.json()

        assert response.status_code == 200
        assert "retrieval_pass_rate" in data
        assert data["section_hit_rate"]["label"] == "章节级命中率"
    finally:
        app.dependency_overrides.clear()


def test_admin_can_trigger_retrieval_evaluation_without_model_call(monkeypatch):
    import app.api.evaluations as evaluations_api

    monkeypatch.setattr(evaluations_api, "run_retrieval_evaluation", lambda kb_id: {
        "kb_id": kb_id,
        "mode": "retrieval",
        "case_count": 52,
        "retrieval_pass_rate": 100,
    })
    client = _client(monkeypatch)
    try:
        admin_token = _login_token(client, "admin", "admin123")
        response = client.post(
            "/api/evaluations/run",
            json={"kb_id": 1, "mode": "retrieval"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = response.json()

        assert response.status_code == 200
        assert data["mode"] == "retrieval"
        assert data["case_count"] == 52
        assert data["retrieval_pass_rate"] == 100
    finally:
        app.dependency_overrides.clear()


def test_admin_can_trigger_evaluation_for_all_knowledge_bases(monkeypatch):
    import app.api.evaluations as evaluations_api

    observed = {}

    def fake_run(kb_id):
        observed["kb_id"] = kb_id
        return {
            "kb_id": kb_id,
            "mode": "retrieval",
            "case_count": 52,
            "evaluated_case_count": 42,
            "skipped_case_count": 10,
            "retrieval_pass_rate": 50,
        }

    monkeypatch.setattr(evaluations_api, "run_retrieval_evaluation", fake_run)
    client = _client(monkeypatch)
    try:
        admin_token = _login_token(client, "admin", "admin123")
        response = client.post(
            "/api/evaluations/run",
            json={"kb_id": [1, 2], "mode": "retrieval"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = response.json()

        assert response.status_code == 200
        assert observed["kb_id"] == [1, 2]
        assert data["kb_id"] == [1, 2]
    finally:
        app.dependency_overrides.clear()

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.services import auth_service, enterprise_service


def _make_client(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.milvus_store, "ensure_collection", lambda: None)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with TestingSessionLocal() as db:
        auth_service.init_default_users(db)
        system_enterprise = enterprise_service.ensure_system_enterprise(db)
        db.add(KnowledgeBase(enterprise_id=system_enterprise.id, name="默认知识库"))
        db.commit()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSessionLocal


def _captcha_payload(client, answer="3"):
    captcha = client.get("/api/auth/captcha").json()
    auth_service._CAPTCHA_STORE[captcha["captcha_id"]]["answer"] = answer
    return {"captcha_id": captcha["captcha_id"], "captcha_answer": answer}


def _login_token(client, username, password):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password, **_captcha_payload(client)},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_employee_cannot_access_admin_console_apis(monkeypatch):
    client, _ = _make_client(monkeypatch)
    try:
        token = _login_token(client, "employee", "employee123")
        headers = {"Authorization": f"Bearer {token}"}

        for method, url in [
            ("get", "/api/admin/overview"),
            ("get", "/api/admin/users"),
            ("post", "/api/admin/users"),
            ("patch", "/api/admin/users/2/status"),
            ("get", "/api/admin/knowledge-overview"),
            ("patch", "/api/admin/knowledge-bases/1"),
            ("get", "/api/admin/audit-logs"),
        ]:
            kwargs = {"json": {}} if method in {"post", "patch"} else {}
            response = getattr(client, method)(url, headers=headers, **kwargs)
            assert response.status_code == 403
            assert response.json()["detail"] == "当前账号无权限访问该功能"
    finally:
        app.dependency_overrides.clear()


def test_admin_can_create_disable_enable_employee_and_audit_actions(monkeypatch):
    client, SessionLocal = _make_client(monkeypatch)
    try:
        admin_token = _login_token(client, "admin", "admin123")
        headers = {"Authorization": f"Bearer {admin_token}"}

        overview = client.get("/api/admin/overview", headers=headers)
        assert overview.status_code == 200
        assert overview.json()["user_count"] == 2

        create = client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "username": "analyst",
                "password": "analyst123",
                "display_name": "知识分析员",
            },
        )
        assert create.status_code == 200
        created = create.json()
        assert created["username"] == "analyst"
        assert created["role"] == "employee"
        assert created["status"] == "active"

        duplicate = client.post(
            "/api/admin/users",
            headers=headers,
            json={"username": "analyst", "password": "analyst123", "display_name": "重复账号"},
        )
        assert duplicate.status_code == 400

        with SessionLocal() as db:
            user = db.execute(select(User).where(User.username == "analyst")).scalar_one()
            assert user.password_hash != "analyst123"
            user_id = user.id

        disabled = client.patch(
            f"/api/admin/users/{user_id}/status",
            headers=headers,
            json={"status": "disabled"},
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"
        denied_login = client.post(
            "/api/auth/login",
            json={"username": "analyst", "password": "analyst123", **_captcha_payload(client)},
        )
        assert denied_login.status_code == 401

        enabled = client.patch(
            f"/api/admin/users/{user_id}/status",
            headers=headers,
            json={"status": "active"},
        )
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "active"

        users = client.get("/api/admin/users", headers=headers)
        assert users.status_code == 200
        assert [u["username"] for u in users.json()] == ["admin", "employee", "analyst"]

        audit = client.get("/api/admin/audit-logs", headers=headers)
        assert audit.status_code == 200
        actions = [item["action"] for item in audit.json()["items"]]
        assert "login" in actions
        assert "create_user" in actions
        assert "update_user_status" in actions
    finally:
        app.dependency_overrides.clear()


def test_admin_knowledge_overview_exposes_governance_data(monkeypatch):
    client, _ = _make_client(monkeypatch)
    try:
        admin_token = _login_token(client, "admin", "admin123")
        headers = {"Authorization": f"Bearer {admin_token}"}

        response = client.get("/api/admin/knowledge-overview", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert "knowledge_bases" in payload
        assert payload["knowledge_bases"][0]["name"] == "默认知识库"
        assert "doc_count" in payload["knowledge_bases"][0]
        assert "chunk_count" in payload["knowledge_bases"][0]
        assert "status_counts" in payload["knowledge_bases"][0]
    finally:
        app.dependency_overrides.clear()


def test_admin_knowledge_overview_hides_legacy_blank_knowledge_base(monkeypatch):
    client, SessionLocal = _make_client(monkeypatch)
    try:
        with SessionLocal() as db:
            system_enterprise = enterprise_service.ensure_system_enterprise(db)
            db.add(KnowledgeBase(enterprise_id=system_enterprise.id, name="   "))
            db.commit()

        admin_token = _login_token(client, "admin", "admin123")
        headers = {"Authorization": f"Bearer {admin_token}"}

        response = client.get("/api/admin/knowledge-overview", headers=headers)
        assert response.status_code == 200
        names = [item["name"] for item in response.json()["knowledge_bases"]]
        assert names == ["默认知识库"]
    finally:
        app.dependency_overrides.clear()


def test_admin_can_rename_knowledge_base_and_audit_it(monkeypatch):
    client, _ = _make_client(monkeypatch)
    try:
        admin_token = _login_token(client, "admin", "admin123")
        headers = {"Authorization": f"Bearer {admin_token}"}

        renamed = client.patch(
            "/api/admin/knowledge-bases/1",
            headers=headers,
            json={"name": "企业制度库"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "企业制度库"

        overview = client.get("/api/admin/knowledge-overview", headers=headers)
        assert overview.status_code == 200
        assert overview.json()["knowledge_bases"][0]["name"] == "企业制度库"

        duplicate = client.post(
            "/api/knowledge-bases",
            headers=headers,
            json={"name": "重复库"},
        )
        assert duplicate.status_code == 200
        conflict = client.patch(
            "/api/admin/knowledge-bases/1",
            headers=headers,
            json={"name": "重复库"},
        )
        assert conflict.status_code == 400

        audit = client.get("/api/admin/audit-logs", headers=headers)
        actions = [item["action"] for item in audit.json()["items"]]
        assert "update_knowledge_base" in actions
    finally:
        app.dependency_overrides.clear()

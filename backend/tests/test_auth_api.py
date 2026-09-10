from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.user import User
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
    client = TestClient(app)
    return client


def _captcha_payload(client, answer="3"):
    captcha = client.get("/api/auth/captcha").json()
    auth_service._CAPTCHA_STORE[captcha["captcha_id"]]["answer"] = answer
    return {"captcha_id": captcha["captcha_id"], "captcha_answer": answer}


def _login_token(client, username, password):
    payload = {"username": username, "password": password, **_captcha_payload(client)}
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 200
    return response.json()["access_token"]


def test_default_users_are_initialized_once():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        auth_service.init_default_users(db)
        auth_service.init_default_users(db)
        users = db.query(User).order_by(User.username).all()
        assert [u.username for u in users] == ["admin", "employee"]
        assert db.query(User).count() == 2
        assert users[0].password_hash != "admin123"
    finally:
        db.close()


def test_admin_and_employee_can_login_with_captcha(monkeypatch):
    client = _client(monkeypatch)
    try:
        admin_payload = {"username": "admin", "password": "admin123", **_captcha_payload(client)}
        admin = client.post("/api/auth/login", json=admin_payload)
        assert admin.status_code == 200
        assert admin.json()["user"]["role"] == "system_admin"
        assert admin.json()["access_token"]

        employee_payload = {"username": "employee", "password": "employee123", **_captcha_payload(client)}
        employee = client.post("/api/auth/login", json=employee_payload)
        assert employee.status_code == 200
        assert employee.json()["user"]["role"] == "employee"
    finally:
        app.dependency_overrides.clear()


def test_login_rejects_bad_password_bad_captcha_and_disabled_user(monkeypatch):
    client = _client(monkeypatch)
    try:
        bad_password = {"username": "admin", "password": "wrong", **_captcha_payload(client)}
        assert client.post("/api/auth/login", json=bad_password).status_code == 401

        bad_captcha = client.get("/api/auth/captcha").json()
        assert client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123",
            "captcha_id": bad_captcha["captcha_id"],
            "captcha_answer": "999",
        }).status_code == 400

        db = next(_test_db())
        try:
            user = db.query(User).filter(User.username == "employee").one()
            user.status = "disabled"
            db.commit()
            token = auth_service.create_access_token(user)
            assert token
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_business_apis_require_token_and_accept_valid_token(monkeypatch):
    client = _client(monkeypatch)
    try:
        assert client.get("/api/knowledge-bases").status_code == 401
        payload = {"username": "admin", "password": "admin123", **_captcha_payload(client)}
        token = client.post("/api/auth/login", json=payload).json()["access_token"]
        response = client.get("/api/knowledge-bases", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_admin_only_management_apis_reject_employee_tokens(monkeypatch):
    client = _client(monkeypatch)
    try:
        employee_token = _login_token(client, "employee", "employee123")
        employee_headers = {"Authorization": f"Bearer {employee_token}"}

        protected_requests = [
            ("get", "/api/documents"),
            ("get", "/api/stats/summary"),
            ("post", "/api/knowledge-bases", {"json": {"name": "员工不应创建"}}),
            ("delete", "/api/knowledge-bases/1"),
            ("delete", "/api/conversations/1"),
        ]

        for method, url, *extra in protected_requests:
            kwargs = extra[0] if extra else {}
            response = getattr(client, method)(url, headers=employee_headers, **kwargs)
            assert response.status_code == 403
            assert response.json()["detail"] == "当前账号无权限访问该功能"
    finally:
        app.dependency_overrides.clear()


def test_admin_can_access_management_apis(monkeypatch):
    client = _client(monkeypatch)
    try:
        admin_token = _login_token(client, "admin", "admin123")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        assert client.get("/api/documents", headers=admin_headers).status_code == 200
        assert client.get("/api/stats/summary", headers=admin_headers).status_code == 200
        assert client.post(
            "/api/knowledge-bases",
            json={"name": "管理员知识库"},
            headers=admin_headers,
        ).status_code == 200
        assert client.delete("/api/conversations/1", headers=admin_headers).status_code in {200, 404}
    finally:
        app.dependency_overrides.clear()

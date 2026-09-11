import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base, get_db
from app.main import app
from app.services import auth_service


def _make_client(monkeypatch):
    import app.main as main
    import app.services.evaluation_run_service as run_service

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(run_service, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(main.milvus_store, "ensure_collection", lambda: None)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            auth_service.init_default_users(db)
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSessionLocal


def _client(monkeypatch):
    return _make_client(monkeypatch)[0]


def _login(client, username="admin", password="admin123", enterprise_code="system"):
    captcha = client.get("/api/auth/captcha").json()
    auth_service._CAPTCHA_STORE[captcha["captcha_id"]]["answer"] = "1"
    response = client.post(
        "/api/auth/login",
        json={
            "enterprise_code": enterprise_code,
            "username": username,
            "password": password,
            "captcha_id": captcha["captcha_id"],
            "captcha_answer": "1",
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}, response.json()["user"]


def _register_enterprise_admin(client, code, username):
    response = client.post(
        "/api/auth/enterprise-register",
        json={
            "enterprise_name": f"{code.upper()}科技有限公司",
            "enterprise_code": code,
            "contact_name": "张三",
            "contact_email": f"{code}@example.com",
            "username": username,
            "display_name": f"{code}管理员",
            "password": "Admin123456",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve_tenant(client, system_headers, code):
    _register_enterprise_admin(client, code, "admin")
    enterprise = next(
        item for item in client.get("/api/system/enterprises", headers=system_headers).json()["items"]
        if item["code"] == code
    )
    response = client.post(f"/api/system/enterprises/{enterprise['id']}/approve", headers=system_headers)
    assert response.status_code == 200, response.text
    return enterprise


def test_enterprise_register_approve_login_and_me_contract(monkeypatch):
    client = _client(monkeypatch)
    try:
        pending = _register_enterprise_admin(client, "acme", "admin")
        assert pending["enterprise"]["status"] == "pending_review"
        assert pending["user"]["status"] == "pending_review"

        system_headers, system_user = _login(client)
        assert system_user["role"] == "system_admin"
        enterprises = client.get("/api/system/enterprises", headers=system_headers).json()["items"]
        acme = next(item for item in enterprises if item["code"] == "acme")

        approved = client.post(
            f"/api/system/enterprises/{acme['id']}/approve",
            headers=system_headers,
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "active"

        acme_headers, acme_user = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        assert acme_user["role"] == "enterprise_admin"
        assert acme_user["enterprise_code"] == "acme"
        me = client.get("/api/auth/me", headers=acme_headers).json()
        assert me["enterprise_name"] == "ACME科技有限公司"
        assert me["brand"]["theme_color"]
    finally:
        app.dependency_overrides.clear()


def test_enterprise_knowledge_bases_are_isolated(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        for code in ("acme", "beta"):
            _register_enterprise_admin(client, code, "admin")
            enterprise = next(
                item for item in client.get("/api/system/enterprises", headers=system_headers).json()["items"]
                if item["code"] == code
            )
            client.post(f"/api/system/enterprises/{enterprise['id']}/approve", headers=system_headers)

        acme_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        beta_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="beta")

        created = client.post("/api/knowledge-bases", json={"name": "默认知识库"}, headers=acme_headers)
        assert created.status_code == 200, created.text
        assert any(item["name"] == "默认知识库" for item in client.get("/api/knowledge-bases", headers=acme_headers).json())
        assert all(item["name"] != "默认知识库" for item in client.get("/api/knowledge-bases", headers=beta_headers).json())
    finally:
        app.dependency_overrides.clear()


def test_enterprise_admin_creates_employee_and_employee_cannot_manage(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _register_enterprise_admin(client, "acme", "admin")
        enterprise = next(
            item for item in client.get("/api/system/enterprises", headers=system_headers).json()["items"]
            if item["code"] == "acme"
        )
        client.post(f"/api/system/enterprises/{enterprise['id']}/approve", headers=system_headers)
        acme_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")

        created = client.post(
            "/api/enterprise/users",
            json={"username": "worker1", "display_name": "员工一", "password": "Worker123456"},
            headers=acme_headers,
        )
        assert created.status_code == 200, created.text
        worker_headers, worker = _login(client, username="worker1", password="Worker123456", enterprise_code="acme")
        assert worker["role"] == "employee"

        forbidden = client.post("/api/knowledge-bases", json={"name": "员工不能建库"}, headers=worker_headers)
        assert forbidden.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_enterprise_model_keys_are_masked_and_missing_key_blocks_qa(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _register_enterprise_admin(client, "acme", "admin")
        enterprise = next(
            item for item in client.get("/api/system/enterprises", headers=system_headers).json()["items"]
            if item["code"] == "acme"
        )
        client.post(f"/api/system/enterprises/{enterprise['id']}/approve", headers=system_headers)
        acme_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")

        qa = client.post("/api/qa/ask", json={"question": "测试", "kb_id": 1}, headers=acme_headers)
        assert qa.status_code == 400
        assert "企业尚未配置模型 API Key" in qa.text

        saved = client.put(
            "/api/enterprise/model-keys",
            json={"deepseek_api_key": "sk-deepseek-123456", "siliconflow_api_key": "sk-sf-abcdef"},
            headers=acme_headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["deepseek"]["masked"] == "************3456"
        assert "sk-deepseek" not in saved.text
    finally:
        app.dependency_overrides.clear()


def test_document_upload_and_background_processing_carry_enterprise_id(monkeypatch):
    client, SessionLocal = _make_client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        acme_headers, acme_user = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        kb = client.post("/api/knowledge-bases", json={"name": "制度库"}, headers=acme_headers).json()
        captured = {}

        def fake_process(doc_id, stored_path, filename, enterprise_id=None):
            captured.update({"doc_id": doc_id, "enterprise_id": enterprise_id, "filename": filename})

        import app.api.documents as documents_api

        monkeypatch.setattr(documents_api.document_service, "process_document_with_limit", fake_process)
        response = client.post(
            f"/api/documents/upload?kb_id={kb['id']}",
            headers=acme_headers,
            files={"file": ("制度.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            from app.models.document import Document

            doc = db.get(Document, response.json()["id"])
            assert doc.enterprise_id == acme_user["enterprise_id"]
        assert captured["enterprise_id"] == acme_user["enterprise_id"]
    finally:
        app.dependency_overrides.clear()


def test_milvus_operations_filter_by_enterprise_and_kb(monkeypatch):
    from app.core import milvus_store

    calls = []

    class FakeClient:
        def search(self, **kwargs):
            calls.append(("search", kwargs["filter"]))
            return [[]]

        def query(self, **kwargs):
            calls.append(("query", kwargs["filter"]))
            return []

        def delete(self, **kwargs):
            calls.append(("delete", kwargs["filter"]))

        def flush(self, **kwargs):
            pass

    monkeypatch.setattr(milvus_store, "_ensure_ready", lambda: None)
    monkeypatch.setattr(milvus_store, "get_client", lambda: FakeClient())
    monkeypatch.setattr(
        milvus_store,
        "_collection_fields",
        {"id", "vector", "text", "source", "doc_id", "kb_id", "enterprise_id", "chunk_id"},
    )

    milvus_store.search([0.1], top_k=3, kb_id=[1, 2], enterprise_id=7)
    milvus_store.list_all_texts(kb_id=1, enterprise_id=7)
    milvus_store.delete_by_doc_ids([3, 4], enterprise_id=7)

    assert calls == [
        ("search", 'enterprise_id == "7" and kb_id in ["1", "2"]'),
        ("query", 'enterprise_id == "7" and kb_id == "1"'),
        ("delete", 'enterprise_id == "7" and doc_id in ["3", "4"]'),
    ]


def test_conversations_are_isolated_by_enterprise(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        _approve_tenant(client, system_headers, "beta")
        acme_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        beta_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="beta")

        conv = client.post("/api/conversations", json={"title": "ACME 会话"}, headers=acme_headers).json()
        assert any(item["id"] == conv["id"] for item in client.get("/api/conversations", headers=acme_headers).json())
        assert all(item["id"] != conv["id"] for item in client.get("/api/conversations", headers=beta_headers).json())
        assert client.get(f"/api/conversations/{conv['id']}/messages", headers=beta_headers).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_conversations_are_isolated_by_user_inside_same_enterprise(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        admin_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")

        create_employee = client.post(
            "/api/admin/users",
            json={"display_name": "ACME员工", "username": "employee1", "password": "Employee123"},
            headers=admin_headers,
        )
        assert create_employee.status_code == 200, create_employee.text
        employee_headers, _ = _login(
            client,
            username="employee1",
            password="Employee123",
            enterprise_code="acme",
        )

        admin_conv = client.post("/api/conversations", json={"title": "管理员会话"}, headers=admin_headers).json()
        assert any(item["id"] == admin_conv["id"] for item in client.get("/api/conversations", headers=admin_headers).json())
        assert all(item["id"] != admin_conv["id"] for item in client.get("/api/conversations", headers=employee_headers).json())
        assert client.get(f"/api/conversations/{admin_conv['id']}/messages", headers=employee_headers).status_code == 404
        assert client.post(
            f"/api/conversations/{admin_conv['id']}/save-turn",
            json={"question": "员工写入", "answer": "不应该成功"},
            headers=employee_headers,
        ).status_code == 404
        assert client.post(
            "/api/qa/ask",
            json={"conversation_id": admin_conv["id"], "question": "绕过会话接口写入", "kb_id": 1},
            headers=employee_headers,
        ).status_code == 404

        employee_conv = client.post("/api/conversations", json={"title": "员工会话"}, headers=employee_headers).json()
        assert any(item["id"] == employee_conv["id"] for item in client.get("/api/conversations", headers=employee_headers).json())
        assert all(item["id"] != employee_conv["id"] for item in client.get("/api/conversations", headers=admin_headers).json())
    finally:
        app.dependency_overrides.clear()


def test_stats_recent_conversations_are_isolated_by_user(monkeypatch):
    client, SessionLocal = _make_client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        admin_headers, acme_admin = _login(client, username="admin", password="Admin123456", enterprise_code="acme")

        with SessionLocal() as db:
            from app.models.user import User

            db.add(
                User(
                    enterprise_id=acme_admin["enterprise_id"],
                    username="admin2",
                    password_hash=auth_service.hash_password("Admin223456"),
                    display_name="ACME二号管理员",
                    role="enterprise_admin",
                    status="active",
                )
            )
            db.commit()
        admin2_headers, _ = _login(
            client,
            username="admin2",
            password="Admin223456",
            enterprise_code="acme",
        )

        admin_conv = client.post("/api/conversations", json={"title": "管理员统计会话"}, headers=admin_headers).json()
        admin2_conv = client.post("/api/conversations", json={"title": "二号管理员统计会话"}, headers=admin2_headers).json()

        admin_recent = client.get("/api/stats/summary", headers=admin_headers).json()["recent_conversations"]
        admin2_recent = client.get("/api/stats/summary", headers=admin2_headers).json()["recent_conversations"]
        assert any(item["id"] == admin_conv["id"] for item in admin_recent)
        assert all(item["id"] != admin2_conv["id"] for item in admin_recent)
        assert any(item["id"] == admin2_conv["id"] for item in admin2_recent)
        assert all(item["id"] != admin_conv["id"] for item in admin2_recent)
    finally:
        app.dependency_overrides.clear()


def test_evaluation_runs_are_isolated_and_report_forbidden_cross_enterprise(monkeypatch):
    client, SessionLocal = _make_client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        _approve_tenant(client, system_headers, "beta")
        acme_headers, acme_user = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        beta_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="beta")

        with SessionLocal() as db:
            from app.models.evaluation import EvaluationRun

            run = EvaluationRun(
                enterprise_id=acme_user["enterprise_id"],
                name="ACME full",
                kb_id="1",
                dataset_version="demo",
                mode="retrieval",
                status="success",
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            run_id = run.id

        acme_runs = client.get("/api/evaluation-runs", headers=acme_headers).json()["items"]
        beta_runs = client.get("/api/evaluation-runs", headers=beta_headers).json()["items"]
        assert any(item["id"] == run_id for item in acme_runs)
        assert all(item["id"] != run_id for item in beta_runs)
        assert client.post(f"/api/evaluation-runs/{run_id}/report", headers=beta_headers).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_system_admin_can_create_impersonation_token_with_audit(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        enterprise = _approve_tenant(client, system_headers, "acme")
        response = client.post(f"/api/system/enterprises/{enterprise['id']}/impersonate", headers=system_headers)
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
        assert me["enterprise_code"] == "acme"
        assert me["role"] == "system_admin"
        logs = client.get("/api/admin/audit-logs", headers=system_headers).json()["items"]
        assert any(item["action"] == "impersonate_enterprise" for item in logs)
    finally:
        app.dependency_overrides.clear()


def test_system_admin_can_crud_and_soft_delete_enterprises(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        created = client.post(
            "/api/system/enterprises",
            headers=system_headers,
            json={
                "name": "北方制造有限公司",
                "code": "north",
                "contact_name": "李四",
                "contact_email": "north@example.com",
                "contact_phone": "13800000000",
                "theme_color": "#0f766e",
            },
        )
        assert created.status_code == 200, created.text
        enterprise_id = created.json()["id"]
        assert created.json()["code"] == "north"
        assert created.json()["status"] == "active"

        detail = client.get(f"/api/system/enterprises/{enterprise_id}", headers=system_headers)
        assert detail.status_code == 200
        assert detail.json()["contact_email"] == "north@example.com"

        updated = client.patch(
            f"/api/system/enterprises/{enterprise_id}",
            headers=system_headers,
            json={"name": "北方智造有限公司", "code": "north-ai", "contact_email": "admin@north.example"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "北方智造有限公司"
        assert updated.json()["code"] == "north-ai"

        deleted = client.delete(f"/api/system/enterprises/{enterprise_id}", headers=system_headers)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["status"] == "deleted"
        assert all(
            item["id"] != enterprise_id
            for item in client.get("/api/system/enterprises", headers=system_headers).json()["items"]
        )
        assert any(
            item["id"] == enterprise_id
            for item in client.get("/api/system/enterprises?status=deleted", headers=system_headers).json()["items"]
        )

        logs = client.get("/api/admin/audit-logs", headers=system_headers).json()["items"]
        actions = [item["action"] for item in logs]
        assert "create_enterprise" in actions
        assert "update_enterprise" in actions
        assert "delete_enterprise" in actions
    finally:
        app.dependency_overrides.clear()


def test_system_enterprise_code_is_protected_and_cannot_be_deleted(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        system_enterprise = next(
            item for item in client.get("/api/system/enterprises", headers=system_headers).json()["items"]
            if item["code"] == "system"
        )
        rename = client.patch(
            f"/api/system/enterprises/{system_enterprise['id']}",
            headers=system_headers,
            json={"code": "platform"},
        )
        assert rename.status_code == 400
        delete = client.delete(f"/api/system/enterprises/{system_enterprise['id']}", headers=system_headers)
        assert delete.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_soft_deleted_enterprise_users_cannot_login(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        created = client.post(
            "/api/system/enterprises",
            headers=system_headers,
            json={"name": "删除测试企业", "code": "gone"},
        )
        enterprise_id = created.json()["id"]
        with_user = client.post(
            "/api/auth/enterprise-register",
            json={
                "enterprise_name": "另一家企业",
                "enterprise_code": "with-user",
                "username": "boss",
                "display_name": "负责人",
                "password": "Admin123456",
            },
        )
        user_enterprise_id = with_user.json()["enterprise"]["id"]
        client.post(f"/api/system/enterprises/{user_enterprise_id}/approve", headers=system_headers)
        client.delete(f"/api/system/enterprises/{user_enterprise_id}", headers=system_headers)

        captcha = client.get("/api/auth/captcha").json()
        auth_service._CAPTCHA_STORE[captcha["captcha_id"]]["answer"] = "1"
        denied = client.post(
            "/api/auth/login",
            json={
                "enterprise_code": "with-user",
                "username": "boss",
                "password": "Admin123456",
                "captcha_id": captcha["captcha_id"],
                "captcha_answer": "1",
            },
        )
        assert denied.status_code == 401
        assert client.get(f"/api/system/enterprises/{enterprise_id}", headers=system_headers).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_admin_users_include_enterprise_identity_and_system_admin_sees_all(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        acme_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        client.post(
            "/api/enterprise/users",
            json={"username": "worker1", "display_name": "员工一", "password": "Worker123456"},
            headers=acme_headers,
        )

        system_users = client.get("/api/admin/users", headers=system_headers).json()
        assert any(item["enterprise_code"] == "system" for item in system_users)
        assert any(item["enterprise_code"] == "acme" and item["username"] == "worker1" for item in system_users)

        acme_users = client.get("/api/admin/users", headers=acme_headers).json()
        assert all(item["enterprise_code"] == "acme" for item in acme_users)
    finally:
        app.dependency_overrides.clear()


def test_system_admin_can_create_employee_for_selected_enterprise(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        created = client.post(
            "/api/admin/users",
            headers=system_headers,
            json={
                "enterprise_code": "acme",
                "username": "acme-worker",
                "display_name": "ACME 员工",
                "password": "Worker123456",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["enterprise_code"] == "acme"
        assert created.json()["enterprise_name"] == "ACME科技有限公司"

        acme_headers, acme_user = _login(
            client,
            username="acme-worker",
            password="Worker123456",
            enterprise_code="acme",
        )
        assert acme_user["role"] == "employee"
        assert acme_headers["Authorization"]
    finally:
        app.dependency_overrides.clear()


def test_enterprise_admin_cannot_create_employee_for_other_enterprise(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        _approve_tenant(client, system_headers, "beta")
        acme_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        response = client.post(
            "/api/admin/users",
            headers=acme_headers,
            json={
                "enterprise_code": "beta",
                "username": "cross-worker",
                "display_name": "越权员工",
                "password": "Worker123456",
            },
        )
        assert response.status_code == 400
        assert "不能为其他企业创建员工" in response.text
    finally:
        app.dependency_overrides.clear()


def test_system_model_keys_are_saved_and_preferred_before_env_fallback(monkeypatch):
    client, SessionLocal = _make_client(monkeypatch)
    try:
        system_headers, system_user = _login(client)
        settings = client.get("/api/enterprise/settings", headers=system_headers)
        assert settings.status_code == 200
        assert settings.json()["enterprise"]["code"] == "system"

        saved = client.put(
            "/api/enterprise/model-keys",
            headers=system_headers,
            json={"deepseek_api_key": "sk-system-deepseek", "siliconflow_api_key": "sk-system-siliconflow"},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["deepseek"]["masked"] == "************seek"

        with SessionLocal() as db:
            from app.services import enterprise_service

            model_config = enterprise_service.require_enterprise_deepseek_key(db, system_user["enterprise_id"])
            embed_config = enterprise_service.require_enterprise_siliconflow_key(db, system_user["enterprise_id"])
        assert model_config["api_key"] == "sk-system-deepseek"
        assert embed_config["api_key"] == "sk-system-siliconflow"
    finally:
        app.dependency_overrides.clear()


def test_enterprise_create_and_update_validate_core_fields(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        invalid_payloads = [
            ({"name": "", "code": "ok-code"}, "企业名称不能为空"),
            ({"name": "无代码企业", "code": ""}, "企业代码不能为空"),
            ({"name": "坏代码企业", "code": "中文 code"}, "企业代码只能包含小写字母、数字、横线或下划线，长度 2-60"),
            ({"name": "坏邮箱企业", "code": "bad-email", "contact_email": "not-email"}, "联系人邮箱格式不正确"),
            ({"name": "坏电话企业", "code": "bad-phone", "contact_phone": "12345"}, "联系人手机号格式不正确"),
            ({"name": "坏 Logo 企业", "code": "bad-logo", "logo_url": "ftp://example.com/logo.png"}, "Logo URL 必须以 http:// 或 https:// 开头"),
        ]
        for payload, message in invalid_payloads:
            response = client.post("/api/system/enterprises", headers=system_headers, json=payload)
            assert response.status_code == 400, response.text
            assert message in response.text

        created = client.post(
            "/api/system/enterprises",
            headers=system_headers,
            json={"name": "大小写代码企业", "code": "North_AI"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["code"] == "north_ai"

        invalid_update = client.patch(
            f"/api/system/enterprises/{created.json()['id']}",
            headers=system_headers,
            json={"contact_email": "wrong"},
        )
        assert invalid_update.status_code == 400
        assert "联系人邮箱格式不正确" in invalid_update.text
    finally:
        app.dependency_overrides.clear()


def test_enterprise_list_can_search_and_filter_together(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        alpha = client.post(
            "/api/system/enterprises",
            headers=system_headers,
            json={
                "name": "华东制造有限公司",
                "code": "east-mfg",
                "contact_name": "王五",
                "contact_email": "east@example.com",
                "contact_phone": "13900000001",
            },
        ).json()
        beta = client.post(
            "/api/system/enterprises",
            headers=system_headers,
            json={
                "name": "西部科技有限公司",
                "code": "west-tech",
                "contact_name": "赵六",
                "contact_email": "west@example.com",
                "contact_phone": "13900000002",
            },
        ).json()
        client.patch(f"/api/system/enterprises/{beta['id']}/status", headers=system_headers, json={"status": "disabled"})

        by_name = client.get("/api/system/enterprises?q=华东", headers=system_headers).json()["items"]
        assert [item["id"] for item in by_name] == [alpha["id"]]

        by_contact = client.get("/api/system/enterprises?q=13900000002&status=disabled", headers=system_headers).json()["items"]
        assert [item["id"] for item in by_contact] == [beta["id"]]

        hidden_deleted_id = client.post(
            "/api/system/enterprises",
            headers=system_headers,
            json={"name": "已删企业", "code": "deleted-demo"},
        ).json()["id"]
        client.delete(f"/api/system/enterprises/{hidden_deleted_id}", headers=system_headers)
        default_items = client.get("/api/system/enterprises?q=已删", headers=system_headers).json()["items"]
        assert all(item["id"] != hidden_deleted_id for item in default_items)
        deleted_items = client.get("/api/system/enterprises?q=已删&status=deleted", headers=system_headers).json()["items"]
        assert [item["id"] for item in deleted_items] == [hidden_deleted_id]
    finally:
        app.dependency_overrides.clear()


def test_model_key_connection_test_endpoint_is_masked_and_admin_only(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        acme_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        client.post(
            "/api/enterprise/users",
            json={"username": "worker2", "display_name": "员工二", "password": "Worker123456"},
            headers=acme_headers,
        )
        worker_headers, _ = _login(client, username="worker2", password="Worker123456", enterprise_code="acme")

        def fake_check(provider, key, base_url, model):
            assert key in {"sk-live-deepseek-abcdef", "sk-live-siliconflow-uvwxyz"}
            return {"ok": True, "message": f"{provider} 连接正常", "latency_ms": 12}

        import app.services.enterprise_service as enterprise_service

        monkeypatch.setattr(enterprise_service, "_check_provider_connection", fake_check)

        response = client.post(
            "/api/enterprise/model-keys/test",
            headers=acme_headers,
            json={
                "deepseek_api_key": "sk-live-deepseek-abcdef",
                "siliconflow_api_key": "sk-live-siliconflow-uvwxyz",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["deepseek"]["ok"] is True
        assert data["siliconflow"]["ok"] is True
        assert "sk-live-deepseek" not in response.text
        assert "sk-live-siliconflow" not in response.text

        forbidden = client.post("/api/enterprise/model-keys/test", headers=worker_headers, json={})
        assert forbidden.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_model_key_connection_test_reports_provider_model_and_not_found(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)

        def fake_check(provider, key, base_url, model):
            if provider == "siliconflow":
                return {"ok": False, "message": "模型或接口不存在，请检查 Base URL 和模型名称", "latency_ms": 9}
            return {"ok": True, "message": "连接正常", "latency_ms": 7}

        import app.services.enterprise_service as enterprise_service

        monkeypatch.setattr(enterprise_service, "_check_provider_connection", fake_check)
        response = client.post(
            "/api/enterprise/model-keys/test",
            headers=system_headers,
            json={
                "deepseek_api_key": "sk-test-deepseek",
                "deepseek_base_url": "https://api.deepseek.com/v1",
                "deepseek_model": "deepseek-v4-flash",
                "siliconflow_api_key": "sk-test-siliconflow",
                "siliconflow_base_url": "https://api.siliconflow.cn/v1",
                "embed_model_name": "BAAI/bge-m3",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["deepseek"]["provider"] == "DeepSeek 对话模型"
        assert data["deepseek"]["model"] == "deepseek-v4-flash"
        assert data["siliconflow"]["provider"] == "SiliconFlow 嵌入模型"
        assert data["siliconflow"]["model"] == "BAAI/bge-m3"
        assert data["siliconflow"]["ok"] is False
        assert "模型或接口不存在" in data["siliconflow"]["message"]
        assert "sk-test" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_provider_error_message_normalizes_model_not_found_variants():
    import app.services.enterprise_service as enterprise_service

    for message in (
        "NOT FOUND",
        "MODEL_NOT_FOUND",
        "NotFoundError: model not found",
        "404 Client Error",
    ):
        normalized = enterprise_service._provider_error_message(Exception(message))
        assert normalized == "模型或接口不存在，请检查 Base URL 和模型名称"


def test_model_key_base_urls_are_normalized_before_save_and_test(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        checked = {}

        def fake_check(provider, key, base_url, model):
            checked[provider] = base_url
            return {"ok": True, "message": "连接正常", "latency_ms": 5}

        import app.services.enterprise_service as enterprise_service

        monkeypatch.setattr(enterprise_service, "_check_provider_connection", fake_check)

        tested = client.post(
            "/api/enterprise/model-keys/test",
            headers=system_headers,
            json={
                "deepseek_api_key": "sk-test-deepseek",
                "deepseek_base_url": "https://api.deepseek.com",
                "siliconflow_api_key": "sk-test-siliconflow",
                "siliconflow_base_url": "https://api.siliconflow.cn",
            },
        )
        assert tested.status_code == 200, tested.text
        assert checked["deepseek"] == "https://api.deepseek.com/v1"
        assert checked["siliconflow"] == "https://api.siliconflow.cn/v1"
        assert tested.json()["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
        assert tested.json()["siliconflow"]["base_url"] == "https://api.siliconflow.cn/v1"

        saved = client.put(
            "/api/enterprise/model-keys",
            headers=system_headers,
            json={
                "deepseek_api_key": "sk-test-deepseek",
                "deepseek_base_url": "https://api.deepseek.com/",
                "siliconflow_api_key": "sk-test-siliconflow",
                "siliconflow_base_url": "https://api.siliconflow.cn/",
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
        assert saved.json()["siliconflow"]["base_url"] == "https://api.siliconflow.cn/v1"
    finally:
        app.dependency_overrides.clear()


def test_enterprise_register_validates_required_fields_and_contact_formats(monkeypatch):
    client = _client(monkeypatch)
    try:
        invalid_payloads = [
            ({"password": "12345"}, "密码至少需要 6 位"),
            ({"contact_email": "bad-email"}, "联系人邮箱格式不正确"),
            ({"contact_phone": "12345"}, "联系人手机号格式不正确"),
            ({"enterprise_code": "中文 code"}, "企业代码只能包含小写字母、数字、横线或下划线"),
        ]
        base = {
            "enterprise_name": "注册校验企业",
            "enterprise_code": "signup-check",
            "contact_name": "张三",
            "contact_email": "signup@example.com",
            "contact_phone": "13800000000",
            "username": "boss",
            "display_name": "企业管理员",
            "password": "Admin123456",
        }
        for override, message in invalid_payloads:
            payload = {**base, **override}
            response = client.post("/api/auth/enterprise-register", json=payload)
            assert response.status_code == 400, response.text
            assert message in response.text
    finally:
        app.dependency_overrides.clear()


def test_system_admin_create_enterprise_can_create_initial_enterprise_admin(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        response = client.post(
            "/api/system/enterprises",
            headers=system_headers,
            json={
                "name": "南方制造有限公司",
                "code": "south-mfg",
                "admin_username": "admin",
                "admin_display_name": "南方企业管理员",
                "admin_password": "SouthAdmin123",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["code"] == "south-mfg"
        assert data["admin_user"]["role"] == "enterprise_admin"
        assert data["admin_user"]["enterprise_code"] == "south-mfg"
        assert data["initial_password"] == "SouthAdmin123"

        headers, user = _login(
            client,
            username="admin",
            password="SouthAdmin123",
            enterprise_code="south-mfg",
        )
        assert user["role"] == "enterprise_admin"
        assert headers["Authorization"]
    finally:
        app.dependency_overrides.clear()


def test_enterprise_admin_can_update_and_delete_own_employees(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        acme_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        created = client.post(
            "/api/admin/users",
            headers=acme_headers,
            json={"username": "worker3", "display_name": "员工三", "password": "Worker123456"},
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]

        updated = client.patch(
            f"/api/admin/users/{user_id}",
            headers=acme_headers,
            json={"display_name": "员工三号", "username": "worker3-updated", "password": "Newpass123"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["display_name"] == "员工三号"
        assert updated.json()["username"] == "worker3-updated"

        login_headers, login_user = _login(
            client,
            username="worker3-updated",
            password="Newpass123",
            enterprise_code="acme",
        )
        assert login_user["role"] == "employee"
        assert login_headers["Authorization"]

        deleted = client.delete(f"/api/admin/users/{user_id}", headers=acme_headers)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["status"] == "deleted"
        users = client.get("/api/admin/users", headers=acme_headers).json()
        assert all(item["id"] != user_id for item in users)
    finally:
        app.dependency_overrides.clear()


def test_enterprise_admin_starts_with_empty_evaluation_dataset_catalog(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        system_items = client.get("/api/evaluation-datasets", headers=system_headers).json()["items"]
        assert any(item["source_type"] == "builtin" for item in system_items)

        _approve_tenant(client, system_headers, "acme")
        acme_headers, _ = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        acme_items = client.get("/api/evaluation-datasets", headers=acme_headers).json()["items"]
        assert acme_items == []
        assert client.get(
            "/api/evaluation-datasets/enterprise_scale_360_v1",
            headers=acme_headers,
        ).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_enterprise_admin_overview_and_audit_logs_are_scoped_to_own_enterprise(monkeypatch):
    client = _client(monkeypatch)
    try:
        system_headers, _ = _login(client)
        _approve_tenant(client, system_headers, "acme")
        _approve_tenant(client, system_headers, "beta")
        acme_headers, acme_user = _login(client, username="admin", password="Admin123456", enterprise_code="acme")
        beta_headers, beta_user = _login(client, username="admin", password="Admin123456", enterprise_code="beta")

        client.post("/api/knowledge-bases", json={"name": "ACME制度库"}, headers=acme_headers)
        client.post("/api/knowledge-bases", json={"name": "BETA制度库"}, headers=beta_headers)

        overview = client.get("/api/admin/overview", headers=acme_headers).json()
        assert overview["recent_audits"]
        assert all(item["enterprise_id"] == acme_user["enterprise_id"] for item in overview["recent_audits"])
        assert all("BETA" not in item["target_name"] for item in overview["recent_audits"])

        audit_items = client.get("/api/admin/audit-logs", headers=acme_headers).json()["items"]
        assert audit_items
        assert all(item["enterprise_id"] == acme_user["enterprise_id"] for item in audit_items)
        assert all(item["enterprise_id"] != beta_user["enterprise_id"] for item in audit_items)
    finally:
        app.dependency_overrides.clear()

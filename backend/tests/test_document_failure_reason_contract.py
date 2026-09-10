from pathlib import Path


def test_document_failure_reason_is_part_of_model_schema_and_admin_payload():
    backend_root = Path(__file__).resolve().parents[1]
    model_source = (backend_root / "app" / "models" / "document.py").read_text(encoding="utf-8")
    schema_source = (backend_root / "app" / "schemas" / "document.py").read_text(encoding="utf-8")
    service_source = (backend_root / "app" / "services" / "document_service.py").read_text(encoding="utf-8")
    admin_source = (backend_root / "app" / "services" / "admin_service.py").read_text(encoding="utf-8")
    database_source = (backend_root / "app" / "database.py").read_text(encoding="utf-8")

    assert "failure_reason" in model_source
    assert "failure_reason" in schema_source
    assert "failure_reason" in service_source
    assert "failure_reason" in admin_source
    assert "ensure_document_failure_reason_column" in database_source


def test_document_failure_reason_has_friendly_categories():
    backend_root = Path(__file__).resolve().parents[1]
    service_source = (backend_root / "app" / "services" / "document_service.py").read_text(encoding="utf-8")

    assert "_format_failure_reason" in service_source
    assert "PDF 解析失败" in service_source
    assert "嵌入模型" in service_source
    assert "向量库写入失败" in service_source

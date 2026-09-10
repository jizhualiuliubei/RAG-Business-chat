import pytest

from app.core import loader


def test_pdf_loader_reports_ocr_install_hint_when_pdf_text_is_empty(monkeypatch, tmp_path):
    pdf_path = tmp_path / "信息安全管理制度.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(loader, "_load_pdf_with_timeout", lambda file_path: [])
    monkeypatch.setattr(loader, "_ocr_fallback", lambda file_path: [])
    monkeypatch.setattr(loader, "_pdf_ocr_available", lambda: False)

    with pytest.raises(ValueError) as exc:
        loader.load_document(str(pdf_path), pdf_path.name)

    message = str(exc.value)
    assert "PDF 解析失败" in message
    assert "tesseract-ocr" in message
    assert "扫描版 PDF" in message

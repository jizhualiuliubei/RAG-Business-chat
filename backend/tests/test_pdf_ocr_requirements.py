from pathlib import Path


def test_pdf_ocr_runtime_dependencies_are_declared():
    root = Path(__file__).resolve().parents[2]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()

    assert "pillow" in requirements
    assert "numpy" in requirements

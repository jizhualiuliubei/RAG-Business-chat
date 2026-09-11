import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_deepseek_models_use_configured_low_temperature(monkeypatch):
    from app.core import llm

    calls = []

    def fake_init_chat_model(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(llm, "init_chat_model", fake_init_chat_model)
    monkeypatch.setattr(llm, "DEEPSEEK_TEMPERATURE", 0.1)
    monkeypatch.setattr(llm, "_model", None)
    monkeypatch.setattr(llm, "_model_cache", {})

    llm.get_model()
    llm.get_model_for_config("sk-enterprise", "https://api.deepseek.com/v1", "deepseek-v4-flash")

    assert calls[0]["temperature"] == 0.1
    assert calls[1]["temperature"] == 0.1

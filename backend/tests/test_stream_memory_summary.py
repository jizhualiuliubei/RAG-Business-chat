from types import SimpleNamespace

from app.services import qa_service


def test_stream_answer_summarizes_older_history_before_streaming(monkeypatch):
    messages = [
        SimpleNamespace(role="user", content=f"用户旧问题 {i}")
        if i % 2 == 0
        else SimpleNamespace(role="assistant", content=f"AI 旧回答 {i}")
        for i in range(8)
    ]
    captured = {}

    monkeypatch.setattr(qa_service.user_profile_service, "extract_and_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(qa_service.user_profile_service, "get_profile_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(qa_service.conversation_service, "list_messages", lambda *args, **kwargs: messages)
    monkeypatch.setattr(qa_service.attachment_service, "retrieve_attachment_sources", lambda *args, **kwargs: [])
    monkeypatch.setattr(qa_service.attachment_service, "build_attachment_rag_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(qa_service.attachment_service, "build_attachments_context", lambda *args, **kwargs: "")
    # db 传入裸 object()，而真实实现会调 db.execute() 查有效文档白名单，这里显式打桩
    monkeypatch.setattr(qa_service, "active_document_ids_for_scope", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        qa_service.rag,
        "retrieve_contexts",
        lambda *args, **kwargs: ([], [{"text": "制度片段", "source": "制度.docx", "chunk_id": 1, "score": 0.9}]),
    )
    monkeypatch.setattr(
        qa_service.rag,
        "summarize_history_messages",
        lambda history, model_config=None: "用户询问过旧问题，AI 已给出旧回答。",
    )

    def fake_stream(question, contexts, history=None, profile_text="", attachments_context="", model_config=None):
        captured["history"] = history
        yield "ok"

    monkeypatch.setattr(qa_service.rag, "stream_generate_answer", fake_stream)

    list(
        qa_service.stream_answer(
            "新问题",
            kb_id=1,
            conversation_id=1,
            db=object(),
            enterprise_id=None,
            user_id=7,
        )
    )

    assert captured["history"][0]["role"] == "system"
    assert "更早对话摘要" in captured["history"][0]["content"]
    assert "用户询问过旧问题" in captured["history"][0]["content"]
    assert captured["history"][1:] == [
        {"role": "user", "content": "用户旧问题 6"},
        {"role": "assistant", "content": "AI 旧回答 7"},
    ]


def test_stream_answer_keeps_short_history_without_summary(monkeypatch):
    """历史不超过阈值时不做摘要，原样透传。"""
    messages = [
        SimpleNamespace(role="user", content="用户问题"),
        SimpleNamespace(role="assistant", content="AI 回答"),
    ]
    captured = {}
    summary_calls = {"count": 0}

    monkeypatch.setattr(qa_service.user_profile_service, "extract_and_save", lambda *a, **k: None)
    monkeypatch.setattr(qa_service.user_profile_service, "get_profile_text", lambda *a, **k: "")
    monkeypatch.setattr(qa_service.conversation_service, "list_messages", lambda *a, **k: messages)
    monkeypatch.setattr(qa_service.attachment_service, "retrieve_attachment_sources", lambda *a, **k: [])
    monkeypatch.setattr(qa_service.attachment_service, "build_attachment_rag_context", lambda *a, **k: "")
    monkeypatch.setattr(qa_service.attachment_service, "build_attachments_context", lambda *a, **k: "")
    monkeypatch.setattr(qa_service, "active_document_ids_for_scope", lambda *a, **k: None)
    monkeypatch.setattr(
        qa_service.rag,
        "retrieve_contexts",
        lambda *a, **k: ([], [{"text": "片段", "source": "制度.docx", "chunk_id": 1, "score": 0.9}]),
    )
    monkeypatch.setattr(
        qa_service.rag,
        "summarize_history_messages",
        lambda *a, **k: summary_calls.__setitem__("count", summary_calls["count"] + 1) or "不应被调用",
    )

    def fake_stream(question, contexts, history=None, profile_text="", attachments_context="", model_config=None):
        captured["history"] = history
        yield "ok"

    monkeypatch.setattr(qa_service.rag, "stream_generate_answer", fake_stream)

    list(
        qa_service.stream_answer(
            "新问题",
            kb_id=1,
            conversation_id=1,
            db=object(),
            enterprise_id=None,
            user_id=7,
        )
    )

    assert summary_calls["count"] == 0
    assert captured["history"] == [
        {"role": "user", "content": "用户问题"},
        {"role": "assistant", "content": "AI 回答"},
    ]


def test_build_memory_history_trigger_boundary(monkeypatch):
    """阈值边界：恰好等于触发条数时透传，多一条才压缩。"""
    from app.core import rag

    six = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(6)
    ]
    monkeypatch.setattr(rag, "summarize_history_messages", lambda history, model_config=None: "摘要内容")

    assert rag.build_memory_history(six) == six

    seven = six + [{"role": "user", "content": "消息6"}]
    compressed = rag.build_memory_history(seven)
    assert compressed[0]["role"] == "system"
    assert "摘要内容" in compressed[0]["content"]
    assert compressed[1:] == [
        {"role": "assistant", "content": "消息5"},
        {"role": "user", "content": "消息6"},
    ]

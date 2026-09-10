import io
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base
from app.models.attachment import ConversationAttachment, ConversationAttachmentChunk
from app.services import attachment_service, qa_service


class _Upload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.file = io.BytesIO(content)
        self.size = len(content)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_create_attachment_builds_status_summary_and_chunks(tmp_path, monkeypatch):
    db = _session()
    monkeypatch.setattr(attachment_service, "ATTACHMENT_DIR", tmp_path)

    att = attachment_service.create_attachment(
        db,
        conversation_id=1,
        file=_Upload("临时报销说明.txt", "差旅报销材料必须包含发票、审批单和付款信息。".encode("utf-8")),
        enterprise_id=7,
        user_id=11,
    )

    chunks = db.query(ConversationAttachmentChunk).filter_by(attachment_id=att.id).all()
    assert att.status == "done"
    assert att.failure_reason == ""
    assert att.chunk_count == len(chunks) == 1
    assert att.content_chars > 0
    assert "差旅报销材料" in att.summary
    assert chunks[0].enterprise_id == 7
    assert chunks[0].user_id == 11
    assert chunks[0].conversation_id == 1
    assert chunks[0].source_type == "attachment"
    assert "临时报销说明.txt" in chunks[0].source


def test_attachment_retrieval_is_scoped_to_current_user_and_conversation(tmp_path, monkeypatch):
    db = _session()
    monkeypatch.setattr(attachment_service, "ATTACHMENT_DIR", tmp_path)

    own = attachment_service.create_attachment(
        db,
        1,
        _Upload("自己的附件.txt", "张三本次报销金额为 300 元。".encode("utf-8")),
        enterprise_id=7,
        user_id=11,
    )
    attachment_service.create_attachment(
        db,
        1,
        _Upload("同企业他人附件.txt", "李四本次报销金额为 900 元。".encode("utf-8")),
        enterprise_id=7,
        user_id=12,
    )
    attachment_service.create_attachment(
        db,
        2,
        _Upload("其他会话附件.txt", "王五本次报销金额为 1200 元。".encode("utf-8")),
        enterprise_id=7,
        user_id=11,
    )

    hits = attachment_service.retrieve_attachment_sources(
        db,
        conversation_id=1,
        question="张三报销金额是多少？",
        enterprise_id=7,
        user_id=11,
    )

    assert [hit["attachment_id"] for hit in hits] == [own.id]
    assert "300 元" in hits[0]["text"]
    assert hits[0]["source_type"] == "attachment"


def test_stream_answer_returns_knowledge_and_attachment_sources(tmp_path, monkeypatch):
    db = _session()
    monkeypatch.setattr(attachment_service, "ATTACHMENT_DIR", tmp_path)
    attachment_service.create_attachment(
        db,
        1,
        _Upload("临时名单.csv", "姓名,状态\n张三,已审批".encode("utf-8")),
        enterprise_id=7,
        user_id=11,
    )

    kb_context = [{"source": "员工手册.docx", "text": "知识库制度要求先审批。", "chunk_id": 1, "score": 0.8}]
    monkeypatch.setattr(qa_service.enterprise_service, "require_enterprise_deepseek_key", lambda db, enterprise_id: {"platform": True})
    monkeypatch.setattr(qa_service.enterprise_service, "require_enterprise_siliconflow_key", lambda db, enterprise_id: {"platform": True})
    monkeypatch.setattr(qa_service.rag, "retrieve_contexts", lambda *args, **kwargs: ([], kb_context))

    captured = {}

    def fake_stream(question, contexts, history=None, profile_text="", attachments_context="", model_config=None):
        captured["attachments_context"] = attachments_context
        captured["contexts"] = contexts
        yield "回答"

    monkeypatch.setattr(qa_service.rag, "stream_generate_answer", fake_stream)

    events = list(qa_service.stream_answer(
        "张三是否已审批？",
        kb_id=1,
        conversation_id=1,
        db=db,
        enterprise_id=7,
        user_id=11,
    ))
    sources_event = json.loads(events[0])

    assert sources_event["hit"] is True
    assert len(sources_event["knowledge_base_sources"]) == 1
    assert len(sources_event["attachment_sources"]) == 1
    assert sources_event["sources"][0]["source_type"] == "knowledge_base"
    assert sources_event["sources"][1]["source_type"] == "attachment"
    assert "会话附件" in captured["attachments_context"]
    assert "两者存在差异" in captured["attachments_context"]

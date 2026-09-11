from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services import knowledge_base_service


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_delete_knowledge_base_removes_document_records_vectors_and_files(tmp_path, monkeypatch):
    db = _session()
    deleted_doc_ids = []
    monkeypatch.setattr(knowledge_base_service.milvus_store, "delete_by_doc_ids", deleted_doc_ids.extend)
    monkeypatch.setattr(knowledge_base_service, "UPLOAD_DIR", tmp_path, raising=False)
    try:
        kb = KnowledgeBase(name="待删除知识库")
        db.add(kb)
        db.commit()
        db.refresh(kb)

        doc = Document(kb_id=kb.id, filename="制度.txt", file_size=10, status="done")
        db.add(doc)
        db.commit()
        db.refresh(doc)

        stored_file = tmp_path / f"doc_{doc.id}.txt"
        stored_file.write_text("临时文档", encoding="utf-8")

        assert knowledge_base_service.delete_knowledge_base(db, kb.id) is True

        assert deleted_doc_ids == [doc.id]
        assert db.query(Document).filter(Document.kb_id == kb.id).count() == 0
        assert db.get(KnowledgeBase, kb.id) is None
        assert not stored_file.exists()
    finally:
        db.close()


def test_create_knowledge_base_rejects_blank_name():
    db = _session()
    try:
        for blank in ("", "   ", "\n\t"):
            try:
                knowledge_base_service.create_knowledge_base(db, blank)
            except ValueError as exc:
                assert str(exc) == "知识库名称不能为空"
            else:
                raise AssertionError("blank knowledge base name should be rejected")
    finally:
        db.close()


def test_list_knowledge_bases_hides_legacy_blank_names():
    db = _session()
    try:
        db.add(KnowledgeBase(name="   "))
        db.add(KnowledgeBase(name="企业规模评测库"))
        db.commit()

        names = [kb.name for kb in knowledge_base_service.list_knowledge_bases(db)]

        assert names == ["企业规模评测库"]
    finally:
        db.close()


def test_create_knowledge_base_checks_duplicate_after_trimming_existing_name():
    db = _session()
    try:
        db.add(KnowledgeBase(name=" 企业规模评测库 "))
        db.commit()

        try:
            knowledge_base_service.create_knowledge_base(db, "企业规模评测库")
        except ValueError as exc:
            assert str(exc) == "知识库名称已存在: 企业规模评测库"
        else:
            raise AssertionError("trimmed duplicate knowledge base name should be rejected")
    finally:
        db.close()

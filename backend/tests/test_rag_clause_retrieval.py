import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_clause_hits_are_promoted_ahead_of_semantic_neighbors():
    from app.core import rag

    hits = [
        {
            "distance": 0.95,
            "entity": {
                "source": "员工手册.docx",
                "text": "来源：员工手册.docx > 福利与关怀 > HR-05-004\n年度体检安排在每年10月完成。",
                "doc_id": "1",
                "kb_id": "9",
                "chunk_id": 1,
            },
        },
        {
            "distance": 0.55,
            "entity": {
                "source": "员工手册.docx",
                "text": "来源：员工手册.docx > 福利与关怀 > HR-05-008\n生日福利以电子券形式发放，不折现、不跨年补发。",
                "doc_id": "1",
                "kb_id": "9",
                "chunk_id": 2,
            },
        },
    ]

    reranked = rag._promote_clause_hits("请说明 HR-05-008 的执行要求", hits)

    assert "HR-05-008" in reranked[0]["entity"]["text"]


def test_exact_clause_hits_are_added_from_full_text_pool(monkeypatch):
    from app.core import rag

    vector_candidates = [
        {
            "distance": 0.91,
            "entity": {
                "source": "员工手册.docx",
                "text": "来源：员工手册.docx > 入职管理 > HR-01-004\n试用期导师需提交反馈。",
                "doc_id": "1",
                "kb_id": "9",
                "chunk_id": 4,
            },
        }
    ]
    all_texts = [
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 入职管理 > HR-01-001\n新员工入职前须完成身份核验、学历核验和保密承诺签署。",
            "doc_id": "1",
            "kb_id": "9",
            "chunk_id": 1,
        },
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 入职管理 > HR-01-004\n试用期导师需提交反馈。",
            "doc_id": "1",
            "kb_id": "9",
            "chunk_id": 4,
        },
    ]

    monkeypatch.setattr(rag, "_rewrite_query", lambda question, model_config=None: "试用期导师 反馈")
    monkeypatch.setattr(rag, "embed_query", lambda query, model_config=None: [0.1, 0.2])
    monkeypatch.setattr(rag.milvus_store, "search", lambda *args, **kwargs: vector_candidates)
    monkeypatch.setattr(rag.milvus_store, "list_all_texts", lambda *args, **kwargs: all_texts)

    _, contexts = rag.retrieve_contexts("请原文引用《员工手册》HR-01-001。", kb_id=9, top_k=1)

    assert len(contexts) == 1
    assert "HR-01-001" in contexts[0]["text"]


def test_retrieve_contexts_does_not_use_orphan_vectors_outside_active_documents(monkeypatch):
    from app.core import rag

    stale_vectors = [
        {
            "distance": 0.99,
            "entity": {
                "source": "客户服务管理制度.docx",
                "text": "来源：客户服务管理制度.docx > 客诉响应 > CS-02-001\n客户投诉响应时限为24小时内。",
                "doc_id": "99",
                "kb_id": "9",
                "chunk_id": 1,
            },
        }
    ]
    active_texts = [
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 入职管理 > HR-01-001\n新员工入职前须完成身份核验。",
            "doc_id": "1",
            "kb_id": "9",
            "chunk_id": 1,
        },
    ]
    seen_doc_scopes = []

    monkeypatch.setattr(rag, "_rewrite_query", lambda question, model_config=None: question)
    monkeypatch.setattr(rag, "embed_query", lambda query, model_config=None: [0.1, 0.2])

    def fake_search(*args, **kwargs):
        seen_doc_scopes.append(kwargs.get("doc_ids"))
        if kwargs.get("doc_ids") == [1]:
            return []
        return stale_vectors

    def fake_list_all_texts(*args, **kwargs):
        seen_doc_scopes.append(kwargs.get("doc_ids"))
        return active_texts if kwargs.get("doc_ids") == [1] else active_texts + [
            {
                "source": "客户服务管理制度.docx",
                "text": "来源：客户服务管理制度.docx > 客诉响应 > CS-02-001\n客户投诉响应时限为24小时内。",
                "doc_id": "99",
                "kb_id": "9",
                "chunk_id": 1,
            }
        ]

    monkeypatch.setattr(rag.milvus_store, "search", fake_search)
    monkeypatch.setattr(rag.milvus_store, "list_all_texts", fake_list_all_texts)

    _, contexts = rag.retrieve_contexts("CS-02-001内容是什么", kb_id=9, top_k=3, allowed_doc_ids=[1])

    assert all(scope == [1] for scope in seen_doc_scopes)
    assert all(ctx["source"] != "客户服务管理制度.docx" for ctx in contexts)


def test_explicit_document_scope_keeps_answer_in_named_document(monkeypatch):
    from app.core import rag

    vector_candidates = [
        {
            "distance": 0.98,
            "entity": {
                "source": "薪酬绩效制度.xlsx",
                "text": "来源：薪酬绩效制度.xlsx > 入职管理 > PAY-01-001\n入职流程涉及薪酬确认和绩效周期。",
                "doc_id": "2",
                "kb_id": "9",
                "chunk_id": 1,
            },
        }
    ]
    all_texts = [
        {
            "source": "薪酬绩效制度.xlsx",
            "text": "来源：薪酬绩效制度.xlsx > 入职管理 > PAY-01-001\n入职流程涉及薪酬确认和绩效周期。",
            "doc_id": "2",
            "kb_id": "9",
            "chunk_id": 1,
        },
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 入职管理 > HR-01-001\n新员工入职前须完成身份核验、学历核验和保密承诺签署。",
            "doc_id": "1",
            "kb_id": "9",
            "chunk_id": 1,
        },
    ]

    monkeypatch.setattr(rag, "_rewrite_query", lambda question, model_config=None: question)
    monkeypatch.setattr(rag, "embed_query", lambda query, model_config=None: [0.1, 0.2])
    monkeypatch.setattr(rag.milvus_store, "search", lambda *args, **kwargs: vector_candidates)
    monkeypatch.setattr(rag.milvus_store, "list_all_texts", lambda *args, **kwargs: all_texts)

    _, contexts = rag.retrieve_contexts("只根据《员工手册》，说明入职管理要求，不要引用其他文件。", kb_id=9, top_k=2)

    assert contexts
    assert {ctx["source"] for ctx in contexts} == {"员工手册.docx"}


def test_retrieve_contexts_applies_rerank_before_final_top_k(monkeypatch):
    from app.core import rag

    all_texts = [
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 入职管理 > HR-01-004\n试用期导师需提交反馈。",
            "doc_id": "1",
            "kb_id": "9",
            "chunk_id": 4,
        },
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 入职管理 > HR-01-001\n新员工入职前须完成身份核验、学历核验和保密承诺签署。",
            "doc_id": "1",
            "kb_id": "9",
            "chunk_id": 1,
        },
    ]

    monkeypatch.setattr(rag, "_rewrite_query", lambda question, model_config=None: question)
    monkeypatch.setattr(rag, "embed_query", lambda query, model_config=None: [0.1, 0.2])
    monkeypatch.setattr(rag.milvus_store, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(rag.milvus_store, "list_all_texts", lambda *args, **kwargs: all_texts)

    def fake_rerank(query, hits, top_k, model_config=None):
        ordered = sorted(hits, key=lambda hit: "HR-01-001" in hit["entity"]["text"], reverse=True)
        return ordered[:top_k]

    monkeypatch.setattr(rag, "rerank_hits", fake_rerank)

    _, contexts = rag.retrieve_contexts("入职前需要完成哪些核验和承诺？", kb_id=9, top_k=1)

    assert contexts[0]["chunk_id"] == 1


def test_contexts_hide_internal_stored_document_names():
    from app.core import rag

    contexts = rag.build_context([
        {
            "distance": 0.9,
            "entity": {
                "source": "员工手册.docx",
                "text": "来源：doc_133.docx > 入职管理 > HR-01-001\n原文见 doc_133.docx。",
                "doc_id": "133",
                "kb_id": "14",
                "chunk_id": 1,
            },
        }
    ])

    assert "doc_133.docx" not in contexts[0]["text"]
    assert "员工手册.docx" in contexts[0]["text"]

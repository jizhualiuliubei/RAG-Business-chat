from app.services.evaluation_service import run_retrieval_evaluation, summarize_catalog


def test_summarize_catalog_counts_all_categories():
    summary = summarize_catalog()

    assert summary["case_count"] == 52
    assert summary["categories"]["A"]["case_count"] == 18
    assert summary["categories"]["B"]["case_count"] == 8
    assert summary["categories"]["C"]["case_count"] == 8
    assert summary["categories"]["D"]["case_count"] == 8
    assert summary["categories"]["E"]["case_count"] == 10


def test_summarize_catalog_exposes_interview_friendly_labels():
    summary = summarize_catalog()

    assert summary["categories"]["A"]["label"] == "直接事实检索"
    assert summary["categories"]["E"]["label"] == "未覆盖问题拒答"


def test_metric_definitions_are_truthful_and_reproducible():
    summary = summarize_catalog()
    metrics = summary["metrics"]

    assert "retrieval_pass_rate" in metrics
    assert "section_hit_rate" in metrics
    assert "refusal_pass_rate" in metrics
    assert metrics["retrieval_pass_rate"]["source"] == "backend/scripts/eval_rag.py"


def test_retrieval_evaluation_skips_refusal_cases_in_retrieval_mode(monkeypatch):
    calls = []

    def fake_retrieve_contexts(question, kb_id, top_k):
        calls.append(kb_id)
        return [], []

    monkeypatch.setattr("app.services.evaluation_service.rag.retrieve_contexts", fake_retrieve_contexts)

    result = run_retrieval_evaluation(kb_id=[1, 2])

    assert calls
    assert calls[0] == [1, 2]
    assert result["evaluated_case_count"] == 42
    assert result["skipped_case_count"] == 10
    assert result["categories"]["E"]["skipped_count"] == 10
    assert result["categories"]["E"]["pass_rate"] is None
    assert all(row["skipped"] for row in result["cases"] if row["category"] == "E")

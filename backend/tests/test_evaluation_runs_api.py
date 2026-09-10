import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base, get_db
from app.main import app
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services import auth_service


def _client(monkeypatch):
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
            if not db.query(KnowledgeBase).first():
                db.add(KnowledgeBase(name="企业规模评测库"))
                db.commit()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _admin_headers(client):
    captcha = client.get("/api/auth/captcha").json()
    auth_service._CAPTCHA_STORE[captcha["captcha_id"]]["answer"] = "1"
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "admin123",
            "captcha_id": captcha["captcha_id"],
            "captcha_answer": "1",
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_evaluation_datasets_expose_enterprise_scale_catalog(monkeypatch):
    client = _client(monkeypatch)
    try:
        response = client.get("/api/evaluation-datasets", headers=_admin_headers(client))
        data = response.json()

        assert response.status_code == 200
        assert data["items"][0]["version"] == "enterprise_scale_360_v1"
        assert data["items"][0]["recommended_kb_name"] == "企业规模评测库"
        assert data["items"][0]["case_count"] == 360
        versions = {item["version"] for item in data["items"]}
        assert "enterprise_scale_v1" in versions
        large = next(item for item in data["items"] if item["version"] == "enterprise_scale_360_v1")
        assert large["case_count"] == 360
        assert "F" in large["categories"]
    finally:
        app.dependency_overrides.clear()


def test_answer_coverage_accepts_semantic_paraphrase_but_keeps_hard_tokens():
    from app.services.evaluation_run_service import _answer_coverage

    answer = "依据 HR-01-001，新员工到岗前需要完成身份核验、学历核验，并签署保密承诺。"
    points = (
        "HR-01-001",
        "新员工入职前须完成身份核验、学历核验和保密承诺签署。",
    )

    assert _answer_coverage(answer, points) == 100
    assert _answer_coverage(answer.replace("HR-01-001", "HR-01-002"), points) == 50


def test_refusal_scoring_accepts_common_no_evidence_phrasing():
    from app.services.evaluation_run_service import _refusal_passed

    assert _refusal_passed("现有制度资料中未找到相关规定，不能编造具体金额。")
    assert _refusal_passed("知识库没有规定供应商必须购买指定品牌的办公设备。")
    assert _refusal_passed("资料未提及员工子女入学名额或学费报销，无法确认该政策。")
    assert _refusal_passed("检索到的制度依据不支持“永久远程办公资格”这个结论。")


def test_refusal_scoring_rejects_plain_business_answer_without_no_evidence():
    from app.services.evaluation_run_service import _refusal_passed

    assert not _refusal_passed("公司规定供应商必须购买指定品牌的办公设备。")
    assert not _refusal_passed("员工子女入学名额每年统一分配一次。")
    assert not _refusal_passed("知识库未明确写明，但公司可以为所有员工提供永久远程办公资格。")
    assert not _refusal_passed("现有资料未提及细则，不过员工股票期权行权价格固定为每股1元。")


def test_negative_evaluation_answer_is_guarded_without_model_call(monkeypatch):
    from app.core import rag

    def fail_model_call():
        raise AssertionError("negative evaluation cases should not call the model")

    monkeypatch.setattr(rag, "get_model", fail_model_call)

    result = rag.generate_evaluation_answer(
        "在《员工手册》相关知识库中，是否规定供应商必须购买指定品牌的办公设备？",
        [{"source": "员工手册.docx", "text": "来源：员工手册.docx > 入职管理 > HR-01-001\n员工入职需完成材料审核。"}],
        {"negative_case": True},
    )

    assert result["answer"].startswith("现有知识库未提供依据，不能确认该说法。")
    assert "供应商必须购买指定品牌的办公设备" in result["answer"]
    assert result["usage"]["total_tokens"] == 0


def test_evaluation_prompt_has_strict_negative_case_instruction():
    from app.core.rag import EVALUATION_SYSTEM_PROMPT, RAG_SYSTEM_PROMPT

    assert "【是否未覆盖拒答题】为 True" in EVALUATION_SYSTEM_PROMPT
    assert "现有知识库未提供依据，不能确认该说法" in EVALUATION_SYSTEM_PROMPT
    assert "不得把相似制度扩展成肯定结论" in EVALUATION_SYSTEM_PROMPT
    assert "即使检索到相似制度" in RAG_SYSTEM_PROMPT
    assert "不得用通用知识或相似条款补全答案" in RAG_SYSTEM_PROMPT


def test_required_citations_are_loaded_for_cross_document_cases():
    from app.services.enterprise_evaluation_dataset import load_full_cases

    case = next(item for item in load_full_cases() if item.case_id == "ES-01-C-001")

    assert case.required_citations == ("员工手册.docx#薪酬发放#HR-06-002", "薪酬绩效制度.xlsx#薪酬发放#PAY-07-001")


def test_cross_document_failure_reports_missing_secondary_evidence():
    from app.services.enterprise_evaluation_dataset import EvaluationCase
    from app.services.evaluation_run_service import _evidence_diagnostics, _failure_reason

    case = EvaluationCase(
        "T-CROSS-001",
        "C",
        "工资发放日遇节假日时，员工手册和薪酬绩效制度应如何共同引用？",
        "员工手册.docx",
        "薪酬发放",
        "HR-06-002",
        (),
        ("主证据HR-06-002", "辅助证据PAY-07-001"),
        required_citations=("员工手册.docx#薪酬发放#HR-06-002", "薪酬绩效制度.xlsx#薪酬发放#PAY-07-001"),
    )
    contexts = [
        {"source": "员工手册.docx", "text": "来源：员工手册.docx > 薪酬发放 > HR-06-002\n工资发放日遇节假日提前。"},
    ]

    diagnostics = _evidence_diagnostics(case, contexts)
    reason = _failure_reason(case, True, True, True, 100, True, False, diagnostics)

    assert diagnostics["required_citation_hit_rate"] == 50
    assert diagnostics["missing_required_citations"] == ["薪酬绩效制度.xlsx#薪酬发放#PAY-07-001"]
    assert reason == "辅助证据未命中：薪酬绩效制度.xlsx#薪酬发放#PAY-07-001"


def test_evaluate_case_augments_missing_required_citation_from_kb_texts(monkeypatch):
    from app.services.enterprise_evaluation_dataset import EvaluationCase
    from app.services.evaluation_run_service import _evaluate_case
    import app.services.evaluation_run_service as run_service

    case = EvaluationCase(
        "T-CROSS-002",
        "C",
        "工资发放日遇节假日时，员工手册和薪酬绩效制度应如何共同引用？",
        "员工手册.docx",
        "薪酬发放",
        "HR-06-002",
        (),
        ("主证据HR-06-002", "辅助证据PAY-07-001"),
        required_citations=("员工手册.docx#薪酬发放#HR-06-002", "薪酬绩效制度.xlsx#薪酬发放#PAY-07-001"),
    )

    monkeypatch.setattr(run_service.rag, "retrieve_contexts", lambda question, kb_id, top_k: ([], [
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 薪酬发放 > HR-06-002\n工资发放日遇节假日提前。",
            "score": 0.8,
        }
    ]))
    monkeypatch.setattr(run_service.milvus_store, "list_all_texts", lambda kb_id: [
        {
            "source": "薪酬绩效制度.xlsx",
            "text": "来源：薪酬绩效制度.xlsx > 薪酬发放 > PAY-07-001\n工资日遇节假日提前至最近一个工作日发放。",
            "kb_id": "9",
        }
    ])

    result = _evaluate_case(case, 9, "retrieval")

    assert result["evidence_diagnostics"]["required_citation_hit_rate"] == 100
    assert any(ctx["source"] == "薪酬绩效制度.xlsx" for ctx in result["contexts"])
    assert result["failure_reason"] == ""


def test_full_evaluation_generates_answer_from_augmented_contexts(monkeypatch):
    from app.services.enterprise_evaluation_dataset import EvaluationCase
    from app.services.evaluation_run_service import _evaluate_case
    import app.services.evaluation_run_service as run_service

    case = EvaluationCase(
        "T-CROSS-003",
        "C",
        "工资发放日遇节假日时，员工手册和薪酬绩效制度应如何共同引用？",
        "员工手册.docx",
        "薪酬发放",
        "HR-06-002",
        (),
        ("主证据HR-06-002", "辅助证据PAY-07-001"),
        required_citations=("员工手册.docx#薪酬发放#HR-06-002", "薪酬绩效制度.xlsx#薪酬发放#PAY-07-001"),
    )
    captured = {}

    monkeypatch.setattr(run_service.rag, "retrieve_contexts", lambda question, kb_id, top_k: ([], [
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 薪酬发放 > HR-06-002\n工资发放日遇节假日提前。",
            "score": 0.8,
        }
    ]))
    monkeypatch.setattr(run_service.milvus_store, "list_all_texts", lambda kb_id: [
        {
            "source": "薪酬绩效制度.xlsx",
            "text": "来源：薪酬绩效制度.xlsx > 薪酬发放 > PAY-07-001\n工资日遇节假日提前至最近一个工作日发放。",
            "kb_id": "9",
        }
    ])
    monkeypatch.setattr(run_service.qa_service, "answer_question", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full evaluation must not re-retrieve contexts")))

    def fake_generate(question, contexts, case_payload):
        captured["contexts"] = contexts
        captured["case_payload"] = case_payload
        return {
            "answer": "主证据 HR-06-002 与辅助证据 PAY-07-001 均说明工资日遇节假日提前发放。",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "model_name": "test-model",
        }

    monkeypatch.setattr(run_service.rag, "generate_evaluation_answer", fake_generate)

    result = _evaluate_case(case, 9, "full")

    assert len(captured["contexts"]) == 2
    assert captured["case_payload"]["category"] == "C"
    assert result["answer_coverage"] == 100
    assert result["citation_passed"] is True


def test_serialize_run_backfills_missing_evidence_metrics_from_saved_cases(monkeypatch):
    import app.services.evaluation_dataset_service as dataset_service
    import app.services.evaluation_run_service as run_service
    from app.database import Base
    from app.models.evaluation import EvaluationCaseResult, EvaluationRun
    from app.services.enterprise_evaluation_dataset import EvaluationCase

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    case = EvaluationCase(
        "T-CROSS-004",
        "C",
        "工资发放日遇节假日时，员工手册和薪酬绩效制度应如何共同引用？",
        "员工手册.docx",
        "薪酬发放",
        "HR-06-002",
        (),
        ("主证据HR-06-002", "辅助证据PAY-07-001"),
        required_citations=("员工手册.docx#薪酬发放#HR-06-002", "薪酬绩效制度.xlsx#薪酬发放#PAY-07-001"),
    )
    monkeypatch.setattr(dataset_service, "load_cases", lambda db, dataset_version: [case])

    with TestingSessionLocal() as db:
        run = EvaluationRun(
            name="旧任务缺少证据指标",
            kb_id="9",
            dataset_version="enterprise_scale_360_v1",
            mode="full",
            status="success",
            summary_json='{"pass_rate":58.33}',
        )
        db.add(run)
        db.flush()
        db.add(EvaluationCaseResult(
            run_id=run.id,
            case_id=case.case_id,
            category=case.category,
            question=case.question,
            expected_doc=case.expected_doc,
            expected_section=case.expected_section,
            expected_clause=case.expected_clause,
            retrieved_contexts_json='[{"source":"员工手册.docx","text":"来源：员工手册.docx > 薪酬发放 > HR-06-002\\n工资发放日遇节假日提前。"}]',
        ))
        db.commit()
        db.refresh(run)

        serialized = run_service.serialize_run(run, db)

    assert serialized["summary"]["pass_rate"] == 58.33
    assert serialized["summary"]["required_citation_hit_rate"] == 50
    assert serialized["summary"]["primary_evidence_hit_rate"] == 100
    assert serialized["summary"]["secondary_evidence_hit_rate"] == 0


def test_summary_counts_failure_reasons_only_for_failed_cases():
    from app.services.enterprise_evaluation_dataset import EvaluationCase
    from app.services.evaluation_run_service import _summarize

    case = EvaluationCase("T-RANK-001", "A", "问题", "员工手册.docx", "入职管理", "HR-01-001", (), ("HR-01-001",))
    summary = _summarize([
        {
            "case": case,
            "passed": True,
            "skipped": False,
            "top1_hit": False,
            "top3_hit": True,
            "top5_hit": True,
            "section_hit": True,
            "answer_coverage": 100,
            "citation_passed": True,
            "refusal_passed": False,
            "latency_ms": 10,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "failure_reason": "排序偏弱：期望来源已进入 Top-5 但不是 Top-1",
            "evidence_diagnostics": {"required_citation_hit_rate": None},
        }
    ])

    assert summary["pass_rate"] == 100
    assert summary["failure_reasons"] == {}


def test_report_doc_lines_ignore_skipped_retrieval_refusal_cases():
    from app.services.evaluation_run_service import _report_doc_lines

    lines = _report_doc_lines([
        {"expected_doc": "员工手册.docx", "passed": True, "skipped": False},
        {"expected_doc": "员工手册.docx", "passed": True, "skipped": False},
        {"expected_doc": "员工手册.docx", "passed": False, "skipped": True},
    ])

    assert lines[-1] == "| 员工手册.docx | 2 | 2 | 100.0% |"


def test_evaluation_job_records_case_level_engineering_failures(monkeypatch):
    import app.services.evaluation_dataset_service as dataset_service
    import app.services.evaluation_run_service as run_service
    from app.database import Base
    from app.models.evaluation import EvaluationCaseResult, EvaluationRun
    from app.services.enterprise_evaluation_dataset import EvaluationCase

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(run_service, "SessionLocal", TestingSessionLocal)

    with TestingSessionLocal() as db:
        run = EvaluationRun(
            name="工程失败复现",
            kb_id="1",
            dataset_version="enterprise_scale_v1",
            mode="full",
            status="running",
            summary_json='{"progress":{"total":2,"completed":0,"percent":0,"status":"等待开始","current_case":""}}',
        )
        db.add(run)
        db.commit()
        run_id = run.id

    try:
        cases = [
            EvaluationCase("T-FAIL-001", "A", "第一题", "员工手册.docx", "入职管理", "HR-01-001", (), ("HR-01-001",)),
            EvaluationCase("T-OK-002", "A", "第二题", "员工手册.docx", "入职管理", "HR-01-002", (), ("HR-01-002",)),
        ]

        def fake_eval(case, kb_scope, mode):
            if case.case_id == "T-FAIL-001":
                raise RuntimeError("embedding api blocked")
            return {
                "case": case,
                "contexts": [{"source": "员工手册.docx", "text": "入职管理 HR-01-002", "score": 0.9}],
                "answer": "依据 HR-01-002 回答。",
                "citations": [{"source": "员工手册.docx"}],
                "passed": True,
                "skipped": False,
                "top1_hit": True,
                "top3_hit": True,
                "top5_hit": True,
                "section_hit": True,
                "answer_coverage": 100,
                "citation_passed": True,
                "refusal_passed": False,
                "latency_ms": 5,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "failure_reason": "",
                "fix_suggestion": "",
            }

        monkeypatch.setattr(dataset_service, "load_cases", lambda db, version: cases)
        monkeypatch.setattr(run_service, "_evaluate_case", fake_eval)
        run_service.run_evaluation_job(run_id)

        with TestingSessionLocal() as db:
            run = db.get(EvaluationRun, run_id)
            results = db.query(EvaluationCaseResult).filter(EvaluationCaseResult.run_id == run_id).order_by(EvaluationCaseResult.case_id).all()

            assert run.status == "success"
            assert len(results) == 2
            assert results[0].passed == 0
            assert results[0].failure_reason.startswith("模型/API失败：embedding api blocked")
            assert run.summary_json
            assert '"completed": 2' in run.summary_json
    finally:
        app.dependency_overrides.clear()


def test_evaluation_job_stops_when_run_is_canceling(monkeypatch):
    import app.services.evaluation_dataset_service as dataset_service
    import app.services.evaluation_run_service as run_service
    from app.database import Base
    from app.models.evaluation import EvaluationCaseResult, EvaluationRun
    from app.services.enterprise_evaluation_dataset import EvaluationCase

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(run_service, "SessionLocal", TestingSessionLocal)

    with TestingSessionLocal() as db:
        run = EvaluationRun(
            name="取消评测复现",
            kb_id="1",
            dataset_version="enterprise_scale_v1",
            mode="retrieval",
            status="running",
            summary_json='{"progress":{"total":2,"completed":0,"percent":0,"status":"等待开始","current_case":""}}',
        )
        db.add(run)
        db.commit()
        run_id = run.id

    cases = [
        EvaluationCase("T-001", "A", "第一题", "员工手册.docx", "入职管理", "HR-01-001", (), ("HR-01-001",)),
        EvaluationCase("T-002", "A", "第二题", "员工手册.docx", "入职管理", "HR-01-002", (), ("HR-01-002",)),
    ]

    def fake_eval(case, kb_scope, mode):
        with TestingSessionLocal() as db:
            run = db.get(EvaluationRun, run_id)
            run.status = "canceling"
            db.commit()
        return {
            "case": case,
            "contexts": [{"source": "员工手册.docx", "text": case.expected_clause or "", "score": 0.9}],
            "answer": "",
            "citations": [],
            "passed": True,
            "skipped": False,
            "top1_hit": True,
            "top3_hit": True,
            "top5_hit": True,
            "section_hit": True,
            "answer_coverage": 100,
            "citation_passed": True,
            "refusal_passed": False,
            "latency_ms": 5,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "failure_reason": "",
            "fix_suggestion": "",
        }

    monkeypatch.setattr(dataset_service, "load_cases", lambda db, version: cases)
    monkeypatch.setattr(run_service, "_evaluate_case", fake_eval)
    run_service.run_evaluation_job(run_id)

    with TestingSessionLocal() as db:
        run = db.get(EvaluationRun, run_id)
        results = db.query(EvaluationCaseResult).filter(EvaluationCaseResult.run_id == run_id).all()

        assert run.status == "canceled"
        assert len(results) == 1
        assert '"completed": 1' in run.summary_json
        assert "已中断" in run.summary_json


def test_admin_can_cancel_running_evaluation_run(monkeypatch):
    import app.services.evaluation_run_service as run_service

    monkeypatch.setattr(run_service, "run_evaluation_job", lambda run_id: None)
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        created = client.post(
            "/api/evaluation-runs",
            json={"kb_id": 1, "dataset_version": "enterprise_scale_v1", "mode": "full"},
            headers=headers,
        )
        run_id = created.json()["id"]

        canceled = client.post(f"/api/evaluation-runs/{run_id}/cancel", headers=headers)

        assert canceled.status_code == 200
        assert canceled.json()["status"] == "canceling"
        assert canceled.json()["summary"]["progress"]["status"] == "正在中断"
    finally:
        app.dependency_overrides.clear()


def test_stale_running_evaluation_runs_are_marked_interrupted():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models.evaluation import EvaluationRun
    from app.services.evaluation_run_service import mark_stale_running_runs_interrupted

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with TestingSessionLocal() as db:
        run = EvaluationRun(
            name="重启中断测试",
            kb_id="1",
            dataset_version="enterprise_scale_v1",
            mode="full",
            status="running",
            summary_json='{"progress":{"total":360,"completed":12,"percent":3,"status":"运行中"}}',
        )
        db.add(run)
        db.commit()

        count = mark_stale_running_runs_interrupted(db)
        db.refresh(run)

        assert count == 1
        assert run.status == "canceled"
        assert run.finished_at is not None
        assert "服务重启导致评测任务中断" in run.error_message
        assert "已中断，请重新评测" in run.summary_json


def test_admin_can_create_persistent_retrieval_run(monkeypatch):
    import app.services.evaluation_run_service as run_service

    def fake_eval(case, kb_scope, mode):
        return {
            "case": case,
            "contexts": [{"source": case.expected_doc or "无", "text": f"{case.expected_section or ''}{case.expected_clause or ''}", "score": 0.9}],
            "answer": "",
            "citations": [],
            "passed": case.category != "E",
            "skipped": case.category == "E",
            "top1_hit": case.category != "E",
            "top3_hit": case.category != "E",
            "top5_hit": case.category != "E",
            "section_hit": case.category != "E",
            "answer_coverage": 100 if case.category != "E" else 0,
            "citation_passed": case.category != "E",
            "refusal_passed": False,
            "latency_ms": 5,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "failure_reason": "",
            "fix_suggestion": "",
        }

    monkeypatch.setattr(run_service, "_evaluate_case", fake_eval)
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        response = client.post(
            "/api/evaluation-runs",
            json={"kb_id": 1, "dataset_version": "enterprise_scale_v1", "mode": "retrieval"},
            headers=headers,
        )
        data = response.json()

        assert response.status_code == 200
        assert data["dataset_version"] == "enterprise_scale_v1"
        assert data["status"] in {"running", "success"}
        assert data["summary"]["progress"]["total"] >= 1

        runs = client.get("/api/evaluation-runs", headers=headers).json()["items"]
        assert runs[0]["summary"]["progress"]["percent"] == 100
        assert runs[0]["summary"]["top5_doc_hit_rate"] == 100
        cases = client.get(f"/api/evaluation-runs/{runs[0]['id']}/cases", headers=headers).json()["items"]
        assert cases
        report = client.post(f"/api/evaluation-runs/{runs[0]['id']}/report", headers=headers).json()
        assert "Top-5 文档命中率" in report["markdown"]
        assert "主证据命中率" in report["markdown"]
        assert "失败原因分布" in report["markdown"]
    finally:
        app.dependency_overrides.clear()


def test_admin_can_delete_evaluation_run_with_cases(monkeypatch):
    import app.services.evaluation_run_service as run_service

    def fake_eval(case, kb_scope, mode):
        return {
            "case": case,
            "contexts": [{"source": case.expected_doc or "无", "text": case.expected_clause or "", "score": 0.9}],
            "answer": "",
            "citations": [],
            "passed": True,
            "skipped": False,
            "top1_hit": True,
            "top3_hit": True,
            "top5_hit": True,
            "section_hit": True,
            "answer_coverage": 100,
            "citation_passed": True,
            "refusal_passed": False,
            "latency_ms": 5,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "failure_reason": "",
            "fix_suggestion": "",
        }

    monkeypatch.setattr(run_service, "_evaluate_case", fake_eval)
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        response = client.post(
            "/api/evaluation-runs",
            json={"kb_id": 1, "dataset_version": "enterprise_scale_v1", "mode": "retrieval"},
            headers=headers,
        )
        assert response.status_code == 200
        run_id = client.get("/api/evaluation-runs", headers=headers).json()["items"][0]["id"]
        assert client.get(f"/api/evaluation-runs/{run_id}/cases", headers=headers).json()["items"]

        deleted = client.delete(f"/api/evaluation-runs/{run_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        assert all(item["id"] != run_id for item in client.get("/api/evaluation-runs", headers=headers).json()["items"])
        assert client.get(f"/api/evaluation-runs/{run_id}/cases", headers=headers).json()["items"] == []
        assert client.get(f"/api/evaluation-runs/{run_id}", headers=headers).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_admin_can_batch_delete_evaluation_runs(monkeypatch):
    import app.services.evaluation_run_service as run_service

    monkeypatch.setattr(run_service, "_evaluate_case", lambda case, kb_scope, mode: {
        "case": case,
        "contexts": [{"source": case.expected_doc or "无", "text": case.expected_clause or "", "score": 0.9}],
        "answer": "",
        "citations": [],
        "passed": True,
        "skipped": False,
        "top1_hit": True,
        "top3_hit": True,
        "top5_hit": True,
        "section_hit": True,
        "answer_coverage": 100,
        "citation_passed": True,
        "refusal_passed": False,
        "latency_ms": 5,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "failure_reason": "",
        "fix_suggestion": "",
    })
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        for _ in range(2):
            response = client.post(
                "/api/evaluation-runs",
                json={"kb_id": 1, "dataset_version": "enterprise_scale_v1", "mode": "retrieval"},
                headers=headers,
            )
            assert response.status_code == 200
        run_ids = [item["id"] for item in client.get("/api/evaluation-runs", headers=headers).json()["items"][:2]]

        deleted = client.request("DELETE", "/api/evaluation-runs", json={"run_ids": run_ids}, headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["deleted_count"] == 2
        remaining = client.get("/api/evaluation-runs", headers=headers).json()["items"]
        assert all(item["id"] not in run_ids for item in remaining)
        for run_id in run_ids:
            assert client.get(f"/api/evaluation-runs/{run_id}/cases", headers=headers).json()["items"] == []
    finally:
        app.dependency_overrides.clear()


def test_admin_can_create_360_full_run_and_download_dataset(monkeypatch):
    import app.services.evaluation_run_service as run_service

    def fake_eval(case, kb_scope, mode):
        is_negative = getattr(case, "negative_case", False)
        return {
            "case": case,
            "contexts": [] if is_negative else [{"source": case.expected_doc or "无", "text": f"{case.expected_section or ''}{case.expected_clause or ''}", "score": 0.9}],
            "answer": "现有知识库未提供依据，不能编造结论。" if is_negative else "已覆盖标准答案要点",
            "citations": [] if is_negative else [{"source": case.expected_doc or "无"}],
            "passed": True,
            "skipped": False,
            "top1_hit": not is_negative,
            "top3_hit": not is_negative,
            "top5_hit": not is_negative,
            "section_hit": not is_negative,
            "answer_coverage": 100,
            "citation_passed": not is_negative,
            "refusal_passed": is_negative,
            "latency_ms": 5,
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
            "failure_reason": "",
            "fix_suggestion": "",
        }

    monkeypatch.setattr(run_service, "_evaluate_case", fake_eval)
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)

        csv_download = client.get(
            "/api/evaluation-datasets/enterprise_scale_360_v1/download?format=csv",
            headers=headers,
        )
        assert csv_download.status_code == 200
        assert "text/csv" in csv_download.headers["content-type"]
        assert "ES-01-A-001" in csv_download.text

        md_download = client.get(
            "/api/evaluation-datasets/enterprise_scale_360_v1/download?format=md",
            headers=headers,
        )
        assert md_download.status_code == 200
        assert "text/markdown" in md_download.headers["content-type"]
        assert "企业规模知识库评测题集" in md_download.text

        response = client.post(
            "/api/evaluation-runs",
            json={"kb_id": 1, "dataset_version": "enterprise_scale_360_v1", "mode": "full"},
            headers=headers,
        )
        assert response.status_code == 200

        runs = client.get("/api/evaluation-runs", headers=headers).json()["items"]
        assert runs[0]["dataset_version"] == "enterprise_scale_360_v1"
        assert runs[0]["summary"]["case_count"] == 360
        assert runs[0]["summary"]["refusal_pass_rate"] == 100
        cases = client.get(f"/api/evaluation-runs/{runs[0]['id']}/cases?category=F", headers=headers).json()["items"]
        assert cases
    finally:
        app.dependency_overrides.clear()


def test_uploaded_dataset_must_be_approved_before_running(monkeypatch):
    import app.services.evaluation_run_service as run_service

    monkeypatch.setattr(run_service, "_evaluate_case", lambda case, kb_scope, mode: {
        "case": case,
        "contexts": [{"source": case.expected_doc or "未覆盖", "text": f"{case.expected_section or ''}{case.expected_clause or ''}", "score": 0.9}],
        "answer": "已覆盖标准答案要点",
        "citations": [{"source": case.expected_doc or "未覆盖"}],
        "passed": True,
        "skipped": False,
        "top1_hit": bool(case.expected_doc),
        "top3_hit": bool(case.expected_doc),
        "top5_hit": bool(case.expected_doc),
        "section_hit": True,
        "answer_coverage": 100,
        "citation_passed": True,
        "refusal_passed": not bool(case.expected_doc),
        "latency_ms": 5,
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
        "failure_reason": "",
        "fix_suggestion": "",
    })
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        upload = client.post(
            "/api/evaluation-datasets/upload",
            headers=headers,
            files={"file": ("my-rag-cases.md", b"# custom cases\nquestion: refund policy?", "text/markdown")},
        )
        assert upload.status_code == 200
        dataset = upload.json()
        assert dataset["source_type"] == "uploaded"
        assert dataset["status"] == "draft"
        assert dataset["case_count"] >= 1

        rejected = client.post(
            "/api/evaluation-runs",
            json={"kb_id": 1, "dataset_version": dataset["version"], "mode": "full"},
            headers=headers,
        )
        assert rejected.status_code == 400
        assert "审核" in rejected.json()["detail"]

        detail = client.get(f"/api/evaluation-datasets/{dataset['version']}", headers=headers).json()
        assert detail["items"][0]["case_id"]

        approved = client.post(f"/api/evaluation-datasets/{dataset['version']}/approve", headers=headers)
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        run = client.post(
            "/api/evaluation-runs",
            json={"kb_id": 1, "dataset_version": dataset["version"], "mode": "full"},
            headers=headers,
        )
        assert run.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_ai_generated_dataset_is_draft_and_downloadable(monkeypatch):
    import app.services.evaluation_dataset_service as dataset_service

    monkeypatch.setattr(dataset_service.milvus_store, "list_all_texts", lambda kb_id: [
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 入职管理 > HR-01-001\nHR-01-001 入职材料应在到岗前完成提交，直属经理和 HRBP 共同确认。",
            "kb_id": str(kb_id),
            "chunk_id": 11,
        }
    ])
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        response = client.post(
            "/api/evaluation-datasets/generate",
            json={"kb_id": 1, "case_count": 6},
            headers=headers,
        )
        assert response.status_code == 200
        dataset = response.json()
        assert dataset["source_type"] == "ai_generated"
        assert dataset["status"] == "draft"
        assert dataset["review_status"] == "pending_review"
        assert "AI 辅助生成草稿题集" in dataset["name"]
        assert "辅助出题草稿" in dataset["description"]
        assert dataset["case_count"] == 6
        assert dataset["recommended_kb_name"] == "企业规模评测库"

        detail = client.get(f"/api/evaluation-datasets/{dataset['version']}", headers=headers).json()
        assert detail["items"][0]["expected_doc"] == "员工手册.docx"
        assert detail["items"][0]["expected_section"] == "入职管理"
        assert detail["items"][0]["expected_clause"] == "HR-01-001"
        assert detail["items"][0]["evidence_text"].startswith("来源：员工手册.docx")
        assert detail["items"][0]["evidence_hash"]
        assert detail["items"][0]["source_chunk_id"] == 11
        assert detail["items"][0]["review_status"] == "pending_review"
        assert "入职材料" in detail["items"][0]["question"]

        csv_download = client.get(
            f"/api/evaluation-datasets/{dataset['version']}/download?format=csv",
            headers=headers,
        )
        assert csv_download.status_code == 200
        assert "case_id" in csv_download.text
        assert "evidence_hash" in csv_download.text

        md_download = client.get(
            f"/api/evaluation-datasets/{dataset['version']}/download?format=md",
            headers=headers,
        )
        assert md_download.status_code == 200
        assert "评测题集" in md_download.text
        assert "题集来源" in md_download.text
        assert "证据快照" in md_download.text
    finally:
        app.dependency_overrides.clear()


def test_ai_generated_cross_document_case_locks_multiple_required_citations(monkeypatch):
    import app.services.evaluation_dataset_service as dataset_service

    monkeypatch.setattr(dataset_service.milvus_store, "list_all_texts", lambda kb_id: [
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 薪酬发放 > HR-06-002\n工资发放日遇节假日时应提前处理。",
            "kb_id": str(kb_id),
            "chunk_id": 1,
        },
        {
            "source": "薪酬绩效制度.xlsx",
            "text": "来源：薪酬绩效制度.xlsx > 薪酬发放 > PAY-07-001\n工资日遇节假日提前至最近一个工作日发放。",
            "kb_id": str(kb_id),
            "chunk_id": 2,
        },
    ])
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        response = client.post(
            "/api/evaluation-datasets/generate",
            json={"kb_id": 1, "case_count": 6, "include_negative_cases": True},
            headers=headers,
        )
        assert response.status_code == 200
        detail = client.get(f"/api/evaluation-datasets/{response.json()['version']}", headers=headers).json()
        c_case = next(item for item in detail["items"] if item["category"] == "C")
        assert len(c_case["required_citations"]) >= 2
        assert "员工手册.docx#薪酬发放#HR-06-002" in c_case["required_citations"]
        assert "薪酬绩效制度.xlsx#薪酬发放#PAY-07-001" in c_case["required_citations"]
        f_case = next(item for item in detail["items"] if item["category"] == "F")
        assert f_case["negative_case"] is True
        assert not f_case["expected_clause"]
        assert f_case["evidence_hash"]
    finally:
        app.dependency_overrides.clear()


def test_approving_ai_generated_dataset_records_human_review_metadata(monkeypatch):
    import app.services.evaluation_dataset_service as dataset_service
    import app.services.evaluation_run_service as run_service

    def fake_eval(case, kb_scope, mode):
        return {
            "case": case,
            "contexts": [{"source": case.expected_doc or "无", "text": case.expected_clause or "", "score": 0.9}],
            "answer": "依据知识库证据回答。",
            "citations": [{"source": case.expected_doc or "无"}],
            "passed": True,
            "skipped": False,
            "top1_hit": True,
            "top3_hit": True,
            "top5_hit": True,
            "section_hit": True,
            "answer_coverage": 100,
            "citation_passed": True,
            "refusal_passed": False,
            "latency_ms": 5,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "failure_reason": "",
            "fix_suggestion": "",
        }

    monkeypatch.setattr(dataset_service.milvus_store, "list_all_texts", lambda kb_id: [
        {
            "source": "员工手册.docx",
            "text": "来源：员工手册.docx > 入职管理 > HR-01-001\nHR-01-001 入职材料应在到岗前完成提交。",
            "kb_id": str(kb_id),
            "chunk_id": 1,
        }
    ])
    monkeypatch.setattr(run_service, "_evaluate_case", fake_eval)
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        dataset = client.post(
            "/api/evaluation-datasets/generate",
            json={"kb_id": 1, "case_count": 3},
            headers=headers,
        ).json()

        approved = client.post(f"/api/evaluation-datasets/{dataset['version']}/approve", headers=headers)
        assert approved.status_code == 200
        body = approved.json()
        assert body["status"] == "approved"
        assert body["review_status"] == "human_reviewed"
        assert body["reviewed_by"] == "系统管理员"
        assert body["reviewed_at"]

        report_case = client.get(f"/api/evaluation-datasets/{dataset['version']}", headers=headers).json()
        assert report_case["review_status"] == "human_reviewed"

        run = client.post(
            "/api/evaluation-runs",
            json={"kb_id": 1, "dataset_version": dataset["version"], "mode": "retrieval"},
            headers=headers,
        ).json()
        report = client.post(f"/api/evaluation-runs/{run['id']}/report", headers=headers).json()
        assert "| 题集来源 | ai_generated |" in report["markdown"]
        assert "| 审核状态 | human_reviewed |" in report["markdown"]
        assert "| 审核人 | 系统管理员 |" in report["markdown"]
    finally:
        app.dependency_overrides.clear()


def test_admin_can_delete_custom_evaluation_dataset_but_not_builtin(monkeypatch):
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        upload = client.post(
            "/api/evaluation-datasets/upload",
            headers=headers,
            files={"file": ("my-rag-cases.md", b"# custom cases\nquestion: refund policy?", "text/markdown")},
        )
        dataset = upload.json()

        blocked = client.delete("/api/evaluation-datasets/enterprise_scale_360_v1", headers=headers)
        assert blocked.status_code == 400
        assert "内置" in blocked.json()["detail"]

        deleted = client.delete(f"/api/evaluation-datasets/{dataset['version']}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        missing = client.get(f"/api/evaluation-datasets/{dataset['version']}", headers=headers)
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_ai_generated_dataset_requires_kb_documents(monkeypatch):
    import app.services.evaluation_dataset_service as dataset_service

    monkeypatch.setattr(dataset_service.milvus_store, "list_all_texts", lambda kb_id: [])
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        response = client.post(
            "/api/evaluation-datasets/generate",
            json={"kb_id": 1, "case_count": 6},
            headers=headers,
        )
        assert response.status_code == 400
        assert "知识库暂无可生成题集的内容" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_uploaded_markdown_question_bank_imports_all_structured_cases(monkeypatch):
    client = _client(monkeypatch)
    try:
        headers = _admin_headers(client)
        md_bytes = (
            Path(__file__).resolve().parents[2]
            .joinpath("evaluation", "enterprise_scale", "企业规模知识库评测题集.md")
            .read_bytes()
        )
        upload = client.post(
            "/api/evaluation-datasets/upload",
            headers=headers,
            files={"file": ("企业规模知识库评测题集.md", md_bytes, "text/markdown")},
        )
        assert upload.status_code == 200
        dataset = upload.json()
        assert dataset["case_count"] == 360
        assert dataset["status"] == "draft"

        detail = client.get(f"/api/evaluation-datasets/{dataset['version']}", headers=headers)
        assert detail.status_code == 200
        cases = detail.json()["items"]
        assert cases[0]["case_id"] == "ES-01-A-001"
        assert cases[0]["expected_doc"] == "员工手册.docx"
        assert cases[-1]["case_id"] == "ES-12-F-002"
        assert cases[-1]["negative_case"] is True
    finally:
        app.dependency_overrides.clear()

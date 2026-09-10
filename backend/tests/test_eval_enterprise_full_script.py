import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_load_cases_reads_360_question_csv():
    from scripts import eval_enterprise_full

    path = Path(__file__).resolve().parents[2] / "evaluation" / "enterprise_scale" / "企业规模知识库评测题集.csv"
    cases = eval_enterprise_full.load_cases(path)

    assert len(cases) == 360
    assert cases[0]["case_id"] == "ES-01-A-001"
    assert cases[0]["expected_doc"] == "员工手册.docx"
    assert cases[0]["expected_clause"] == "HR-01-001"


def test_evaluate_case_scores_non_negative_case_with_injected_dependencies():
    from scripts import eval_enterprise_full

    case = {
        "case_id": "T-A-1",
        "category": "A 单文档事实检索",
        "question": "入职前需要做什么？",
        "expected_doc": "员工手册.docx",
        "expected_section": "入职管理",
        "expected_clause": "HR-01-001",
        "answer_points": "HR-01-001；身份核验",
        "negative_case": "false",
    }

    contexts = [
        {
            "source": "员工手册.docx",
            "text": "入职管理 HR-01-001 新员工入职前须完成身份核验。",
            "chunk_id": 1,
            "score": 0.91,
        }
    ]

    result = eval_enterprise_full.evaluate_case(
        case,
        kb_id=2,
        top_k=5,
        retrieve_fn=lambda question, kb_id, top_k: contexts,
        answer_fn=lambda question, kb_id: {
            "answer": "依据 HR-01-001，新员工入职前须完成身份核验。",
            "sources": contexts,
            "usage": {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18},
            "model_name": "fake-model",
        },
        retries=0,
        retry_sleep=0,
    )

    assert result["passed"] == "true"
    assert result["top1_doc_hit"] == "true"
    assert result["top5_doc_hit"] == "true"
    assert result["section_hit"] == "true"
    assert result["answer_points_coverage"] == "100"
    assert result["citation_passed"] == "true"
    assert result["failure_reason"] == ""


def test_cli_answer_coverage_accepts_paraphrased_points():
    from scripts import eval_enterprise_full

    answer = "依据 HR-01-001，新员工到岗前需要完成身份核验、学历核验，并签署保密承诺。"

    assert eval_enterprise_full._answer_coverage(
        answer,
        "HR-01-001；新员工入职前须完成身份核验、学历核验和保密承诺签署。",
    ) == 100


def test_evaluate_case_scores_negative_case_refusal_separately():
    from scripts import eval_enterprise_full

    case = {
        "case_id": "T-F-1",
        "category": "F 文档未覆盖与幻觉测试",
        "question": "公司是否提供不存在的补贴？",
        "expected_doc": "员工手册.docx",
        "expected_section": "",
        "expected_clause": "",
        "answer_points": "明确拒答或说明无依据；知识库没有提供该补贴",
        "negative_case": "true",
    }

    result = eval_enterprise_full.evaluate_case(
        case,
        kb_id=2,
        top_k=5,
        retrieve_fn=lambda question, kb_id, top_k: [],
        answer_fn=lambda question, kb_id: {
            "answer": "现有知识库未提供该补贴依据，不能编造结论。",
            "sources": [],
            "usage": {"total_tokens": 12},
            "model_name": "fake-model",
        },
        retries=0,
        retry_sleep=0,
    )

    assert result["passed"] == "true"
    assert result["top1_doc_hit"] == "not_applicable"
    assert result["section_hit"] == "not_applicable"
    assert result["refusal_passed"] == "true"
    assert result["failure_reason"] == ""


def test_summarize_results_separates_failures_and_negative_cases():
    from scripts import eval_enterprise_full

    results = [
        {"category": "A 单文档事实检索", "expected_doc": "员工手册.docx", "passed": "true", "negative_case": "false", "top1_doc_hit": "true", "top3_doc_hit": "true", "top5_doc_hit": "true", "section_hit": "true", "citation_passed": "true", "refusal_passed": "not_applicable", "answer_points_coverage": "100", "latency_ms": "100", "total_tokens": "20", "failure_reason": ""},
        {"category": "A 单文档事实检索", "expected_doc": "员工手册.docx", "passed": "false", "negative_case": "false", "top1_doc_hit": "false", "top3_doc_hit": "false", "top5_doc_hit": "false", "section_hit": "false", "citation_passed": "false", "refusal_passed": "not_applicable", "answer_points_coverage": "0", "latency_ms": "300", "total_tokens": "25", "failure_reason": "文档未命中：期望来源未进入 Top-5"},
        {"category": "F 文档未覆盖与幻觉测试", "expected_doc": "员工手册.docx", "passed": "true", "negative_case": "true", "top1_doc_hit": "not_applicable", "top3_doc_hit": "not_applicable", "top5_doc_hit": "not_applicable", "section_hit": "not_applicable", "citation_passed": "not_applicable", "refusal_passed": "true", "answer_points_coverage": "50", "latency_ms": "200", "total_tokens": "15", "failure_reason": ""},
    ]

    summary = eval_enterprise_full.summarize_results(results)

    assert summary["case_count"] == 3
    assert summary["pass_rate"] == 66.67
    assert summary["top5_doc_hit_rate"] == 50.0
    assert summary["answer_coverage_rate"] == 50
    assert summary["answer_coverage_pass_rate"] == 33.33
    assert summary["refusal_pass_rate"] == 100.0
    assert summary["failure_reasons"]["文档未命中：期望来源未进入 Top-5"] == 1


def test_write_outputs_creates_three_report_files(tmp_path):
    from scripts import eval_enterprise_full

    result = {
        "case_id": "T-A-1",
        "category": "A 单文档事实检索",
        "difficulty": "基础",
        "question": "入职前需要做什么？",
        "expected_doc": "员工手册.docx",
        "expected_section": "入职管理",
        "expected_clause": "HR-01-001",
        "answer": "依据 HR-01-001，需要身份核验。",
        "retrieved_top_k": '[{"source":"员工手册.docx"}]',
        "passed": "true",
        "failure_reason": "",
        "fix_suggestion": "",
        "negative_case": "false",
        "top1_doc_hit": "true",
        "top3_doc_hit": "true",
        "top5_doc_hit": "true",
        "section_hit": "true",
        "citation_passed": "true",
        "refusal_passed": "not_applicable",
        "answer_points_coverage": "100",
        "latency_ms": "100",
        "input_tokens": "1",
        "output_tokens": "2",
        "total_tokens": "3",
    }

    paths = eval_enterprise_full.write_outputs(
        [result],
        eval_enterprise_full.summarize_results([result]),
        tmp_path,
        environment_name="local",
    )

    assert paths["results_csv"].exists()
    assert paths["report_md"].exists()
    assert paths["failures_md"].exists()
    with paths["results_csv"].open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["case_id"] == "T-A-1"
    assert "企业规模知识库全量评测报告" in paths["report_md"].read_text(encoding="utf-8")

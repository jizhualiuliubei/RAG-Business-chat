import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.loader import load_document
from scripts.generate_enterprise_scale_docs import DOCS
from scripts.generate_enterprise_evaluation_questions import CATEGORY_TARGETS, FIELDS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_DIR = PROJECT_ROOT / "evaluation" / "enterprise_scale"
CSV_PATH = EVALUATION_DIR / "企业规模知识库评测题集.csv"
MD_PATH = EVALUATION_DIR / "企业规模知识库评测题集.md"
ENTERPRISE_DOC_DIR = PROJECT_ROOT / "data" / "enterprise_scale"


def _read_rows() -> list[dict[str, str]]:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _category_key(row: dict[str, str]) -> str:
    return row["category"].split(" ", 1)[0]


def test_enterprise_question_files_exist_and_have_expected_schema():
    assert CSV_PATH.exists()
    assert MD_PATH.exists()

    rows = _read_rows()
    assert len(rows) == 360
    assert list(rows[0].keys()) == FIELDS
    assert all(row["case_id"] for row in rows)
    assert all(row["question"] for row in rows)
    assert all(row["standard_answer"] for row in rows)
    assert all(row["evaluation_focus"] for row in rows)


def test_enterprise_question_files_have_target_distribution():
    rows = _read_rows()

    assert Counter(_category_key(row) for row in rows) == CATEGORY_TARGETS

    expected_docs = {f"{doc.name}{doc.suffix}" for doc in DOCS}
    by_doc = Counter(row["expected_doc"] for row in rows)
    assert set(by_doc) == expected_docs
    assert all(count == 30 for count in by_doc.values())


def test_enterprise_question_sources_and_negative_cases_are_valid():
    rows = _read_rows()

    for row in rows:
        category = _category_key(row)
        answer_points = [point for point in row["answer_points"].split("；") if point.strip()]
        assert len(answer_points) >= 2, row["case_id"]

        if category in {"C", "D", "E"}:
            assert len(answer_points) >= 3, row["case_id"]

        if category == "F":
            assert row["negative_case"] == "true"
            assert row["expected_doc"]
            assert row["expected_section"] == ""
            assert row["expected_clause"] == ""
            assert row["required_citations"] == ""
            assert "未提供依据" in row["standard_answer"]
            assert "不能编造" in row["standard_answer"]
        else:
            assert row["negative_case"] == "false"
            assert row["expected_doc"]
            assert row["expected_section"]
            assert row["expected_clause"]
            assert row["required_citations"]


def test_enterprise_question_expected_clauses_exist_in_parsed_corpus():
    rows = _read_rows()
    text_by_file: dict[str, str] = {}

    for doc in DOCS:
        filename = f"{doc.name}{doc.suffix}"
        path = ENTERPRISE_DOC_DIR / filename
        loaded = load_document(str(path), filename)
        text_by_file[filename] = "\n".join(item.page_content for item in loaded)

    for row in rows:
        if _category_key(row) == "F":
            continue
        doc_text = text_by_file[row["expected_doc"]]
        assert row["expected_section"] in doc_text, row["case_id"]
        assert row["expected_clause"] in doc_text, row["case_id"]


def test_enterprise_question_set_has_no_duplicate_questions_or_ids():
    rows = _read_rows()
    ids = [row["case_id"] for row in rows]
    questions = [row["question"] for row in rows]

    assert len(ids) == len(set(ids))
    assert len(questions) == len(set(questions))


def test_enterprise_question_markdown_matches_csv_scope():
    rows = _read_rows()
    markdown = MD_PATH.read_text(encoding="utf-8")

    assert "本题集用于人工审题、项目展示和后续导入评测系统" in markdown
    assert "RAGAS、DeepEval、NIST AI RMF、Stanford HELM" in markdown
    for row in rows[:12]:
        assert row["case_id"] in markdown
    for doc in DOCS:
        assert f"{doc.name}{doc.suffix}" in markdown

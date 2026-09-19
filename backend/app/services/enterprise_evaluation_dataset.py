"""Enterprise-scale synthetic evaluation dataset.

The source documents are controlled demo materials. Public documents may guide
their structure, but the facts below are synthetic so expected answers can be
verified exactly.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


FULL_DATASET_VERSION = "enterprise_scale_360_v1"
FULL_DATASET_NAME = "企业规模知识库 360 题全量评测集"
ENTERPRISE_KB_NAME = "企业规模评测库"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVALUATION_DIR = PROJECT_ROOT / "evaluation" / "enterprise_scale"
FULL_DATASET_CSV = EVALUATION_DIR / "企业规模知识库评测题集.csv"
FULL_DATASET_MD = EVALUATION_DIR / "企业规模知识库评测题集.md"

CATEGORY_LABELS = {
    "A": "直接事实检索",
    "B": "跨文档综合",
    "C": "规则应用与计算",
    "D": "易错点与矛盾检测",
    "E": "未覆盖问题拒答",
}

# 360 题集用的是另一套类别语义（六类，含 F 拒答题）。
CATEGORY_LABELS_360 = {
    "A": "单文档事实检索",
    "B": "单文档细节定位与条款解释",
    "C": "跨文档关联推理",
    "D": "场景应用与规则计算",
    "E": "边界条件与易错判断",
    "F": "文档未覆盖与幻觉测试",
}


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    category: str
    question: str
    expected_doc: str | None
    expected_section: str | None
    expected_clause: str | None
    expected_keywords: tuple[str, ...]
    answer_points: tuple[str, ...]
    difficulty: str = ""
    negative_case: bool = False
    required_citations: tuple[str, ...] = ()


def _category_key(raw: str) -> str:
    return (raw or "").strip()[:1]


def _split_semicolon(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split("；") if item.strip())


def load_full_cases() -> list[EvaluationCase]:
    with FULL_DATASET_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    cases: list[EvaluationCase] = []
    for row in rows:
        cases.append(
            EvaluationCase(
                case_id=row["case_id"],
                category=_category_key(row["category"]),
                question=row["question"],
                expected_doc=row.get("expected_doc") or None,
                expected_section=row.get("expected_section") or None,
                expected_clause=row.get("expected_clause") or None,
                expected_keywords=_split_semicolon(row.get("expected_keywords")),
                answer_points=_split_semicolon(row.get("answer_points")),
                difficulty=row.get("difficulty", ""),
                negative_case=row.get("negative_case", "").lower() == "true",
                required_citations=_split_semicolon(row.get("required_citations")),
            )
        )
    return cases


def summarize_full_dataset() -> dict:
    categories: dict[str, dict] = {}
    for case in load_full_cases():
        categories.setdefault(case.category, {"label": CATEGORY_LABELS_360.get(case.category, case.category), "case_count": 0})
        categories[case.category]["case_count"] += 1
    return {
        "version": FULL_DATASET_VERSION,
        "name": FULL_DATASET_NAME,
        "case_count": sum(item["case_count"] for item in categories.values()),
        "categories": categories,
        "recommended_kb_name": ENTERPRISE_KB_NAME,
        "download_formats": ["csv", "md"],
    }


def get_cases(dataset_version: str) -> list[EvaluationCase]:
    if dataset_version == FULL_DATASET_VERSION:
        return load_full_cases()
    raise ValueError(f"未知评测集版本: {dataset_version}")


def summarize_by_version(dataset_version: str) -> dict:
    if dataset_version == FULL_DATASET_VERSION:
        return summarize_full_dataset()
    raise ValueError(f"未知评测集版本: {dataset_version}")


def list_datasets() -> list[dict]:
    return [summarize_full_dataset()]

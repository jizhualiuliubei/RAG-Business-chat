"""Run full 360-case enterprise RAG evaluation from CSV.

This script is intentionally independent from the admin evaluation task API:
it reads the large CSV case file, runs every case serially, and writes
auditable result files for interview/demo review.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from app.core import rag
from app.database import SessionLocal
from app.models.knowledge_base import KnowledgeBase
from app.services import qa_service


RESULT_FIELDS = [
    "case_id",
    "category",
    "difficulty",
    "question",
    "expected_doc",
    "expected_section",
    "expected_clause",
    "retrieved_top_k",
    "top1_doc_hit",
    "top3_doc_hit",
    "top5_doc_hit",
    "section_hit",
    "answer",
    "answer_points_coverage",
    "citation_passed",
    "refusal_passed",
    "passed",
    "failure_reason",
    "fix_suggestion",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "model_name",
    "negative_case",
]

REFUSAL_MARKERS = (
    "未覆盖",
    "未找到",
    "未明确",
    "未规定",
    "未提供依据",
    "没有相关",
    "无法从知识库",
    "不能编造",
    "没有检索到",
    "现有制度资料中未找到",
)

FABRICATION_MARKERS = (
    "固定为",
    "每年发放",
    "统一标准为",
    "可以获得",
    "必须使用",
    "承诺提供",
)


def load_cases(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"评测题集为空: {path}")
    required = {
        "case_id",
        "category",
        "question",
        "expected_doc",
        "expected_section",
        "expected_clause",
        "answer_points",
        "negative_case",
    }
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"评测题集缺少字段: {sorted(missing)}")
    return rows


def find_kb_id_by_name(name: str) -> int:
    with SessionLocal() as db:
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.name == name).first()
        if not kb:
            raise LookupError(f"找不到知识库: {name}")
        return kb.id


def retrieve_contexts(question: str, kb_id: int, top_k: int) -> list[dict]:
    _, contexts = rag.retrieve_contexts(question, kb_id=kb_id, top_k=top_k)
    return contexts


def answer_question(question: str, kb_id: int) -> dict:
    return qa_service.answer_question(question, kb_id=kb_id)


def evaluate_case(
    case: dict[str, str],
    *,
    kb_id: int,
    top_k: int,
    retrieve_fn: Callable[[str, int, int], list[dict]] = retrieve_contexts,
    answer_fn: Callable[[str, int], dict] = answer_question,
    retries: int = 2,
    retry_sleep: float = 1.0,
) -> dict[str, str]:
    started = time.perf_counter()
    is_negative = _is_negative(case)
    contexts: list[dict] = []
    answer_result: dict = {}
    error_message = ""

    try:
        contexts = retrieve_fn(case["question"], kb_id, top_k)
        answer_result = _with_retries(
            lambda: answer_fn(case["question"], kb_id),
            retries=retries,
            retry_sleep=retry_sleep,
        )
    except Exception as exc:
        error_message = str(exc) or exc.__class__.__name__
        traceback.print_exc()

    answer = str(answer_result.get("answer", "")) if answer_result else ""
    sources = answer_result.get("sources") if answer_result else []
    if not isinstance(sources, list):
        sources = []
    usage = answer_result.get("usage") if answer_result else {}
    if not isinstance(usage, dict):
        usage = {}

    if is_negative:
        top1 = top3 = top5 = section = citation = "not_applicable"
        refusal = _refusal_passed(answer)
        coverage = _answer_coverage(answer, case.get("answer_points", ""))
        passed = refusal and not _looks_fabricated_negative_answer(answer)
    else:
        top1_bool = _doc_hit(case["expected_doc"], contexts, 1)
        top3_bool = _doc_hit(case["expected_doc"], contexts, 3)
        top5_bool = _doc_hit(case["expected_doc"], contexts, 5)
        section_bool = _section_hit(case.get("expected_section", ""), case.get("expected_clause", ""), contexts)
        coverage = _answer_coverage(answer, case.get("answer_points", ""))
        citation_bool = _citation_passed(case["expected_doc"], sources or contexts)
        top1 = _bool(top1_bool)
        top3 = _bool(top3_bool)
        top5 = _bool(top5_bool)
        section = _bool(section_bool)
        citation = _bool(citation_bool)
        refusal = False
        passed = top5_bool and section_bool and coverage >= 60 and citation_bool

    failure_reason = ""
    if error_message:
        failure_reason = f"模型/API失败：{error_message}"
        passed = False
    elif not passed:
        failure_reason = _failure_reason(case, contexts, answer, coverage, sources)

    return {
        "case_id": case.get("case_id", ""),
        "category": case.get("category", ""),
        "difficulty": case.get("difficulty", ""),
        "question": case.get("question", ""),
        "expected_doc": case.get("expected_doc", ""),
        "expected_section": case.get("expected_section", ""),
        "expected_clause": case.get("expected_clause", ""),
        "retrieved_top_k": json.dumps(_compact_contexts(contexts), ensure_ascii=False),
        "top1_doc_hit": top1,
        "top3_doc_hit": top3,
        "top5_doc_hit": top5,
        "section_hit": section,
        "answer": answer,
        "answer_points_coverage": str(coverage),
        "citation_passed": citation,
        "refusal_passed": _bool(refusal) if is_negative else "not_applicable",
        "passed": _bool(passed),
        "failure_reason": failure_reason,
        "fix_suggestion": _fix_suggestion(failure_reason),
        "latency_ms": str(round((time.perf_counter() - started) * 1000)),
        "input_tokens": str(int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)),
        "output_tokens": str(int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)),
        "total_tokens": str(int(usage.get("total_tokens") or 0)),
        "model_name": str(answer_result.get("model_name", "")) if answer_result else "",
        "negative_case": "true" if is_negative else "false",
    }


def summarize_results(results: list[dict[str, str]]) -> dict:
    non_negative = [row for row in results if row["negative_case"] != "true"]
    negative = [row for row in results if row["negative_case"] == "true"]
    failures = Counter(row["failure_reason"] for row in results if row["failure_reason"])
    by_category = defaultdict(list)
    by_doc = defaultdict(list)
    for row in results:
        by_category[row["category"]].append(row)
        by_doc[row["expected_doc"]].append(row)
    latencies = [_int(row.get("latency_ms")) for row in results]
    p95 = 0
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, round(len(ordered) * 0.95) - 1)]
    return {
        "case_count": len(results),
        "pass_count": _count_true(results, "passed"),
        "pass_rate": _rate(_count_true(results, "passed"), len(results)),
        "top1_doc_hit_rate": _rate(_count_true(non_negative, "top1_doc_hit"), len(non_negative)),
        "top3_doc_hit_rate": _rate(_count_true(non_negative, "top3_doc_hit"), len(non_negative)),
        "top5_doc_hit_rate": _rate(_count_true(non_negative, "top5_doc_hit"), len(non_negative)),
        "section_hit_rate": _rate(_count_true(non_negative, "section_hit"), len(non_negative)),
        "answer_coverage_rate": round(mean([_int(row["answer_points_coverage"]) for row in results]), 2) if results else 0,
        "answer_coverage_pass_rate": _rate(sum(1 for row in results if _int(row["answer_points_coverage"]) >= 60), len(results)),
        "citation_pass_rate": _rate(_count_true(non_negative, "citation_passed"), len(non_negative)),
        "refusal_pass_rate": _rate(_count_true(negative, "refusal_passed"), len(negative)) if negative else None,
        "avg_latency_ms": round(mean(latencies)) if latencies else 0,
        "p95_latency_ms": p95,
        "token_usage": {
            "input_tokens": sum(_int(row.get("input_tokens")) for row in results),
            "output_tokens": sum(_int(row.get("output_tokens")) for row in results),
            "total_tokens": sum(_int(row.get("total_tokens")) for row in results),
        },
        "failure_reasons": dict(failures),
        "categories": {key: _bucket_summary(rows) for key, rows in sorted(by_category.items())},
        "documents": {key: _bucket_summary(rows) for key, rows in sorted(by_doc.items())},
    }


def write_outputs(results: list[dict[str, str]], summary: dict, output_dir: Path, *, environment_name: str) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_csv = output_dir / "企业规模知识库全量评测结果.csv"
    report_md = output_dir / "企业规模知识库全量评测报告.md"
    failures_md = output_dir / "企业规模知识库失败样例.md"

    with results_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    report_md.write_text(_build_report(summary, results, environment_name), encoding="utf-8")
    failures_md.write_text(_build_failures_report(results), encoding="utf-8")
    return {"results_csv": results_csv, "report_md": report_md, "failures_md": failures_md}


def _with_retries(fn: Callable[[], dict], *, retries: int, retry_sleep: float) -> dict:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(retry_sleep)
    raise last_exc or RuntimeError("unknown retry failure")


def _is_negative(case: dict[str, str]) -> bool:
    return case.get("negative_case", "").lower() == "true" or case.get("category", "").startswith("F")


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _int(value: str | int | None) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    return round(count / max(total, 1) * 100, 2)


def _count_true(rows: list[dict[str, str]], field: str) -> int:
    return sum(1 for row in rows if row.get(field) == "true")


def _bucket_summary(rows: list[dict[str, str]]) -> dict:
    return {
        "case_count": len(rows),
        "pass_count": _count_true(rows, "passed"),
        "pass_rate": _rate(_count_true(rows, "passed"), len(rows)),
    }


def _doc_stem(expected_doc: str) -> str:
    for suffix in (".txt", ".pdf", ".docx", ".csv", ".xlsx", ".xls"):
        expected_doc = expected_doc.replace(suffix, "")
    return expected_doc.strip()


def _doc_hit(expected_doc: str, contexts: list[dict], top_n: int) -> bool:
    expected = _doc_stem(expected_doc)
    return any(expected in _doc_stem(str(ctx.get("source", ""))) for ctx in contexts[:top_n])


def _section_hit(expected_section: str, expected_clause: str, contexts: list[dict]) -> bool:
    needles = [item for item in (expected_section, expected_clause) if item]
    if not needles:
        return False
    return any(any(needle in str(ctx.get("text", "")) for needle in needles) for ctx in contexts)


def _answer_coverage(answer: str, answer_points: str) -> int:
    points = [point.strip() for point in answer_points.split("；") if point.strip()]
    if not points:
        return 0
    matched = sum(1 for point in points if _answer_point_matched(answer, point))
    return round(matched / len(points) * 100)


_STOP_CHARS = set("的了和与及或在对为是中上内按需须应可并将由把个项条款规定要求进行完成")
_KEY_TOKEN_RE = re.compile(
    r"[A-Za-z]{2,}-\d{2}-\d{3}|[A-Za-z]+|\d+(?:\.\d+)?(?:万元|元|个工作日|工作日|天|小时|分钟|%|年|月|日|次|级)?"
)


def _normalize_eval_text(text: str) -> str:
    return re.sub(r"[\s，。；;：:、,.!?！？（）()《》\"'“”‘’\[\]【】#/-]+", "", str(text or "").lower())


def _answer_point_matched(answer: str, point: str) -> bool:
    answer_norm = _normalize_eval_text(answer)
    point_norm = _normalize_eval_text(point)
    if not point_norm:
        return False
    if point_norm in answer_norm:
        return True

    raw_answer = str(answer or "")
    raw_point = str(point or "")
    hard_tokens = [_normalize_eval_text(token) for token in _KEY_TOKEN_RE.findall(raw_point)]
    if hard_tokens and not all(token in _normalize_eval_text(raw_answer) for token in hard_tokens):
        return False

    chinese_chars = {
        char
        for char in point_norm
        if "\u4e00" <= char <= "\u9fff" and char not in _STOP_CHARS
    }
    if not chinese_chars:
        return bool(hard_tokens)
    overlap = sum(1 for char in chinese_chars if char in answer_norm)
    return overlap / len(chinese_chars) >= 0.62


def _citation_passed(expected_doc: str, sources: list[dict]) -> bool:
    return _doc_hit(expected_doc, sources, len(sources))


def _refusal_passed(answer: str) -> bool:
    return any(marker in answer for marker in REFUSAL_MARKERS)


def _looks_fabricated_negative_answer(answer: str) -> bool:
    if not answer:
        return True
    if _refusal_passed(answer):
        return False
    return any(marker in answer for marker in FABRICATION_MARKERS)


def _failure_reason(case: dict[str, str], contexts: list[dict], answer: str, coverage: int, sources: list[dict]) -> str:
    if _is_negative(case):
        if not _refusal_passed(answer):
            return "拒答失败：未覆盖问题没有明确说明知识库无依据"
        if _looks_fabricated_negative_answer(answer):
            return "拒答失败：回答包含无依据制度、金额或承诺"
        return ""
    if not _doc_hit(case["expected_doc"], contexts, 5):
        return "文档未命中：期望来源未进入 Top-5"
    if not _section_hit(case.get("expected_section", ""), case.get("expected_clause", ""), contexts):
        return "章节未命中：期望章节或条款未进入上下文"
    if coverage < 60:
        return "答案要点缺失：模型回答未覆盖主要标准答案"
    if not _citation_passed(case["expected_doc"], sources or contexts):
        return "引用不支持：回答来源不足以支撑结论"
    return ""


def _fix_suggestion(reason: str) -> str:
    if reason.startswith("文档未命中"):
        return "检查企业规模资料是否已完整上传到目标知识库，必要时重建向量或增强关键词召回。"
    if reason.startswith("章节未命中"):
        return "检查切片是否保留章节标题和条款编号，必要时优化标题切分和chunk overlap。"
    if reason.startswith("答案要点"):
        return "优化回答提示词，要求逐条覆盖标准答案要点并引用来源。"
    if reason.startswith("引用"):
        return "检查 sources 映射和引用生成策略，要求关键结论绑定对应来源片段。"
    if reason.startswith("拒答"):
        return "强化未覆盖问题的拒答提示词，禁止输出知识库不存在的制度、金额或承诺。"
    if reason.startswith("模型/API"):
        return "检查模型Key、网络、限流和超时；该类错误单独归类，不计为RAG准确率问题。"
    return ""


def _compact_contexts(contexts: list[dict]) -> list[dict]:
    compact = []
    for index, ctx in enumerate(contexts, start=1):
        text = str(ctx.get("text", ""))
        compact.append(
            {
                "rank": index,
                "source": ctx.get("source", ""),
                "chunk_id": ctx.get("chunk_id", ""),
                "score": ctx.get("score", ""),
                "text_preview": text[:500],
            }
        )
    return compact


def _build_report(summary: dict, results: list[dict[str, str]], environment_name: str) -> str:
    lines = [
        "# 企业规模知识库全量评测报告",
        "",
        "本报告由 `backend/scripts/eval_enterprise_full.py` 自动生成，所有指标来自逐题真实运行结果。",
        "",
        "## 一、评测环境",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 环境 | {environment_name} |",
        "| 题集 | evaluation/enterprise_scale/企业规模知识库评测题集.csv |",
        f"| 生成时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |",
        "",
        "## 二、总体指标",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 题目总数 | {summary['case_count']} |",
        f"| 总通过率 | {summary['pass_rate']}% |",
        f"| Top-1 文档命中率 | {summary['top1_doc_hit_rate']}% |",
        f"| Top-3 文档命中率 | {summary['top3_doc_hit_rate']}% |",
        f"| Top-5 文档命中率 | {summary['top5_doc_hit_rate']}% |",
        f"| 章节级命中率 | {summary['section_hit_rate']}% |",
        f"| 答案要点覆盖率 | {summary['answer_coverage_rate']}% |",
        f"| 引用正确率 | {summary['citation_pass_rate']}% |",
        f"| 拒答正确率 | {summary['refusal_pass_rate']}% |",
        f"| 平均耗时 | {summary['avg_latency_ms']} ms |",
        f"| P95 耗时 | {summary['p95_latency_ms']} ms |",
        f"| Token 总量 | {summary['token_usage']['total_tokens']} |",
        "",
        "## 三、题型结果",
        "",
        "| 题型 | 题数 | 通过数 | 通过率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, data in summary["categories"].items():
        lines.append(f"| {name} | {data['case_count']} | {data['pass_count']} | {data['pass_rate']}% |")
    lines.extend(["", "## 四、文档结果", "", "| 文档 | 题数 | 通过数 | 通过率 |", "| --- | ---: | ---: | ---: |"])
    for name, data in summary["documents"].items():
        lines.append(f"| {name} | {data['case_count']} | {data['pass_count']} | {data['pass_rate']}% |")
    lines.extend(["", "## 五、失败原因分布", "", "| 失败原因 | 数量 |", "| --- | ---: |"])
    if summary["failure_reasons"]:
        for reason, count in summary["failure_reasons"].items():
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("| 无 | 0 |")
    lines.extend(["", "## 六、典型失败样例", ""])
    failed = [row for row in results if row["passed"] != "true"][:20]
    if failed:
        lines.extend(["| 题号 | 题型 | 问题 | 失败原因 | 修复建议 |", "| --- | --- | --- | --- | --- |"])
        for row in failed:
            lines.append(
                f"| {row['case_id']} | {row['category']} | {_md(row['question'])} | {_md(row['failure_reason'])} | {_md(row['fix_suggestion'])} |"
            )
    else:
        lines.append("本次评测没有失败样例。")
    return "\n".join(lines)


def _build_failures_report(results: list[dict[str, str]]) -> str:
    lines = [
        "# 企业规模知识库失败样例",
        "",
        "本文件只记录未通过题目，便于定位是检索、切片、回答、引用还是拒答问题。",
        "",
    ]
    failed = [row for row in results if row["passed"] != "true"]
    if not failed:
        lines.append("本次评测没有失败样例。")
        return "\n".join(lines)
    for row in failed[:20]:
        lines.extend(
            [
                f"## {row['case_id']} · {row['category']}",
                "",
                f"- 问题：{row['question']}",
                f"- 期望来源：{row['expected_doc']} / {row['expected_section']} / {row['expected_clause']}",
                f"- 失败原因：{row['failure_reason']}",
                f"- 修复建议：{row['fix_suggestion']}",
                "",
                "### 模型回答",
                "",
                row["answer"] or "无回答",
                "",
                "### Top-K 召回",
                "",
                "```json",
                row["retrieved_top_k"],
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def _md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def run(args: argparse.Namespace) -> dict[str, Path]:
    kb_id = args.kb_id or find_kb_id_by_name(args.kb_name)
    cases = load_cases(Path(args.input))
    results: list[dict[str, str]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['case_id']} {case['category']} ...", flush=True)
        result = evaluate_case(
            case,
            kb_id=kb_id,
            top_k=args.top_k,
            retries=args.retries,
            retry_sleep=args.retry_sleep,
        )
        results.append(result)
        print(
            f"  passed={result['passed']} top5={result['top5_doc_hit']} section={result['section_hit']} "
            f"coverage={result['answer_points_coverage']} reason={result['failure_reason']}",
            flush=True,
        )
        if args.sleep > 0 and index < len(cases):
            time.sleep(args.sleep)
    summary = summarize_results(results)
    paths = write_outputs(results, summary, Path(args.output_dir), environment_name=args.environment)
    print("评测完成：", flush=True)
    for path in paths.values():
        print(f"- {path}", flush=True)
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run enterprise 360-case full RAG evaluation")
    parser.add_argument("--kb-name", default="企业规模评测库")
    parser.add_argument("--kb-id", type=int, default=None)
    parser.add_argument(
        "--input",
        default=str(PROJECT_ROOT / "evaluation" / "enterprise_scale" / "企业规模知识库评测题集.csv"),
    )
    parser.add_argument("--mode", choices=["full"], default="full")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--sleep", type=float, default=0.8)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep", type=float, default=1.0)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "reports" / "enterprise_full_eval"))
    parser.add_argument("--environment", default="local")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()

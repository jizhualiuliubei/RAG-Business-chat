"""RAG 评测服务。

这一层把原来只能命令行运行的 52 题评测集整理成可被 API 和前端复用的数据。
第一阶段只做可复现的检索评测，不把主观生成质量包装成虚高指标。
"""
from __future__ import annotations

from typing import TypeAlias

from app.config import SCORE_THRESHOLD, TOP_K
from app.core import rag
from scripts.eval_rag import CASES, _doc_match

KbScope: TypeAlias = int | str | list[int] | list[str]


CATEGORY_LABELS = {
    "A": "直接事实检索",
    "B": "跨文档综合",
    "C": "规则应用与计算",
    "D": "易错点与矛盾检测",
    "E": "未覆盖问题拒答",
}

METRIC_DEFINITIONS = {
    "case_count": {
        "label": "测试题数量",
        "description": "当前评测集中用于覆盖 RAG 能力的题目总数。",
        "source": "backend/scripts/eval_rag.py",
    },
    "retrieval_pass_rate": {
        "label": "检索通过率",
        "description": "A/B/C/D 类问题中，期望文档和期望章节都进入检索结果的比例。",
        "source": "backend/scripts/eval_rag.py",
    },
    "section_hit_rate": {
        "label": "章节级命中率",
        "description": "不仅判断文档命中，还要求答案所在章节进入候选上下文。",
        "source": "backend/scripts/eval_rag.py",
    },
    "refusal_pass_rate": {
        "label": "拒答正确率",
        "description": "E 类未覆盖问题在端到端模式下是否明确说明知识库未覆盖。",
        "source": "backend/scripts/eval_rag.py --e2e",
    },
    "top1_source": {
        "label": "Top1 来源",
        "description": "检索排序第一的来源文档，用于分析召回质量。",
        "source": "backend/scripts/eval_rag.py",
    },
    "top1_score": {
        "label": "Top1 分数",
        "description": "检索排序第一的融合分数，用于辅助阈值标定。",
        "source": "backend/scripts/eval_rag.py --calib",
    },
}


def summarize_catalog() -> dict:
    """汇总 52 题测试集目录，供管理员概览页展示。"""
    categories = {
        key: {"label": label, "case_count": 0}
        for key, label in CATEGORY_LABELS.items()
    }
    for _, category, *_ in CASES:
        categories[category]["case_count"] += 1

    return {
        "case_count": len(CASES),
        "categories": categories,
        "metrics": METRIC_DEFINITIONS,
    }


def get_metric_definitions() -> dict:
    """返回评测指标定义，保证前端和报告使用同一套口径。"""
    return METRIC_DEFINITIONS


def _top1_from_contexts(contexts: list[dict]) -> dict:
    if not contexts:
        return {"top1_source": "无", "top1_score": 0}
    first = contexts[0]
    return {
        "top1_source": first.get("source", "无"),
        "top1_score": round(float(first.get("score", 0)), 4),
    }


def run_retrieval_evaluation(
    kb_id: KbScope = 1,
    top_k: int = TOP_K,
    enterprise_id: int | None = None,
) -> dict:
    """运行一次检索层评测。

    这里只判断检索结果是否包含期望文档与章节；E 类拒答要依赖生成模型，
    第一阶段不默认触发，避免后台页面一次点击产生不可控模型成本。
    """
    rows = []
    category_stats = {
        key: {"label": label, "case_count": 0, "pass_count": 0, "skipped_count": 0, "pass_rate": 0}
        for key, label in CATEGORY_LABELS.items()
    }

    for case_id, category, expected_doc, expected_section, question in CASES:
        if category == "E":
            contexts = []
            top1 = {"top1_source": "未运行", "top1_score": 0}
            passed = None
            skipped = True
            reason = "E 类未覆盖问题需要端到端拒答评测；检索层仅记录样本。"
        else:
            try:
                _, contexts = rag.retrieve_contexts(
                    question,
                    kb_id=kb_id,
                    top_k=top_k,
                    enterprise_id=enterprise_id,
                )
            except TypeError as exc:
                if "enterprise_id" not in str(exc):
                    raise
                _, contexts = rag.retrieve_contexts(question, kb_id=kb_id, top_k=top_k)
            top1 = _top1_from_contexts(contexts)
            doc_hit = any(_doc_match(expected_doc, c.get("source", "")) for c in contexts)
            section_hit = True
            if expected_section:
                section_hit = any(expected_section in c.get("text", "") for c in contexts)
            passed = doc_hit and section_hit
            skipped = False
            reason = "期望文档与章节均命中" if passed else "期望文档或章节未进入检索结果"

        category_stats[category]["case_count"] += 1
        category_stats[category]["pass_count"] += 1 if passed is True else 0
        category_stats[category]["skipped_count"] += 1 if skipped else 0
        rows.append({
            "case_id": case_id,
            "category": category,
            "category_label": CATEGORY_LABELS[category],
            "question": question,
            "expected_doc": expected_doc,
            "expected_section": expected_section,
            "passed": passed,
            "skipped": skipped,
            "reason": reason,
            **top1,
        })

    for item in category_stats.values():
        total = item["case_count"] - item["skipped_count"]
        item["pass_rate"] = None if total <= 0 else round(item["pass_count"] / total * 100, 2)

    evaluated_cases = [row for row in rows if not row["skipped"]]
    skipped_cases = [row for row in rows if row["skipped"]]
    retrieval_pass = sum(1 for row in evaluated_cases if row["passed"])

    return {
        "kb_id": kb_id,
        "mode": "retrieval",
        "top_k": top_k,
        "score_threshold": SCORE_THRESHOLD,
        "case_count": len(rows),
        "evaluated_case_count": len(evaluated_cases),
        "skipped_case_count": len(skipped_cases),
        "retrieval_case_count": len(evaluated_cases),
        "retrieval_pass_count": retrieval_pass,
        "retrieval_pass_rate": round(retrieval_pass / max(len(evaluated_cases), 1) * 100, 2),
        "categories": category_stats,
        "cases": rows,
        "note": "当前为检索层评测，仅统计 A-D 类命中；E 类拒答需要端到端模型评测，已标记为未运行。",
    }

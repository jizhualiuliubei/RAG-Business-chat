"""Optional RAGAs evaluation bridge.

This module keeps RAGAs behind a small adapter so the main evaluation service
can be tested without installing or calling external judge models.
"""
from __future__ import annotations

import math
from typing import Any

from app.config import (
    RAGAS_ANSWER_RELEVANCY_THRESHOLD,
    RAGAS_CONTEXT_RECALL_THRESHOLD,
    RAGAS_FACTUAL_CORRECTNESS_THRESHOLD,
    RAGAS_FAITHFULNESS_THRESHOLD,
)
from app.core.embeddings import get_embed_model, get_embed_model_for_config
from app.core.llm import get_model, get_model_for_config

THRESHOLDS = {
    "faithfulness": RAGAS_FAITHFULNESS_THRESHOLD,
    "context_recall": RAGAS_CONTEXT_RECALL_THRESHOLD,
    "llm_context_recall": RAGAS_CONTEXT_RECALL_THRESHOLD,
    "factual_correctness": RAGAS_FACTUAL_CORRECTNESS_THRESHOLD,
    "answer_relevancy": RAGAS_ANSWER_RELEVANCY_THRESHOLD,
    "response_relevancy": RAGAS_ANSWER_RELEVANCY_THRESHOLD,
}


def evaluate_case(
    *,
    question: str,
    answer: str,
    contexts: list[dict],
    reference: str,
    model_config: dict | None = None,
    embed_config: dict | None = None,
) -> dict:
    """Run RAGAs on one QA result and return normalized scores."""
    try:
        from ragas import EvaluationDataset, evaluate
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.metrics import FactualCorrectness, Faithfulness, LLMContextRecall
        try:
            from ragas.metrics import AnswerRelevancy
        except ImportError:
            from ragas.metrics import ResponseRelevancy as AnswerRelevancy
    except Exception as exc:
        raise RuntimeError("RAGAs 未安装或版本不兼容，请执行 pip install -r requirements.txt 后重启后端。") from exc

    dataset = EvaluationDataset.from_list([
        {
            "user_input": question,
            "retrieved_contexts": [ctx.get("text", "") for ctx in contexts if ctx.get("text")],
            "response": answer,
            "reference": reference,
        }
    ])
    llm = _llm(model_config)
    embeddings = _embeddings(embed_config)
    result = evaluate(
        dataset,
        metrics=[
            Faithfulness(),
            LLMContextRecall(),
            FactualCorrectness(),
            AnswerRelevancy(),
        ],
        llm=LangchainLLMWrapper(llm),
        embeddings=LangchainEmbeddingsWrapper(embeddings),
    )
    scores = _first_score_row(result)
    failed = []
    for name, threshold in THRESHOLDS.items():
        if name in scores and scores[name] < threshold:
            failed.append(f"{name} {scores[name]:.2f} < {threshold:.2f}")
    return {
        "scores": scores,
        "passed": not failed,
        "failure_reason": "" if not failed else "RAGAs未通过：" + "；".join(failed),
        "skip_reason": "",
    }


def _llm(model_config: dict | None = None):
    if model_config and not model_config.get("platform"):
        return get_model_for_config(
            model_config["api_key"],
            model_config.get("base_url"),
            model_config.get("model"),
        )
    return get_model()


def _embeddings(embed_config: dict | None = None):
    if embed_config and not embed_config.get("platform"):
        return get_embed_model_for_config(
            embed_config["api_key"],
            embed_config.get("base_url"),
            embed_config.get("model"),
        )
    return get_embed_model()


def _first_score_row(result: Any) -> dict[str, float]:
    if hasattr(result, "to_pandas"):
        rows = result.to_pandas().to_dict(orient="records")
        return _numeric_scores(rows[0] if rows else {})
    if hasattr(result, "scores"):
        scores = getattr(result, "scores")
        if isinstance(scores, list):
            return _numeric_scores(scores[0] if scores else {})
        if isinstance(scores, dict):
            return _numeric_scores(scores)
    if isinstance(result, dict):
        return _numeric_scores(result)
    return {}


def _numeric_scores(row: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in row.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        out[str(key)] = round(number, 4)
    return out

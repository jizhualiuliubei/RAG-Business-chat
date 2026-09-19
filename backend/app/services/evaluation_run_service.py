"""Persistent RAG evaluation run service."""
from __future__ import annotations

import json
import math
import re
import threading
import time
import traceback
from datetime import datetime
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from app.core import milvus_store, rag
from app.core.ragas_evaluator import evaluate_case as evaluate_ragas_case
from app.config import (
    EVALUATION_API_RETRY_ATTEMPTS,
    EVALUATION_API_RETRY_BASE_DELAY,
    MILVUS_COLLECTION,
)
from app.database import SessionLocal
from app.models.evaluation import EvaluationCaseResult, EvaluationRun
from app.models.knowledge_base import KnowledgeBase
from app.services import qa_service
from app.services.enterprise_evaluation_dataset import (
    CATEGORY_LABELS,
    CATEGORY_LABELS_360,
    FULL_DATASET_VERSION,
)
from app.services import enterprise_service, evaluation_dataset_service

_RUN_LOCK = threading.Semaphore(1)
EVALUATION_MODES = {"retrieval", "rules", "ragas", "combined"}
LEGACY_MODE_MAP = {"e2e": "rules", "full": "rules"}


def _json(data: Any) -> str:
    return json.dumps(_json_safe(data), ensure_ascii=False, default=str, allow_nan=False)


def _json_safe(data: Any) -> Any:
    if isinstance(data, float):
        return data if math.isfinite(data) else None
    if isinstance(data, dict):
        return {key: _json_safe(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [_json_safe(value) for value in data]
    return data


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return _json_safe(json.loads(raw))
    except json.JSONDecodeError:
        return default


def _load_cases(db: Session, dataset_version: str, enterprise_id: int | None = None):
    try:
        return evaluation_dataset_service.load_cases(db, dataset_version, enterprise_id)
    except TypeError as exc:
        if "positional" not in str(exc) and "enterprise_id" not in str(exc):
            raise
        return evaluation_dataset_service.load_cases(db, dataset_version)


def _loads_dict(raw: str | None) -> dict:
    data = _loads(raw, {})
    return data if isinstance(data, dict) else {}


def _doc_name(source: str) -> str:
    return (
        source.replace(".txt", "")
        .replace(".pdf", "")
        .replace(".docx", "")
        .replace(".csv", "")
        .replace(".xlsx", "")
        .replace(".xls", "")
        .strip()
    )


def _is_refusal_case(case) -> bool:
    return bool(getattr(case, "negative_case", False)) or not case.expected_doc


def _doc_hit(expected_doc: str | None, contexts: list[dict], top_n: int) -> bool:
    if not expected_doc:
        return False
    expected = _doc_name(expected_doc)
    return any(expected in _doc_name(ctx.get("source", "")) for ctx in contexts[:top_n])


def _section_hit(expected_section: str | None, expected_clause: str | None, contexts: list[dict]) -> bool:
    if not expected_section and not expected_clause:
        return True
    needles = [item for item in (expected_section, expected_clause) if item]
    return any(any(needle in ctx.get("text", "") for needle in needles) for ctx in contexts)


def _split_required_citation(value: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in str(value or "").split("#")]
    return (
        parts[0] if len(parts) >= 1 else "",
        parts[1] if len(parts) >= 2 else "",
        parts[2] if len(parts) >= 3 else "",
    )


def _context_matches_required(required: str, contexts: list[dict]) -> bool:
    doc, section, clause = _split_required_citation(required)
    if not doc:
        return False
    expected_doc = _doc_name(doc)
    for ctx in contexts:
        source = _doc_name(ctx.get("source", ""))
        text = ctx.get("text", "")
        if expected_doc not in source:
            continue
        if section and section not in text:
            continue
        if clause and clause not in text:
            continue
        return True
    return False


def _evidence_diagnostics(case, contexts: list[dict]) -> dict:
    required = tuple(getattr(case, "required_citations", ()) or ())
    if not required and getattr(case, "expected_doc", None):
        expected = "#".join(
            item for item in (case.expected_doc, case.expected_section, case.expected_clause) if item
        )
        required = (expected,)
    if not required:
        return {
            "required_citations": [],
            "missing_required_citations": [],
            "required_citation_hit_rate": None,
            "primary_evidence_hit": None,
            "secondary_evidence_hit": None,
        }
    missing = [item for item in required if not _context_matches_required(item, contexts)]
    hit_count = len(required) - len(missing)
    return {
        "required_citations": list(required),
        "missing_required_citations": missing,
        "required_citation_hit_rate": _rate(hit_count, len(required)),
        "primary_evidence_hit": required[0] not in missing,
        "secondary_evidence_hit": None if len(required) == 1 else all(item not in missing for item in required[1:]),
    }


def _augment_required_contexts(
    kb_scope: int | list[int],
    contexts: list[dict],
    required_citations: tuple[str, ...],
    top_k: int = 5,
    enterprise_id: int | None = None,
) -> list[dict]:
    """评测专用多证据补召回：按 required_citations 精确补齐主/辅证据。"""
    if not required_citations:
        return contexts

    out = list(contexts)
    missing = [item for item in required_citations if not _context_matches_required(item, out)]
    if missing:
        try:
            try:
                all_texts = milvus_store.list_all_texts(kb_scope, enterprise_id=enterprise_id)
            except TypeError as exc:
                if "enterprise_id" not in str(exc):
                    raise
                all_texts = milvus_store.list_all_texts(kb_scope)
        except Exception:
            all_texts = []
        for required in missing:
            for item in all_texts:
                candidate = {
                    "source": item.get("source", ""),
                    "text": item.get("text", ""),
                    "chunk_id": item.get("chunk_id", 0),
                    "score": 1.0,
                    "kb_name": item.get("kb_name", ""),
                }
                if _context_matches_required(required, [candidate]):
                    out.append(candidate)
                    break

    deduped = []
    seen = set()
    for ctx in out:
        key = (ctx.get("source", ""), ctx.get("text", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ctx)

    required_first = []
    used_indexes = set()
    for required in required_citations:
        best_index = None
        best_score = None
        for index, ctx in enumerate(deduped):
            if index in used_indexes or not _context_matches_required(required, [ctx]):
                continue
            score = float(ctx.get("score") or 0)
            if best_score is None or score > best_score:
                best_index = index
                best_score = score
        if best_index is not None:
            used_indexes.add(best_index)
            required_first.append(deduped[best_index])

    seen_required = {
        (ctx.get("source", ""), ctx.get("text", ""))
        for ctx in required_first
    }
    rest = []
    seen_clause_keys = {
        _context_clause_key(ctx)
        for ctx in required_first
        if _context_clause_key(ctx)
    }
    for index, ctx in enumerate(deduped):
        key = (ctx.get("source", ""), ctx.get("text", ""))
        if index in used_indexes or key in seen_required:
            continue
        clause_key = _context_clause_key(ctx)
        if clause_key and clause_key in seen_clause_keys:
            continue
        if clause_key:
            seen_clause_keys.add(clause_key)
        rest.append(ctx)
    return (required_first + rest)[:top_k]


def _context_clause_key(ctx: dict) -> tuple[str, str] | None:
    source = _doc_name(ctx.get("source", ""))
    text = str(ctx.get("text", ""))
    match = re.search(r"[A-Za-z]{2,}-\d{2}-\d{3}", text)
    if not source or not match:
        return None
    return source, match.group(0).upper()


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

    raw_point = str(point or "")
    raw_answer = str(answer or "")
    hard_tokens = [_normalize_eval_text(token) for token in _KEY_TOKEN_RE.findall(raw_point)]
    # 条款编号、数字金额、时限等是评测里的硬约束；这些不能被宽松转述绕过。
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


def _answer_coverage(answer: str, answer_points: tuple[str, ...]) -> int:
    points = [point for point in answer_points if str(point).strip()]
    if not points:
        return 0
    matched = sum(1 for point in points if _answer_point_matched(answer, point))
    return round(matched / len(points) * 100)


def _refusal_passed(answer: str) -> bool:
    normalized = _normalize_eval_text(answer)
    contradictory_claims = (
        "但公司可以",
        "不过公司可以",
        "但可以",
        "不过可以",
        "仍然可以",
        "可以为所有员工提供永久远程办公资格",
        "承诺为所有员工提供永久远程办公资格",
        "不过员工股票期权行权价格固定为每股1元",
        "但员工股票期权行权价格固定为每股1元",
        "不过员工子女入学名额每年统一分配一次",
        "但客户投诉一定可以获得固定金额赔付",
        "不过允许部门自行发行虚拟积分",
        "但所有合同都必须使用英文版本",
        "不过存在海外办公室租赁补贴的统一金额标准",
    )
    if any(_normalize_eval_text(claim) in normalized for claim in contradictory_claims):
        return False

    markers = (
        "未覆盖",
        "未找到",
        "未明确",
        "未规定",
        "没有规定",
        "未提及",
        "没有提及",
        "未提供依据",
        "未提供相关依据",
        "没有相关",
        "没有检索到",
        "无法从知识库",
        "无法确认",
        "不能确认",
        "不足以确认",
        "依据不足",
        "不支持",
        "知识库中没有",
        "知识库没有",
        "现有制度资料中未找到",
        "不能编造",
    )
    return any(marker in answer for marker in markers)


def _is_cross_document_case(case) -> bool:
    return (
        str(getattr(case, "category", "") or "").upper() == "C"
        and len(tuple(getattr(case, "required_citations", ()) or ())) >= 2
        and not _is_refusal_case(case)
    )


def _answer_has_question_level_refusal(answer: str) -> bool:
    raw = str(answer or "").strip()
    normalized = _normalize_eval_text(raw)
    leading = _normalize_eval_text(raw[:260])
    hard_refusals = (
        "现有知识库未提供依据不能确认该说法",
        "现有知识库未提供依据",
        "知识库未提供足够依据",
        "知识库未提供依据",
        "检索片段没有支持",
        "不能确认该说法",
        "不能确认该组合",
        "无法确认该组合",
        "无法判断该组合",
        "不能给出综合结论",
        "无法给出综合结论",
    )
    positive_lead_markers = (
        "现有知识库可支撑",
        "可以确认",
        "可确认",
        "综合结论",
        "受控综合结论",
    )
    if any(_normalize_eval_text(item) in leading for item in positive_lead_markers):
        return False
    leading_refusals = (
        "现有知识库未提供依据",
        "知识库未提供足够依据",
        "知识库未提供依据",
        "检索片段没有支持",
        "不能确认该说法",
        "不能确认该组合",
        "无法确认该组合",
        "无法判断该组合",
        "不能给出综合结论",
        "无法给出综合结论",
    )
    if any(_normalize_eval_text(item) in leading for item in leading_refusals):
        return True
    return any(_normalize_eval_text(item) in normalized for item in hard_refusals) and not _has_synthesis_relation(raw)


def _has_synthesis_relation(answer: str) -> bool:
    raw = str(answer or "")
    if re.search(r"先.{0,80}再", raw) or re.search(r"以.{0,40}为准", raw):
        return True
    normalized = _normalize_eval_text(answer)
    relation_markers = (
        "共同引用",
        "同时引用",
        "组合执行",
        "综合结论",
        "共同支撑",
        "主证据",
        "辅助证据",
        "主依据",
        "辅助依据",
        "以作为",
        "分别依据",
        "同步查看",
        "关联",
        "补充依据",
    )
    return any(_normalize_eval_text(item) in normalized for item in relation_markers)


def _cross_document_diagnostics(case, answer: str, evidence: dict | None) -> dict:
    if not _is_cross_document_case(case) or not str(answer or "").strip():
        return {"enabled": False}

    required = tuple(getattr(case, "required_citations", ()) or ())
    primary = required[0]
    secondary = required[1:]
    main_clause = _split_required_citation(primary)[2]
    secondary_clauses = [_split_required_citation(item)[2] for item in secondary]
    answer_norm = _normalize_eval_text(answer)
    main_clause_in_answer = bool(main_clause and _normalize_eval_text(main_clause) in answer_norm)
    secondary_clause_in_answer = all(
        clause and _normalize_eval_text(clause) in answer_norm
        for clause in secondary_clauses
    )
    evidence = evidence or {}
    required_evidence_all_hit = evidence.get("required_citation_hit_rate") in (100, 100.0)
    synthesis_relation_present = _has_synthesis_relation(answer)
    over_refusal = bool(required_evidence_all_hit and _answer_has_question_level_refusal(answer))
    passed = bool(
        required_evidence_all_hit
        and main_clause_in_answer
        and secondary_clause_in_answer
        and synthesis_relation_present
        and not over_refusal
    )
    if over_refusal:
        reason = "主辅证据已命中，但回答将可综合的问题整体拒答"
    elif not main_clause_in_answer:
        reason = "回答缺少主证据条款"
    elif not secondary_clause_in_answer:
        reason = "回答缺少辅助证据条款"
    elif not synthesis_relation_present:
        reason = "回答没有说明两个制度之间的关系"
    elif not required_evidence_all_hit:
        reason = "主辅证据未全部命中"
    else:
        reason = ""
    return {
        "enabled": True,
        "main_clause_in_answer": main_clause_in_answer,
        "secondary_clause_in_answer": secondary_clause_in_answer,
        "required_evidence_all_hit": required_evidence_all_hit,
        "synthesis_relation_present": synthesis_relation_present,
        "over_refusal": over_refusal,
        "passed": passed,
        "reason": reason,
    }


def _failure_reason(
    case,
    top1: bool,
    top5: bool,
    section: bool,
    answer_cov: int,
    citation: bool,
    refusal: bool,
    evidence: dict | None = None,
    cross_document: dict | None = None,
) -> str:
    if _is_refusal_case(case):
        return "" if refusal else "拒答失败：未覆盖问题没有明确说明知识库无依据"
    evidence = evidence or {}
    missing_required = evidence.get("missing_required_citations") or []
    if missing_required:
        if missing_required[0] == (evidence.get("required_citations") or [""])[0]:
            return f"主证据未命中：{missing_required[0]}"
        return f"辅助证据未命中：{missing_required[0]}"
    if not top5:
        return "文档未命中：期望来源未进入 Top-5"
    if not section:
        return "章节未命中：期望章节或条款未进入上下文"
    cross_document = cross_document or {}
    if cross_document.get("enabled") and not cross_document.get("passed"):
        if cross_document.get("over_refusal"):
            return "跨文档综合不足：主辅证据已命中，但回答过度拒答"
        if not cross_document.get("secondary_clause_in_answer"):
            return "跨文档综合不足：缺少辅助证据引用"
        if not cross_document.get("main_clause_in_answer"):
            return "跨文档综合不足：缺少主证据引用"
        if not cross_document.get("synthesis_relation_present"):
            return "跨文档综合不足：没有说明两个制度之间的关系"
    if answer_cov < 60:
        return "答案要点缺失：模型回答未覆盖主要标准答案"
    if not citation:
        return "引用不支持：回答来源不足以支撑结论"
    return "" if top1 else "排序偏弱：期望来源已进入 Top-5 但不是 Top-1"


def _fix_suggestion(reason: str) -> str:
    if reason.startswith("跨文档综合"):
        return "检查跨文档问答提示词和主辅证据排序，要求命中证据后给出受控综合结论。"
    if reason.startswith("文档未命中"):
        return "检查知识库是否已上传完整长文档，必要时降低阈值或增加关键词召回。"
    if reason.startswith("主证据") or reason.startswith("辅助证据"):
        return "检查跨文档题的主辅证据是否都已入库，并启用按 required_citations 的多证据召回。"
    if reason.startswith("章节未命中"):
        return "检查切片是否保留章节标题和条款编号，必要时优化按标题切分策略。"
    if reason.startswith("答案要点"):
        return "优化问答提示词，要求按标准条款逐点回答并引用来源。"
    if reason.startswith("引用"):
        return "增加引用校验，要求每个关键结论至少有一个来源片段支撑。"
    if reason.startswith("拒答"):
        return "强化无依据拒答提示词，并把 E 类问题纳入端到端回归。"
    if reason.startswith("排序"):
        return "检查 rerank 或融合排序，让更精确章节排到更靠前。"
    if reason.startswith("模型/API"):
        return "检查本机或服务器到嵌入模型、大模型和向量库的网络连通性；工程失败不计为RAG能力问题。"
    return ""


def _tokens(result: dict) -> tuple[int, int, int]:
    usage = result.get("usage") or {}
    return (
        int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        int(usage.get("total_tokens") or 0),
    )


def _ragas_warning_reason(reason: str) -> str:
    reason = str(reason or "").strip()
    if not reason:
        return ""
    return reason.replace("RAGAs未通过", "RAGAS诊断预警").replace("RAGAs评测失败", "RAGAS诊断失败")


def normalize_evaluation_mode(mode: str | None) -> str:
    normalized = (mode or "retrieval").strip().lower()
    normalized = LEGACY_MODE_MAP.get(normalized, normalized)
    if normalized not in EVALUATION_MODES:
        raise ValueError("评测模式必须是 retrieval、rules、ragas 或 combined")
    return normalized


def _generate_evaluation_answer_with_compat(
    question: str,
    contexts: list,
    case_payload: dict,
    model_config: dict | None = None,
) -> dict:
    try:
        return rag.generate_evaluation_answer(
            question,
            contexts,
            case_payload,
            model_config=model_config,
        )
    except TypeError as exc:
        if "model_config" not in str(exc):
            raise
        return rag.generate_evaluation_answer(question, contexts, case_payload)


def create_run(db: Session, *, kb_id: int | list[int], mode: str, actor: dict, dataset_version: str = FULL_DATASET_VERSION) -> EvaluationRun:
    mode = normalize_evaluation_mode(mode)
    enterprise_id = actor.get("enterprise_id")
    if enterprise_id is not None:
        kb_ids = kb_id if isinstance(kb_id, list) else [kb_id]
        requested = {int(item) for item in kb_ids}
        query = db.query(KnowledgeBase).filter(KnowledgeBase.id.in_(requested))
        rows = query.all()
        if rows:
            for kb in rows:
                if kb.enterprise_id != int(enterprise_id):
                    if not (kb.enterprise_id is None and enterprise_service.is_system_enterprise(db, int(enterprise_id))):
                        raise ValueError("知识库不存在")
            if {kb.id for kb in rows} != requested:
                raise ValueError("知识库不存在")
    dataset_summary = evaluation_dataset_service.get_dataset_summary(db, dataset_version, enterprise_id)
    if dataset_summary.get("status") != "approved":
        raise ValueError("评测题集尚未审核通过，不能运行评测")
    dataset_name = dataset_summary["name"]
    total_cases = int(dataset_summary.get("case_count") or 0)
    run = EvaluationRun(
        enterprise_id=enterprise_id,
        name=f"{dataset_name} · {mode}",
        kb_id=_json(kb_id),
        dataset_version=dataset_version,
        mode=mode,
        status="running",
        created_by=int(actor.get("sub", 0)),
        created_by_name=actor.get("display_name", ""),
        started_at=datetime.now(),
        summary_json=_json({
            "dataset": dataset_summary,
            "progress": _progress(total_cases, 0, "等待开始", ""),
        }),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def run_evaluation_job(run_id: int) -> None:
    with _RUN_LOCK:
        with SessionLocal() as db:
            run = db.get(EvaluationRun, run_id)
            if not run:
                return
            try:
                started = time.perf_counter()
                kb_scope = _loads(run.kb_id, 1)
                cases = _load_cases(db, run.dataset_version, run.enterprise_id)
                total = len(cases)
                case_results = []
                run.summary_json = _json({
                    **_loads_dict(run.summary_json),
                    "progress": _progress(total, 0, "运行中", ""),
                })
                db.commit()
                for index, case in enumerate(cases, start=1):
                    if _is_cancel_requested(db, run):
                        _finish_canceled_run(db, run, case_results, total, index - 1, started)
                        return
                    try:
                        item = _call_evaluate_case_with_retry(db, case, kb_scope, run.mode, run.enterprise_id)
                    except Exception as exc:
                        traceback.print_exc()
                        item = _failed_case_result(case, exc)
                    case_results.append(item)
                    db.add(_case_to_model(run.id, item, enterprise_id=run.enterprise_id))
                    run.summary_json = _json({
                        **_loads_dict(run.summary_json),
                        "progress": _progress(total, index, "运行中", case.case_id),
                    })
                    db.commit()
                    if _is_cancel_requested(db, run):
                        _finish_canceled_run(db, run, case_results, total, index, started)
                        return
                summary = _summarize(case_results)
                summary["progress"] = _progress(total, total, "已完成", "")
                run.summary_json = _json(summary)
                run.status = "success"
                run.finished_at = datetime.now()
                run.duration_ms = round((time.perf_counter() - started) * 1000)
                db.commit()
            except Exception as exc:
                traceback.print_exc()
                run.status = "failed"
                run.error_message = str(exc)
                run.finished_at = datetime.now()
                current_summary = _loads_dict(run.summary_json)
                progress = current_summary.get("progress") or {}
                run.summary_json = _json({
                    **current_summary,
                    "progress": {**progress, "status": "失败"},
                })
                db.commit()


def _is_cancel_requested(db: Session, run: EvaluationRun) -> bool:
    db.refresh(run)
    return run.status in {"canceling", "canceled"}


def _finish_canceled_run(
    db: Session,
    run: EvaluationRun,
    case_results: list[dict],
    total: int,
    completed: int,
    started: float,
) -> None:
    summary = _summarize(case_results) if case_results else _loads_dict(run.summary_json)
    summary["progress"] = _progress(total, completed, "已中断", "")
    run.summary_json = _json(summary)
    run.status = "canceled"
    run.finished_at = datetime.now()
    run.duration_ms = round((time.perf_counter() - started) * 1000)
    db.commit()


def cancel_run(db: Session, run_id: int, enterprise_id: int | None = None) -> EvaluationRun:
    run = get_run(db, run_id, enterprise_id)
    if not run:
        raise LookupError("评测任务不存在")
    if run.status not in {"running", "canceling"}:
        return run
    current_summary = _loads_dict(run.summary_json)
    progress = current_summary.get("progress") or {}
    run.status = "canceling"
    run.summary_json = _json({
        **current_summary,
        "progress": {**progress, "status": "正在中断"},
    })
    db.commit()
    db.refresh(run)
    return run


def mark_stale_running_runs_interrupted(db: Session) -> int:
    """应用重启后，数据库里遗留的运行中任务已没有后台执行线程，需要明确收尾。"""
    stale_runs = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.status.in_(["running", "canceling"]))
        .all()
    )
    if not stale_runs:
        return 0
    now = datetime.now()
    message = "服务重启导致评测任务中断，请重新运行评测。"
    for run in stale_runs:
        current_summary = _loads_dict(run.summary_json)
        progress = current_summary.get("progress") or {}
        run.status = "canceled"
        run.finished_at = now
        run.error_message = message
        run.summary_json = _json({
            **current_summary,
            "progress": {**progress, "status": "已中断，请重新评测"},
        })
    db.commit()
    return len(stale_runs)


def _failed_case_result(case, exc: Exception) -> dict:
    reason = f"模型/API失败：{str(exc) or exc.__class__.__name__}"[:255]
    return {
        "case": case,
        "contexts": [],
        "answer": "",
        "citations": [],
        "passed": False,
        "skipped": False,
        "top1_hit": False,
        "top3_hit": False,
        "top5_hit": False,
        "section_hit": False,
        # 检索阶段就没跑成，检索侧指标算未统计，不能用 False 冒充 0 命中。
        "retrieval_top1_hit": None,
        "retrieval_top3_hit": None,
        "retrieval_top5_hit": None,
        "retrieval_section_hit": None,
        # 端到端（不补召回）判定。None = 本次没统计，不能用 False 冒充不通过。
        "e2e_passed": None,
        "e2e_answer_coverage": None,
        "e2e_refusal_passed": None,
        "e2e_failure_reason": None,
        "answer_coverage": 0,
        "citation_passed": False,
        "refusal_passed": False,
        "cross_document_diagnostics": {"enabled": False},
        "latency_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "failure_reason": reason,
        "fix_suggestion": _fix_suggestion(reason),
    }


# 工程失败：外部服务的超时、连接中断、限流、5xx。它们不代表 RAG 能力，
# 用这个前缀统一标记，重试和统计时据此把它们摘出来。
ENGINEERING_FAILURE_PREFIX = "模型/API失败"

_RETRYABLE_ERROR_TYPES = {
    "APITimeoutError",
    "APIConnectionError",
    "APIConnectionTimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ConnectTimeoutError",
    "ReadTimeout",
    "ReadError",
    "RemoteProtocolError",
    "TimeoutException",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
}

_RETRYABLE_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "connection error",
    "connection reset",
    "connection aborted",
    "connection refused",
    "remote end closed",
    "remote protocol error",
    "temporarily unavailable",
    "rate limit",
    "too many requests",
    "502 bad gateway",
    "503 service",
    "504 gateway",
    "internal server error",
)


def _is_engineering_failure(item: dict) -> bool:
    """这条单题结果是不是工程失败，而不是 RAG 能力失败。"""
    return str(item.get("failure_reason") or "").startswith(ENGINEERING_FAILURE_PREFIX)


def _is_retryable_engineering_error(exc: Exception) -> bool:
    """判断异常是不是「重试一下就可能好」的工程问题。

    异常类型优先、错误文本兜底：LangChain 会把底层 openai/httpx 异常包一层，
    类型判断覆盖直传的情况，文本兜底覆盖被包装过的情况。
    """
    if {cls.__name__ for cls in type(exc).__mro__} & _RETRYABLE_ERROR_TYPES:
        return True
    text = f"{exc.__class__.__name__} {exc}".lower()
    return any(marker in text for marker in _RETRYABLE_ERROR_MARKERS)


def _call_evaluate_case_with_retry(
    db: Session,
    case,
    kb_scope,
    mode: str,
    enterprise_id: int | None,
) -> dict:
    """对工程失败自动重试；非工程失败（例如 Key 无效、参数错误）直接抛出。

    长跑评测要发几千次外部请求，一次网络抖动不该把整道题判成 RAG 能力失败。
    """
    attempts = max(EVALUATION_API_RETRY_ATTEMPTS, 1)
    for attempt in range(1, attempts + 1):
        try:
            return _call_evaluate_case(db, case, kb_scope, mode, enterprise_id)
        except Exception as exc:
            if attempt >= attempts or not _is_retryable_engineering_error(exc):
                raise
            delay = EVALUATION_API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            print(
                f"[评测重试] {getattr(case, 'case_id', '')} 第 {attempt} 次工程失败，"
                f"{delay:.0f}s 后重试：{exc}",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _call_evaluate_case(
    db: Session,
    case,
    kb_scope: int | list[int],
    mode: str,
    enterprise_id: int | None = None,
) -> dict:
    try:
        return _evaluate_case(db, case, kb_scope, mode, enterprise_id=enterprise_id)
    except TypeError as exc:
        if "positional" not in str(exc) and "enterprise_id" not in str(exc):
            raise
        return _evaluate_case(case, kb_scope, mode)


def _evaluate_case(
    db_or_case,
    case_or_kb_scope,
    kb_scope_or_mode=None,
    mode: str | None = None,
    enterprise_id: int | None = None,
) -> dict:
    if isinstance(db_or_case, Session):
        db = db_or_case
        case = case_or_kb_scope
        kb_scope = kb_scope_or_mode
        mode = mode or "retrieval"
    else:
        db = None
        case = db_or_case
        kb_scope = case_or_kb_scope
        mode = kb_scope_or_mode or "retrieval"
    mode = normalize_evaluation_mode(mode)
    t0 = time.perf_counter()
    model_config = embed_config = None
    if enterprise_id is not None:
        with SessionLocal() as scoped_db:
            model_config = enterprise_service.require_enterprise_deepseek_key(db or scoped_db, enterprise_id)
            embed_config = enterprise_service.require_enterprise_siliconflow_key(db or scoped_db, enterprise_id)
    try:
        _, contexts = rag.retrieve_contexts(
            case.question,
            kb_id=kb_scope,
            top_k=5,
            enterprise_id=enterprise_id,
            model_config=model_config,
            embed_config=embed_config,
        )
    except TypeError as exc:
        if "enterprise_id" not in str(exc) and "model_config" not in str(exc) and "embed_config" not in str(exc):
            raise
        _, contexts = rag.retrieve_contexts(case.question, kb_id=kb_scope, top_k=5)
    # 补召回之前的那一批就是用户真实路径拿到的证据；端到端判定要用它。
    pre_contexts = list(contexts)
    # 补召回之前先记录一次：这才是真实检索的排序表现，用来单独衡量检索质量。
    retrieval_top1 = _doc_hit(case.expected_doc, contexts, 1)
    retrieval_top3 = _doc_hit(case.expected_doc, contexts, 3)
    retrieval_top5 = _doc_hit(case.expected_doc, contexts, 5)
    retrieval_section = _section_hit(case.expected_section, case.expected_clause, contexts)
    contexts = _augment_required_contexts(
        kb_scope,
        contexts,
        tuple(getattr(case, "required_citations", ()) or ()),
        top_k=5,
        enterprise_id=enterprise_id,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000)
    # 补召回之后：生成阶段实际拿到的证据是否完整，属于“证据可用率”，不是检索排序质量。
    top1 = _doc_hit(case.expected_doc, contexts, 1)
    top3 = _doc_hit(case.expected_doc, contexts, 3)
    top5 = _doc_hit(case.expected_doc, contexts, 5)
    section = _section_hit(case.expected_section, case.expected_clause, contexts)
    evidence = _evidence_diagnostics(case, contexts)
    evidence_ok = evidence["required_citation_hit_rate"] in (None, 100.0, 100)
    answer = ""
    citations = []
    answer_cov = 0
    citation_passed = False
    refusal = False
    input_tokens = output_tokens = total_tokens = 0

    if mode in {"rules", "ragas", "combined"}:
        case_payload = _case_payload(case)
        qa_result = _generate_evaluation_answer_with_compat(case.question, contexts, case_payload, model_config)
        answer = qa_result.get("answer", "")
        citations = contexts
        answer_cov = _answer_coverage(answer, case.answer_points)
        citation_passed = _is_refusal_case(case) or any(_doc_hit(case.expected_doc, [src], 1) for src in citations)
        refusal = _refusal_passed(answer)
        input_tokens, output_tokens, total_tokens = _tokens(qa_result)
        early_cross_document = _cross_document_diagnostics(case, answer, evidence)
        if early_cross_document.get("over_refusal") and early_cross_document.get("required_evidence_all_hit"):
            repair_payload = {
                **case_payload,
                "repair_cross_document_over_refusal": True,
                "previous_answer": answer,
            }
            repair_result = _generate_evaluation_answer_with_compat(case.question, contexts, repair_payload, model_config)
            repair_answer = repair_result.get("answer", "")
            repair_cross_document = _cross_document_diagnostics(case, repair_answer, evidence)
            if repair_answer and not repair_cross_document.get("over_refusal"):
                answer = repair_answer
                qa_result = repair_result
                answer_cov = _answer_coverage(answer, case.answer_points)
                refusal = _refusal_passed(answer)
                input_tokens, output_tokens, total_tokens = _tokens(qa_result)
    elif _is_refusal_case(case):
        refusal = False
    else:
        answer_cov = 100 if top5 and section else 0
        citation_passed = top5 and section

    ragas_scores = {}
    ragas_passed = False
    ragas_skip_reason = ""
    ragas_failure_reason = ""
    if mode in {"ragas", "combined"}:
        if _is_refusal_case(case):
            ragas_skip_reason = "未覆盖拒答题不适用 RAGAs，使用拒答规则评分"
        else:
            try:
                ragas_result = evaluate_ragas_case(
                    question=case.question,
                    answer=answer,
                    contexts=contexts,
                    reference=getattr(case, "standard_answer", "") or "\n".join(case.answer_points),
                    model_config=model_config,
                    embed_config=embed_config,
                )
                ragas_scores = ragas_result.get("scores") or {}
                ragas_passed = bool(ragas_result.get("passed"))
                ragas_failure_reason = ragas_result.get("failure_reason") or ""
                ragas_skip_reason = ragas_result.get("skip_reason") or ""
            except Exception as exc:
                ragas_failure_reason = f"RAGAs评测失败：{str(exc) or exc.__class__.__name__}"[:255]

    cross_document = _cross_document_diagnostics(case, answer, evidence)
    if cross_document.get("passed"):
        answer_cov = max(answer_cov, 60)
    cross_document_ok = not cross_document.get("enabled") or bool(cross_document.get("passed"))
    rules_passed = bool(top5 and section and evidence_ok and answer_cov >= 60 and citation_passed and cross_document_ok)
    ragas_warning_reason = _ragas_warning_reason(ragas_failure_reason)
    needs_review = bool(rules_passed and ragas_warning_reason)
    if _is_refusal_case(case):
        passed = bool(refusal) if mode in {"rules", "ragas", "combined"} else False
        skipped = mode == "retrieval"
    elif mode == "ragas":
        passed = rules_passed
        skipped = False
    elif mode == "combined":
        passed = rules_passed
        skipped = False
    else:
        passed = rules_passed
        skipped = False

    reason = "" if skipped or passed else _failure_reason(
        case,
        top1,
        top5,
        section,
        answer_cov,
        citation_passed,
        refusal,
        evidence,
        cross_document,
    )
    return {
        "case": case,
        "contexts": contexts,
        "answer": answer,
        "citations": citations,
        "passed": passed,
        "skipped": skipped,
        "top1_hit": top1,
        "top3_hit": top3,
        "top5_hit": top5,
        "section_hit": section,
        "retrieval_top1_hit": retrieval_top1,
        "retrieval_top3_hit": retrieval_top3,
        "retrieval_top5_hit": retrieval_top5,
        "retrieval_section_hit": retrieval_section,
        "answer_coverage": answer_cov,
        "citation_passed": citation_passed,
        "refusal_passed": refusal,
        "ragas_scores": ragas_scores,
        "ragas_passed": ragas_passed,
        "ragas_skip_reason": ragas_skip_reason,
        "ragas_warning_reason": ragas_warning_reason,
        "needs_review": needs_review,
        "evidence_diagnostics": evidence,
        "cross_document_diagnostics": cross_document,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "failure_reason": reason,
        "fix_suggestion": _fix_suggestion(reason),
        # 端到端（用户真实路径）那一组：不补召回、不做过度拒答修复
        **_end_to_end_verdict(
            case,
            pre_contexts,
            contexts,
            mode,
            {"passed": passed, "answer_coverage": answer_cov,
             "refusal_passed": refusal, "failure_reason": reason},
            {"top1": retrieval_top1, "top5": retrieval_top5, "section": retrieval_section},
            model_config,
        ),
    }


def _case_payload(case) -> dict:
    """评测生成用的题目载荷。

    主链路和端到端那一轮共用同一个构造，保证两次生成之间唯一的差别是证据。
    """
    return {
        "category": case.category,
        "expected_doc": case.expected_doc,
        "expected_section": case.expected_section,
        "expected_clause": case.expected_clause,
        "answer_points": list(case.answer_points),
        "required_citations": list(getattr(case, "required_citations", ()) or ()),
        "negative_case": _is_refusal_case(case),
    }


def _same_context_list(left: list, right: list) -> bool:
    """两份上下文是不是同一批、同一顺序。用来判断补召回有没有真的改变输入。"""
    if len(left) != len(right):
        return False
    return all(
        (a.get("source", ""), a.get("text", "")) == (b.get("source", ""), b.get("text", ""))
        for a, b in zip(left, right)
    )


def _end_to_end_verdict(case, pre_contexts, post_contexts, mode, main, retrieval, model_config) -> dict:
    """端到端判定：用户真实路径上，同一个问题、同一套评分规则，但没有补召回、
    也没有过度拒答的二次生成修复。

    为什么要单独算这一组：
    补召回之后的总通过率要读作「证据齐全时生成端不出错的比例」，带着一个用户
    根本拿不到的限定（用户提问时系统不知道正确答案）。两组并列才能看出"评测口径"
    与"用户真实路径"差多少。

    为什么不重复生成：
    补召回只做两件事 —— 补进缺失的必需证据、把必需证据提到上下文前部。如果它
    什么都没改（两份列表逐项一致），两次生成的输入就是同一份，结果必然相同，
    直接复用主结果即可，不必再花一次模型调用。所以这一组只让那些"补召回确实
    改变过输入"的题多跑一次，评测总时长几乎不变。

    返回的 4 个键直接并进条目字典；retrieval 模式不生成答案，三者留 None 表示未统计。
    """
    if mode not in {"rules", "ragas", "combined"}:
        return {"e2e_passed": None, "e2e_answer_coverage": None,
                "e2e_refusal_passed": None, "e2e_failure_reason": None}

    if _same_context_list(pre_contexts, post_contexts):
        return {
            "e2e_passed": main["passed"],
            "e2e_answer_coverage": main["answer_coverage"],
            "e2e_refusal_passed": main["refusal_passed"],
            "e2e_failure_reason": main["failure_reason"],
        }

    # 上下文真的不一样 → 用真实上下文重新生成一次，才知道用户会拿到什么。
    # 这里刻意不触发过度拒答的二次生成修复：那个机制只有评测链路有，用户拿不到。
    evidence = _evidence_diagnostics(case, pre_contexts)
    evidence_ok = evidence["required_citation_hit_rate"] in (None, 100.0, 100)
    qa_result = _generate_evaluation_answer_with_compat(case.question, pre_contexts, _case_payload(case), model_config)
    answer = qa_result.get("answer", "")
    refusal = _refusal_passed(answer)

    if _is_refusal_case(case):
        return {
            "e2e_passed": bool(refusal),
            "e2e_answer_coverage": 0,
            "e2e_refusal_passed": refusal,
            "e2e_failure_reason": "" if refusal else "未覆盖问题未正确拒答",
        }

    answer_cov = _answer_coverage(answer, case.answer_points)
    cross_document = _cross_document_diagnostics(case, answer, evidence)
    if cross_document.get("passed"):
        answer_cov = max(answer_cov, 60)
    citation_passed = any(_doc_hit(case.expected_doc, [src], 1) for src in pre_contexts)
    # 评分规则和主链路完全一致，唯一的差别是证据只用真实检索到的那批 ——
    # 这样两个通过率的差才能干净地归因到"补召回替用户补齐了多少"。
    passed = bool(
        retrieval["top5"] and retrieval["section"] and evidence_ok
        and answer_cov >= 60 and citation_passed
        and (not cross_document.get("enabled") or bool(cross_document.get("passed")))
    )
    reason = "" if passed else _failure_reason(
        case,
        retrieval["top1"],
        retrieval["top5"],
        retrieval["section"],
        answer_cov,
        citation_passed,
        refusal,
        evidence,
        cross_document,
    )
    return {
        "e2e_passed": passed,
        "e2e_answer_coverage": answer_cov,
        "e2e_refusal_passed": refusal,
        "e2e_failure_reason": reason,
    }


def _case_to_model(run_id: int, item: dict, enterprise_id: int | None = None) -> EvaluationCaseResult:
    case = item["case"]
    return EvaluationCaseResult(
        enterprise_id=enterprise_id,
        run_id=run_id,
        case_id=case.case_id,
        category=case.category,
        question=case.question,
        expected_doc=case.expected_doc,
        expected_section=case.expected_section,
        expected_clause=case.expected_clause,
        expected_keywords_json=_json(list(case.expected_keywords)),
        answer_points_json=_json(list(case.answer_points)),
        retrieved_contexts_json=_json(item["contexts"]),
        answer=item["answer"],
        citations_json=_json(item["citations"]),
        passed=1 if item["passed"] else 0,
        skipped=1 if item["skipped"] else 0,
        top1_hit=1 if item["top1_hit"] else 0,
        top3_hit=1 if item["top3_hit"] else 0,
        top5_hit=1 if item["top5_hit"] else 0,
        section_hit=1 if item["section_hit"] else 0,
        # 补召回前的检索表现：None 表示本次没统计，不能写成 0。
        retrieval_top1_hit=_optional_hit(item.get("retrieval_top1_hit")),
        retrieval_top3_hit=_optional_hit(item.get("retrieval_top3_hit")),
        retrieval_top5_hit=_optional_hit(item.get("retrieval_top5_hit")),
        retrieval_section_hit=_optional_hit(item.get("retrieval_section_hit")),
        # 端到端（用户真实路径）判定：None 表示本次没统计（旧数据或 retrieval 模式）。
        e2e_passed=_optional_hit(item.get("e2e_passed")),
        e2e_answer_coverage=(
            None if item.get("e2e_answer_coverage") is None else int(item["e2e_answer_coverage"])
        ),
        e2e_refusal_passed=_optional_hit(item.get("e2e_refusal_passed")),
        e2e_failure_reason=item.get("e2e_failure_reason"),
        answer_coverage=item["answer_coverage"],
        citation_passed=1 if item["citation_passed"] else 0,
        refusal_passed=1 if item["refusal_passed"] else 0,
        ragas_scores_json=_json(item.get("ragas_scores") or {}),
        ragas_passed=1 if item.get("ragas_passed") else 0,
        ragas_skip_reason=item.get("ragas_skip_reason") or "",
        ragas_warning_reason=item.get("ragas_warning_reason") or "",
        needs_review=1 if item.get("needs_review") else 0,
        latency_ms=item["latency_ms"],
        input_tokens=item["input_tokens"],
        output_tokens=item["output_tokens"],
        total_tokens=item["total_tokens"],
        failure_reason=item["failure_reason"],
        fix_suggestion=item["fix_suggestion"],
    )


def _rate(count: int, total: int) -> float:
    return round(count / max(total, 1) * 100, 2)


def _optional_hit(value) -> int | None:
    """命中标记的落库转换：None（本次未统计）保持 None，不能退化成 0。"""
    if value is None:
        return None
    return 1 if value else 0


def _optional_rate(items: list[dict], field: str) -> float | None:
    """统计命中字段的比例；没有数据时返回 None，报告里显示“未统计”而不是 0%。"""
    hits = [item for item in items if item.get(field) is not None]
    if not hits:
        return None
    return _rate(sum(1 for item in hits if item[field]), len(hits))


def _row_optional_hit(row, field: str) -> bool | None:
    """从 ORM 行读可选的命中标记；NULL 表示该次评测没有统计这一项。"""
    value = getattr(row, field, None)
    return None if value is None else bool(value)


def _progress(total: int, completed: int, status: str, current_case: str) -> dict:
    return {
        "total": total,
        "completed": completed,
        "percent": _rate(completed, total) if total else 0,
        "status": status,
        "current_case": current_case,
    }


def _summarize(items: list[dict]) -> dict:
    evaluated = [item for item in items if not item["skipped"]]
    # 工程失败（超时、断连、限流、5xx）不代表 RAG 能力，从能力指标的分母里摘出去。
    # 但要单独计数、并在失败原因分布里保留，不能藏起来。
    engineering_failed = [item for item in evaluated if _is_engineering_failure(item)]
    scored = [item for item in evaluated if not _is_engineering_failure(item)]
    non_refusal = [item for item in scored if not _is_refusal_case(item["case"])]
    refusal_items = [item for item in scored if _is_refusal_case(item["case"])]
    categories = {}
    label_map = CATEGORY_LABELS_360 if any(item["case"].category == "F" for item in items) else CATEGORY_LABELS
    for key, label in label_map.items():
        rows = [item for item in items if item["case"].category == key]
        judged = [item for item in rows if not item["skipped"]]
        categories[key] = {
            "label": label,
            "case_count": len(rows),
            "pass_count": sum(1 for item in judged if item["passed"]),
            "skipped_count": sum(1 for item in rows if item["skipped"]),
            "pass_rate": None if not judged else _rate(sum(1 for item in judged if item["passed"]), len(judged)),
            "e2e_pass_count": sum(1 for item in judged if item.get("e2e_passed")),
            "e2e_pass_rate": _optional_rate(judged, "e2e_passed"),
        }
    failure_reasons: dict[str, int] = {}
    for item in evaluated:
        if item["passed"]:
            continue
        if item["failure_reason"]:
            failure_reasons[item["failure_reason"]] = failure_reasons.get(item["failure_reason"], 0) + 1
    latencies = [item["latency_ms"] for item in scored]
    evidence_items = [
        item.get("evidence_diagnostics") or {}
        for item in non_refusal
        if (item.get("evidence_diagnostics") or {}).get("required_citation_hit_rate") is not None
    ]
    ragas_items = [item for item in non_refusal if item.get("ragas_scores") or item.get("ragas_skip_reason")]
    # 只有统计过补召回前检索表现的条目才计入检索侧指标，避免把旧数据当成 0 命中。
    retrieval_items = [item for item in non_refusal if item.get("retrieval_top1_hit") is not None]
    # 端到端那一组同理：旧评测（没有这几列）与 retrieval 模式都留 None，显示"未统计"。
    e2e_items = [item for item in scored if item.get("e2e_passed") is not None]
    ragas_score_keys = sorted({
        key
        for item in ragas_items
        for key, value in (item.get("ragas_scores") or {}).items()
        if isinstance(value, (int, float))
    })
    ragas_scores = {
        key: round(mean([float(item["ragas_scores"][key]) for item in ragas_items if key in (item.get("ragas_scores") or {})]), 4)
        for key in ragas_score_keys
    }
    p95 = 0
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, round(len(ordered) * 0.95) - 1)]
    return {
        "dataset": getattr(items[0]["case"], "dataset_summary", None) if items else {},
        "case_count": len(items),
        "evaluated_case_count": len(evaluated),
        "scored_case_count": len(scored),
        # 工程失败单列：既不计入能力指标分母，也不隐藏，报告里会单独展示。
        "engineering_failure_count": len(engineering_failed),
        "skipped_case_count": sum(1 for item in items if item["skipped"]),
        "pass_count": sum(1 for item in scored if item["passed"]),
        "pass_rate": _rate(sum(1 for item in scored if item["passed"]), len(scored)),
        # 端到端通过率：只用真实检索到的证据评分（不补召回、不做过度拒答修复），
        # 也就是用户真实路径上会拿到什么结果。旧数据/retrieval 模式返回 None。
        "e2e_case_count": len(e2e_items),
        "e2e_pass_rate": _optional_rate(e2e_items, "e2e_passed"),
        "needs_review_count": sum(1 for item in scored if item.get("needs_review")),
        "ragas_warning_count": sum(1 for item in scored if item.get("ragas_warning_reason")),
        "ragas_warning_rate": _rate(sum(1 for item in scored if item.get("ragas_warning_reason")), len(ragas_items)) if ragas_items else None,
        "top1_doc_hit_rate": _rate(sum(1 for item in non_refusal if item["top1_hit"]), len(non_refusal)),
        "top3_doc_hit_rate": _rate(sum(1 for item in non_refusal if item["top3_hit"]), len(non_refusal)),
        "top5_doc_hit_rate": _rate(sum(1 for item in non_refusal if item["top5_hit"]), len(non_refusal)),
        "section_hit_rate": _rate(sum(1 for item in non_refusal if item["section_hit"]), len(non_refusal)),
        # 补召回之前的真实检索表现。旧评测没有这几列，返回 None 表示“未统计”。
        "retrieval_case_count": len(retrieval_items),
        "retrieval_top1_doc_hit_rate": _optional_rate(retrieval_items, "retrieval_top1_hit"),
        "retrieval_top3_doc_hit_rate": _optional_rate(retrieval_items, "retrieval_top3_hit"),
        "retrieval_top5_doc_hit_rate": _optional_rate(retrieval_items, "retrieval_top5_hit"),
        "retrieval_section_hit_rate": _optional_rate(retrieval_items, "retrieval_section_hit"),
        "answer_coverage_rate": round(mean([item["answer_coverage"] for item in scored]), 2) if scored else 0,
        "answer_coverage_pass_rate": _rate(sum(1 for item in scored if item["answer_coverage"] >= 60), len(scored)),
        "citation_pass_rate": _rate(sum(1 for item in non_refusal if item["citation_passed"]), len(non_refusal)),
        "required_citation_hit_rate": round(mean([item["required_citation_hit_rate"] for item in evidence_items]), 2) if evidence_items else 0,
        "primary_evidence_hit_rate": _rate(sum(1 for item in evidence_items if item.get("primary_evidence_hit")), len(evidence_items)),
        "secondary_evidence_hit_rate": _rate(
            sum(1 for item in evidence_items if item.get("secondary_evidence_hit") is not False),
            len(evidence_items),
        ),
        "refusal_pass_rate": None if not refusal_items else _rate(sum(1 for item in refusal_items if item["refusal_passed"]), len(refusal_items)),
        "ragas_pass_rate": None if not ragas_items else _rate(sum(1 for item in ragas_items if item.get("ragas_passed")), len(ragas_items)),
        "ragas_scores": ragas_scores,
        "avg_latency_ms": round(mean(latencies)) if latencies else 0,
        "p95_latency_ms": p95,
        "token_usage": {
            "input_tokens": sum(item["input_tokens"] for item in evaluated),
            "output_tokens": sum(item["output_tokens"] for item in evaluated),
            "total_tokens": sum(item["total_tokens"] for item in evaluated),
        },
        "failure_reasons": failure_reasons,
        "categories": categories,
    }


def list_runs(db: Session, enterprise_id: int | None = None) -> list[dict]:
    query = db.query(EvaluationRun)
    if enterprise_id is not None:
        query = query.filter(EvaluationRun.enterprise_id == enterprise_id)
    runs = query.order_by(EvaluationRun.created_at.desc()).limit(20).all()
    return [serialize_run(run, db) for run in runs]


def get_run(db: Session, run_id: int, enterprise_id: int | None = None) -> EvaluationRun | None:
    run = db.get(EvaluationRun, run_id)
    if run and enterprise_id is not None and run.enterprise_id != enterprise_id:
        return None
    return run


def delete_run(db: Session, run_id: int, enterprise_id: int | None = None) -> dict:
    run = get_run(db, run_id, enterprise_id)
    if not run:
        raise LookupError("评测任务不存在")
    result_query = db.query(EvaluationCaseResult).filter(EvaluationCaseResult.run_id == run_id)
    if enterprise_id is not None:
        result_query = result_query.filter(EvaluationCaseResult.enterprise_id == enterprise_id)
    result_query.delete()
    db.delete(run)
    db.commit()
    return {"deleted": True, "id": run_id}


def delete_runs(db: Session, run_ids: list[int], enterprise_id: int | None = None) -> dict:
    ids = sorted({int(run_id) for run_id in run_ids if int(run_id) > 0})
    if not ids:
        return {"deleted_count": 0, "ids": []}
    query = db.query(EvaluationRun.id).filter(EvaluationRun.id.in_(ids))
    if enterprise_id is not None:
        query = query.filter(EvaluationRun.enterprise_id == enterprise_id)
    existing_ids = [row.id for row in query.all()]
    if not existing_ids:
        return {"deleted_count": 0, "ids": []}
    result_query = db.query(EvaluationCaseResult).filter(EvaluationCaseResult.run_id.in_(existing_ids))
    if enterprise_id is not None:
        result_query = result_query.filter(EvaluationCaseResult.enterprise_id == enterprise_id)
    result_query.delete(synchronize_session=False)
    run_query = db.query(EvaluationRun).filter(EvaluationRun.id.in_(existing_ids))
    if enterprise_id is not None:
        run_query = run_query.filter(EvaluationRun.enterprise_id == enterprise_id)
    run_query.delete(synchronize_session=False)
    db.commit()
    return {"deleted_count": len(existing_ids), "ids": existing_ids}


def list_cases(
    db: Session,
    run_id: int,
    category: str | None = None,
    passed: str | None = None,
    failure_reason: str | None = None,
    enterprise_id: int | None = None,
) -> list[dict]:
    run = get_run(db, run_id, enterprise_id)
    if not run:
        return []
    query = db.query(EvaluationCaseResult).filter(EvaluationCaseResult.run_id == run_id)
    if enterprise_id is not None:
        query = query.filter(EvaluationCaseResult.enterprise_id == enterprise_id)
    if category:
        query = query.filter(EvaluationCaseResult.category == category)
    if passed in {"true", "false"}:
        query = query.filter(EvaluationCaseResult.passed == (1 if passed == "true" else 0))
    if failure_reason:
        query = query.filter(EvaluationCaseResult.failure_reason == failure_reason)
    cases_by_id = _case_map_for_run(db, run) if run else {}
    return [serialize_case(row, cases_by_id.get(row.case_id)) for row in query.order_by(EvaluationCaseResult.case_id).all()]


def _case_map_for_run(db: Session, run: EvaluationRun) -> dict[str, object]:
    try:
        return {
            case.case_id: case
            for case in _load_cases(db, run.dataset_version, run.enterprise_id)
        }
    except Exception:
        return {}


def _backfill_evidence_summary(db: Session | None, run: EvaluationRun, summary: dict) -> dict:
    if not db or all(
        key in summary
        for key in ("required_citation_hit_rate", "primary_evidence_hit_rate", "secondary_evidence_hit_rate")
    ):
        return summary
    cases_by_id = _case_map_for_run(db, run)
    if not cases_by_id:
        return summary
    evidence_items = []
    query = db.query(EvaluationCaseResult).filter(EvaluationCaseResult.run_id == run.id)
    if run.enterprise_id is not None:
        query = query.filter(EvaluationCaseResult.enterprise_id == run.enterprise_id)
    rows = query.all()
    for row in rows:
        case = cases_by_id.get(row.case_id)
        if not case:
            continue
        evidence = _evidence_diagnostics(case, _loads(row.retrieved_contexts_json, []) or [])
        if evidence.get("required_citation_hit_rate") is not None:
            evidence_items.append(evidence)
    if not evidence_items:
        return summary
    return {
        **summary,
        "required_citation_hit_rate": round(mean([item["required_citation_hit_rate"] for item in evidence_items]), 2),
        "primary_evidence_hit_rate": _rate(sum(1 for item in evidence_items if item.get("primary_evidence_hit")), len(evidence_items)),
        "secondary_evidence_hit_rate": _rate(
            sum(1 for item in evidence_items if item.get("secondary_evidence_hit") is not False),
            len(evidence_items),
        ),
    }


def serialize_run(run: EvaluationRun, db: Session | None = None) -> dict:
    summary = _backfill_evidence_summary(db, run, _loads_dict(run.summary_json))
    return {
        "id": run.id,
        "name": run.name,
        "kb_id": _loads(run.kb_id, run.kb_id),
        "dataset_version": run.dataset_version,
        "mode": run.mode,
        "status": run.status,
        "created_by": run.created_by,
        "created_by_name": run.created_by_name,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": run.duration_ms,
        "summary": summary,
        "error_message": run.error_message,
        "created_at": run.created_at,
    }


def serialize_case(row: EvaluationCaseResult, case: object | None = None) -> dict:
    contexts = _loads(row.retrieved_contexts_json, []) or []
    evidence = _evidence_diagnostics(case, contexts) if case else None
    cross_document = _cross_document_diagnostics(case, row.answer or "", evidence) if case else {"enabled": False}
    return {
        "id": row.id,
        "run_id": row.run_id,
        "case_id": row.case_id,
        "category": row.category,
        "question": row.question,
        "expected_doc": row.expected_doc,
        "expected_section": row.expected_section,
        "expected_clause": row.expected_clause,
        "expected_keywords": _loads(row.expected_keywords_json, []),
        "answer_points": _loads(row.answer_points_json, []),
        "retrieved_contexts": contexts,
        "answer": row.answer,
        "citations": _loads(row.citations_json, []),
        "passed": bool(row.passed),
        "skipped": bool(row.skipped),
        "top1_hit": bool(row.top1_hit),
        "top3_hit": bool(row.top3_hit),
        "top5_hit": bool(row.top5_hit),
        "section_hit": bool(row.section_hit),
        # 补召回前的检索表现；旧数据为 NULL，返回 None 让前端显示“未统计”。
        "retrieval_top1_hit": _row_optional_hit(row, "retrieval_top1_hit"),
        "retrieval_top3_hit": _row_optional_hit(row, "retrieval_top3_hit"),
        "retrieval_top5_hit": _row_optional_hit(row, "retrieval_top5_hit"),
        "retrieval_section_hit": _row_optional_hit(row, "retrieval_section_hit"),
        # 端到端（用户真实路径）判定；旧数据为 NULL，前端显示"未统计"。
        "e2e_passed": _row_optional_hit(row, "e2e_passed"),
        "e2e_answer_coverage": getattr(row, "e2e_answer_coverage", None),
        "e2e_refusal_passed": _row_optional_hit(row, "e2e_refusal_passed"),
        "e2e_failure_reason": getattr(row, "e2e_failure_reason", None) or "",
        "answer_coverage": row.answer_coverage,
        "citation_passed": bool(row.citation_passed),
        "refusal_passed": bool(row.refusal_passed),
        "ragas_scores": _loads(row.ragas_scores_json, {}),
        "ragas_passed": bool(row.ragas_passed),
        "ragas_skip_reason": row.ragas_skip_reason,
        "ragas_warning_reason": getattr(row, "ragas_warning_reason", "") or "",
        "needs_review": bool(getattr(row, "needs_review", 0)),
        "latency_ms": row.latency_ms,
        "token_usage": {
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "total_tokens": row.total_tokens,
        },
        "failure_reason": row.failure_reason,
        "fix_suggestion": row.fix_suggestion,
        "required_citations": list(getattr(case, "required_citations", ()) or ()) if case else [],
        "evidence_diagnostics": evidence,
        "cross_document_diagnostics": cross_document,
    }


def generate_report(db: Session, run_id: int, enterprise_id: int | None = None) -> dict:
    run = get_run(db, run_id, enterprise_id)
    if not run:
        raise LookupError("评测任务不存在")
    data = serialize_run(run, db)
    cases = list_cases(db, run_id, enterprise_id=enterprise_id)
    summary = data["summary"]
    dataset_meta = summary.get("dataset") or evaluation_dataset_service.get_dataset_summary(
        db,
        data["dataset_version"],
        enterprise_id,
    )
    failed = [case for case in cases if not case["passed"] and not case["skipped"]][:10]
    review_cases = [case for case in cases if case.get("needs_review")][:10]
    category_lines = _report_category_lines(summary.get("categories") or {})
    doc_lines = _report_doc_lines(cases)
    failure_lines = _report_failure_lines(summary.get("failure_reasons") or {})
    metric_lines = _report_core_metric_lines(summary, data["mode"])
    lines = [
        f"# {data['name']} 评测报告",
        "",
        "## 一、评测环境",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        "| 前端 | Vue3 + Element Plus + Vite |",
        "| 后端 | FastAPI + LangChain + DeepSeek |",
        "| 关系库 | MySQL / SQLite 兼容 |",
        f"| 向量库 | Zilliz Cloud {MILVUS_COLLECTION} |",
        f"| 知识库 | {data['kb_id']} |",
        f"| 评测集 | {data['dataset_version']} |",
        f"| 题集来源 | {dataset_meta.get('source_type', '')} |",
        f"| 审核状态 | {dataset_meta.get('review_status', '')} |",
        f"| 审核人 | {dataset_meta.get('reviewed_by') or '-'} |",
        f"| 模式 | {data['mode']} |",
        "",
        "## 二、核心指标",
        "",
        *metric_lines,
        "",
    ]
    section_no = 3
    if _report_should_include_ragas(data["mode"]):
        lines.extend([
            f"## {section_no}、RAGAS 辅助诊断",
            "",
            "RAGAS 作为第三方通用 RAG 质量诊断，用于提示忠实度、上下文召回、事实一致性和回答相关性风险；总通过率仍以企业制度主评测规则为准。",
            "",
            *_report_ragas_lines(summary),
            "",
        ])
        section_no += 1
    lines.extend([
        f"## {section_no}、题型通过率",
        "",
        *category_lines,
        "",
        f"## {section_no + 1}、文档通过率",
        "",
        *doc_lines,
        "",
        f"## {section_no + 2}、主失败原因分布",
        "",
        *failure_lines,
        "",
        f"## {section_no + 3}、失败样例",
        "",
    ])
    if failed:
        lines.extend(["| 题号 | 问题 | 失败原因 | 跨文档诊断 | 修复建议 |", "| --- | --- | --- | --- | --- |"])
        for case in failed:
            lines.append(
                f"| {case['case_id']} | {case['question']} | {case['failure_reason']} | "
                f"{_format_cross_document_report(case.get('cross_document_diagnostics'))} | {case['fix_suggestion']} |"
            )
    else:
        lines.append("本次评测没有失败样例。")
    if _report_should_include_ragas(data["mode"]):
        lines.extend([
            "",
            f"## {section_no + 4}、需复核样例",
            "",
        ])
        if review_cases:
            lines.extend(["| 题号 | 问题 | RAGAS 诊断预警 | 建议复核点 |", "| --- | --- | --- | --- |"])
            for case in review_cases:
                lines.append(
                    f"| {case['case_id']} | {case['question']} | "
                    f"{case.get('ragas_warning_reason') or '-'} | "
                    "业务规则已通过，建议检查回答是否过长、是否包含非必要补充或上下文片段是否过宽。 |"
                )
        else:
            lines.append("本次评测没有需复核样例。")
    return {"run_id": run_id, "markdown": "\n".join(lines)}


def _format_report_rate(value) -> str:
    if value is None:
        return "未运行"
    return f"{value}%"


def _report_has_generation_metrics(mode: str | None) -> bool:
    return normalize_evaluation_mode(mode) in {"rules", "combined"}


def _report_should_include_ragas(mode: str | None) -> bool:
    return normalize_evaluation_mode(mode) in {"ragas", "combined"}


def _report_core_metric_lines(summary: dict, mode: str | None) -> list[str]:
    # 检索侧拆成两组：补召回前是真实检索能力，补召回后是生成拿到的证据是否完整。
    # 两者混在一起会把「证据在不在库里」误读成「检索排得准不准」。
    retrieval_rate = summary.get("retrieval_top5_doc_hit_rate")
    e2e_line = (
        "未统计（旧评测没有这一列）"
        if not summary.get("e2e_case_count")
        else _format_report_rate(summary.get("e2e_pass_rate"))
    )
    lines = [
        "**检索命中率**（补召回前，反映真实检索能力）",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
    ]
    if retrieval_rate is None:
        lines.append("| 本次未统计（旧评测没有这几列） | - |")
    else:
        lines.extend([
            f"| Top-1 文档命中率 | {_format_report_rate(summary.get('retrieval_top1_doc_hit_rate'))} |",
            f"| Top-3 文档命中率 | {_format_report_rate(summary.get('retrieval_top3_doc_hit_rate'))} |",
            f"| Top-5 文档命中率 | {_format_report_rate(retrieval_rate)} |",
            f"| 章节级命中率 | {_format_report_rate(summary.get('retrieval_section_hit_rate'))} |",
        ])
    lines.extend([
        "",
        "**证据可用率**（补召回后，反映生成阶段实际拿到的证据是否完整）",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| Top-1 证据可用率 | {summary.get('top1_doc_hit_rate', 0)}% |",
        f"| Top-3 证据可用率 | {summary.get('top3_doc_hit_rate', 0)}% |",
        f"| Top-5 证据可用率 | {summary.get('top5_doc_hit_rate', 0)}% |",
        f"| 章节级证据可用率 | {summary.get('section_hit_rate', 0)}% |",
        f"| 必需证据命中率 | {summary.get('required_citation_hit_rate', 0)}% |",
        f"| 主证据命中率 | {summary.get('primary_evidence_hit_rate', 0)}% |",
        f"| 辅助证据命中率 | {summary.get('secondary_evidence_hit_rate', 0)}% |",
        "",
        "**生成与工程指标**",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 总通过率 | {summary.get('pass_rate', 0)}% |",
    ])
    if _report_has_generation_metrics(mode):
        lines.extend([
            # 端到端那一组跟着生成侧指标走：retrieval 模式不生成答案，
            # 报它只会多一行"不适用"。旧评测没有这一列时显示"未统计"。
            f"| 端到端通过率（不补召回，用户真实路径） | {e2e_line} |",
            f"| 答案要点覆盖率 | {summary.get('answer_coverage_rate', 0)}% |",
            f"| 引用正确率 | {summary.get('citation_pass_rate', 0)}% |",
            f"| 拒答正确率 | {_format_report_rate(summary.get('refusal_pass_rate'))} |",
        ])
    if _report_should_include_ragas(mode):
        lines.append(f"| 需复核题数 | {summary.get('needs_review_count', 0)} |")
    engineering_count = int(summary.get("engineering_failure_count") or 0)
    if engineering_count:
        scored_count = summary.get("scored_case_count", 0)
        lines.append(
            f"| 工程失败题数 | {engineering_count}（超时/断连/限流等，"
            f"已从以上能力指标的分母中剔除，实际计分 {scored_count} 题） |"
        )
    lines.extend([
        f"| 平均检索耗时 | {summary.get('avg_latency_ms', 0)} ms |",
        f"| P95 检索耗时 | {summary.get('p95_latency_ms', 0)} ms |",
        f"| Token 总量 | {summary.get('token_usage', {}).get('total_tokens', 0)} |",
        "",
        "> 耗时只统计**检索链路**（查询改写、向量与 BM25 召回、融合重排、多证据补召回），"
        "**不含答案生成**。所以 `retrieval` 模式下这个数字同样有效。",
    ])
    # 通过率口径的说明放在**整张表之后**：之前放在生成指标块里，会把后面
    # 的耗时 / Token 几行挤到引用块外面，Markdown 里表格就被截断了。
    if _report_has_generation_metrics(mode) and summary.get("e2e_case_count"):
        lines.extend([
            "",
            "> **两组通过率的区别**：`总通过率` 是在补召回之后的上下文上评的，要读作「证据齐全时",
            "> 生成端不出错的比例」；`端到端通过率` 只用真实检索到的那批证据，既不做补召回，",
            "> 也不做过度拒答的二次生成修复 —— 这两件事都只在评测链路里存在。两者的差就是",
            "> 评测口径替用户补齐的部分，用户拿不到，所以对外讲能力以端到端那一组为准。",
        ])
    return lines


def _format_cross_document_report(diagnostics: dict | None) -> str:
    diagnostics = diagnostics or {}
    if not diagnostics.get("enabled"):
        return "不适用"
    if diagnostics.get("passed"):
        return "已完成"
    return diagnostics.get("reason") or "未通过"


def _report_category_lines(categories: dict) -> list[str]:
    if not categories:
        return ["暂无题型统计。"]
    lines = ["| 题型 | 题数 | 通过数 | 跳过数 | 通过率 |", "| --- | ---: | ---: | ---: | ---: |"]
    for key, item in categories.items():
        rate = item.get("pass_rate")
        lines.append(
            f"| {key} {item.get('label', '')} | {item.get('case_count', 0)} | "
            f"{item.get('pass_count', 0)} | {item.get('skipped_count', 0)} | {_format_report_rate(rate)} |"
        )
    return lines


def _report_doc_lines(cases: list[dict]) -> list[str]:
    stats: dict[str, dict[str, int]] = {}
    for case in cases:
        if case.get("skipped"):
            continue
        doc = case.get("expected_doc") or "未覆盖拒答题"
        item = stats.setdefault(doc, {"total": 0, "passed": 0})
        item["total"] += 1
        item["passed"] += 1 if case.get("passed") else 0
    if not stats:
        return ["暂无文档统计。"]
    lines = ["| 文档 | 题数 | 通过数 | 通过率 |", "| --- | ---: | ---: | ---: |"]
    for doc, item in sorted(stats.items()):
        lines.append(f"| {doc} | {item['total']} | {item['passed']} | {_rate(item['passed'], item['total'])}% |")
    return lines


def _report_failure_lines(failure_reasons: dict[str, int]) -> list[str]:
    if not failure_reasons:
        return ["本次评测没有失败原因。"]
    lines = ["| 失败原因 | 次数 |", "| --- | ---: |"]
    for reason, count in sorted(failure_reasons.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"| {reason} | {count} |")
    return lines


def _ragas_label(metric: str) -> str:
    labels = {
        "faithfulness": "忠实度",
        "context_recall": "上下文召回",
        "llm_context_recall": "上下文召回",
        "factual_correctness": "事实一致性",
        "answer_relevancy": "回答相关性",
        "response_relevancy": "回答相关性",
    }
    return labels.get(metric, metric)


def _report_ragas_lines(summary: dict) -> list[str]:
    lines = [
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| RAGAS 诊断通过率 | {_format_report_rate(summary.get('ragas_pass_rate'))} |",
        f"| RAGAS 预警题数 | {summary.get('ragas_warning_count', 0)} |",
        f"| 需人工复核题数 | {summary.get('needs_review_count', 0)} |",
    ]
    warning_rate = summary.get("ragas_warning_rate")
    lines.append(f"| RAGAS 预警率 | {_format_report_rate(warning_rate)} |")
    scores = summary.get("ragas_scores") or {}
    for key, value in scores.items():
        lines.append(f"| {_ragas_label(key)} | {value} |")
    return lines


def datasets() -> list[dict]:
    with SessionLocal() as db:
        return evaluation_dataset_service.list_datasets(db)

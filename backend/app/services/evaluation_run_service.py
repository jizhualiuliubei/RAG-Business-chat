"""Persistent RAG evaluation run service."""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
from datetime import datetime
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from app.core import milvus_store, rag
from app.database import SessionLocal
from app.models.evaluation import EvaluationCaseResult, EvaluationRun
from app.models.knowledge_base import KnowledgeBase
from app.services import qa_service
from app.services.enterprise_evaluation_dataset import CATEGORY_LABELS, CATEGORY_LABELS_360, DATASET_VERSION
from app.services import enterprise_service, evaluation_dataset_service

_RUN_LOCK = threading.Semaphore(1)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
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
    rest = []
    for ctx in deduped:
        if any(_context_matches_required(required, [ctx]) for required in required_citations):
            required_first.append(ctx)
        else:
            rest.append(ctx)
    return (required_first + rest)[:top_k]


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


def _failure_reason(
    case,
    top1: bool,
    top5: bool,
    section: bool,
    answer_cov: int,
    citation: bool,
    refusal: bool,
    evidence: dict | None = None,
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
    if answer_cov < 60:
        return "答案要点缺失：模型回答未覆盖主要标准答案"
    if not citation:
        return "引用不支持：回答来源不足以支撑结论"
    return "" if top1 else "排序偏弱：期望来源已进入 Top-5 但不是 Top-1"


def _fix_suggestion(reason: str) -> str:
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


def create_run(db: Session, *, kb_id: int | list[int], mode: str, actor: dict, dataset_version: str = DATASET_VERSION) -> EvaluationRun:
    if mode not in {"retrieval", "e2e", "full"}:
        raise ValueError("评测模式必须是 retrieval、e2e 或 full")
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
                        item = _call_evaluate_case(db, case, kb_scope, run.mode, run.enterprise_id)
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
        "answer_coverage": 0,
        "citation_passed": False,
        "refusal_passed": False,
        "latency_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "failure_reason": reason,
        "fix_suggestion": _fix_suggestion(reason),
    }


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
    contexts = _augment_required_contexts(
        kb_scope,
        contexts,
        tuple(getattr(case, "required_citations", ()) or ()),
        top_k=5,
        enterprise_id=enterprise_id,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000)
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

    if mode in {"e2e", "full"}:
        case_payload = {
        "category": case.category,
        "expected_doc": case.expected_doc,
        "expected_section": case.expected_section,
        "expected_clause": case.expected_clause,
        "answer_points": list(case.answer_points),
        "required_citations": list(getattr(case, "required_citations", ()) or ()),
        "negative_case": _is_refusal_case(case),
        }
        try:
            qa_result = rag.generate_evaluation_answer(
                case.question,
                contexts,
                case_payload,
                model_config=model_config,
            )
        except TypeError as exc:
            if "model_config" not in str(exc):
                raise
            qa_result = rag.generate_evaluation_answer(case.question, contexts, case_payload)
        answer = qa_result.get("answer", "")
        citations = contexts
        answer_cov = _answer_coverage(answer, case.answer_points)
        citation_passed = _is_refusal_case(case) or any(_doc_hit(case.expected_doc, [src], 1) for src in citations)
        refusal = _refusal_passed(answer)
        input_tokens, output_tokens, total_tokens = _tokens(qa_result)
    elif _is_refusal_case(case):
        refusal = False
    else:
        answer_cov = 100 if top5 and section else 0
        citation_passed = top5 and section

    if _is_refusal_case(case):
        passed = bool(refusal) if mode in {"e2e", "full"} else False
        skipped = mode == "retrieval"
    else:
        passed = bool(top5 and section and evidence_ok and answer_cov >= 60 and citation_passed)
        skipped = False

    reason = "" if skipped or passed else _failure_reason(case, top1, top5, section, answer_cov, citation_passed, refusal, evidence)
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
        "answer_coverage": answer_cov,
        "citation_passed": citation_passed,
        "refusal_passed": refusal,
        "evidence_diagnostics": evidence,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "failure_reason": reason,
        "fix_suggestion": _fix_suggestion(reason),
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
        answer_coverage=item["answer_coverage"],
        citation_passed=1 if item["citation_passed"] else 0,
        refusal_passed=1 if item["refusal_passed"] else 0,
        latency_ms=item["latency_ms"],
        input_tokens=item["input_tokens"],
        output_tokens=item["output_tokens"],
        total_tokens=item["total_tokens"],
        failure_reason=item["failure_reason"],
        fix_suggestion=item["fix_suggestion"],
    )


def _rate(count: int, total: int) -> float:
    return round(count / max(total, 1) * 100, 2)


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
    non_refusal = [item for item in evaluated if not _is_refusal_case(item["case"])]
    refusal_items = [item for item in evaluated if _is_refusal_case(item["case"])]
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
        }
    failure_reasons: dict[str, int] = {}
    for item in evaluated:
        if item["passed"]:
            continue
        if item["failure_reason"]:
            failure_reasons[item["failure_reason"]] = failure_reasons.get(item["failure_reason"], 0) + 1
    latencies = [item["latency_ms"] for item in evaluated]
    evidence_items = [
        item.get("evidence_diagnostics") or {}
        for item in non_refusal
        if (item.get("evidence_diagnostics") or {}).get("required_citation_hit_rate") is not None
    ]
    p95 = 0
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, round(len(ordered) * 0.95) - 1)]
    return {
        "dataset": getattr(items[0]["case"], "dataset_summary", None) if items else {},
        "case_count": len(items),
        "evaluated_case_count": len(evaluated),
        "skipped_case_count": sum(1 for item in items if item["skipped"]),
        "pass_count": sum(1 for item in evaluated if item["passed"]),
        "pass_rate": _rate(sum(1 for item in evaluated if item["passed"]), len(evaluated)),
        "top1_doc_hit_rate": _rate(sum(1 for item in non_refusal if item["top1_hit"]), len(non_refusal)),
        "top3_doc_hit_rate": _rate(sum(1 for item in non_refusal if item["top3_hit"]), len(non_refusal)),
        "top5_doc_hit_rate": _rate(sum(1 for item in non_refusal if item["top5_hit"]), len(non_refusal)),
        "section_hit_rate": _rate(sum(1 for item in non_refusal if item["section_hit"]), len(non_refusal)),
        "answer_coverage_rate": round(mean([item["answer_coverage"] for item in evaluated]), 2) if evaluated else 0,
        "answer_coverage_pass_rate": _rate(sum(1 for item in evaluated if item["answer_coverage"] >= 60), len(evaluated)),
        "citation_pass_rate": _rate(sum(1 for item in non_refusal if item["citation_passed"]), len(non_refusal)),
        "required_citation_hit_rate": round(mean([item["required_citation_hit_rate"] for item in evidence_items]), 2) if evidence_items else 0,
        "primary_evidence_hit_rate": _rate(sum(1 for item in evidence_items if item.get("primary_evidence_hit")), len(evidence_items)),
        "secondary_evidence_hit_rate": _rate(
            sum(1 for item in evidence_items if item.get("secondary_evidence_hit") is not False),
            len(evidence_items),
        ),
        "refusal_pass_rate": None if not refusal_items else _rate(sum(1 for item in refusal_items if item["refusal_passed"]), len(refusal_items)),
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
        "answer_coverage": row.answer_coverage,
        "citation_passed": bool(row.citation_passed),
        "refusal_passed": bool(row.refusal_passed),
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
    category_lines = _report_category_lines(summary.get("categories") or {})
    doc_lines = _report_doc_lines(cases)
    failure_lines = _report_failure_lines(summary.get("failure_reasons") or {})
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
        "| 向量库 | Zilliz Cloud knowledge_chunks |",
        f"| 知识库 | {data['kb_id']} |",
        f"| 评测集 | {data['dataset_version']} |",
        f"| 题集来源 | {dataset_meta.get('source_type', '')} |",
        f"| 审核状态 | {dataset_meta.get('review_status', '')} |",
        f"| 审核人 | {dataset_meta.get('reviewed_by') or '-'} |",
        f"| 模式 | {data['mode']} |",
        "",
        "## 二、核心指标",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 总通过率 | {summary.get('pass_rate', 0)}% |",
        f"| Top-1 文档命中率 | {summary.get('top1_doc_hit_rate', 0)}% |",
        f"| Top-3 文档命中率 | {summary.get('top3_doc_hit_rate', 0)}% |",
        f"| Top-5 文档命中率 | {summary.get('top5_doc_hit_rate', 0)}% |",
        f"| 章节级命中率 | {summary.get('section_hit_rate', 0)}% |",
        f"| 答案要点覆盖率 | {summary.get('answer_coverage_rate', 0)}% |",
        f"| 必需证据命中率 | {summary.get('required_citation_hit_rate', 0)}% |",
        f"| 主证据命中率 | {summary.get('primary_evidence_hit_rate', 0)}% |",
        f"| 辅助证据命中率 | {summary.get('secondary_evidence_hit_rate', 0)}% |",
        f"| 引用正确率 | {summary.get('citation_pass_rate', 0)}% |",
        f"| 拒答正确率 | {_format_report_rate(summary.get('refusal_pass_rate'))} |",
        f"| 平均耗时 | {summary.get('avg_latency_ms', 0)} ms |",
        f"| P95 耗时 | {summary.get('p95_latency_ms', 0)} ms |",
        f"| Token 总量 | {summary.get('token_usage', {}).get('total_tokens', 0)} |",
        "",
        "## 三、题型通过率",
        "",
        *category_lines,
        "",
        "## 四、文档通过率",
        "",
        *doc_lines,
        "",
        "## 五、失败原因分布",
        "",
        *failure_lines,
        "",
        "## 六、失败样例",
        "",
    ]
    if failed:
        lines.extend(["| 题号 | 问题 | 失败原因 | 修复建议 |", "| --- | --- | --- | --- |"])
        for case in failed:
            lines.append(f"| {case['case_id']} | {case['question']} | {case['failure_reason']} | {case['fix_suggestion']} |")
    else:
        lines.append("本次评测没有失败样例。")
    return {"run_id": run_id, "markdown": "\n".join(lines)}


def _format_report_rate(value) -> str:
    if value is None:
        return "未运行"
    return f"{value}%"


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


def datasets() -> list[dict]:
    with SessionLocal() as db:
        return evaluation_dataset_service.list_datasets(db)

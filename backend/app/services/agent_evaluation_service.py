"""Agent 场景评测运行、落库与租户隔离。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agent.evaluation import BUILTIN_AGENT_EVALUATION_CASES, evaluate_builtin_case, evaluation_manifest
from app.agent.production import ProductionAgentDependencies
from app.database import SessionLocal
from app.models.agent import AgentEvaluationCaseResult, AgentEvaluationRun


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def create_evaluation_run(db: Session, enterprise_id: int) -> AgentEvaluationRun:
    run = AgentEvaluationRun(
        enterprise_id=enterprise_id,
        status="pending",
        total_cases=len(BUILTIN_AGENT_EVALUATION_CASES),
        passed_cases=0,
        metrics_json="{}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def list_evaluation_runs(db: Session, enterprise_id: int) -> list[AgentEvaluationRun]:
    return list(
        db.execute(
            select(AgentEvaluationRun)
            .where(AgentEvaluationRun.enterprise_id == enterprise_id)
            .order_by(AgentEvaluationRun.id.desc())
            .limit(50)
        ).scalars().all()
    )


def _build_metrics(results: list[dict]) -> dict:
    categories: dict[str, list[bool]] = defaultdict(list)
    for item in results:
        categories[item["category"]].append(bool(item["passed"]))
    category_rates = {
        name: round(sum(values) / len(values), 4) if values else 0.0
        for name, values in categories.items()
    }
    total = len(results)
    passed = sum(bool(item["passed"]) for item in results)
    return {
        **evaluation_manifest(),
        "suite_type": "component_contract",
        "case_pass_rate": round(passed / total, 4) if total else 0.0,
        # Compatibility for saved clients; this is not end-to-end task completion.
        "completion_rate": round(passed / total, 4) if total else 0.0,
        "routing_accuracy": category_rates.get("skill_routing", 0.0),
        "field_extraction_accuracy": category_rates.get("field_extraction", 0.0),
        "risk_gate_pass_rate": category_rates.get("risk_and_approval", 0.0),
        "security_pass_rate": category_rates.get("tool_authorization", 0.0),
        "recovery_pass_rate": category_rates.get("state_recovery", 0.0),
        "category_rates": category_rates,
    }


def execute_evaluation_run(
    run_id: int,
    *,
    session_factory: Callable = SessionLocal,
    field_extractor: Callable | None = None,
) -> dict:
    with session_factory() as db:
        run = db.get(AgentEvaluationRun, int(run_id))
        if run is None:
            raise ValueError("Agent 评测任务不存在")
        if run.status == "completed":
            return serialize_evaluation_run(db, run)
        run.status = "running"
        run.total_cases = len(BUILTIN_AGENT_EVALUATION_CASES)
        run.metrics_json = _json(evaluation_manifest())
        db.execute(delete(AgentEvaluationCaseResult).where(AgentEvaluationCaseResult.run_id == run.id))
        db.commit()

        try:
            if field_extractor is None:
                dependencies = ProductionAgentDependencies(session_factory)

                def field_extractor(case):
                    return dependencies.extract_fields(
                        {
                            "enterprise_id": run.enterprise_id,
                            "skill_id": case.skill_id,
                            "goal": case.input["goal"],
                            "request": {},
                        }
                    )

            results = [
                evaluate_builtin_case(case, field_extractor=field_extractor)
                for case in BUILTIN_AGENT_EVALUATION_CASES
            ]
            for item in results:
                db.add(
                    AgentEvaluationCaseResult(
                        enterprise_id=run.enterprise_id,
                        run_id=run.id,
                        case_id=item["case_id"],
                        skill_id=item["skill_id"],
                        passed=1 if item["passed"] else 0,
                        diagnostics_json=_json(item),
                    )
                )
            run.passed_cases = sum(bool(item["passed"]) for item in results)
            run.metrics_json = _json(_build_metrics(results))
            run.status = "completed"
            db.commit()
            db.refresh(run)
            return serialize_evaluation_run(db, run)
        except Exception as exc:
            db.rollback()
            run = db.get(AgentEvaluationRun, int(run_id))
            run.status = "failed"
            run.metrics_json = _json({**evaluation_manifest(), "error": str(exc)[:1000]})
            db.commit()
            raise


def serialize_evaluation_run(db: Session, run: AgentEvaluationRun, *, include_cases: bool = False) -> dict:
    payload = {
        "id": run.id,
        "enterprise_id": run.enterprise_id,
        "status": run.status,
        "total_cases": run.total_cases,
        "passed_cases": run.passed_cases,
        "metrics": _loads(run.metrics_json, {}),
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if include_cases:
        rows = db.execute(
            select(AgentEvaluationCaseResult)
            .where(
                AgentEvaluationCaseResult.run_id == run.id,
                AgentEvaluationCaseResult.enterprise_id == run.enterprise_id,
            )
            .order_by(AgentEvaluationCaseResult.id.asc())
        ).scalars().all()
        payload["cases"] = [
            {
                "id": item.id,
                "case_id": item.case_id,
                "skill_id": item.skill_id,
                "passed": bool(item.passed),
                "diagnostics": _loads(item.diagnostics_json, {}),
            }
            for item in rows
        ]
    return payload

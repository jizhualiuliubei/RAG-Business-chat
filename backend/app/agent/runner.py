"""在 Worker 中运行 LangGraph，并把安全状态同步回 MySQL。"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from typing import Any

from langgraph.types import Command
from sqlalchemy import func, select

from app.agent.graph import build_agent_graph
from app.agent.production import ProductionAgentDependencies
from app.agent.state import AgentTaskStatus, assert_status_transition
from app.config import AGENT_LOCK_TIMEOUT_SECONDS
from app.models.agent import (
    AgentApproval,
    AgentArtifact,
    AgentOutbox,
    AgentTask,
    AgentTaskStep,
)
from app.services import agent_task_service


def _loads(value: str, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class _TaskStopped(Exception):
    """数据库中的撤回优先于模型节点结果。"""


def _lock_task(db, task_id):
    return db.execute(select(AgentTask).where(AgentTask.id == task_id)
                      .with_for_update().execution_options(populate_existing=True)).scalar_one()


def _check_task_running(task):
    if task.deleted_at is not None or task.status in {"cancel_requested", "cancelled", "rejected"}:
        raise _TaskStopped("任务已请求安全停止")


def _finish_cancelled(db, task, event_publisher=None):
    if task.status == "cancel_requested":
        task.status = AgentTaskStatus.CANCELLED
        task.current_node = "cancelled"
        task.version += 1
    for step in db.execute(select(AgentTaskStep).where(
        AgentTaskStep.task_id == task.id, AgentTaskStep.status == "running",
    )).scalars():
        step.status = "interrupted"
        step.output_summary = "申请已撤回，节点结果不再用于后续执行" if task.withdrawn_at else "任务已中断"
    _mark_outbox(db, task.id, "done")
    db.commit()
    db.refresh(task)
    if event_publisher:
        event_publisher("task_status", {"status": task.status, "version": task.version})
    return agent_task_service.serialize_task(task)


def _latest_approval(db, task_id: int) -> AgentApproval | None:
    return db.execute(
        select(AgentApproval)
        .where(AgentApproval.task_id == task_id)
        .order_by(AgentApproval.id.desc())
    ).scalars().first()


def _initial_state(db, task: AgentTask) -> dict:
    policy = agent_task_service.get_risk_policy(db, task.enterprise_id)
    approval = _latest_approval(db, task.id)
    approval_payload = {}
    if approval and approval.status != "pending":
        approval_payload = {
            "decision": approval.status,
            "comment": approval.comment,
            "approval_id": approval.id,
            "decided_by": approval.decided_by,
        }
    # Submission and completed steps survive checkpoint loss; reviewed legacy tasks can also resume.
    input_confirmed = task.current_node == "input_updated" or db.execute(select(AgentTaskStep.id).where(
        AgentTaskStep.task_id == task.id,
        AgentTaskStep.node_name.in_(["input_confirmation", "wait_for_input", "retrieve_policy"]),
        AgentTaskStep.status == "completed",
    ).limit(1)).first() is not None
    return {
        "task_id": task.id,
        "enterprise_id": task.enterprise_id,
        "user_id": task.user_id,
        "goal": task.goal,
        "skill_id": task.skill_id,
        "skill_version": task.skill_version,
        "kb_ids": _loads(task.kb_ids_json, []),
        "request": _loads(task.request_json, {}),
        "input_confirmed": input_confirmed,
        "evidence": _loads(task.evidence_json, []),
        "plan": _loads(task.plan_json, {}),
        "audit": _loads(task.audit_json, {}),
        "approval": approval_payload,
        "result": _loads(task.result_json, {}),
        "risk_policy": agent_task_service.risk_policy_rules(policy),
        "revision_count": 0,
    }


def _resume_command(db, task: AgentTask, next_nodes: tuple[str, ...]):
    if "wait_for_input" in next_nodes and task.current_node == "input_updated":
        return Command(resume={"fields": _loads(task.request_json, {})})
    if "wait_for_approval" in next_nodes:
        approval = _latest_approval(db, task.id)
        if approval is not None and approval.status != "pending":
            return Command(
                resume={
                    "decision": approval.status,
                    "comment": approval.comment,
                    "approval_id": approval.id,
                    "decided_by": approval.decided_by,
                }
            )
    return None


def _record_step(db, task: AgentTask, node_name: str, update: Any, duration_ms: int, *, token_usage=None) -> None:
    if node_name.startswith("__"):
        return
    task = _lock_task(db, task.id)
    _check_task_running(task)
    keys = sorted(update.keys()) if isinstance(update, dict) else []
    if isinstance(update, dict):
        for key in ("request", "missing_fields", "evidence", "plan", "audit", "result", "risk"):
            if key in update:
                setattr(task, f"{key}_json", _dumps(update[key]))
        if "risk" in update:
            task.risk_level = str((update["risk"] or {}).get("level") or "")
    running_step = db.execute(select(AgentTaskStep).where(
        AgentTaskStep.task_id == task.id, AgentTaskStep.node_name == node_name,
        AgentTaskStep.status == "running",
    ).order_by(AgentTaskStep.id.desc())).scalars().first()
    previous_attempt = db.execute(
        select(func.max(AgentTaskStep.attempt)).where(
            AgentTaskStep.task_id == task.id,
            AgentTaskStep.node_name == node_name,
        )
    ).scalar_one_or_none()
    task.current_node = node_name
    task.version += 1
    if running_step is not None:
        running_step.status = "completed"
        running_step.duration_ms = max(0, duration_ms)
        running_step.input_tokens = max(0, int((token_usage or {}).get("input_tokens") or 0))
        running_step.output_tokens = max(0, int((token_usage or {}).get("output_tokens") or 0))
        running_step.output_summary = f"已更新：{', '.join(keys) if keys else '无状态字段'}"
    else:
        db.add(AgentTaskStep(
            enterprise_id=task.enterprise_id,
            task_id=task.id,
            node_name=node_name,
            status="completed",
            attempt=int(previous_attempt or 0) + 1,
            duration_ms=max(0, duration_ms),
            input_tokens=max(0, int((token_usage or {}).get("input_tokens") or 0)),
            output_tokens=max(0, int((token_usage or {}).get("output_tokens") or 0)),
            input_summary=f"Skill={task.skill_id}; task_version={task.version}",
            output_summary=f"已更新：{', '.join(keys) if keys else '无状态字段'}",
        ))
    db.commit()


def _record_started_step(db, task, node_name):
    task = _lock_task(db, task.id)
    _check_task_running(task)
    previous = db.execute(select(func.max(AgentTaskStep.attempt)).where(
        AgentTaskStep.task_id == task.id, AgentTaskStep.node_name == node_name,
    )).scalar_one_or_none()
    for old in db.execute(select(AgentTaskStep).where(
        AgentTaskStep.task_id == task.id, AgentTaskStep.status == "running",
    )).scalars():
        old.status = "interrupted"
        old.output_summary = "上次节点执行中断，已从安全节点恢复"
    task.current_node = node_name
    task.version += 1
    db.add(AgentTaskStep(enterprise_id=task.enterprise_id, task_id=task.id,
                        node_name=node_name, status="running", attempt=int(previous or 0) + 1,
                        input_summary=f"Skill={task.skill_id}; task_version={task.version}",
                        output_summary="节点执行中"))
    db.commit()


def _record_failed_step(db, task: AgentTask, node_name: str, error: Exception) -> None:
    running_step = db.execute(select(AgentTaskStep).where(
        AgentTaskStep.task_id == task.id, AgentTaskStep.node_name == node_name,
        AgentTaskStep.status == "running",
    ).order_by(AgentTaskStep.id.desc())).scalars().first()
    if running_step is not None:
        running_step.status = "failed"
        running_step.error_message = str(error)[:1000]
        running_step.output_summary = "节点执行失败"
        running_step.duration_ms = max(0, int((datetime.now() - running_step.created_at).total_seconds() * 1000))
        return
    previous_attempt = db.execute(
        select(func.max(AgentTaskStep.attempt)).where(
            AgentTaskStep.task_id == task.id,
            AgentTaskStep.node_name == node_name,
        )
    ).scalar_one_or_none()
    db.add(
        AgentTaskStep(
            enterprise_id=task.enterprise_id,
            task_id=task.id,
            node_name=node_name,
            status="failed",
            attempt=int(previous_attempt or 0) + 1,
            duration_ms=0,
            input_summary=f"Skill={task.skill_id}; task_version={task.version}",
            output_summary="节点执行失败",
            error_message=str(error)[:1000],
        )
    )


def _sync_task_state(db, task: AgentTask, values: dict) -> None:
    task.request_json = _dumps(values.get("request") or {})
    task.missing_fields_json = _dumps(values.get("missing_fields") or [])
    task.evidence_json = _dumps(values.get("evidence") or [])
    task.plan_json = _dumps(values.get("plan") or {})
    task.audit_json = _dumps(values.get("audit") or {})
    task.result_json = _dumps(values.get("result") or {})
    if "risk" in values:
        task.risk_json = _dumps(values.get("risk") or {})
    task.risk_level = str((values.get("risk") or {}).get("level") or task.risk_level or "")


def _save_artifact(db, task: AgentTask, content: str) -> None:
    if not content:
        return
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = db.execute(
        select(AgentArtifact).where(
            AgentArtifact.task_id == task.id,
            AgentArtifact.content_hash == content_hash,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            AgentArtifact(
                enterprise_id=task.enterprise_id,
                task_id=task.id,
                artifact_type="markdown",
                content=content,
                content_hash=content_hash,
            )
        )


def _mark_outbox(db, task_id: int, status: str, error: str = "") -> None:
    item = db.execute(select(AgentOutbox).where(AgentOutbox.task_id == task_id)).scalar_one_or_none()
    if item is not None:
        item.status = status
        item.last_error = error[:1000]
        item.attempts += 1
        if status == "processing":
            item.next_attempt_at = datetime.now() + timedelta(seconds=AGENT_LOCK_TIMEOUT_SECONDS)
        elif status == "failed":
            item.next_attempt_at = datetime.now() + timedelta(seconds=15)
        else:
            item.next_attempt_at = None


def process_agent_task(
    task_id: int,
    *,
    session_factory,
    checkpointer,
    dependencies=None,
    event_publisher=None,
    execution_guard=None,
) -> dict:
    """运行或恢复单个任务；正式状态以 MySQL 为准。"""
    dependencies = dependencies or ProductionAgentDependencies(session_factory)
    if isinstance(dependencies, ProductionAgentDependencies):
        dependencies.execution_guard = execution_guard
        dependencies.event_publisher = event_publisher

    with session_factory() as db:
        task = db.execute(select(AgentTask).where(AgentTask.id == task_id).with_for_update()).scalar_one_or_none()
        if task is None:
            raise ValueError(f"Agent 任务不存在：{task_id}")
        thread_id = task.checkpoint_thread_id or f"enterprise:{task.enterprise_id}:task:{task.id}"
        config = {"configurable": {"thread_id": thread_id}}
        if task.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.CANCELLED, AgentTaskStatus.REJECTED}:
            _mark_outbox(db, task.id, "done")
            db.commit()
            db.refresh(task)
            return agent_task_service.serialize_task(task)
        if task.status == AgentTaskStatus.CANCEL_REQUESTED:
            return _finish_cancelled(db, task, event_publisher)
        if task.status not in {
            AgentTaskStatus.PENDING_DISPATCH,
            AgentTaskStatus.QUEUED,
            AgentTaskStatus.RUNNING,
            AgentTaskStatus.FAILED,
        }:
            return agent_task_service.serialize_task(task)
        db.commit()

        def task_execution_guard():
            if execution_guard:
                execution_guard()
            current = _lock_task(db, task_id)
            try:
                _check_task_running(current)
            finally:
                # 不持有 MySQL 行锁等待模型、检索或 interrupt()。
                db.commit()

        def node_started(name):
            _record_started_step(db, task, name)
            if event_publisher:
                event_publisher("node_started", {"node": name, "version": task.version})

        graph = build_agent_graph(dependencies, checkpointer=checkpointer,
                                  execution_guard=task_execution_guard, node_started=node_started)

        snapshot = graph.get_state(config)
        initial_next = tuple(snapshot.next or ())
        latest_approval = _latest_approval(db, task.id)
        resume_after_changes = bool(
            snapshot.values
            and "wait_for_approval" in initial_next
            and latest_approval is not None
            and latest_approval.status == "changes_requested"
        )
        command = _resume_command(db, task, initial_next) if snapshot.values else None
        graph_input = command if command is not None else (
            None if snapshot.values and initial_next else _initial_state(db, task)
        )
        if command is None and task.current_node in {"input_updated", "materials_updated"}:
            graph_input = _initial_state(db, task)
        task = _lock_task(db, task_id)
        if task.status in {"cancel_requested", "cancelled"} or task.deleted_at is not None:
            return _finish_cancelled(db, task, event_publisher)
        if task.status == AgentTaskStatus.FAILED:
            task.status = AgentTaskStatus.PENDING_DISPATCH
        assert_status_transition(task.status, AgentTaskStatus.RUNNING)
        task.status = AgentTaskStatus.RUNNING
        task.current_node = "load_context" if not snapshot.values else str((snapshot.next or ["resume"])[0])
        task.version += 1
        task.error_message = ""
        _mark_outbox(db, task.id, "processing")
        db.commit()
        if event_publisher:
            event_publisher("task_status", {"status": "running", "node": task.current_node})

        try:
            last_at = time.perf_counter()

            def stream_once(input_value) -> bool:
                nonlocal last_at
                for event in graph.stream(input_value, config=config, stream_mode="updates"):
                    now = time.perf_counter()
                    for node_name, update in event.items():
                        usage = dependencies.consume_token_usage() if isinstance(dependencies, ProductionAgentDependencies) else {}
                        _record_step(db, task, node_name, update, int((now - last_at) * 1000), token_usage=usage)
                        if event_publisher and not node_name.startswith("__"):
                            event_publisher("node_completed", {"node": node_name, "version": task.version})
                    last_at = now
                    db.refresh(task, attribute_names=["status"])
                    if task.status == AgentTaskStatus.CANCEL_REQUESTED:
                        return True
                return False

            cancelled = stream_once(graph_input)
            if not cancelled and resume_after_changes:
                intermediate = graph.get_state(config)
                if "wait_for_input" in tuple(intermediate.next or ()):
                    cancelled = stream_once(
                        Command(resume={"fields": _loads(task.request_json, {})})
                    )

            if cancelled:
                return _finish_cancelled(db, _lock_task(db, task_id), event_publisher)

            snapshot = graph.get_state(config)
            values = dict(snapshot.values or {})
            task = _lock_task(db, task_id)
            _check_task_running(task)
            _sync_task_state(db, task, values)
            next_nodes = tuple(snapshot.next or ())

            if "wait_for_input" in next_nodes:
                assert_status_transition(task.status, AgentTaskStatus.WAITING_INPUT)
                task.status = AgentTaskStatus.WAITING_INPUT
                task.current_node = "wait_for_input"
                task.version += 1
                _mark_outbox(db, task.id, "done")
            elif "wait_for_approval" in next_nodes:
                assert_status_transition(task.status, AgentTaskStatus.WAITING_APPROVAL)
                task.status = AgentTaskStatus.WAITING_APPROVAL
                task.current_node = "wait_for_approval"
                task.version += 1
                agent_task_service.create_approval(db, task)
                _mark_outbox(db, task.id, "done")
            elif values.get("final_status") == "cancelled":
                assert_status_transition(task.status, AgentTaskStatus.CANCELLED)
                task.status = AgentTaskStatus.CANCELLED
                task.current_node = "cancelled"
                task.version += 1
                _mark_outbox(db, task.id, "done")
            elif values.get("final_status") == "completed":
                assert_status_transition(task.status, AgentTaskStatus.COMPLETED)
                task.status = AgentTaskStatus.COMPLETED
                task.current_node = "finalize"
                task.completed_at = datetime.now()
                task.version += 1
                _save_artifact(db, task, str(values.get("artifact") or ""))
                _mark_outbox(db, task.id, "done")
            else:
                raise RuntimeError("LangGraph 未到达可持久化的终态或中断点")

            db.commit()
            db.refresh(task)
            if event_publisher:
                event_publisher("task_status", {"status": task.status, "node": task.current_node})
            return agent_task_service.serialize_task(task)
        except Exception as exc:
            if execution_guard is not None:
                try:
                    execution_guard()
                except Exception:
                    # The old owner must not overwrite a new owner's DB state.
                    db.rollback()
                    raise exc
            failure_values = {}
            try:
                failure_snapshot = graph.get_state(config)
                failure_values = dict(failure_snapshot.values or {})
                failed_node = str(
                    (tuple(failure_snapshot.next or ()) or (task.current_node or "unknown",))[0]
                )
            except Exception:
                failed_node = str(task.current_node or "unknown")
            db.rollback()
            task = _lock_task(db, task_id)
            if task is not None:
                if task.status in {"cancel_requested", "cancelled"} or task.deleted_at is not None:
                    return _finish_cancelled(db, task, event_publisher)
                if failure_values:
                    _sync_task_state(db, task, failure_values)
                task.status = AgentTaskStatus.FAILED
                task.current_node = failed_node
                task.error_message = str(exc)[:1000]
                task.version += 1
                _record_failed_step(db, task, failed_node, exc)
                _mark_outbox(db, task.id, "failed", str(exc))
                db.commit()
                if event_publisher:
                    event_publisher("node_failed", {"node": failed_node, "version": task.version})
            raise

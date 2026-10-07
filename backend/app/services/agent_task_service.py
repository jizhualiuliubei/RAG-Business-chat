"""Agent 任务、审批、业务记录与 Skill 配置服务。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.skills import assess_risk, get_skill, list_skills, missing_fields, route_skill
from app.agent.state import AgentTaskStatus, assert_status_transition
from app.models.agent import (
    AgentApproval,
    AgentArtifact,
    AgentTaskAttachment,
    AgentBusinessRecord,
    AgentOutbox,
    AgentRiskPolicy,
    AgentSkillConfig,
    AgentTask,
    AgentTaskStep,
)
from app.models.user import User
from app.agent.permissions import can_review_enterprise, eligible_reviewers
from app.agent.execution_contract import has_current_contract, execution_contract
from app.agent.policy_checks import require_policy_compliance
from app.agent.contracts import audit_passed


def _loads(value: str, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def serialize_task(task: AgentTask, *, steps: list[AgentTaskStep] | None = None) -> dict:
    return {
        "id": task.id,
        "enterprise_id": task.enterprise_id,
        "user_id": task.user_id,
        "goal": task.goal,
        "skill_id": task.skill_id,
        "skill_version": task.skill_version,
        "status": task.status,
        "risk_level": task.risk_level,
        "risk": _loads(task.risk_json, {}),
        "current_node": task.current_node,
        "kb_ids": _loads(task.kb_ids_json, []),
        "request": _loads(task.request_json, {}),
        "missing_fields": _loads(task.missing_fields_json, []),
        "evidence": _loads(task.evidence_json, []),
        "plan": _loads(task.plan_json, {}),
        "audit": _loads(task.audit_json, {}),
        "result": _loads(task.result_json, {}),
        "error_message": task.error_message,
        "version": task.version,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "withdrawn_at": task.withdrawn_at.isoformat() if task.withdrawn_at else None,
        "deleted_at": task.deleted_at.isoformat() if task.deleted_at else None,
        "steps": [serialize_step(step) for step in (steps or [])],
    }


def serialize_step(step: AgentTaskStep) -> dict:
    return {
        "id": step.id,
        "node_name": step.node_name,
        "status": step.status,
        "attempt": step.attempt,
        "duration_ms": step.duration_ms,
        "input_summary": step.input_summary,
        "output_summary": step.output_summary,
        "error_message": step.error_message,
        "input_tokens": step.input_tokens,
        "output_tokens": step.output_tokens,
        "created_at": step.created_at.isoformat() if step.created_at else None,
    }


def transition_task(task: AgentTask, target: str, *, node: str | None = None) -> None:
    assert_status_transition(task.status, target)
    task.status = AgentTaskStatus(target).value
    if node is not None:
        task.current_node = node
    task.version += 1
    if task.status == AgentTaskStatus.COMPLETED:
        task.completed_at = datetime.now()


def create_task(db: Session, enterprise_id: int, user_id: int, goal: str, kb_ids: list[int]) -> AgentTask:
    skill_id = route_skill(goal)
    skill = get_skill(skill_id)
    skill_config = db.execute(
        select(AgentSkillConfig).where(
            AgentSkillConfig.enterprise_id == enterprise_id,
            AgentSkillConfig.skill_id == skill_id,
        )
    ).scalar_one_or_none()
    if skill_config is not None and not bool(skill_config.enabled):
        raise ValueError(f"{skill.name} Skill 已停用")
    task = AgentTask(
        enterprise_id=enterprise_id,
        user_id=user_id,
        goal=goal.strip(),
        skill_id=skill_id,
        skill_version=skill.version,
        status=AgentTaskStatus.DRAFT,
        kb_ids_json=_dumps([int(item) for item in kb_ids]),
    )
    db.add(task)
    db.flush()
    task.checkpoint_thread_id = f"enterprise:{enterprise_id}:task:{task.id}"
    transition_task(task, AgentTaskStatus.PENDING_DISPATCH, node="created")
    db.add(AgentOutbox(task_id=task.id, status="pending"))
    db.commit()
    db.refresh(task)
    return task


def list_tasks(db: Session, enterprise_id: int, user_id: int, role: str) -> list[AgentTask]:
    stmt = select(AgentTask).where(AgentTask.enterprise_id == enterprise_id, AgentTask.deleted_at.is_(None))
    if role not in {"enterprise_admin", "system_admin", "admin"}:
        stmt = stmt.where(AgentTask.user_id == user_id)
    return list(db.execute(stmt.order_by(AgentTask.created_at.desc(), AgentTask.id.desc())).scalars().all())


def get_task(db: Session, task_id: int, enterprise_id: int, user_id: int, role: str, *, include_deleted: bool = False) -> AgentTask | None:
    stmt = select(AgentTask).where(AgentTask.id == task_id, AgentTask.enterprise_id == enterprise_id)
    if not include_deleted:
        stmt = stmt.where(AgentTask.deleted_at.is_(None))
    if role not in {"enterprise_admin", "system_admin", "admin"}:
        stmt = stmt.where(AgentTask.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def get_task_detail(db: Session, task: AgentTask, *, user_id: int | None = None, role: str = "employee") -> dict:
    steps = list(
        db.execute(
            select(AgentTaskStep).where(AgentTaskStep.task_id == task.id).order_by(AgentTaskStep.id.asc())
        ).scalars().all()
    )
    payload = serialize_task(task, steps=steps)
    payload["applicant"] = serialize_applicant(db.get(User, task.user_id), task.enterprise_id)
    approval = db.execute(
        select(AgentApproval).where(AgentApproval.task_id == task.id).order_by(AgentApproval.id.desc())
    ).scalars().first()
    payload["approval"] = serialize_approval(approval) if approval else None
    artifact = db.execute(
        select(AgentArtifact).where(AgentArtifact.task_id == task.id).order_by(AgentArtifact.id.desc())
    ).scalars().first()
    payload["artifact"] = {"id": artifact.id, "type": artifact.artifact_type} if artifact else None
    attachments = list(
        db.execute(
            select(AgentTaskAttachment)
            .where(
                AgentTaskAttachment.task_id == task.id,
                AgentTaskAttachment.enterprise_id == task.enterprise_id,
            )
            .order_by(AgentTaskAttachment.id.asc())
        ).scalars().all()
    )
    payload["attachments"] = [
        {
            "id": item.id,
            "filename": item.filename,
            "file_hash": item.file_hash,
            "file_size": item.file_size,
            "status": item.status,
            "summary": item.summary,
            "failure_reason": item.failure_reason,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in attachments
    ]
    latest = steps[-1] if steps else None
    payload["execution"] = {
        "node": task.current_node,
        "status": latest.status if latest and latest.node_name == task.current_node else task.status,
        "attempt": latest.attempt if latest and latest.node_name == task.current_node else None,
        "started_at": latest.created_at.isoformat() if latest and latest.created_at else None,
    }
    has_record = db.execute(select(AgentBusinessRecord.id).where(AgentBusinessRecord.task_id == task.id).limit(1)).first() is not None
    upload_allowed = task.status in {"waiting_input", "failed"} and not has_record
    payload["capabilities"] = {
        "upload_attachment": {"allowed": upload_allowed, "reason": "" if upload_allowed else (
            "正式记录已生成，不能变更材料" if has_record else
            "需企业管理员退回后补充材料" if task.status == "waiting_approval" else
            "任务执行中不能变更材料" if task.status in {"running", "executing", "pending_dispatch", "queued"} else "当前任务状态不接受材料"
        )},
        "retry": {"allowed": task.status == "failed", "reason": "只有失败任务可以重试"},
        "submit_input": {"allowed": task.status in {"waiting_input", "failed"} and not has_record},
    }
    management_reason = _task_management_reason(db, task, user_id)
    payload["capabilities"].update({
        "withdraw": {
            "allowed": not management_reason and task.status not in {"cancel_requested", "cancelled"},
            "reason": management_reason or ("申请正在安全停止" if task.status == "cancel_requested" else "申请已停止" if task.status == "cancelled" else ""),
        },
        "delete": {
            "allowed": not management_reason and task.status == "cancelled",
            "reason": management_reason or ("等待任务安全停止后可删除" if task.status == "cancel_requested" else "请先撤回申请，再删除任务" if task.status != "cancelled" else ""),
        },
    })
    payload["approval_context"] = approval_context(db, task, approval, payload, user_id=user_id, role=role)
    return payload


def serialize_applicant(user: User | None, enterprise_id: int) -> dict:
    if user is None or user.enterprise_id != enterprise_id:
        return {"id": None, "display_name": "已移除用户", "username": ""}
    return {"id": user.id, "display_name": user.display_name or user.username, "username": user.username}


def approval_context(db: Session, task: AgentTask, approval: AgentApproval | None, payload: dict, *, user_id: int | None, role: str) -> dict:
    eligible = eligible_reviewers(db, task.enterprise_id, task.user_id)
    actor = db.get(User, user_id) if user_id else None
    can_decide = bool(approval and approval.status == "pending" and approval.task_version == task.version
                      and task.status == "waiting_approval" and task.deleted_at is None
                      and user_id in eligible and actor and role == actor.role)
    reason = "等待其他企业管理员审批"
    if not eligible:
        reason = "当前企业没有可用的其他企业管理员，暂无法完成审批"
    if role in {"system_admin", "admin"} and not can_review_enterprise(db, actor, task.enterprise_id):
        reason = "系统管理员只能审批本人所属 system 企业的任务，不能审批其他企业任务"
    elif user_id == task.user_id and role in {"enterprise_admin", "system_admin", "admin"}:
        reason = "申请人与审批人不能相同，请由其他企业管理员处理"
    context = {
        "required": task.status == "waiting_approval" or bool(payload["risk"].get("requires_approval")),
        "status": approval.status if approval else None, "can_decide": can_decide,
        "eligible_approver_count": len(eligible), "reason": reason,
    }
    stored_plan = payload["plan"]
    context["plan_outdated"] = bool(stored_plan.get("actions") and not has_current_contract(stored_plan))
    assessment = execution_contract({"skill_id": task.skill_id, "goal": task.goal,
        "request": _loads(task.request_json, {}), "evidence": _loads(task.evidence_json, []),
        "risk_policy": {}})["policy_assessment"]
    policy_blocked = bool(assessment["blocking_issues"])
    manual_review = bool(payload["audit"] and not audit_passed(payload["audit"])) or policy_blocked
    blocked = "内部登记方案复核未通过，请退回补充并重新审查，不能直接批准" if manual_review else ""
    context.update({
        "manual_review_required": manual_review,
        "can_approve": can_decide and audit_passed(payload["audit"]) and not policy_blocked and not context["plan_outdated"],
        "approval_blocked_reason": "制度核对未通过：" + "；".join(assessment["blocking_issues"]) if policy_blocked else blocked,
    })
    if manual_review and task.status == "waiting_approval":
        context["reason"] = ("制度条件与申请冲突，需企业管理员退回，由申请人修改后重提；不能直接批准登记"
            if policy_blocked else "两次自动修订后复核仍未通过，需企业管理员退回补充或拒绝；不能直接批准登记")
    return context


def merge_task_input(db: Session, task: AgentTask, fields: dict[str, Any]) -> AgentTask:
    task = db.execute(select(AgentTask).where(AgentTask.id == task.id)
                      .with_for_update().execution_options(populate_existing=True)).scalar_one()
    if task.status not in {AgentTaskStatus.WAITING_INPUT, AgentTaskStatus.FAILED}:
        raise ValueError("任务当前不接受补充信息")
    request_data = _loads(task.request_json, {})
    safe_fields = dict(fields)
    if task.skill_id == "procurement":
        safe_fields.pop("quotation_attached", None)
        request_data["quotation_attached"] = False
    request_data.update(safe_fields)
    model = get_skill(task.skill_id).input_model.model_validate(request_data)
    normalized = model.model_dump(mode="json")
    if normalized != _loads(task.request_json, {}):
        for approval in db.execute(select(AgentApproval).where(
            AgentApproval.task_id == task.id, AgentApproval.status.in_(["approved", "pending"]),
        )).scalars():
            approval.status = "superseded"
            approval.comment = "申请信息已变更，原审批不能用于新申请"
    task.request_json = _dumps(normalized)
    missing = missing_fields(task.skill_id, normalized)
    task.missing_fields_json = _dumps(missing)
    task.version += 1
    if not missing and task.status in {AgentTaskStatus.WAITING_INPUT, AgentTaskStatus.FAILED}:
        attempt = db.scalar(select(func.max(AgentTaskStep.attempt)).where(
            AgentTaskStep.task_id == task.id, AgentTaskStep.node_name == "input_confirmation",
        )) or 0
        db.add(AgentTaskStep(enterprise_id=task.enterprise_id, task_id=task.id,
                            node_name="input_confirmation", status="completed", attempt=attempt + 1,
                            input_summary=f"task_version={task.version}",
                            output_summary="申请信息已确认提交，开始制度检索与审查"))
        task.status = AgentTaskStatus.PENDING_DISPATCH
        task.current_node = "input_updated"
        upsert_outbox(db, task.id)
    db.commit()
    db.refresh(task)
    return task


def get_risk_policy(db: Session, enterprise_id: int) -> AgentRiskPolicy:
    policy = db.execute(select(AgentRiskPolicy).where(AgentRiskPolicy.enterprise_id == enterprise_id)).scalar_one_or_none()
    if policy is None:
        policy = AgentRiskPolicy(
            enterprise_id=enterprise_id,
            procurement_approval_amount=50000,
            procurement_requires_quotation=True,
            procurement_sensitive_data_requires_approval=True,
            access_high_risk_classification="secret",
            access_max_duration_days=30,
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return policy


def risk_policy_dict(policy: AgentRiskPolicy) -> dict:
    return {
        "enterprise_id": policy.enterprise_id,
        "procurement_approval_amount": policy.procurement_approval_amount,
        "procurement_requires_quotation": bool(policy.procurement_requires_quotation),
        "procurement_sensitive_data_requires_approval": bool(
            policy.procurement_sensitive_data_requires_approval
        ),
        "access_high_risk_classification": policy.access_high_risk_classification,
        "access_max_duration_days": policy.access_max_duration_days,
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
    }


def risk_policy_rules(policy: AgentRiskPolicy) -> dict:
    return {
        "procurement_approval_amount": policy.procurement_approval_amount,
        "procurement_requires_quotation": bool(policy.procurement_requires_quotation),
        "procurement_sensitive_data_requires_approval": bool(
            policy.procurement_sensitive_data_requires_approval
        ),
        "access_high_risk_classification": policy.access_high_risk_classification,
        "access_max_duration_days": policy.access_max_duration_days,
    }


def calculate_task_risk(db: Session, task: AgentTask):
    policy = get_risk_policy(db, task.enterprise_id)
    result = assess_risk(
        task.skill_id,
        _loads(task.request_json, {}),
        risk_policy_rules(policy),
    )
    task.risk_level = result.level
    db.flush()
    return result


def upsert_outbox(db: Session, task_id: int) -> AgentOutbox:
    item = db.execute(select(AgentOutbox).where(AgentOutbox.task_id == task_id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one_or_none()
    if item is None:
        item = AgentOutbox(task_id=task_id, status="pending")
        db.add(item)
    else:
        item.status = "pending"
        item.last_error = ""
        item.next_attempt_at = None
    return item


def cancel_task(db: Session, task: AgentTask) -> AgentTask:
    task = db.execute(select(AgentTask).where(AgentTask.id == task.id)
                      .with_for_update().execution_options(populate_existing=True)).scalar_one()
    return _cancel_locked_task(db, task)


def _cancel_locked_task(db: Session, task: AgentTask) -> AgentTask:
    if task.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.CANCELLED}:
        return task
    if task.status == AgentTaskStatus.CANCEL_REQUESTED:
        return task
    committed_record = db.execute(select(AgentBusinessRecord.id).where(
        AgentBusinessRecord.task_id == task.id,
        AgentBusinessRecord.enterprise_id == task.enterprise_id,
    ).limit(1).with_for_update()).first()
    if committed_record is not None:
        raise ValueError("已生成正式业务记录，中断不能撤销该记录；请完成或重试生成执行档案")
    if task.status in {AgentTaskStatus.RUNNING, AgentTaskStatus.EXECUTING}:
        transition_task(task, AgentTaskStatus.CANCEL_REQUESTED, node="cancel_requested")
    else:
        transition_task(task, AgentTaskStatus.CANCELLED, node="cancelled")
        # 即使业务状态已结束，也要让 Worker 清理等待节点留下的 Redis Checkpoint。
        upsert_outbox(db, task.id)
    pending_approvals = db.execute(
        select(AgentApproval).where(
            AgentApproval.task_id == task.id,
            AgentApproval.status == "pending",
        ).with_for_update().execution_options(populate_existing=True)
    ).scalars().all()
    for approval in pending_approvals:
        approval.status = "cancelled"
        approval.comment = approval.comment or "任务已中断，审批自动撤回"
        approval.decided_at = datetime.now()
    db.commit()
    db.refresh(task)
    return task


def _task_management_reason(db: Session, task: AgentTask, user_id: int | None, *, locked: bool = False) -> str:
    if task.user_id != user_id:
        return "仅申请人可以撤回或删除自己的任务"
    if task.deleted_at is not None:
        return "任务已删除"
    approval_query = select(AgentApproval.id).where(
        AgentApproval.task_id == task.id, AgentApproval.status == "approved",
    ).limit(1)
    if db.execute(approval_query.with_for_update() if locked else approval_query).first() is not None:
        return "申请已获管理员批准，不能撤回或删除"
    record_query = select(AgentBusinessRecord.id).where(
        AgentBusinessRecord.task_id == task.id,
    ).limit(1)
    if db.execute(record_query.with_for_update() if locked else record_query).first() is not None:
        return "已生成正式业务记录，不能撤回或删除"
    if task.status == "completed":
        return "已完成任务不能撤回或删除"
    return ""


def _lock_owned_task(db: Session, task: AgentTask, user_id: int) -> AgentTask:
    task = db.execute(select(AgentTask).where(AgentTask.id == task.id)
                      .with_for_update().execution_options(populate_existing=True)).scalar_one()
    if task.user_id != user_id:
        raise PermissionError("仅申请人可以撤回或删除自己的任务")
    return task


def withdraw_task(db: Session, task: AgentTask, *, user_id: int) -> AgentTask:
    task = _lock_owned_task(db, task, user_id)
    reason = _task_management_reason(db, task, user_id, locked=True)
    if reason:
        raise ValueError(reason)
    if task.withdrawn_at is not None and task.status in {"cancel_requested", "cancelled"}:
        return task
    task.withdrawn_at = datetime.now()
    task.version += 1
    result = _cancel_locked_task(db, task)
    db.commit()
    db.refresh(result)
    return result


def delete_task(db: Session, task: AgentTask, *, user_id: int) -> AgentTask:
    task = _lock_owned_task(db, task, user_id)
    if task.deleted_at is not None:
        return task
    reason = _task_management_reason(db, task, user_id, locked=True)
    if reason:
        raise ValueError(reason)
    if task.status != "cancelled":
        raise ValueError("请先撤回申请，并等待任务安全停止后再删除")
    task.deleted_at = datetime.now()
    task.version += 1
    # 留存审计事实；晚到的投递仍看到 cancelled，不能重新执行。
    upsert_outbox(db, task.id)
    db.commit()
    db.refresh(task)
    return task


def retry_task(db: Session, task: AgentTask) -> AgentTask:
    task = db.execute(select(AgentTask).where(AgentTask.id == task.id)
                      .with_for_update().execution_options(populate_existing=True)).scalar_one()
    if task.status != AgentTaskStatus.FAILED:
        raise ValueError("只有失败任务可以重试")
    node = "materials_updated" if task.current_node == "materials_updated" else "retry_requested"
    transition_task(task, AgentTaskStatus.PENDING_DISPATCH, node=node)
    task.error_message = ""
    upsert_outbox(db, task.id)
    db.commit()
    db.refresh(task)
    return task


def create_approval(db: Session, task: AgentTask) -> AgentApproval:
    existing = db.execute(
        select(AgentApproval).where(
            AgentApproval.task_id == task.id,
            AgentApproval.task_version == task.version,
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    approval = AgentApproval(
        enterprise_id=task.enterprise_id,
        task_id=task.id,
        task_version=task.version,
        status="pending",
    )
    db.add(approval)
    db.flush()
    return approval


def list_pending_approvals(db: Session, enterprise_id: int) -> list[AgentApproval]:
    return list(
        db.execute(
            select(AgentApproval)
            .join(AgentTask, AgentTask.id == AgentApproval.task_id)
            .where(AgentApproval.enterprise_id == enterprise_id, AgentTask.enterprise_id == enterprise_id,
                   AgentApproval.status == "pending", AgentTask.status == "waiting_approval",
                   AgentApproval.task_version == AgentTask.version, AgentTask.deleted_at.is_(None))
            .order_by(AgentApproval.created_at.asc())
        ).scalars().all()
    )


def list_approval_history(db: Session, enterprise_id: int, user_id: int, role: str, *, q: str = "", status: str = "all", skill_id: str = "all", sort: str = "pending_first", page: int = 1, page_size: int = 20) -> dict:
    # 任务与审批各自限定租户；姓名联接也限定归属，不信任客户端或损坏的关联。
    stmt = (select(AgentApproval, AgentTask, User)
            .join(AgentTask, AgentTask.id == AgentApproval.task_id)
            .outerjoin(User, and_(User.id == AgentTask.user_id, User.enterprise_id == enterprise_id))
            .where(AgentApproval.enterprise_id == enterprise_id, AgentTask.enterprise_id == enterprise_id))
    if status != "all":
        stmt = stmt.where(AgentApproval.status == status)
    if skill_id != "all":
        stmt = stmt.where(AgentTask.skill_id == skill_id)
    if q.strip():
        term = q.strip()
        conditions = [column.contains(term, autoescape=True) for column in
                      (AgentTask.goal, AgentTask.request_json, User.display_name, User.username)]
        number = term.lstrip("#")
        if number.isascii() and number.isdecimal() and len(number) <= 18:
            conditions.append(AgentTask.id == int(number))
        stmt = stmt.where(or_(*conditions))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    pending = and_(AgentApproval.status == "pending", AgentTask.status == "waiting_approval",
                   AgentApproval.task_version == AgentTask.version, AgentTask.deleted_at.is_(None))
    if sort == "pending_first":
        stmt = stmt.order_by(case((pending, 0), else_=1),
                             case((pending, AgentTask.created_at)).asc(),
                             AgentTask.created_at.desc(), AgentApproval.id.desc())
    else:
        stmt = stmt.order_by(AgentTask.created_at.asc() if sort == "oldest" else AgentTask.created_at.desc(),
                             AgentApproval.id.asc() if sort == "oldest" else AgentApproval.id.desc())
    items = []
    for approval, task, applicant in db.execute(stmt.offset((page - 1) * page_size).limit(page_size)):
        payload = serialize_task(task)
        context = approval_context(db, task, approval, payload, user_id=user_id, role=role)
        summary = {key: payload[key] for key in ("id", "goal", "skill_id", "status", "risk_level", "request", "created_at", "deleted_at", "withdrawn_at")}
        summary["approval_context"] = context
        items.append({**serialize_approval(approval), "applicant": serialize_applicant(applicant, enterprise_id),
                      "applied_at": payload["created_at"], "task": summary})
    return {"items": items, "total": total, "pending_count": len(list_pending_approvals(db, enterprise_id)),
            "page": page, "page_size": page_size}


def decide_approval(
    db: Session,
    approval: AgentApproval,
    *,
    decision: str,
    comment: str,
    decided_by: int,
) -> AgentApproval:
    task = db.execute(select(AgentTask).where(AgentTask.id == approval.task_id)
                      .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    approval = db.execute(select(AgentApproval).where(AgentApproval.id == approval.id)
                          .with_for_update().execution_options(populate_existing=True)).scalar_one()
    reviewer = db.get(User, decided_by)
    if not can_review_enterprise(db, reviewer, approval.enterprise_id):
        raise ValueError("审批人没有本企业审批权限")
    if task is not None and task.user_id == decided_by:
        raise ValueError("申请人与审批人不能是同一人，请由其他企业管理员处理")
    if approval.status != "pending":
        if approval.status != decision:
            raise ValueError("审批已处理，不能修改已有决定")
        return approval
    if task is None or task.enterprise_id != approval.enterprise_id:
        raise ValueError("审批对应的任务不存在")
    if task.version != approval.task_version:
        raise ValueError("任务内容已更新，请刷新后重新审批")
    if task.status != AgentTaskStatus.WAITING_APPROVAL:
        raise ValueError("任务当前不在待审批状态")
    if decision == "approved":
        current_state = {"skill_id": task.skill_id, "goal": task.goal,
            "request": _loads(task.request_json, {}), "evidence": _loads(task.evidence_json, []), "risk_policy": {}}
        require_policy_compliance(current_state, execution_contract(current_state)["policy_assessment"])
        stored_plan = _loads(task.plan_json, {})
        if stored_plan.get("actions") and not has_current_contract(stored_plan):
            raise ValueError("历史计划未绑定真实执行契约，请先退回补充并重新审查，不能直接批准")
        if not audit_passed(_loads(task.audit_json, {})):
            raise ValueError("内部登记方案复核未通过，请先退回补充并重新审查，不能直接批准")
    approval.status = decision
    approval.comment = comment.strip()
    approval.decided_by = decided_by
    approval.decided_at = datetime.now()
    # 走统一状态机（含 version 自增），不要在分支里直接赋 status ——
    # 直接赋值会绕过合法转换校验，这类地方以后最容易漏掉一条路径。
    if decision == "approved":
        transition_task(task, AgentTaskStatus.PENDING_DISPATCH, node="approval_resumed")
        upsert_outbox(db, task.id)
    elif decision == "changes_requested":
        transition_task(task, AgentTaskStatus.WAITING_INPUT, node="approval_changes_requested")
    else:
        # 驳回是终态、不执行业务动作。用 REJECTED 而不是 CANCELLED：
        # 档案里要能分清「审批没过」和「申请人自己撤回」。
        # 仍要写 Outbox —— 派发出去的 worker 会立刻返回并顺手清掉 Redis
        # checkpoint，否则被驳回的任务会留下一份永不清理的检查点。
        transition_task(task, AgentTaskStatus.REJECTED, node="approval_rejected")
        upsert_outbox(db, task.id)
    db.commit()
    db.refresh(approval)
    return approval


def serialize_approval(approval: AgentApproval) -> dict:
    return {
        "id": approval.id,
        "enterprise_id": approval.enterprise_id,
        "task_id": approval.task_id,
        "task_version": approval.task_version,
        "status": approval.status,
        "decided_by": approval.decided_by,
        "comment": approval.comment,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
    }


def create_business_record(db: Session, task: AgentTask, record_type: str, payload: dict) -> AgentBusinessRecord:
    # One immutable business action per task, even if replayed model text changes.
    db.flush()
    task = db.execute(select(AgentTask).where(AgentTask.id == task.id)
                      .with_for_update().execution_options(populate_existing=True)).scalar_one()
    if task.deleted_at is not None or task.status in {"cancel_requested", "cancelled", "rejected"}:
        raise ValueError("任务已请求停止，不能写入正式业务记录")
    material = f"{task.enterprise_id}:{task.id}:{record_type}"
    key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    existing = db.execute(
        select(AgentBusinessRecord).where(
            AgentBusinessRecord.enterprise_id == task.enterprise_id,
            AgentBusinessRecord.task_id == task.id,
            AgentBusinessRecord.record_type == record_type,
        ).order_by(AgentBusinessRecord.id.asc())
    ).scalars().first()
    if existing:
        return existing
    record = AgentBusinessRecord(
        enterprise_id=task.enterprise_id,
        task_id=task.id,
        record_type=record_type,
        payload_json=_dumps(payload),
        idempotency_key=key,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.execute(
            select(AgentBusinessRecord).where(AgentBusinessRecord.idempotency_key == key)
        ).scalar_one()
    db.refresh(record)
    return record


def list_skill_payloads(db: Session, enterprise_id: int) -> list[dict]:
    configs = {
        item.skill_id: item
        for item in db.execute(
            select(AgentSkillConfig).where(AgentSkillConfig.enterprise_id == enterprise_id)
        ).scalars().all()
    }
    return [
        skill.public_dict(
            enabled=bool(configs.get(skill.id).enabled) if configs.get(skill.id) else True,
            config=_loads(configs.get(skill.id).config_json, {}) if configs.get(skill.id) else {},
        )
        for skill in list_skills()
    ]


def validate_skill_config(skill_id: str, config: dict | None) -> dict:
    """校验企业提交的 Skill 配置。

    只允许白名单键，且值必须落在 [下限, 代码级上限] 之间 —— 配置只能收紧
    安全边界，不能放宽。原先这里是个自由字典：any 键、any 值，存下来还没人消费。
    """
    skill = get_skill(skill_id)
    payload = dict(config or {})
    unknown = set(payload) - {"max_policy_searches"}
    if unknown:
        raise ValueError(f"不支持的 Skill 配置项：{', '.join(sorted(unknown))}")
    cleaned: dict = {}
    if "max_policy_searches" in payload:
        value = payload["max_policy_searches"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("max_policy_searches 必须是整数")
        if not 1 <= value <= skill.max_policy_searches:
            raise ValueError(f"max_policy_searches 必须在 1~{skill.max_policy_searches} 之间")
        cleaned["max_policy_searches"] = value
    return cleaned


def skill_runtime_config(db: Session, enterprise_id: int, skill_id: str) -> dict:
    """取该企业对这个 Skill 的**有效**运行时配置（已夹在安全区间内）。

    历史数据里可能有绕过校验存进去的脏值，所以这里再夹一次，不能只信写入时校验。
    """
    skill = get_skill(skill_id)
    item = db.execute(
        select(AgentSkillConfig).where(
            AgentSkillConfig.enterprise_id == enterprise_id,
            AgentSkillConfig.skill_id == skill_id,
        )
    ).scalar_one_or_none()
    raw = _loads(item.config_json, {}) if item is not None else {}
    # 历史脏数据或人工维护可能把它写成 [] / null / "str" / 5 —— 直接 .get 会
    # AttributeError 把任务打挂。读取侧的防御不能省，哪怕写入侧已经校验过。
    if not isinstance(raw, dict):
        return {}
    requested = raw.get("max_policy_searches")
    if isinstance(requested, bool) or not isinstance(requested, int):
        return {}
    return {"max_policy_searches": max(1, min(requested, skill.max_policy_searches))}


def update_skill_config(db: Session, enterprise_id: int, skill_id: str, enabled: bool, config: dict, user_id: int):
    skill = get_skill(skill_id)
    cleaned = validate_skill_config(skill_id, config)
    item = db.execute(
        select(AgentSkillConfig).where(
            AgentSkillConfig.enterprise_id == enterprise_id,
            AgentSkillConfig.skill_id == skill_id,
        )
    ).scalar_one_or_none()
    if item is None:
        item = AgentSkillConfig(enterprise_id=enterprise_id, skill_id=skill_id)
        db.add(item)
    item.version = skill.version
    item.enabled = int(enabled)
    item.config_json = _dumps(cleaned)
    item.updated_by = user_id
    db.commit()
    return skill.public_dict(enabled=enabled, config=cleaned)

"""企业合规执行中心 API。"""

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import current_enterprise_id, current_user_id, require_enterprise_admin, require_full_access
from app.database import get_db
from app.models.agent import AgentApproval, AgentArtifact, AgentEvaluationRun, AgentTask
from app.models.user import User
from app.schemas.agent import (
    AgentApprovalDecision,
    AgentRiskPolicyUpdate,
    AgentSkillUpdate,
    AgentTaskCreate,
    AgentTaskInput,
)
from app.agent import events as agent_events
from app.agent.permissions import can_review_enterprise
from app.services import agent_attachment_service, agent_evaluation_service, agent_task_service, audit_service


router = APIRouter(prefix="/api/agent", tags=["agent"])


def _identity(request: Request) -> tuple[int, int, str, str]:
    user = getattr(request.state, "auth_user", None) or {}
    return (
        current_enterprise_id(request),
        current_user_id(request),
        str(user.get("role") or "employee"),
        str(user.get("display_name") or user.get("username") or "用户"),
    )


def _task_or_404(db: Session, task_id: int, request: Request, *, include_deleted: bool = False) -> AgentTask:
    enterprise_id, user_id, role, _ = _identity(request)
    task = agent_task_service.get_task(db, task_id, enterprise_id, user_id, role, include_deleted=include_deleted)
    if task is None:
        raise HTTPException(status_code=404, detail="Agent 任务不存在")
    return task


def _require_agent_approver(request: Request, db: Session = Depends(get_db)):
    if not can_review_enterprise(db, db.get(User, current_user_id(request)), current_enterprise_id(request)):
        raise HTTPException(status_code=403, detail="当前账号没有本企业 Agent 审批权限")


@router.post("/tasks", dependencies=[Depends(require_full_access)])
def create_agent_task(body: AgentTaskCreate, request: Request, db: Session = Depends(get_db)):
    enterprise_id, user_id, _, actor_name = _identity(request)
    try:
        task = agent_task_service.create_task(db, enterprise_id, user_id, body.goal, body.kb_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_service.record_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="create_agent_task",
        target_type="agent_task",
        target_name=f"#{task.id} {task.goal[:80]}",
        enterprise_id=enterprise_id,
    )
    from app.agent.runtime import dispatch_pending_task

    dispatch_pending_task(task.id)
    return agent_task_service.serialize_task(task)


@router.get("/tasks")
def list_agent_tasks(request: Request, db: Session = Depends(get_db)):
    enterprise_id, user_id, role, _ = _identity(request)
    return [
        agent_task_service.serialize_task(task)
        for task in agent_task_service.list_tasks(db, enterprise_id, user_id, role)
    ]


@router.get("/tasks/{task_id}")
def get_agent_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    _, user_id, role, _ = _identity(request)
    return agent_task_service.get_task_detail(db, _task_or_404(db, task_id, request), user_id=user_id, role=role)


@router.post("/tasks/{task_id}/attachments", dependencies=[Depends(require_full_access)])
def upload_agent_task_attachment(
    task_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    task = _task_or_404(db, task_id, request)
    if task.status in {"completed", "cancelled"}:
        raise HTTPException(status_code=409, detail="已结束任务不能继续上传附件")
    try:
        item = agent_attachment_service.create_task_attachment(
            db,
            task,
            current_user_id(request),
            file,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    enterprise_id, user_id, _, actor_name = _identity(request)
    audit_service.record_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="upload_agent_attachment",
        target_type="agent_task",
        target_name=f"#{task.id} {item.filename}",
        enterprise_id=enterprise_id,
    )
    return agent_attachment_service.serialize_attachment(item)


@router.get("/tasks/{task_id}/artifact", response_class=PlainTextResponse)
def get_agent_task_artifact(task_id: int, request: Request, db: Session = Depends(get_db)):
    task = _task_or_404(db, task_id, request)
    artifact = db.execute(
        select(AgentArtifact)
        .where(AgentArtifact.task_id == task.id, AgentArtifact.enterprise_id == task.enterprise_id)
        .order_by(AgentArtifact.id.desc())
    ).scalars().first()
    if artifact is None:
        raise HTTPException(status_code=404, detail="任务执行档案尚未生成")
    return PlainTextResponse(
        artifact.content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="agent-task-{task.id}.md"'},
    )


@router.get("/tasks/{task_id}/events")
async def stream_agent_task_events(task_id: int, request: Request, db: Session = Depends(get_db)):
    # 先把企业 id 取出来：db.close() 之后 ORM 实例会失效，不能再读属性。
    task_enterprise_id = _task_or_404(db, task_id, request).enterprise_id
    # StreamingResponse 会保持依赖生命周期；事件流只读 Redis，提前归还数据库连接。
    db.close()
    last_event_id = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id") or "0-0"

    async def event_stream():
        current_id = last_event_id
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            try:
                items = await asyncio.to_thread(
                    lambda: list(agent_events.read_events(
                        task_id, current_id, block_ms=5000, enterprise_id=task_enterprise_id,
                    ))
                )
            except Exception as exc:
                payload = json.dumps(
                    {"message": f"实时事件暂不可用：{str(exc)[:160]}"},
                    ensure_ascii=False,
                )
                yield f"event: degraded\ndata: {payload}\n\n"
                return
            if not items:
                yield ": keepalive\n\n"
                continue
            for item in items:
                current_id = item["id"]
                payload = json.dumps(item["payload"], ensure_ascii=False)
                yield f"id: {item['id']}\nevent: {item['type']}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/health")
def agent_health():
    runtime_status = agent_events.runtime_health()
    return {
        "status": "ok" if runtime_status["available"] else "degraded",
        "agent_available": runtime_status["available"],
        "runtime": runtime_status,
        "rag_available": True,
    }


@router.post("/tasks/{task_id}/input")
def submit_agent_task_input(task_id: int, body: AgentTaskInput, request: Request, db: Session = Depends(get_db)):
    task = _task_or_404(db, task_id, request)
    try:
        task = agent_task_service.merge_task_input(db, task, body.fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    enterprise_id, user_id, _, actor_name = _identity(request)
    audit_service.record_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="submit_agent_input",
        target_type="agent_task",
        target_name=f"#{task.id}",
        enterprise_id=enterprise_id,
    )
    from app.agent.runtime import dispatch_pending_task

    if task.status == "pending_dispatch":
        dispatch_pending_task(task.id)
    return agent_task_service.get_task_detail(db, task, user_id=user_id, role=_identity(request)[2])


@router.post("/tasks/{task_id}/withdraw")
def withdraw_agent_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    enterprise_id, user_id, role, actor_name = _identity(request)
    try:
        task = agent_task_service.withdraw_task(db, _task_or_404(db, task_id, request), user_id=user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_service.record_audit(db, actor_id=user_id, actor_name=actor_name, action="withdraw_agent_task",
                               target_type="agent_task", target_name=f"#{task.id}", enterprise_id=enterprise_id)
    agent_events.publish_event(task.id, "task_status", {"status": task.status, "version": task.version},
                              enterprise_id=enterprise_id)
    from app.agent.runtime import dispatch_pending_task

    if task.status == "cancelled":
        dispatch_pending_task(task.id)
    return agent_task_service.get_task_detail(db, task, user_id=user_id, role=role)


@router.delete("/tasks/{task_id}")
def delete_agent_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    enterprise_id, user_id, _, actor_name = _identity(request)
    try:
        task = agent_task_service.delete_task(db, _task_or_404(db, task_id, request, include_deleted=True), user_id=user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_service.record_audit(db, actor_id=user_id, actor_name=actor_name, action="delete_agent_task",
                               target_type="agent_task", target_name=f"#{task.id}", enterprise_id=enterprise_id)
    agent_events.publish_event(task.id, "task_deleted", {"task_id": task.id, "version": task.version},
                               enterprise_id=enterprise_id)
    from app.agent.runtime import dispatch_pending_task

    dispatch_pending_task(task.id)
    return {"task_id": task.id, "deleted": True}


@router.post("/tasks/{task_id}/cancel")
def cancel_agent_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        task = agent_task_service.cancel_task(db, _task_or_404(db, task_id, request))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    enterprise_id, user_id, _, actor_name = _identity(request)
    audit_service.record_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="cancel_agent_task",
        target_type="agent_task",
        target_name=f"#{task.id}",
        enterprise_id=enterprise_id,
    )
    from app.agent.runtime import dispatch_pending_task

    dispatch_pending_task(task.id)
    return agent_task_service.serialize_task(task)


@router.post("/tasks/{task_id}/retry")
def retry_agent_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        task = agent_task_service.retry_task(db, _task_or_404(db, task_id, request))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    enterprise_id, user_id, _, actor_name = _identity(request)
    audit_service.record_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="retry_agent_task",
        target_type="agent_task",
        target_name=f"#{task.id}",
        enterprise_id=enterprise_id,
    )
    from app.agent.runtime import dispatch_pending_task

    dispatch_pending_task(task.id)
    return agent_task_service.serialize_task(task)


@router.get("/approvals", dependencies=[Depends(_require_agent_approver)])
def list_agent_approvals(
    request: Request, db: Session = Depends(get_db),
    scope: Literal["pending", "all"] = "pending",
    q: str = Query("", max_length=200),
    status: Literal["all", "pending", "approved", "changes_requested", "rejected", "cancelled", "superseded"] = "all",
    skill_id: Literal["all", "procurement", "access_request"] = "all",
    sort: Literal["pending_first", "newest", "oldest"] = "pending_first",
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    enterprise_id = current_enterprise_id(request)
    if scope == "all":
        _, user_id, role, _ = _identity(request)
        return agent_task_service.list_approval_history(db, enterprise_id, user_id, role,
            q=q, status=status, skill_id=skill_id, sort=sort, page=page, page_size=page_size)
    return [agent_task_service.serialize_approval(item) for item in agent_task_service.list_pending_approvals(db, enterprise_id)]


@router.post("/approvals/{approval_id}/decision", dependencies=[Depends(_require_agent_approver)])
def decide_agent_approval(
    approval_id: int,
    body: AgentApprovalDecision,
    request: Request,
    db: Session = Depends(get_db),
):
    enterprise_id, user_id, _, actor_name = _identity(request)
    approval = db.get(AgentApproval, approval_id)
    if approval is None or approval.enterprise_id != enterprise_id:
        raise HTTPException(status_code=404, detail="审批任务不存在")
    try:
        approval = agent_task_service.decide_approval(
            db,
            approval,
            decision=body.decision,
            comment=body.comment,
            decided_by=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_service.record_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="decide_agent_approval",
        target_type="agent_approval",
        target_name=f"#{approval.id} {body.decision}",
        enterprise_id=enterprise_id,
    )
    if body.decision in {"approved", "rejected"}:
        from app.agent.runtime import dispatch_pending_task

        dispatch_pending_task(approval.task_id)
    return agent_task_service.serialize_approval(approval)


@router.get("/skills")
def list_agent_skills(request: Request, db: Session = Depends(get_db)):
    return {"items": agent_task_service.list_skill_payloads(db, current_enterprise_id(request))}


@router.patch("/skills/{skill_id}", dependencies=[Depends(require_enterprise_admin)])
def update_agent_skill(
    skill_id: str,
    body: AgentSkillUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        payload = agent_task_service.update_skill_config(
            db,
            current_enterprise_id(request),
            skill_id,
            body.enabled,
            body.config,
            current_user_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    enterprise_id, user_id, _, actor_name = _identity(request)
    audit_service.record_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="update_agent_skill",
        target_type="agent_skill",
        target_name=f"{skill_id} ({'enabled' if body.enabled else 'disabled'})",
        enterprise_id=enterprise_id,
    )
    return payload


@router.get("/risk-policies")
def get_agent_risk_policy(request: Request, db: Session = Depends(get_db)):
    return agent_task_service.risk_policy_dict(
        agent_task_service.get_risk_policy(db, current_enterprise_id(request))
    )


@router.patch("/risk-policies", dependencies=[Depends(require_enterprise_admin)])
def update_agent_risk_policy(
    body: AgentRiskPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    policy = agent_task_service.get_risk_policy(db, current_enterprise_id(request))
    updates = body.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(policy, field, value)
    policy.updated_by = current_user_id(request)
    db.commit()
    db.refresh(policy)
    enterprise_id, user_id, _, actor_name = _identity(request)
    audit_service.record_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="update_agent_risk_policy",
        target_type="agent_risk_policy",
        target_name=(
            f"采购门槛 {policy.procurement_approval_amount} 元；"
            f"报价要求 {'开启' if policy.procurement_requires_quotation else '关闭'}；"
            f"权限期限 {policy.access_max_duration_days} 天"
        ),
        enterprise_id=enterprise_id,
    )
    return agent_task_service.risk_policy_dict(policy)


@router.post("/evaluations", dependencies=[Depends(require_enterprise_admin)])
def create_agent_evaluation(request: Request, db: Session = Depends(get_db)):
    enterprise_id, user_id, _, actor_name = _identity(request)
    run = agent_evaluation_service.create_evaluation_run(db, enterprise_id)
    audit_service.record_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="run_agent_evaluation",
        target_type="agent_evaluation_run",
        target_name=f"#{run.id}",
        enterprise_id=enterprise_id,
    )
    from app.agent.runtime import dispatch_agent_evaluation

    dispatch_agent_evaluation(run.id)
    return agent_evaluation_service.serialize_evaluation_run(db, run)


@router.get("/evaluations", dependencies=[Depends(require_enterprise_admin)])
def list_agent_evaluations(request: Request, db: Session = Depends(get_db)):
    return [
        agent_evaluation_service.serialize_evaluation_run(db, run)
        for run in agent_evaluation_service.list_evaluation_runs(db, current_enterprise_id(request))
    ]


@router.get("/evaluations/{run_id}", dependencies=[Depends(require_enterprise_admin)])
def get_agent_evaluation(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(AgentEvaluationRun, int(run_id))
    if run is None or run.enterprise_id != current_enterprise_id(request):
        raise HTTPException(status_code=404, detail="Agent 评测任务不存在")
    return agent_evaluation_service.serialize_evaluation_run(db, run, include_cases=True)

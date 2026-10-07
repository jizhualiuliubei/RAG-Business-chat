"""
审计日志服务
================
提供统一写入和查询入口，避免各个路由直接操作审计表。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


ACTION_LABELS = {
    "login": "登录系统",
    "create_user": "创建员工",
    "update_user_status": "更新账号状态",
    "create_knowledge_base": "新建知识库",
    "update_knowledge_base": "更新知识库",
    "delete_knowledge_base": "删除知识库",
    "upload_document": "上传文档",
    "delete_document": "删除文档",
    "run_evaluation": "运行评测",
    "cancel_evaluation_run": "中断评测任务",
    "delete_evaluation_run": "删除评测任务",
    "create_evaluation_dataset": "创建评测集",
    "approve_evaluation_dataset": "审核评测集",
    "delete_evaluation_dataset": "删除评测集",
    "create_enterprise": "创建企业",
    "update_enterprise": "编辑企业",
    "approve_enterprise": "审核通过企业",
    "reject_enterprise": "拒绝企业申请",
    "update_enterprise_status": "更新企业状态",
    "delete_enterprise": "删除企业",
    "impersonate_enterprise": "代管企业",
    "update_model_keys": "更新模型 Key",
    "create_agent_task": "创建合规执行任务",
    "upload_agent_attachment": "上传 Agent 任务材料",
    "submit_agent_input": "补充 Agent 任务信息",
    "cancel_agent_task": "中断 Agent 任务",
    "withdraw_agent_task": "撤回合规申请",
    "delete_agent_task": "删除合规任务",
    "retry_agent_task": "重试 Agent 任务",
    "decide_agent_approval": "处理 Agent 审批",
    "update_agent_skill": "更新 Agent Skill",
    "update_agent_risk_policy": "更新 Agent 风险规则",
    "run_agent_evaluation": "运行 Agent 评测",
}


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def record_audit(
    db: Session,
    *,
    actor_id: int | None = None,
    actor_name: str = "系统",
    action: str,
    target_type: str = "",
    target_id: int | None = None,
    target_name: str = "",
    result: str = "success",
    enterprise_id: int | None = None,
) -> AuditLog:
    log = AuditLog(
        enterprise_id=enterprise_id,
        actor_id=actor_id,
        actor_name=actor_name or "系统",
        action=action,
        target_type=target_type,
        target_name=target_name or "",
        result=result,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def serialize_audit(log: AuditLog) -> dict:
    return {
        "id": log.id,
        "actor_id": log.actor_id,
        "actor_name": log.actor_name,
        "enterprise_id": log.enterprise_id,
        "action": log.action,
        "action_label": ACTION_LABELS.get(log.action, log.action),
        "target_type": log.target_type,
        "target_name": log.target_name,
        "result": log.result,
        "created_at": _iso(log.created_at),
    }


def list_audit_logs(db: Session, limit: int = 80, enterprise_id: int | None = None) -> list[dict]:
    stmt = select(AuditLog)
    if enterprise_id is not None:
        stmt = stmt.where(AuditLog.enterprise_id == enterprise_id)
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    return [serialize_audit(item) for item in db.execute(stmt).scalars().all()]

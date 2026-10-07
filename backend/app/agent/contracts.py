"""两个 Agent 之间传递的结构化契约。"""

from typing import Any

from pydantic import BaseModel, Field

from app.agent.skills import get_skill


class ExecutionAction(BaseModel):
    action_type: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    summary: str = Field(min_length=2, max_length=2000)
    policy_basis: list[str] = Field(default_factory=list, max_length=30)
    actions: list[ExecutionAction] = Field(min_length=1, max_length=5)
    expected_result: str = Field(min_length=2, max_length=1000)
    execution_contract: dict[str, Any] = Field(default_factory=dict)
    review_notes: list[str] = Field(default_factory=list, max_length=20)


class AuditVerdict(BaseModel):
    approved: bool
    evidence_sufficient: bool
    reasons: list[str] = Field(default_factory=list, max_length=20)
    required_changes: list[str] = Field(default_factory=list, max_length=20)


def audit_passed(audit: dict) -> bool:
    return (audit.get("approved") is True and audit.get("evidence_sufficient") is True
            and not audit.get("required_changes"))


def validate_plan_tools(skill_id: str, plan: ExecutionPlan) -> None:
    if not isinstance(plan, ExecutionPlan):
        raise ValueError("计划生成结构化输出无效，不能执行写工具")
    allowed = set(get_skill(skill_id).allowed_write_tools)
    if len(plan.actions) != 1:
        raise ValueError(f"Skill {skill_id} 的执行计划只能包含一个写动作")
    for action in plan.actions:
        if action.action_type not in allowed:
            raise ValueError(f"Skill {skill_id} 无权调用工具：{action.action_type}")

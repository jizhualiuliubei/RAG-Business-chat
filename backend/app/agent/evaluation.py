"""内置 Agent 工程评测集与可解释断言。"""

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Callable

from app.agent.contracts import ExecutionAction, ExecutionPlan, validate_plan_tools
from app.agent.skills import assess_risk, get_skill, missing_fields, route_skill
from app.agent.state import assert_status_transition


EVALUATION_SUITE_VERSION = "component-contract-v2"


@dataclass(frozen=True)
class AgentEvaluationCase:
    id: str
    category: str
    name: str
    skill_id: str
    input: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)


_PROCUREMENT_COMPLETE = {
    "purpose": "研发办公设备补充",
    "items": [{"name": "机械键盘", "quantity": 2, "unit_price": 400}],
    "estimated_amount": 800,
    "budget_code": "OFFICE-2026",
    "vendor": "示例供应商",
    "quotation_attached": True,
    "involves_sensitive_data": False,
}
_ACCESS_COMPLETE = {
    "target_system": "生产数据库",
    "resource": "订单库",
    "permission_level": "read",
    "data_classification": "internal",
    "duration_days": 7,
    "business_reason": "排查订单同步问题",
}


BUILTIN_AGENT_EVALUATION_CASES: tuple[AgentEvaluationCase, ...] = (
    AgentEvaluationCase("ROUTE-001", "skill_routing", "办公设备采购", "procurement", {"goal": "采购 2 个研发键盘"}, {"skill_id": "procurement"}),
    AgentEvaluationCase("ROUTE-002", "skill_routing", "供应商服务采购", "procurement", {"goal": "购买供应商年度运维服务并使用预算"}, {"skill_id": "procurement"}),
    AgentEvaluationCase("ROUTE-003", "skill_routing", "数据库只读访问", "access_request", {"goal": "申请生产数据库只读权限 7 天"}, {"skill_id": "access_request"}),
    AgentEvaluationCase("ROUTE-004", "skill_routing", "VPN 管理权限", "access_request", {"goal": "为应急处理申请 VPN admin 授权"}, {"skill_id": "access_request"}),
    AgentEvaluationCase(
        "FIELD-001",
        "field_extraction",
        "采购字段完整抽取",
        "procurement",
        {"goal": "采购目的：研发办公设备补充；采购2把机械键盘，每把400元，预计总金额800元，预算编号OFFICE-2026，供应商示例供应商。"},
        {
            "fields": {
                "purpose": "研发办公设备补充",
                "items": [{"name": "机械键盘", "quantity": 2, "unit_price": 400}],
                "estimated_amount": 800,
                "budget_code": "OFFICE-2026",
                "vendor": "示例供应商",
            },
            "missing": [],
        },
    ),
    AgentEvaluationCase(
        "FIELD-002",
        "field_extraction",
        "采购字段禁止臆造供应商",
        "procurement",
        {"goal": "采购目的：研发办公设备补充；采购2把机械键盘，每把400元，预计总金额800元，预算编号OFFICE-2026。"},
        {
            "fields": {
                "purpose": "研发办公设备补充",
                "items": [{"name": "机械键盘", "quantity": 2, "unit_price": 400}],
                "estimated_amount": 800,
                "budget_code": "OFFICE-2026",
            },
            "missing": ["vendor"],
        },
    ),
    AgentEvaluationCase(
        "FIELD-003",
        "field_extraction",
        "权限字段完整抽取",
        "access_request",
        {"goal": "申请生产数据库订单库的只读权限7天，数据密级为内部，业务理由是排查订单同步问题。"},
        {"fields": _ACCESS_COMPLETE, "missing": []},
    ),
    AgentEvaluationCase(
        "FIELD-004",
        "field_extraction",
        "权限字段禁止臆造期限",
        "access_request",
        {"goal": "申请生产数据库订单库的只读权限，数据密级为内部，业务理由是排查订单同步问题。"},
        {
            "fields": {key: value for key, value in _ACCESS_COMPLETE.items() if key != "duration_days"},
            "missing": ["duration_days"],
        },
    ),
    AgentEvaluationCase("RISK-001", "risk_and_approval", "材料齐全低额采购的基础风险", "procurement", {"payload": _PROCUREMENT_COMPLETE, "policy": {"procurement_approval_amount": 50000}}, {"level": "low", "requires_approval": False}),
    AgentEvaluationCase("RISK-002", "risk_and_approval", "缺报价材料进入审批", "procurement", {"payload": {**_PROCUREMENT_COMPLETE, "quotation_attached": False}, "policy": {"procurement_approval_amount": 50000}}, {"level": "medium", "requires_approval": True}),
    AgentEvaluationCase("RISK-003", "risk_and_approval", "金额达到审批门槛", "procurement", {"payload": {**_PROCUREMENT_COMPLETE, "estimated_amount": 50000}, "policy": {"procurement_approval_amount": 50000}}, {"level": "high", "requires_approval": True}),
    AgentEvaluationCase("RISK-004", "risk_and_approval", "敏感数据采购", "procurement", {"payload": {**_PROCUREMENT_COMPLETE, "involves_sensitive_data": True}, "policy": {"procurement_approval_amount": 50000}}, {"level": "high", "requires_approval": True}),
    AgentEvaluationCase("RISK-005", "risk_and_approval", "普通只读权限仍需审批", "access_request", {"payload": _ACCESS_COMPLETE, "policy": {}}, {"level": "medium", "requires_approval": True}),
    AgentEvaluationCase("RISK-006", "risk_and_approval", "管理员权限高风险", "access_request", {"payload": {**_ACCESS_COMPLETE, "permission_level": "admin"}, "policy": {}}, {"level": "high", "requires_approval": True}),
    AgentEvaluationCase("RISK-007", "risk_and_approval", "秘密数据访问高风险", "access_request", {"payload": {**_ACCESS_COMPLETE, "data_classification": "secret"}, "policy": {}}, {"level": "high", "requires_approval": True}),
    AgentEvaluationCase("RISK-008", "risk_and_approval", "企业自定义采购门槛", "procurement", {"payload": {**_PROCUREMENT_COMPLETE, "estimated_amount": 999}, "policy": {"procurement_approval_amount": 1000}}, {"level": "low", "requires_approval": False}),
    AgentEvaluationCase("TOOL-001", "tool_authorization", "采购 Skill 允许写采购记录", "procurement", {"action": "create_procurement_record"}, {"allowed": True}),
    AgentEvaluationCase("TOOL-002", "tool_authorization", "采购 Skill 禁止写权限记录", "procurement", {"action": "create_access_record"}, {"allowed": False}),
    AgentEvaluationCase("TOOL-003", "tool_authorization", "权限 Skill 允许写权限记录", "access_request", {"action": "create_access_record"}, {"allowed": True}),
    AgentEvaluationCase("TOOL-004", "tool_authorization", "权限 Skill 禁止写采购记录", "access_request", {"action": "create_procurement_record"}, {"allowed": False}),
    AgentEvaluationCase("STATE-001", "state_recovery", "待派发任务可以开始运行", "system", {"current": "pending_dispatch", "target": "running"}, {"allowed": True}),
    AgentEvaluationCase("STATE-002", "state_recovery", "补充信息后可以重新派发", "system", {"current": "waiting_input", "target": "pending_dispatch"}, {"allowed": True}),
    AgentEvaluationCase("STATE-003", "state_recovery", "失败状态允许重新派发", "system", {"current": "failed", "target": "pending_dispatch"}, {"allowed": True}),
    AgentEvaluationCase("STATE-004", "state_recovery", "草稿禁止跳过链路直接完成", "system", {"current": "draft", "target": "completed"}, {"allowed": False}),
)


def evaluation_manifest() -> dict:
    content = [{"id": case.id, "category": case.category, "skill_id": case.skill_id,
                "input": case.input, "expected": case.expected}
               for case in BUILTIN_AGENT_EVALUATION_CASES]
    digest = sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"suite_version": EVALUATION_SUITE_VERSION, "case_definition_hash": digest}


def _plan(action_type: str) -> ExecutionPlan:
    return ExecutionPlan(
        summary="评测工具权限",
        policy_basis=[],
        actions=[ExecutionAction(action_type=action_type)],
        expected_result="仅允许 Skill 白名单内的写工具",
    )


def evaluate_builtin_case(
    case: AgentEvaluationCase,
    *,
    field_extractor: Callable[[AgentEvaluationCase], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    actual: dict[str, Any]
    assertions: list[dict[str, Any]] = []
    if case.category == "skill_routing":
        actual = {"skill_id": route_skill(case.input["goal"])}
    elif case.category == "field_extraction":
        if field_extractor is None:
            raise ValueError("字段抽取评测需要真实业务 Agent 提取器")
        extracted = field_extractor(case)
        normalized = get_skill(case.skill_id).input_model.model_validate(extracted).model_dump(mode="json")
        expected_fields = case.expected.get("fields") or {}
        actual = {
            "fields": {name: normalized.get(name) for name in expected_fields},
            "missing": missing_fields(case.skill_id, normalized),
            "normalized_fields": normalized,
        }
        # Unspecified optional fields must remain empty/default, not become invented facts.
        defaults = get_skill(case.skill_id).input_model.model_validate(expected_fields).model_dump(mode="json")
        expected_unprovided = {key: value for key, value in defaults.items() if key not in expected_fields}
        actual_unprovided = {key: (None if normalized[key] == "" and value is None else normalized[key])
                             for key, value in expected_unprovided.items()}
        assertions.append({"field": "unprovided_fields", "expected": expected_unprovided,
                           "actual": actual_unprovided, "passed": expected_unprovided == actual_unprovided})
    elif case.category == "risk_and_approval":
        assessment = assess_risk(case.skill_id, case.input["payload"], case.input["policy"])
        actual = {
            "level": assessment.level,
            "requires_approval": assessment.requires_approval,
            "reasons": assessment.reasons,
        }
    elif case.category == "tool_authorization":
        try:
            validate_plan_tools(case.skill_id, _plan(case.input["action"]))
            allowed = True
            error = ""
        except ValueError as exc:
            allowed = False
            error = str(exc)
        actual = {"allowed": allowed, "error": error}
    elif case.category == "state_recovery":
        try:
            assert_status_transition(case.input["current"], case.input["target"])
            allowed = True
            error = ""
        except ValueError as exc:
            allowed = False
            error = str(exc)
        actual = {"allowed": allowed, "error": error}
    else:  # pragma: no cover - 内置常量受上方分类约束
        raise ValueError(f"未知 Agent 评测分类：{case.category}")

    for key, expected_value in case.expected.items():
        actual_value = actual.get(key)
        assertions.append(
            {
                "field": key,
                "expected": expected_value,
                "actual": actual_value,
                "passed": actual_value == expected_value,
            }
        )
    return {
        "case_id": case.id,
        "name": case.name,
        "category": case.category,
        "skill_id": case.skill_id,
        "suite_version": EVALUATION_SUITE_VERSION,
        "input": case.input,
        "passed": all(item["passed"] for item in assertions),
        "assertions": assertions,
        "actual": actual,
    }

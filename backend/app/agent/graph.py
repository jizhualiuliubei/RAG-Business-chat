"""企业合规执行中心的 LangGraph 状态机。"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.skills import get_skill, missing_fields, route_skill
from app.agent.execution_contract import assess_execution_risk, bind_execution_plan, execution_contract
from app.agent.policy_checks import enforce_policy_audit, require_policy_compliance
from app.agent.contracts import ExecutionPlan, audit_passed, validate_plan_tools


class AgentTaskState(TypedDict, total=False):
    task_id: int
    enterprise_id: int
    user_id: int
    goal: str
    skill_id: str
    skill_version: str
    kb_ids: list[int]
    request: dict[str, Any]
    seed_fields: dict[str, Any]
    missing_fields: list[str]
    input_confirmed: bool
    evidence: list[dict[str, Any]]
    plan: dict[str, Any]
    risk_policy: dict[str, Any]
    risk: dict[str, Any]
    audit: dict[str, Any]
    approval: dict[str, Any]
    result: dict[str, Any]
    artifact: str
    revision_count: int
    final_status: str
    error: str


class AgentGraphDependencies:
    """图节点的受控外部能力；生产实现和测试替身共享同一接口。"""

    def extract_fields(self, state: AgentTaskState) -> dict[str, Any]:
        raise NotImplementedError

    def retrieve_policy(self, state: AgentTaskState) -> list[dict[str, Any]]:
        raise NotImplementedError

    def build_plan(self, state: AgentTaskState) -> dict[str, Any]:
        raise NotImplementedError

    def audit_plan(self, state: AgentTaskState) -> dict[str, Any]:
        raise NotImplementedError

    def execute_tools(self, state: AgentTaskState) -> dict[str, Any]:
        raise NotImplementedError

    def build_artifact(self, state: AgentTaskState) -> str:
        raise NotImplementedError


def _has_verified_quotation(evidence: list[dict[str, Any]]) -> bool:
    return any(
        item.get("source_type") == "task_attachment"
        and "报价" in f"{item.get('source', '')}{item.get('text', '')}"
        for item in evidence
    )


def build_agent_graph(dependencies: AgentGraphDependencies, *, checkpointer=None, execution_guard=None, node_started=None):
    """构建可中断、可恢复的企业合规执行图。"""

    def load_context(state: AgentTaskState) -> dict:
        return {
            "request": dict(state.get("request") or {}),
            "evidence": list(state.get("evidence") or []),
            "plan": dict(state.get("plan") or {}),
            "audit": dict(state.get("audit") or {}),
            "approval": dict(state.get("approval") or {}),
            "result": dict(state.get("result") or {}),
            "revision_count": int(state.get("revision_count") or 0),
            "final_status": "",
            "error": "",
        }

    def select_skill(state: AgentTaskState) -> dict:
        skill_id = state.get("skill_id") or route_skill(state.get("goal", ""))
        skill = get_skill(skill_id)
        return {"skill_id": skill.id, "skill_version": skill.version}

    def extract_fields(state: AgentTaskState) -> dict:
        extracted = dependencies.extract_fields(state)
        return {"request": {**dict(extracted or {}), **dict(state.get("request") or {})}}

    def validate_fields(state: AgentTaskState) -> dict:
        request = get_skill(state["skill_id"]).input_model.model_validate(
            state.get("request") or {}
        ).model_dump(mode="json")
        if state["skill_id"] == "procurement":
            # 该字段只能由已解析任务附件推导，模型和客户端输入都不可信。
            request["quotation_attached"] = False
        return {
            "request": request,
            "missing_fields": missing_fields(state["skill_id"], request),
        }

    def route_after_validation(state: AgentTaskState) -> str:
        return "wait_for_input" if state.get("missing_fields") or not state.get("input_confirmed") else "retrieve_policy"

    def wait_for_input(state: AgentTaskState) -> dict:
        supplied = interrupt(
            {
                "type": "input_required",
                "task_id": state["task_id"],
                "skill_id": state["skill_id"],
                "missing_fields": list(state.get("missing_fields") or []),
                "confirmation_required": not bool(state.get("input_confirmed")),
                "input_schema": get_skill(state["skill_id"]).input_model.model_json_schema(),
            }
        )
        fields = dict((supplied or {}).get("fields") or {})
        return {"request": {**dict(state.get("request") or {}), **fields}, "input_confirmed": True,
                "revision_count": 0, "approval": {}}

    def retrieve_policy(state: AgentTaskState) -> dict:
        evidence = list(dependencies.retrieve_policy(state) or [])
        update: dict[str, Any] = {"evidence": evidence}
        if state["skill_id"] == "procurement":
            update["request"] = {
                **dict(state.get("request") or {}),
                "quotation_attached": _has_verified_quotation(evidence),
            }
        return update

    def build_plan(state: AgentTaskState) -> dict:
        produced = dependencies.build_plan(state)
        # 依赖可以返回裸计划，也可以返回 {"plan": ..., "gathered_evidence": [...]}：
        # 后者是模型自己检索到的证据增量，要并进任务证据供审计与档案使用。
        payload = produced.get("plan") if isinstance(produced, dict) and "plan" in produced else produced
        plan = ExecutionPlan.model_validate(payload or {})
        validate_plan_tools(state["skill_id"], plan)

        # 顺序很重要：**先把补检到的证据并进 state，再绑定计划**。
        # 反过来的话 bind_execution_plan 会拿旧证据算制度依据，模型自己检索到的
        # 条款进不了计划与执行契约 —— 计划里的依据和证据会对不上。
        gathered = produced.get("gathered_evidence") if isinstance(produced, dict) else None
        merged_evidence = list(state.get("evidence") or [])
        if gathered:
            seen = {(item.get("source"), item.get("text")) for item in merged_evidence}
            for item in gathered:
                key = (item.get("source"), item.get("text"))
                if key not in seen:
                    merged_evidence.append(item)
                    seen.add(key)

        bound_state = {**state, "evidence": merged_evidence}
        update: dict[str, Any] = {"plan": bind_execution_plan(bound_state, plan.model_dump(mode="json"))}
        if gathered:
            update["evidence"] = merged_evidence
        return update

    def deterministic_checks(state: AgentTaskState) -> dict:
        risk_payload = dict(state.get("request") or {})
        if state["skill_id"] == "procurement":
            # 报价材料状态只能由已解析附件推导，不能相信模型或客户端提交的布尔值。
            risk_payload["quotation_attached"] = _has_verified_quotation(
                list(state.get("evidence") or [])
            )
        risk = assess_execution_risk({**state, "request": risk_payload})
        return {"risk": risk.model_dump(mode="json")}

    def compliance_audit(state: AgentTaskState) -> dict:
        assessment = execution_contract(state)["policy_assessment"]
        return {"audit": enforce_policy_audit(state, assessment, dict(dependencies.audit_plan(state) or {}))}

    def route_after_audit(state: AgentTaskState) -> str:
        audit = state.get("audit") or {}
        if audit.get("policy_blocking_issues"):
            return "manual_review"
        if audit_passed(audit):
            return "approval_gate"
        if int(state.get("revision_count") or 0) < 2:
            return "revise_plan"
        return "manual_review"

    def manual_review(state: AgentTaskState) -> dict:
        return {"audit": {**state.get("audit", {}), "manual_review_required": True}}

    def revise_plan(state: AgentTaskState) -> dict:
        return {"revision_count": int(state.get("revision_count") or 0) + 1}

    def approval_gate_node(_state: AgentTaskState) -> dict:
        return {}

    def route_approval_gate(state: AgentTaskState) -> str:
        if (state.get("approval") or {}).get("decision") == "approved":
            return "execute_tools"
        if (state.get("approval") or {}).get("decision"):
            return "wait_for_approval"
        return "wait_for_approval" if (state.get("risk") or {}).get("requires_approval") else "execute_tools"

    def wait_for_approval(state: AgentTaskState) -> dict:
        decision = interrupt(
            {
                "type": "approval_required",
                "task_id": state["task_id"],
                "skill_id": state["skill_id"],
                "risk": state.get("risk") or {},
                "plan": state.get("plan") or {},
                "audit": state.get("audit") or {},
            }
        )
        return {"approval": dict(decision or {})}

    def route_after_approval(state: AgentTaskState) -> str:
        decision = (state.get("approval") or {}).get("decision")
        if decision == "approved":
            # 审计没过时人工批准无效（approve 接口已经拦了，这里是纵深防御）：
            # 不能让人一键绕过审计 —— 退回补充，让申请人补齐证据重新走一轮。
            return "execute_tools" if audit_passed(state.get("audit") or {}) else "wait_for_input"
        if decision == "changes_requested":
            return "wait_for_input"
        # 驳回与撤回由审批/撤回接口直接落到终态且不派发，正常走不到这里；
        # 真走到了也按终止处理，不能静默继续执行。
        return "mark_cancelled"

    def execute_tools(state: AgentTaskState) -> dict:
        require_policy_compliance(state, execution_contract(state)["policy_assessment"])
        if not audit_passed(state.get("audit") or {}):
            raise ValueError("内部登记方案复核未通过，请退回补充并重新审查，不能执行")
        return {"result": dict(dependencies.execute_tools(state) or {})}

    def verify_result(state: AgentTaskState) -> dict:
        result = state.get("result") or {}
        if not result.get("record_id") or result.get("status") != "completed":
            raise RuntimeError("Agent 工具执行结果未通过核验")
        return {}

    def build_artifact(state: AgentTaskState) -> dict:
        return {"artifact": str(dependencies.build_artifact(state) or "")}

    def finalize(_state: AgentTaskState) -> dict:
        return {"final_status": "completed"}

    def mark_cancelled(_state: AgentTaskState) -> dict:
        return {"final_status": "cancelled"}

    builder = StateGraph(AgentTaskState)
    # A lost lease must stop the next node, not just the renewal thread.
    if execution_guard is not None or node_started is not None:
        original_add_node = builder.add_node

        def add_guarded_node(name, action):
            def guarded(state):
                if execution_guard:
                    execution_guard()
                if node_started and name not in {"wait_for_input", "wait_for_approval"}:
                    node_started(name)
                result = action(state)
                if execution_guard:
                    execution_guard()
                return result
            return original_add_node(name, guarded)

        add_node = add_guarded_node
    else:
        add_node = builder.add_node
    add_node("load_context", load_context)
    add_node("route_skill", select_skill)
    add_node("extract_fields", extract_fields)
    add_node("validate_fields", validate_fields)
    add_node("wait_for_input", wait_for_input)
    add_node("retrieve_policy", retrieve_policy)
    add_node("build_plan", build_plan)
    add_node("deterministic_checks", deterministic_checks)
    add_node("compliance_audit", compliance_audit)
    add_node("revise_plan", revise_plan)
    add_node("manual_review", manual_review)
    add_node("approval_gate", approval_gate_node)
    add_node("wait_for_approval", wait_for_approval)
    add_node("execute_tools", execute_tools)
    add_node("verify_result", verify_result)
    add_node("build_artifact", build_artifact)
    add_node("finalize", finalize)
    add_node("mark_cancelled", mark_cancelled)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "route_skill")
    builder.add_edge("route_skill", "extract_fields")
    builder.add_edge("extract_fields", "validate_fields")
    builder.add_conditional_edges("validate_fields", route_after_validation)
    builder.add_edge("wait_for_input", "validate_fields")
    builder.add_edge("retrieve_policy", "build_plan")
    builder.add_edge("build_plan", "deterministic_checks")
    builder.add_edge("deterministic_checks", "compliance_audit")
    builder.add_conditional_edges("compliance_audit", route_after_audit)
    builder.add_edge("revise_plan", "build_plan")
    builder.add_edge("manual_review", "wait_for_approval")
    builder.add_conditional_edges("approval_gate", route_approval_gate)
    builder.add_conditional_edges("wait_for_approval", route_after_approval)
    builder.add_edge("execute_tools", "verify_result")
    builder.add_edge("verify_result", "build_artifact")
    builder.add_edge("build_artifact", "finalize")
    builder.add_edge("finalize", END)
    builder.add_edge("mark_cancelled", END)
    return builder.compile(checkpointer=checkpointer)

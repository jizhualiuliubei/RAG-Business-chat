"""企业合规执行中心生产依赖与执行档案。"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from langchain_core.callbacks import get_usage_metadata_callback

from app.agent.agents import BusinessExecutionAgent, ComplianceAuditAgent
from app.agent.contracts import ExecutionPlan, audit_passed, validate_plan_tools
from app.agent.graph import AgentGraphDependencies, _has_verified_quotation
from app.agent.policy_tool import make_policy_search_tool, search_policy
from app.agent.execution_contract import assess_execution_risk, bind_execution_plan, execution_contract, has_current_contract, REGISTRATION_BOUNDARY
from app.agent.permissions import can_review_enterprise
from app.agent.policy_checks import require_policy_compliance
from app.agent.skill_docs import load_skill_body
from app.agent.skills import get_skill
from app.core.llm import get_model, get_model_for_config
from app.models.agent import AgentApproval, AgentBusinessRecord, AgentTask, AgentTaskAttachment, AgentTaskStep
from app.models.user import User
from app.models.enterprise import Enterprise
from app.services import agent_task_service, enterprise_service


logger = logging.getLogger(__name__)


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def render_execution_artifact(state: dict[str, Any]) -> str:
    evidence_lines = []
    for index, item in enumerate(state.get("evidence") or [], start=1):
        location = " / ".join(
            str(part)
            for part in (item.get("source"), item.get("section"), item.get("clause_id"))
            if part
        )
        evidence_lines.append(f"{index}. {location or '未命名来源'}：{item.get('text', '')}")

    approval = state.get("approval") or {}
    approval_text = (
        f"- 决定：{approval.get('decision', '无需审批')}\n"
        f"- 意见：{approval.get('comment') or '无'}"
    )
    return "\n".join(
        [
            f"# 企业合规执行档案：任务 #{state.get('task_id')}",
            "",
            "## 任务目标",
            str(state.get("goal") or ""),
            "",
            "## 结构化申请",
            "```json",
            _pretty(state.get("request") or {}),
            "```",
            "",
            "## 制度证据",
            *(evidence_lines or ["未检索到可引用制度证据。"]),
            "",
            "## 风险与审计",
            "```json",
            _pretty({"risk": state.get("risk") or {}, "audit": state.get("audit") or {}}),
            "```",
            "",
            "## 人工审批",
            approval_text,
            "",
            "## 执行结果",
            REGISTRATION_BOUNDARY,
            "登记完成不代表下列制度要求已完成；各项原文仍需核对适用条件并由业务责任人处理。",
            *(f"- {item['source']}#{item['clause_id']}：{item['quote']}（尚未核验）"
              for item in (state.get("result") or {}).get("policy_requirements", [])),
            "```json",
            _pretty(state.get("result") or {}),
            "```",
            "",
        ]
    )


def _model_for_enterprise(db, enterprise_id: int):
    config = enterprise_service.require_enterprise_deepseek_key(db, enterprise_id)
    if config.get("api_key"):
        return get_model_for_config(
            config["api_key"],
            config.get("base_url"),
            config.get("model"),
            temperature=0,
        )
    return get_model(temperature=0)


class ProductionAgentDependencies(AgentGraphDependencies):
    """生产图依赖：模型负责判断，数据库执行器负责受控副作用。"""

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.execution_guard = None
        self.event_publisher = None
        self.token_usage = {}

    def _tracked_call(self, function):
        with get_usage_metadata_callback() as usage:
            try:
                return function()
            finally:
                # 累加而不是覆盖：同一个节点里可能发起多次模型调用（例如先自主
                # 检索再生成计划），覆盖会只留下最后一次的用量。
                for key in ("input_tokens", "output_tokens"):
                    self.token_usage[key] = self.token_usage.get(key, 0) + sum(
                        int(item.get(key) or 0) for item in usage.usage_metadata.values()
                    )

    def consume_token_usage(self):
        result, self.token_usage = self.token_usage, {}
        return result

    def _agents(self, enterprise_id: int):
        with self.session_factory() as db:
            model = _model_for_enterprise(db, enterprise_id)
        return BusinessExecutionAgent(model), ComplianceAuditAgent(model)

    def extract_fields(self, state):
        business, _ = self._agents(state["enterprise_id"])
        return self._tracked_call(lambda: business.extract_fields(state))

    def retrieve_policy(self, state):
        query = f"{state.get('goal', '')}\n结构化申请：{_pretty(state.get('request') or {})}"
        with self.session_factory() as db:
            evidence = search_policy(
                db,
                enterprise_id=state["enterprise_id"],
                kb_ids=list(state.get("kb_ids") or []),
                query=query,
            )
            if state["skill_id"] in {"procurement", "access_request"}:
                # Retrieve approval controls separately so purpose-related hits cannot crowd them out.
                controls = search_policy(db, enterprise_id=state["enterprise_id"],
                    kb_ids=list(state.get("kb_ids") or []),
                    query=("采购审批 金额分级 直属负责人 部门负责人 采购经理 总经理；涉及客户数据供应商信息安全评估"
                           if state["skill_id"] == "procurement" else "账号权限申请有效期 审批 最小权限 数据分级公开内部秘密机密"))
                seen = {(item.get("source"), item.get("text")) for item in evidence}
                for item in controls:
                    key = (item.get("source"), item.get("text"))
                    if key not in seen:
                        evidence.append(item)
                        seen.add(key)
            attachments = list(
                db.execute(
                    select(AgentTaskAttachment).where(
                        AgentTaskAttachment.task_id == state["task_id"],
                        AgentTaskAttachment.enterprise_id == state["enterprise_id"],
                        AgentTaskAttachment.status == "done",
                    )
                ).scalars().all()
            )
            evidence.extend(
                {
                    "source": item.filename,
                    "source_type": "task_attachment",
                    "section": "任务附件",
                    "clause_id": "",
                    "text": item.summary,
                    "score": 1.0,
                    "attachment_id": item.id,
                }
                for item in attachments
            )
            return evidence

    def build_plan(self, state):
        business, _ = self._agents(state["enterprise_id"])
        # 给业务 Agent 挂上只读制度检索工具：模型自己决定查哪些制度角度。
        # 企业/知识库上下文由服务端注入闭包，模型无法表达，也拿不到写工具。
        gathered: list[dict] = []
        publisher = self.event_publisher

        def on_search(call):
            # 两件事都做：事件给实时推送，步骤行给"刷新后仍可追溯"。
            # 只发事件的话，模型自己查了什么在界面上留不下来。
            query_text = str(call.get("query") or "")
            failed = call.get("status") == "failed"
            if publisher:
                publisher("tool_completed", {
                    "tool": "policy_search",
                    "query": query_text[:200],
                    "hits": int(call.get("hits") or 0),
                    "status": call.get("status") or "completed",
                    "duration_ms": int(call.get("duration_ms") or 0),
                })
            try:
                with self.session_factory() as db:
                    # 同一次修订后的再次检索要能区分开，所以按已有条数递增，
                    # 不是每条都写 attempt=1。
                    prior = db.execute(
                        select(func.count()).select_from(AgentTaskStep).where(
                            AgentTaskStep.task_id == state["task_id"],
                            AgentTaskStep.node_name == "policy_search",
                        )
                    ).scalar_one()
                    db.add(AgentTaskStep(
                        enterprise_id=state["enterprise_id"],
                        task_id=state["task_id"],
                        node_name="policy_search",
                        status="failed" if failed else "completed",
                        attempt=int(prior or 0) + 1,
                        duration_ms=max(0, int(call.get("duration_ms") or 0)),
                        input_summary=f"Skill={state.get('skill_id')}",
                        output_summary=(
                            f"检索「{query_text[:120]}」失败（{call.get('error') or '未知错误'}）"
                            if failed
                            else f"检索「{query_text[:120]}」，命中 {call.get('hits')} 条"
                        ),
                        error_message=str(call.get("error") or "")[:200],
                    ))
                    db.commit()
            except Exception as exc:
                # 记录失败不能影响检索本身 —— 那次检索已经成功了
                logger.warning("记录自主检索步骤失败：%s", type(exc).__name__)

        # Skill 正文没读到就留一条诊断 —— 否则"这次有没有按 Skill 规范跑"无从判断
        if not load_skill_body(state["skill_id"]):
            self._record_skill_doc_missing(state)

        # 自主检索次数：企业可以调小，但夹在 Skill 的代码级上限之内 ——
        # 配置只能收紧安全边界，不能放宽。这是 config_json 唯一被运行时消费的项。
        with self.session_factory() as db:
            runtime_config = agent_task_service.skill_runtime_config(
                db, state["enterprise_id"], state["skill_id"]
            )
        max_calls = int(
            runtime_config.get("max_policy_searches")
            or get_skill(state["skill_id"]).max_policy_searches
        )

        business.policy_search_tool = make_policy_search_tool(
            session_factory=self.session_factory,
            enterprise_id=state["enterprise_id"],
            kb_ids=list(state.get("kb_ids") or []),
            collected=gathered,
            max_calls=max_calls,
            on_search=on_search,
        )
        business.gathered_evidence = gathered
        try:
            plan = self._tracked_call(lambda: business.build_plan(state))
        finally:
            # 用 finally：自主检索失败后如果计划生成又报错，降级轨迹不能跟着丢。
            # 这条记录本身就是给"任务没跑成"时看的，挂在成功路径上等于白记。
            if business.retrieval_failed:
                self._record_retrieval_degraded(state, gathered)
        # 只回传模型**额外**检索到的证据增量，基线证据已在 state 里，由图上合并去重。
        return {"plan": plan, "gathered_evidence": list(gathered)}

    def _record_retrieval_degraded(self, state, gathered) -> None:
        """把"自主检索失败、退回基线证据"写进运行轨迹。

        任务照样能完成，但完成不等于自主检索成功过 —— 这条记录用来区分
        「检索成功」「模型没检索」「检索失败后降级」三种情况。

        失败前可能已经查到一部分：那些结果已经并入计划依据，如实说明保留了多少，
        否则用户会把模型自检到的条款误当成代码预设检索的结果。
        """
        kept = len(list(gathered or []))
        if kept:
            summary = (
                f"自主检索中途失败，但失败前取得的 {kept} 条结果已保留并计入计划依据；"
                "其余证据来自代码预设的检索"
            )
        else:
            summary = "自主检索失败，未取得任何自主检索证据；本任务的证据来自代码预设的检索"
        self._record_diagnostic_step(state, node_name="policy_search_degraded", summary=summary)

    def _record_skill_doc_missing(self, state) -> None:
        """Skill 正文没加载到 —— 同样要留痕，否则"这次有没有按 Skill 规范跑"无从判断。"""
        self._record_diagnostic_step(
            state,
            node_name="skill_doc_missing",
            summary=f"未加载到 {state.get('skill_id')} 的 SKILL.md，本次任务没有该 Skill 的额外操作规范",
        )

    def _record_diagnostic_step(self, state, *, node_name: str, summary: str) -> None:
        try:
            with self.session_factory() as db:
                db.add(AgentTaskStep(
                    enterprise_id=state["enterprise_id"],
                    task_id=state["task_id"],
                    node_name=node_name,
                    status="completed",
                    attempt=1,
                    input_summary=f"Skill={state.get('skill_id')}",
                    output_summary=summary,
                ))
                db.commit()
        except Exception as exc:
            logger.warning("记录诊断步骤失败（%s）：%s", node_name, type(exc).__name__)

    def audit_plan(self, state):
        _, auditor = self._agents(state["enterprise_id"])
        return self._tracked_call(lambda: auditor.audit(state))

    def execute_tools(self, state):
        if self.execution_guard:
            self.execution_guard()
        plan = ExecutionPlan.model_validate(state.get("plan") or {})
        validate_plan_tools(state["skill_id"], plan)
        with self.session_factory() as db:
            policy = agent_task_service.get_risk_policy(db, state["enterprise_id"])
            # Serialize cancellation, approval and the irreversible write.
            task = db.execute(select(AgentTask).where(AgentTask.id == state["task_id"])
                              .with_for_update()).scalar_one_or_none()
            if task is None or task.enterprise_id != state["enterprise_id"]:
                raise ValueError("Agent 任务不存在或企业边界不匹配")
            if task.status in {"cancelled", "cancel_requested"}:
                raise ValueError("任务已取消或正在中断，不能执行写入")
            if task.skill_id != state["skill_id"]:
                raise ValueError("执行计划与任务 Skill 不匹配")
            stored_plan = json.loads(task.plan_json or "{}")
            if stored_plan.get("actions") and not has_current_contract(stored_plan):
                existing = db.execute(select(AgentBusinessRecord.id).where(
                    AgentBusinessRecord.task_id == task.id, AgentBusinessRecord.enterprise_id == task.enterprise_id,
                ).limit(1)).first()
                if existing is None:
                    raise ValueError("历史计划未绑定真实执行契约，禁止新增写入；请重新提交申请并审查")
            stored_audit = json.loads(task.audit_json or "{}")
            if not audit_passed(state.get("audit") or {}) or (stored_audit and not audit_passed(stored_audit)):
                raise ValueError("内部登记方案复核未通过，禁止执行写入")
            enterprise = db.get(Enterprise, task.enterprise_id)
            applicant = db.get(User, task.user_id)
            if (enterprise is None or enterprise.status != "active" or applicant is None
                    or applicant.status != "active" or (applicant.enterprise_id != task.enterprise_id
                    and applicant.role not in {"admin", "system_admin"})):
                raise ValueError("企业或申请账号已停用，不能继续执行")

            request = dict(state.get("request") or {})
            if task.skill_id == "procurement":
                attachments = db.execute(select(AgentTaskAttachment).where(
                    AgentTaskAttachment.task_id == task.id,
                    AgentTaskAttachment.enterprise_id == task.enterprise_id,
                    AgentTaskAttachment.status == "done",
                )).scalars().all()
                request["quotation_attached"] = _has_verified_quotation([
                    {"source_type": "task_attachment", "source": item.filename, "text": item.summary}
                    for item in attachments
                ])
            payload = {
                **request,
                "task_id": task.id,
                "enterprise_id": task.enterprise_id,
                "applicant_id": applicant.id,
                "applicant_role": applicant.role,
                "skill_version": task.skill_version,
                "policy_basis": plan.policy_basis,
                "risk": state.get("risk") or {},
            }
            record_type = "procurement" if task.skill_id == "procurement" else "access_grant"
            execution_state = {**state, "request": request, "risk_policy": agent_task_service.risk_policy_rules(policy)}
            deterministic_risk = assess_execution_risk(execution_state)
            contract = execution_contract(execution_state)
            require_policy_compliance(execution_state, contract["policy_assessment"])
            payload["policy_basis"] = bind_execution_plan(execution_state, plan.model_dump(mode="json"))["policy_basis"]
            approval = None
            if deterministic_risk.requires_approval:
                approval = db.execute(
                    select(AgentApproval)
                    .where(AgentApproval.task_id == task.id, AgentApproval.enterprise_id == task.enterprise_id)
                    .order_by(AgentApproval.id.desc())
                ).scalars().first()
                reviewer = db.get(User, approval.decided_by) if approval and approval.decided_by else None
                if (approval is None or approval.status != "approved" or approval.decided_at is None
                        or not can_review_enterprise(db, reviewer, task.enterprise_id)
                        or reviewer.id == task.user_id):
                    action_name = "权限授予" if record_type == "access_grant" else "采购执行"
                    raise ValueError(f"{action_name}缺少企业管理员审批")
                payload.update(
                    {
                        "approved_by": approval.decided_by,
                        "approval_id": approval.id,
                        "approver_role": reviewer.role,
                        "approved_at": approval.decided_at.isoformat(),
                    }
                )

            if record_type == "access_grant":
                if approval is None:  # 权限 Skill 的确定性规则始终要求审批。
                    raise ValueError("权限授予缺少企业管理员审批")
                effective_at = approval.decided_at
                payload.update(
                    {
                        "effective_at": effective_at.isoformat(),
                        "expires_at": (
                            effective_at + timedelta(days=int(request.get("duration_days") or 0))
                        ).isoformat(),
                    }
                )

            payload["risk"] = deterministic_risk.model_dump(mode="json")
            payload.update({"execution_scope": "internal_registration", "business_execution_status": "not_executed",
                            "registration_controls": contract["registration_controls"], "review_notes": plan.review_notes,
                            "policy_requirements": contract["policy_requirements"],
                            "policy_assessment": contract["policy_assessment"], "execution_boundary": REGISTRATION_BOUNDARY})
            if self.execution_guard:
                self.execution_guard()
            record = agent_task_service.create_business_record(db, task, record_type, payload)
            return {
                "record_type": record.record_type,
                "record_id": record.id,
                "status": record.status,
                "execution_scope": "internal_registration",
                "business_execution_status": "not_executed",
                "policy_requirements": contract["policy_requirements"],
                "execution_boundary": REGISTRATION_BOUNDARY,
            }

    def build_artifact(self, state):
        return render_execution_artifact(state)

"""业务执行 Agent 与独立合规审计 Agent。"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from app.agent.contracts import AuditVerdict, ExecutionPlan, validate_plan_tools
from app.agent.skill_docs import skill_prompt_block
from app.agent.skills import get_skill
from app.agent.execution_contract import assess_execution_risk, bind_execution_plan, execution_contract
from app.agent.policy_checks import enforce_policy_audit


logger = logging.getLogger(__name__)

# 自主检索循环的模型回合上限。create_agent 默认递归上限是 10000，对本场景
# 等于没有限制 —— 工具次数虽有上限，但模型仍可以反复空转。12 足够
# 3 次检索 ×（发起 + 收结果）+ 余量；撞上限会被下面的 except 兜住、记为降级。
AGENT_MAX_ROUNDS = 12


def _structured_call(model, schema, messages, stage):
    runnable = model.with_structured_output(schema)
    for attempt in range(2):
        try:
            result = runnable.invoke(messages)
            if result is None:
                raise OutputParserException("模型没有返回结构化对象")
            return schema.model_validate(result)
        except (OutputParserException, ValidationError) as exc:
            # Do not log model responses: they may contain application materials.
            logger.warning("Agent structured output: stage=%s attempt=%s error_type=%s", stage, attempt + 1, type(exc).__name__)
            if attempt == 1:
                raise ValueError(f"{stage}结构化输出失败：已尝试 2 次，未得到有效结果，请稍后从安全节点重试") from exc
            messages = [*messages, ("human", "上一轮结构化输出无效，请严格按指定 Schema 返回对象；不得改变申请事实或审批约束。")]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _merge_evidence(baseline: Any, gathered: Any) -> list[dict]:
    """合并基线检索与模型自主检索到的证据，按 (来源, 正文) 去重。"""
    merged: list[dict] = []
    seen: set[tuple] = set()
    for item in [*(baseline or []), *(gathered or [])]:
        if not isinstance(item, dict):
            continue
        key = (item.get("source"), item.get("text"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


class BusinessExecutionAgent:
    """只做字段抽取、制度检索与计划生成，不持有任何写工具。"""

    def __init__(self, model, policy_search_tool=None, gathered_evidence: list | None = None):
        self.model = model
        self.policy_search_tool = policy_search_tool
        # 与 policy_tool 共用同一个列表：模型每次检索到的片段都落在这里。
        self.gathered_evidence = gathered_evidence if gathered_evidence is not None else []
        # 最近一次计划生成实际用到的证据（基线 + 模型自主检索，已去重）。
        self.last_evidence: list[dict] = []
        # 自主检索是否失败并退回了基线证据。任务最终完成**不能**证明自主检索
        # 成功跑过 —— 这个标记让"降级"在轨迹里可见，而不是只留一行日志。
        self.retrieval_failed = False

    def gather_evidence(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """让模型自己决定检索哪些制度角度。

        模型只拿到 policy_search 这一个**只读**工具、没有任何写工具，由它自己
        决定检索什么、检索几次、什么时候认为够了。检索结果落在
        self.gathered_evidence，由调用方并入任务证据，供审计与执行档案使用。

        没有工具（测试替身）或模型不支持工具调用时返回空列表，调用方沿用基线
        检索结果兜底 —— 这条退化路径不影响主流程。
        """
        if self.policy_search_tool is None or not hasattr(self.model, "bind_tools"):
            return []
        from langchain.agents import create_agent

        agent = create_agent(
            model=self.model,
            tools=[self.policy_search_tool],
            system_prompt=(
                "你是企业合规执行中心的制度检索助手。你的任务是把这份申请需要依据的"
                "制度条款查清楚：用制度里可能出现的正式表述去检索，不要用口语说法。"
                "可以换不同角度多查几次，但不要超过工具允许的次数。"
                "查完后简短说明你查了哪些角度、还缺什么。"
                # 「该查哪些角度」正是 SKILL.md 里写的东西 —— 它必须进这个循环，
                # 否则改 Skill 里"采购应检索哪些维度"不会影响自主检索。
                f"{skill_prompt_block(state['skill_id'])}"
            ),
        )
        try:
            agent.invoke(
                {"messages": [(
                    "human",
                    f"申请目标：{state.get('goal', '')}\n"
                    f"结构化申请：{_json(state.get('request') or {})}",
                )]},
                # 硬预算：工具次数有上限，但模型回合也要封顶，否则它可以一直空转。
                # create_agent 默认递归上限是 10000，对本场景等于没有限制。
                config={"recursion_limit": AGENT_MAX_ROUNDS},
            )
        except Exception as exc:
            # 自主检索失败不能拖垮整个任务：退回基线证据继续走。
            # 同时打上标记，让调用方把这次降级写进运行轨迹。
            self.retrieval_failed = True
            logger.warning("Agent 自主检索失败，退回基线证据：%s", type(exc).__name__)
        return list(self.gathered_evidence)

    def extract_fields(self, state: dict[str, Any]) -> dict[str, Any]:
        schema = get_skill(state["skill_id"]).input_model
        skill_guidance = ""
        if state["skill_id"] == "access_request":
            skill_guidance = (
                "权限申请字段边界：target_system 是承载资源的系统或平台，resource 是该系统内的具体对象；"
                "例如‘生产数据库订单库’必须拆为目标系统=生产数据库、目标资源=订单库，不得把二者合并。"
                "用户没有提供期限时，duration_days 必须保持空值。"
            )
        result = _structured_call(self.model, schema,
            [
                (
                    "system",
                    "你是企业合规执行中心的业务执行 Agent。只从用户原话中抽取申请字段，"
                    "不得猜测金额、供应商、权限级别、数据密级或期限；缺失字段保持空值。"
                    "你不能执行数据库写入，也不能决定是否免审批。"
                    f"{skill_guidance}"
                    # skills/<id>/SKILL.md 的正文在这里进入模型上下文 ——
                    # 改 Skill 文件即改模型收到的规范，不是只改文档。
                    f"{skill_prompt_block(state['skill_id'])}",
                ),
                (
                    "human",
                    f"Skill：{state['skill_id']}\n用户目标：{state.get('goal', '')}\n"
                    f"已有字段：{_json(state.get('request') or {})}",
                ),
            ], "字段抽取"
        )
        return result.model_dump(mode="json", exclude_none=True)

    def build_plan(self, state: dict[str, Any]) -> dict[str, Any]:
        skill = get_skill(state["skill_id"])
        # 先让模型自己检索它认为需要的制度角度（无工具能力时是空操作），
        # 再把它检索到的证据与基线证据合并，一起交给计划生成。
        evidence = _merge_evidence(state.get("evidence"), self.gather_evidence(state))
        self.last_evidence = evidence
        payload = dict(state.get("request") or {})
        if skill.id == "procurement":
            payload["quotation_attached"] = any(
                item.get("source_type") == "task_attachment" and "报价" in f"{item.get('source', '')}{item.get('text', '')}"
                for item in evidence
            )
        constrained_state = {**state, "request": payload, "evidence": evidence}
        risk = assess_execution_risk(constrained_state).model_dump(mode="json")
        result = _structured_call(self.model, ExecutionPlan,
            [
                (
                    "system",
                    "你是企业合规执行中心的业务执行 Agent。请基于结构化申请和制度证据生成"
                    "可执行计划。只能选择给定写工具，不得直接声称已经写库、授权或审批。"
                    "制度没有给出的事实必须明确保留边界；审计退回意见必须逐项修订。"
                    "服务端确定性风险结果是硬约束；需要人工审批时不得写无需审批或 approval_required=false。"
                    "说明使用中文标题，不要将代码字段名写入用户说明；只能创建内部申请记录，不得虚构外部报价、付款或系统授权。"
                    "服务端会重新绑定动作、参数、摘要和执行边界。场景解释和逐项修订回复必须放在 review_notes 中，"
                    "每项说明实际采取的控制或尚未核验的边界，禁止承诺系统未实现的审批、评估或权限回收能力。"
                    "面向普通员工写说明：每项只解释一个问题，采用‘简短标题：具体说明’。"
                    "先说明本次申请如何处理，再说明需要谁处理哪些事项。使用短句和自然中文，"
                    "不要堆叠‘确定性约束、门控、闭环、内部登记方案’等术语；不反复复制执行边界。"
                    "审计要求修订时逐项回应，不把问题、解释和建议混成一段，也不重复已有申请字段。"
                    "申请参数不能由你修改；需要改期限或密级时，只能要求申请人修改后重提，禁止声称已修改。"
                    "企业期限配置仅是风险触发门槛，不是制度期限上限；密级配置仅是风险触发门槛，不是资源的实际密级。"
                    "计划启用不等于已启用，不得虚构已完成多因素认证。"
                    "优先按服务端制度核对结果说明金额对应审批角色，不合并其他金额区间的审批人。"
                    f"{skill_prompt_block(skill.id)}",
                ),
                (
                    "human",
                    "\n".join(
                        [
                            f"Skill：{skill.id}",
                            f"允许写工具：{_json(skill.allowed_write_tools)}",
                            f"申请数据：{_json(state.get('request') or {})}",
                            f"服务端企业风险策略：{_json(state.get('risk_policy') or {})}",
                            f"确定性风险结果：{_json(risk)}",
                            f"真实执行契约：{_json(execution_contract(constrained_state))}",
                            f"制度证据：{_json(evidence)}",
                            f"上一轮审计：{_json(state.get('audit') or {})}",
                            f"修订次数：{int(state.get('revision_count') or 0)}",
                        ]
                    ),
                ),
            ], "计划生成"
        )
        validate_plan_tools(skill.id, result)
        # 注意：这里**不**校验 result.policy_basis。模型写的制度依据会被
        # bind_execution_plan 整体丢弃、改用从检索证据解析出的条款重建，
        # 所以模型根本影响不到最终依据 —— 那是比校验更强的保证。
        return bind_execution_plan(constrained_state, result.model_dump(mode="json"))


class ComplianceAuditAgent:
    """独立复核计划；没有写工具，不能覆盖确定性风险结果。"""

    def __init__(self, model):
        self.model = model

    def audit(self, state: dict[str, Any]) -> dict[str, Any]:
        contract = execution_contract(state)
        audited_plan = {key: value for key, value in (state.get("plan") or {}).items()
                        if key != "model_review_notes"}
        result = _structured_call(self.model, AuditVerdict,
            [
                (
                    "system",
                    "你是独立的合规审计 Agent，没有任何写工具。检查计划是否被证据支持、"
                    "动作是否越权、是否满足最小权限和留痕要求。确定性风险结果是硬约束，"
                    "你不得降低风险等级或取消系统要求的人工审批；证据不足时给出具体修订项。"
                    "required_changes 只列执行前必须解决的阻断项，非阻断建议放在 reasons。"
                    "required_changes 非空时 approved 必须为 false；approved=true 时必须为空。"
                    "服务端企业风险策略给出的数值门槛和审批条件已经确定，不得将其视为未知。"
                    "企业配置不是制度原文，金额低于配置门槛不等于制度免审批。"
                    "复核对象仅为内部登记方案；不得将登记批准视为制度审批链、安全评估、采购或实际系统授权已完成。"
                    "不得因计划写了待办就声称待办会自动执行。未实现或未核验的制度检查必须保留为未核验。"
                    "理由和修订意见使用清晰中文，不输出代码字段名；Schema 非必填字段缺失不能单独成为阻断项。"
                    "这是审批前对内部登记方案的检查，不是实际采购或权限授予的放行审查。"
                    "计划明确先审批后登记时，不得因管理员尚未批准、真实审批人和时间尚未产生而否决。"
                    "服务端登记控制会在写入时绑定审批身份和时间，请结合 review_notes 检查修订是否已解释清楚。"
                    "申请人声明的数据密级、用途和期限只能作为登记字段，不要求其等同已完成数据所有者认证；"
                    "缺少资源专属条款时应保留人工业务核验边界，不能宣称其已获授权，也不能仅因此否决安全的内部登记。"
                    "候选制度须核对触发条件：管理员账号、供应商远程生产访问、客户数据、云服务等条款不能自动套到普通只读或办公采购。"
                    "真正存在越权动作、虚构已完成审批、参数冲突、取消审批或未保留业务核验边界时，必须拒绝并给出阻断项。"
                    "面向普通员工写意见，每项只解释一个问题，采用‘简短标题：具体说明’。"
                    "先说明事实，再说明影响；阻断项最后说明应补充或修改什么，不编造处理人或操作流程。"
                    "普通建议用‘建议’开头，尚未核验的事项用‘尚待核验’说明，不把它们写成已完成的检查。"
                    "使用短句，每项一般为两到三句；重要条件、金额、期限和条款号必须完整保留。"
                    "不要重复同一个审批或业务边界，不在多项里复制整份申请，不写长段术语或代码字段名。"
                    f"{skill_prompt_block(str(state.get('skill_id') or ''))}",
                ),
                (
                    "human",
                    "\n".join(
                        [
                            f"Skill：{state.get('skill_id')}",
                            f"申请数据：{_json(state.get('request') or {})}",
                            f"制度证据：{_json(state.get('evidence') or [])}",
                            f"执行计划：{_json(audited_plan)}",
                            f"确定性风险结果：{_json(state.get('risk') or {})}",
                            f"服务端企业风险策略：{_json(state.get('risk_policy') or {})}",
                            f"真实执行契约：{_json(contract)}",
                            "语义边界：企业期限配置仅是风险触发门槛，不是制度期限上限；"
                            "密级配置仅是风险触发门槛，不是资源的实际密级。"
                            "计划启用不等于已启用。不得要求模型修改申请字段，也不得声称字段已被修改。"
                            "请按制度核对结果识别明确冲突，超出制度上限时要求申请人修改后重提。"
                            "不能因为期限超过风险门槛但仍符合制度就要求强制缩短；不能要求资源密级等于配置门槛。",
                        ]
                    ),
                ),
            ], "合规复核"
        )
        return enforce_policy_audit(state, contract["policy_assessment"], result.model_dump(mode="json"))

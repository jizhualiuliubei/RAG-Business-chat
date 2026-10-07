"""服务端执行边界：制度证据不是已完成的审批或评估。"""

import re

from app.agent.skills import assess_risk
from app.agent.policy_checks import assess_policy, registration_notes


REGISTRATION_BOUNDARY = (
    "本系统仅完成内部申请登记，不执行实际采购、付款、供应商安全评估或外部系统授权。"
    "企业管理员批准的是内部登记方案，不替代制度要求的直属负责人等审批和业务检查。"
)

# 条款号形如 HR-01-001。这里不能用 \b 作边界：Python 里中文字符属于 \w，
# 条款号紧邻中文（「见HR-01-001规定」）时两侧 \b 都不成立，会整体失配，
# 导致执行档案里的条款号变成空字符串。改用「前后不是字母数字」的 lookaround。
_CLAUSE_BODY = r"[A-Z]{2,8}-\d{2,3}-\d{3}"
_CLAUSE_PATTERN = rf"(?<![A-Za-z0-9]){_CLAUSE_BODY}(?![A-Za-z0-9])"


def has_current_contract(plan):
    contract = plan.get("execution_contract") or {}
    return (contract.get("version") == 1 and contract.get("scope") == "internal_registration"
            and contract.get("business_execution_status") == "not_executed")


def policy_requirements(state):
    """保留原文供人工核对，不用关键字推断金额适用区间或审批已完成。"""
    requirements, seen = [], set()
    for item in state.get("evidence") or []:
        if item.get("source_type") == "task_attachment":
            continue
        text = str(item.get("text") or "").strip()
        source = str(item.get("source") or "").strip()
        if not text or not source:
            continue
        # Table context may carry a previous heading; quote the actual row, not that heading.
        body = text.split("表格行：", 1)[-1] if "表格行：" in text else text.split("条款：", 1)[-1]
        if "条款内容：" in body:
            body = body.split("条款内容：", 1)[1].split("；责任部门：", 1)[0]
        quote = body.strip()
        if not re.search(r"审批|评估|审查|不得|须|应|需|至少|不低于|最小权限|有效期|最长", quote):
            continue
        match = re.search(_CLAUSE_PATTERN, quote)
        if not match:
            match = re.search(rf"条款编号[：:]\s*({_CLAUSE_BODY})", text)
            if match:
                clause_id = match.group(1)
            else:
                source_match = re.search(rf">\s*({_CLAUSE_BODY})(?![A-Za-z0-9])", text)
                clause_id = source_match.group(1) if source_match else str(item.get("clause_id") or "")
        else:
            clause_id = match.group()
        normalized_quote = re.sub(r"\s+", "", re.sub(r"^[A-Z]{2,8}-\d{2,3}-\d{3}\s*", "", quote))
        key = (source, clause_id, normalized_quote)
        if key in seen:
            continue
        seen.add(key)
        requirements.append({
            "source": source, "clause_id": clause_id, "quote": quote,
            "status": "unverified", "applicability": "候选制度原文，需按适用对象和触发条件核对，不能默认全部适用",
        })
    return requirements


def assess_execution_risk(state):
    risk = assess_risk(state["skill_id"], state.get("request") or {}, state.get("risk_policy") or {})
    if state["skill_id"] == "procurement":
        requirements = policy_requirements(state)
        # This is a conservative registration gate, not an automatic policy-role mapper.
        policy_approval = any("审批" in item["quote"] for item in requirements)
        if policy_approval or not requirements:
            risk.requires_approval = True
            if risk.level == "low":
                risk.level = "medium"
            risk.reasons.append("制度存在审批要求，内部登记需人工复核；金额门槛不能作为制度免批依据"
                                if policy_approval else "未找到可核对的制度要求，禁止自动登记")
    return risk


def execution_contract(state):
    risk = assess_execution_risk(state)
    requirements = policy_requirements(state)
    return {
        "version": 1, "scope": "internal_registration",
        "requires_approval": risk.requires_approval,
        "sequence": ["制度与风险检查", "内部登记审批" if risk.requires_approval else "内部登记门控", "写入内部记录", "核验与生成档案"],
        "record_status": "completed", "business_execution_status": "not_executed",
        "boundary": REGISTRATION_BOUNDARY,
        "policy_requirements": requirements,
        "policy_assessment": assess_policy(state, requirements),
        "configuration_requirements": list(risk.reasons),
        "registration_controls": {
            "identity_source": "企业、申请人及角色由服务端绑定，模型不能覆盖",
            "approval_sequence": ("先独立复核，再由非申请人的本企业管理员决定，批准后才写入内部记录"
                                  if risk.requires_approval else "独立复核和规则检查通过后写入内部记录，不虚构不存在的人工审批"),
            "approval_trace": "需要审批时，写入记录真实审批人、角色、决定时间、审批编号与任务编号；待审批阶段不虚构这些值",
            "request_binding": dict(state.get("request") or {}),
            "declaration_boundary": "申请字段是申请人声明，不等于已验证的数据分级、预算有效性或供应商资质",
            "policy_boundary": "制度审批和业务检查仍为未核验；内部记录保留候选制度原文，不以内部审批替代",
            "permission_expiry": ("按批准时间和申请天数计算内部登记期限，不会自动回收外部系统权限"
                                  if state["skill_id"] == "access_request" else "采购登记不涉及系统权限生效或回收"),
        },
    }


def bind_execution_plan(state, plan):
    """模型选择动作；真实参数、结果与制度引用由服务端绑定。"""
    contract = execution_contract(state)
    approval = "方案复核和企业管理员审批通过后" if contract["requires_approval"] else "规则检查和方案复核通过后"
    noun = "采购申请" if state["skill_id"] == "procurement" else "权限申请"
    summary = f"{approval}，系统保存本次{noun}，核对保存结果，并生成可下载的执行档案。"
    return {
        **plan,
        "model_review_notes": list(plan.get("model_review_notes", plan.get("review_notes", []))),
        "review_notes": registration_notes(state, contract["policy_assessment"]),
        "summary": summary,
        "actions": [{"action_type": action["action_type"], "arguments": dict(state.get("request") or {})}
                    for action in plan["actions"]],
        "policy_basis": [f"{item['source']}#{item['clause_id']}：{item['quote']}" for item in contract["policy_requirements"]],
        "expected_result": f"生成一条内部{noun}记录和一份执行档案；{'实际采购' if state['skill_id'] == 'procurement' else '实际授权'}仍需按制度另行办理。",
        "execution_contract": contract,
    }

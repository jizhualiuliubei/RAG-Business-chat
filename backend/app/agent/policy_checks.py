"""核对可明确解析的制度条件；不把企业风险配置解释成制度原文。"""

from decimal import Decimal
import re


_MONEY = r"\d+(?:\.\d+)?[万亿]?"
_RANGE = re.compile(rf"预算内({_MONEY})至({_MONEY})元采购需([^。；]+?)审批")
_BELOW = re.compile(rf"预算内({_MONEY})元以下采购需([^。；]+?)审批")
_ABOVE = re.compile(rf"超过({_MONEY})元采购需([^。；]+?)审批")
_DURATION = re.compile(r"(?:管理员账号必须启用多因素认证[，。])?权限申请有效期最长(\d+)天")


def _money(value, inherited_unit=""):
    unit = value[-1] if value[-1] in "万亿" else inherited_unit
    number = value[:-1] if value[-1] in "万亿" else value
    return Decimal(number) * {"": 1, "万": 10000, "亿": 100000000}[unit]


def assess_policy(state, requirements):
    """仅支持明确金额区间和期限句式，其他原文仍需人工核对。"""
    request, policy = state.get("request") or {}, state.get("risk_policy") or {}
    checks, blocked = [], []
    for item in requirements:
        quote = re.sub(r"\s+", "", re.sub(r"^[A-Z]{2,8}-\d{2,3}-\d{3}\s*", "", item["quote"]))
        # 只解释完整明确的句式；额外前提、否定或例外不能被子串匹配丢掉。
        sentence = quote.rstrip("。")
        common = {"source": item["source"], "clause_id": item["clause_id"], "quote": item["quote"]}
        if state["skill_id"] == "procurement":
            amount = request.get("estimated_amount")
            match = _RANGE.fullmatch(sentence) or _BELOW.fullmatch(sentence) or _ABOVE.fullmatch(sentence)
            # 明确预算外的任务不能套用预算内金额规则；预算真实性始终未核验。
            if match and amount is not None and "预算外" not in str(state.get("goal") or ""):
                actual = Decimal(str(amount))
                if match.re is _RANGE:
                    low, high, roles = match.groups()
                    unit = high[-1] if high[-1] in "万亿" else ""
                    applicable = _money(low, unit) <= actual <= _money(high)
                elif match.re is _BELOW:
                    limit, roles = match.groups()
                    applicable = actual <= _money(limit)
                else:
                    limit, roles = match.groups()
                    applicable = actual > _money(limit)
                required_roles = re.split(r"和|、|及", roles)
                checks.append({**common, "kind": "procurement_approval", "applicable": applicable,
                    "required_roles": required_roles, "status": "pending_business_verification",
                    "explanation": (f"按预算内采购的金额区间，本次{actual:g}元需{'和'.join(required_roles)}审批；"
                                    "预算有效性和业务审批完成情况尚待核验。" if applicable else "不匹配本次金额区间。")})
        elif state["skill_id"] == "access_request":
            match = _DURATION.fullmatch(sentence)
            if match:
                limit, days = int(match.group(1)), request.get("duration_days")
                violated = days is not None and int(days) > limit
                explanation = (f"申请期限{days}天超过{item['clause_id'] or '该制度'}规定的最长{limit}天。"
                    "请申请人修改期限后重新提交；系统不会擅自改动申请。" if violated else
                    f"申请期限{days}天未超过制度上限{limit}天；这不代表已获批准或已授予权限。")
                checks.append({**common, "kind": "permission_duration", "applicable": True,
                    "limit_days": limit, "actual_days": days, "status": "blocked" if violated else "matched",
                    "explanation": explanation})
                if violated:
                    blocked.append(f"权限期限：{explanation} 来源：{item['source']}。")
            if match and "管理员账号必须启用多因素认证" in quote and request.get("permission_level") == "admin":
                checks.append({**common, "kind": "admin_mfa", "applicable": True, "status": "unverified",
                    "explanation": "管理员账号须启用多因素认证；实际启用状态尚待核验，计划启用不等于已启用。"})
    approvals = [c for c in checks if c["kind"] == "procurement_approval" and c["applicable"]]
    if len({tuple(c["required_roles"]) for c in approvals}) > 1:
        blocked.append("审批条款冲突：同一金额命中不同审批角色要求，需人工核对适用条件，不能自行选择或合并审批链。")
    limits = {c["limit_days"] for c in checks if c["kind"] == "permission_duration"}
    if len(limits) > 1:
        blocked.append("权限期限条款冲突：候选制度存在不同期限上限，需人工核对适用条件，不能自行采用较宽限制。")
    return {"checks": checks, "blocking_issues": blocked,
        "coverage": "partial" if checks else "unverified", "request_facts": dict(request),
        "configuration_context": {
            "duration_risk_trigger_days": int(policy.get("access_max_duration_days") or 30),
            "classification_risk_trigger": str(policy.get("access_high_risk_classification") or "secret"),
            "meaning": "仅用于判断风险和是否审批，不是制度期限上限，也不是资源的实际密级。"}}


def registration_notes(state, assessment):
    notes = ["申请事实：保留申请人提交的金额、权限、密级和期限；系统未代替申请人修改这些字段。"]
    notes.extend(f"制度核对：{c['explanation']} 依据：{c['source']}#{c['clause_id']}。"
                 for c in assessment["checks"] if c["applicable"])
    if state["skill_id"] == "procurement":
        request = state.get("request") or {}
        if not request.get("quotation_attached"):
            notes.append("报价材料：尚未解析到报价材料，保留材料缺失状态；不能视为已完成比价或采购准备。")
        if request.get("involves_sensitive_data"):
            notes.append("敏感数据：申请声明涉及敏感数据，相关供应商安全评估和数据分级尚待业务人员核验。")
    else:
        notes.append("风险边界：企业配置的期限和密级门槛仅触发风险审查，不会自动缩短申请期限或改变资源密级。")
    notes.append("业务边界：这里只保存内部申请，实际采购、授权以及制度要求的业务审批和检查仍需另行办理。")
    return notes


def enforce_policy_audit(state, assessment, audit):
    """确定的制度冲突不能被模型放行；不把模型拒绝强行改成通过。"""
    result = dict(audit)
    issues = assessment["blocking_issues"]
    if issues:
        result["approved"] = False
        result["policy_blocking_issues"] = list(issues)
        result["required_changes"] = list(dict.fromkeys([*issues, *result.get("required_changes", [])]))
    return result


def require_policy_compliance(state, assessment):
    if assessment["blocking_issues"]:
        raise ValueError("制度核对未通过：" + "；".join(assessment["blocking_issues"]))

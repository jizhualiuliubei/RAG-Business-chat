"""内置 Agent Skills、动态表单 Schema 与确定性风险规则。"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field


PROCUREMENT_SKILL_ID = "procurement"
ACCESS_SKILL_ID = "access_request"


class ProcurementItem(BaseModel):
    name: str = Field(min_length=1, max_length=200, title="物品或服务")
    quantity: int = Field(default=1, ge=1, le=100000, title="数量")
    unit_price: float | None = Field(default=None, ge=0, title="单价")


class ProcurementRequest(BaseModel):
    purpose: str | None = Field(default=None, min_length=2, max_length=1000, title="采购目的")
    items: list[ProcurementItem] = Field(default_factory=list, title="采购明细")
    estimated_amount: float | None = Field(default=None, ge=0, title="预计金额")
    budget_code: str | None = Field(default=None, max_length=120, title="预算编号")
    vendor: str | None = Field(default=None, max_length=200, title="供应商")
    expected_date: str | None = Field(default=None, max_length=40, title="期望日期")
    quotation_attached: bool = Field(
        default=False,
        title="报价材料已核验",
        json_schema_extra={"readOnly": True},
    )
    involves_sensitive_data: bool = Field(default=False, title="涉及敏感数据")


class AccessRequest(BaseModel):
    target_system: str | None = Field(default=None, min_length=2, max_length=200, title="目标系统")
    resource: str | None = Field(default=None, min_length=1, max_length=300, title="目标资源")
    permission_level: Literal["read", "write", "admin"] | None = Field(default=None, title="权限级别")
    data_classification: Literal["public", "internal", "secret", "confidential"] | None = Field(default=None, title="数据密级")
    duration_days: int | None = Field(default=None, ge=1, le=365, title="申请天数")
    business_reason: str | None = Field(default=None, min_length=2, max_length=1000, title="业务理由")


class RiskAssessment(BaseModel):
    level: Literal["low", "medium", "high"]
    requires_approval: bool
    reasons: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    # 这里只放**模型能调用的**工具。附件解析是服务端流程（代码读文件、抽摘要），
    # 模型拿不到那个工具 —— 声明里混进去会让"声明"和"实际绑定"对不上。
    allowed_read_tools: tuple[str, ...]
    allowed_write_tools: tuple[str, ...]
    # 自主检索次数的**代码级上限**。企业可以在 1~这个值之间调小，但调不大 ——
    # 配置只能收紧安全边界，不能放宽。
    max_policy_searches: int = 3

    def public_dict(self, enabled: bool = True, config: dict | None = None) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "enabled": enabled,
            "input_schema": self.input_model.model_json_schema(),
            "allowed_read_tools": list(self.allowed_read_tools),
            "allowed_write_tools": list(self.allowed_write_tools),
            # 让前端知道这个 Skill 有哪些可配项、上限是多少，而不是一个自由字典
            "configurable": {"max_policy_searches": {"min": 1, "max": self.max_policy_searches}},
            "config": config or {},
        }


_SKILLS = {
    PROCUREMENT_SKILL_ID: SkillDefinition(
        id=PROCUREMENT_SKILL_ID,
        name="采购申请",
        version="1.0.0",
        description="依据企业制度检查采购信息、材料、预算和供应商准入。",
        input_model=ProcurementRequest,
        allowed_read_tools=("policy_search",),
        allowed_write_tools=("create_procurement_record",),
    ),
    ACCESS_SKILL_ID: SkillDefinition(
        id=ACCESS_SKILL_ID,
        name="系统权限申请",
        version="1.0.0",
        description="检查最小权限、职责分离、数据密级和授权期限。",
        input_model=AccessRequest,
        allowed_read_tools=("policy_search",),
        allowed_write_tools=("create_access_record",),
    ),
}


def get_skill(skill_id: str) -> SkillDefinition:
    try:
        return _SKILLS[skill_id]
    except KeyError as exc:
        raise ValueError(f"不支持的 Agent Skill：{skill_id}") from exc


def list_skills() -> list[SkillDefinition]:
    return list(_SKILLS.values())


def route_skill(goal: str) -> str:
    text = str(goal or "").strip().lower()
    access_words = ("权限", "授权", "访问", "账号", "数据库", "系统", "vpn", "admin", "只读")
    procurement_words = ("采购", "购买", "供应商", "报价", "预算", "物品", "设备", "服务")
    access_score = sum(word in text for word in access_words)
    procurement_score = sum(word in text for word in procurement_words)
    if access_score == procurement_score == 0:
        raise ValueError("暂时只能处理采购申请或系统权限申请，请明确业务目标")
    return ACCESS_SKILL_ID if access_score > procurement_score else PROCUREMENT_SKILL_ID


def missing_fields(skill_id: str, payload: dict[str, Any]) -> list[str]:
    if skill_id == PROCUREMENT_SKILL_ID:
        required = ("purpose", "items", "estimated_amount", "budget_code", "vendor")
    elif skill_id == ACCESS_SKILL_ID:
        required = ("target_system", "resource", "permission_level", "data_classification", "duration_days", "business_reason")
    else:
        get_skill(skill_id)
        return []
    return [name for name in required if payload.get(name) in (None, "", [])]


def assess_risk(skill_id: str, payload: dict[str, Any], policy: dict[str, Any]) -> RiskAssessment:
    if skill_id == ACCESS_SKILL_ID:
        reasons = ["实际权限授予必须由企业管理员审批"]
        classification_rank = {"public": 0, "internal": 1, "secret": 2, "confidential": 3}
        high_risk_classification = str(policy.get("access_high_risk_classification") or "secret")
        classification_is_high = classification_rank.get(str(payload.get("data_classification") or ""), -1) >= classification_rank.get(high_risk_classification, 2)
        duration_limit = int(policy.get("access_max_duration_days") or 30)
        duration_is_high = int(payload.get("duration_days") or 0) > duration_limit
        privileged = payload.get("permission_level") == "admin"
        high = privileged or classification_is_high or duration_is_high
        if privileged:
            reasons.append("申请包含管理员权限")
        if classification_is_high:
            reasons.append(f"数据密级达到企业高风险边界 {high_risk_classification}")
        if duration_is_high:
            reasons.append(f"申请期限超过企业高风险边界 {duration_limit} 天")
        return RiskAssessment(level="high" if high else "medium", requires_approval=True, reasons=reasons)

    if skill_id != PROCUREMENT_SKILL_ID:
        get_skill(skill_id)
    amount = float(payload.get("estimated_amount") or 0)
    threshold = float(policy.get("procurement_approval_amount", 50000))
    requires_quotation = bool(policy.get("procurement_requires_quotation", True))
    sensitive_requires_approval = bool(
        policy.get("procurement_sensitive_data_requires_approval", True)
    )
    reasons: list[str] = []
    if amount >= threshold:
        reasons.append(f"预计金额达到企业审批门槛 {threshold:g} 元")
    if requires_quotation and not payload.get("quotation_attached"):
        reasons.append("未附报价材料")
    if sensitive_requires_approval and payload.get("involves_sensitive_data"):
        reasons.append("采购事项涉及敏感数据")
    if sensitive_requires_approval and payload.get("involves_sensitive_data") or amount >= threshold:
        level = "high"
    elif reasons:
        level = "medium"
    else:
        level = "low"
    return RiskAssessment(level=level, requires_approval=level != "low", reasons=reasons)

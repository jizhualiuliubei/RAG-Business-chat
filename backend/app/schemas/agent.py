"""企业合规执行中心 API Schema。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentTaskCreate(BaseModel):
    goal: str = Field(min_length=2, max_length=4000)
    kb_ids: list[int] = Field(default_factory=list, max_length=20)


class AgentTaskInput(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)


class AgentApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected", "changes_requested"]
    comment: str = Field(default="", max_length=1000)


class AgentSkillUpdate(BaseModel):
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)


class AgentRiskPolicyUpdate(BaseModel):
    procurement_approval_amount: int | None = Field(default=None, ge=0, le=1_000_000_000)
    procurement_requires_quotation: bool | None = None
    procurement_sensitive_data_requires_approval: bool | None = None
    access_high_risk_classification: Literal["internal", "secret", "confidential"] | None = None
    access_max_duration_days: int | None = Field(default=None, ge=1, le=365)

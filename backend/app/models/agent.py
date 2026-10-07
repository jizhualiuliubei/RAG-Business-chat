"""企业合规执行中心数据模型。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentTask(Base):
    __tablename__ = "agent_task"
    __table_args__ = (
        Index("ix_agent_task_enterprise_user_created", "enterprise_id", "user_id", "created_at"),
        Index("ix_agent_task_enterprise_status", "enterprise_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    skill_id: Mapped[str] = mapped_column(String(60), nullable=False)
    skill_version: Mapped[str] = mapped_column(String(30), default="1.0.0", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    risk_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    current_node: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    checkpoint_thread_id: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    kb_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    request_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    missing_fields_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    audit_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    result_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_message: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentTaskStep(Base):
    __tablename__ = "agent_task_step"
    __table_args__ = (Index("ix_agent_step_task_created", "task_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("agent_task.id", ondelete="CASCADE"), nullable=False, index=True)
    node_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    output_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error_message: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class AgentTaskAttachment(Base):
    __tablename__ = "agent_task_attachment"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("agent_task.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="done", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    failure_reason: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class AgentApproval(Base):
    __tablename__ = "agent_approval"
    __table_args__ = (UniqueConstraint("task_id", "task_version", name="uq_agent_approval_task_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("agent_task.id", ondelete="CASCADE"), nullable=False, index=True)
    task_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    comment: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentBusinessRecord(Base):
    __tablename__ = "agent_business_record"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_agent_business_idempotency"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("agent_task.id"), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="completed", nullable=False)
    schema_version: Mapped[str] = mapped_column(String(30), default="1.0.0", nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class AgentArtifact(Base):
    __tablename__ = "agent_artifact"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("agent_task.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(String(30), default="markdown", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class AgentSkillConfig(Base):
    __tablename__ = "agent_skill_config"
    __table_args__ = (UniqueConstraint("enterprise_id", "skill_id", name="uq_agent_skill_enterprise"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[str] = mapped_column(String(30), default="1.0.0", nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class AgentRiskPolicy(Base):
    __tablename__ = "agent_risk_policy"
    __table_args__ = (UniqueConstraint("enterprise_id", name="uq_agent_risk_enterprise"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    procurement_approval_amount: Mapped[int] = mapped_column(Integer, default=50000, nullable=False)
    procurement_requires_quotation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    procurement_sensitive_data_requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    access_high_risk_classification: Mapped[str] = mapped_column(String(20), default="secret", nullable=False)
    access_max_duration_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class AgentOutbox(Base):
    __tablename__ = "agent_outbox"
    __table_args__ = (UniqueConstraint("task_id", name="uq_agent_outbox_task"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("agent_task.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class AgentEvaluationRun(Base):
    __tablename__ = "agent_evaluation_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    total_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class AgentEvaluationCaseResult(Base):
    __tablename__ = "agent_evaluation_case_result"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise.id"), nullable=False, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_evaluation_run.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    skill_id: Mapped[str] = mapped_column(String(60), nullable=False)
    passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    diagnostics_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

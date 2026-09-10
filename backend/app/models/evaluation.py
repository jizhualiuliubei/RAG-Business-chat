"""RAG evaluation run models."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EvaluationRun(Base):
    __tablename__ = "evaluation_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="retrieval")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class EvaluationDataset(Base):
    __tablename__ = "evaluation_dataset"
    __table_args__ = (UniqueConstraint("enterprise_id", "version", name="uq_eval_dataset_enterprise_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    version: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="uploaded")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recommended_kb_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    reviewed_by: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generation_policy: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class EvaluationDatasetCase(Base):
    __tablename__ = "evaluation_dataset_case"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    dataset_version: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(10), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="中等")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_doc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_clause: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expected_keywords_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    standard_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answer_points_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    required_citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evaluation_focus: Mapped[str] = mapped_column(Text, nullable=False, default="")
    negative_case: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_chunk_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_review")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class EvaluationCaseResult(Base):
    __tablename__ = "evaluation_case_result"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprise.id"), nullable=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(10), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_doc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_clause: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expected_keywords_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    answer_points_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    retrieved_contexts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top1_hit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top3_hit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top5_hit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    section_hit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answer_coverage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    citation_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refusal_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    fix_suggestion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

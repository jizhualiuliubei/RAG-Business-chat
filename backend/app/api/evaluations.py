"""RAG 评测接口。"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import current_enterprise_id, require_admin
from app.database import get_db
from app.models.knowledge_base import KnowledgeBase
from app.services import audit_service
from app.services.evaluation_service import (
    get_metric_definitions,
    run_retrieval_evaluation,
    summarize_catalog,
)

router = APIRouter(
    prefix="/api/evaluations",
    tags=["RAG评测"],
    dependencies=[Depends(require_admin)],
)


class EvaluationRunIn(BaseModel):
    kb_id: int | list[int] | None = Field(default=None)
    mode: str = Field(default="retrieval")


@router.get("/catalog")
def get_catalog():
    return summarize_catalog()


@router.get("/metrics-definition")
def get_metrics_definition():
    return get_metric_definitions()


@router.post("/run")
def run_evaluation(payload: EvaluationRunIn, request: Request, db: Session = Depends(get_db)):
    enterprise_id = current_enterprise_id(request)
    kb_scope = payload.kb_id
    if kb_scope is None:
        kb_scope = [
            kb.id
            for kb in db.query(KnowledgeBase)
            .filter(KnowledgeBase.enterprise_id == enterprise_id)
            .order_by(KnowledgeBase.id)
            .all()
        ]
    try:
        result = run_retrieval_evaluation(kb_id=kb_scope, enterprise_id=enterprise_id)
    except TypeError as exc:
        if "enterprise_id" not in str(exc):
            raise
        result = run_retrieval_evaluation(kb_id=kb_scope)
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="run_evaluation",
        target_type="evaluation",
        target_name=f"kb_id={kb_scope}",
        enterprise_id=enterprise_id,
    )
    return result

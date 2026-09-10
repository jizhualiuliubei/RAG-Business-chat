"""Persistent RAG evaluation task APIs."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from urllib.parse import quote

from app.api.dependencies import current_enterprise_id, require_admin
from app.database import get_db
from app.services import audit_service, evaluation_run_service
from app.services import evaluation_dataset_service

router = APIRouter(
    prefix="/api",
    tags=["RAG评测任务"],
    dependencies=[Depends(require_admin)],
)


class EvaluationRunCreate(BaseModel):
    kb_id: int | list[int]
    dataset_version: str = Field(default="enterprise_scale_v1")
    mode: str = Field(default="retrieval")


class EvaluationDatasetGenerate(BaseModel):
    kb_id: int
    case_count: int = Field(default=12, ge=3, le=60)
    category_mix: list[str] | None = None
    include_negative_cases: bool = True
    difficulty: str = "中等"


class EvaluationCaseUpdate(BaseModel):
    category: str | None = None
    difficulty: str | None = None
    question: str | None = None
    expected_doc: str | None = None
    expected_section: str | None = None
    expected_clause: str | None = None
    expected_keywords: list[str] | str | None = None
    standard_answer: str | None = None
    answer_points: list[str] | str | None = None
    required_citations: list[str] | str | None = None
    evaluation_focus: str | None = None
    negative_case: bool | None = None


class EvaluationRunBatchDelete(BaseModel):
    run_ids: list[int] = Field(default_factory=list)


@router.get("/evaluation-datasets")
def list_evaluation_datasets(request: Request, db: Session = Depends(get_db)):
    return {"items": evaluation_dataset_service.list_datasets(db, current_enterprise_id(request))}


@router.get("/evaluation-datasets/{dataset_version}/download")
def download_evaluation_dataset(
    dataset_version: str,
    request: Request,
    format: str = Query(default="csv"),
    db: Session = Depends(get_db),
):
    try:
        filename, media_type, content = evaluation_dataset_service.build_download(
            db,
            dataset_version,
            format,
            current_enterprise_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/evaluation-datasets/{dataset_version}")
def get_evaluation_dataset(dataset_version: str, request: Request, db: Session = Depends(get_db)):
    try:
        enterprise_id = current_enterprise_id(request)
        summary = evaluation_dataset_service.get_dataset_summary(db, dataset_version, enterprise_id)
        return {**summary, "items": evaluation_dataset_service.list_cases(db, dataset_version, enterprise_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/evaluation-datasets/upload")
async def upload_evaluation_dataset(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    dataset = await evaluation_dataset_service.create_uploaded_dataset(db, file, request.state.auth_user)
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="create_evaluation_dataset",
        target_type="evaluation_dataset",
        target_name=dataset["name"],
        enterprise_id=current_enterprise_id(request),
    )
    return dataset


@router.post("/evaluation-datasets/generate")
def generate_evaluation_dataset(
    payload: EvaluationDatasetGenerate,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        dataset = evaluation_dataset_service.create_ai_generated_dataset(
            db,
            kb_id=payload.kb_id,
            case_count=payload.case_count,
            actor=request.state.auth_user,
            category_mix=payload.category_mix,
            include_negative_cases=payload.include_negative_cases,
            difficulty=payload.difficulty,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="create_evaluation_dataset",
        target_type="evaluation_dataset",
        target_name=dataset["name"],
        enterprise_id=current_enterprise_id(request),
    )
    return dataset


@router.patch("/evaluation-datasets/{dataset_version}/cases/{case_id}")
def update_evaluation_dataset_case(
    dataset_version: str,
    case_id: str,
    payload: EvaluationCaseUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return evaluation_dataset_service.update_case(
            db,
            dataset_version,
            case_id,
            payload.model_dump(exclude_none=True),
            current_enterprise_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/evaluation-datasets/{dataset_version}/approve")
def approve_evaluation_dataset(dataset_version: str, request: Request, db: Session = Depends(get_db)):
    try:
        dataset = evaluation_dataset_service.approve_dataset(
            db,
            dataset_version,
            current_enterprise_id(request),
            actor=request.state.auth_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="approve_evaluation_dataset",
        target_type="evaluation_dataset",
        target_name=dataset["name"],
        enterprise_id=current_enterprise_id(request),
    )
    return dataset


@router.delete("/evaluation-datasets/{dataset_version}")
def delete_evaluation_dataset(dataset_version: str, request: Request, db: Session = Depends(get_db)):
    try:
        result = evaluation_dataset_service.delete_dataset(db, dataset_version, current_enterprise_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="delete_evaluation_dataset",
        target_type="evaluation_dataset",
        target_name=dataset_version,
        enterprise_id=current_enterprise_id(request),
    )
    return result


@router.post("/evaluation-runs")
def create_evaluation_run(
    payload: EvaluationRunCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        run = evaluation_run_service.create_run(
            db,
            kb_id=payload.kb_id,
            dataset_version=payload.dataset_version,
            mode=payload.mode,
            actor=request.state.auth_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="run_evaluation",
        target_type="evaluation_run",
        target_name=f"run_id={run.id}",
        enterprise_id=current_enterprise_id(request),
    )
    background_tasks.add_task(evaluation_run_service.run_evaluation_job, run.id)
    return evaluation_run_service.serialize_run(run)


@router.get("/evaluation-runs")
def list_evaluation_runs(request: Request, db: Session = Depends(get_db)):
    return {"items": evaluation_run_service.list_runs(db, current_enterprise_id(request))}


@router.delete("/evaluation-runs")
def delete_evaluation_runs(payload: EvaluationRunBatchDelete, request: Request, db: Session = Depends(get_db)):
    result = evaluation_run_service.delete_runs(db, payload.run_ids, current_enterprise_id(request))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="delete_evaluation_run",
        target_type="evaluation_run",
        target_name=f"run_ids={','.join(str(item) for item in result['ids'])}",
        enterprise_id=current_enterprise_id(request),
    )
    return result


@router.get("/evaluation-runs/{run_id}")
def get_evaluation_run(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = evaluation_run_service.get_run(db, run_id, current_enterprise_id(request))
    if not run:
        raise HTTPException(status_code=404, detail="评测任务不存在")
    return evaluation_run_service.serialize_run(run)


@router.post("/evaluation-runs/{run_id}/cancel")
def cancel_evaluation_run(run_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        run = evaluation_run_service.cancel_run(db, run_id, current_enterprise_id(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="cancel_evaluation_run",
        target_type="evaluation_run",
        target_name=f"run_id={run_id}",
        enterprise_id=current_enterprise_id(request),
    )
    return evaluation_run_service.serialize_run(run)


@router.delete("/evaluation-runs/{run_id}")
def delete_evaluation_run(run_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        result = evaluation_run_service.delete_run(db, run_id, current_enterprise_id(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    audit_service.record_audit(
        db,
        actor_id=int(request.state.auth_user["sub"]),
        actor_name=request.state.auth_user["display_name"],
        action="delete_evaluation_run",
        target_type="evaluation_run",
        target_name=f"run_id={run_id}",
        enterprise_id=current_enterprise_id(request),
    )
    return result


@router.get("/evaluation-runs/{run_id}/cases")
def list_evaluation_cases(
    run_id: int,
    request: Request,
    category: str | None = Query(default=None),
    passed: str | None = Query(default=None),
    failure_reason: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return {
        "items": evaluation_run_service.list_cases(
            db,
            run_id,
            category=category,
            passed=passed,
            failure_reason=failure_reason,
            enterprise_id=current_enterprise_id(request),
        )
    }


@router.post("/evaluation-runs/{run_id}/report")
def generate_evaluation_report(run_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        return evaluation_run_service.generate_report(db, run_id, current_enterprise_id(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

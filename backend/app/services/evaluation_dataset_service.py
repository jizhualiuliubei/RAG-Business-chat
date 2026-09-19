"""Evaluation dataset registry for built-in and uploaded cases."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.models.evaluation import EvaluationDataset, EvaluationDatasetCase
from app.services.enterprise_evaluation_dataset import (
    FULL_DATASET_VERSION,
    EvaluationCase,
    get_cases as get_builtin_cases,
    summarize_full_dataset,
)
from app.services import enterprise_service


SUPPORTED_DOWNLOAD_FORMATS = {"csv", "md", "markdown", "xlsx"}
DEFAULT_CATEGORY_LABELS = {
    "A": "单文档事实检索",
    "B": "单文档细节定位与条款解释",
    "C": "跨文档关联推理",
    "D": "场景应用与规则计算",
    "E": "边界条件与易错判断",
    "F": "文档未覆盖与幻觉测试",
}


def _can_access_builtin_datasets(db: Session, enterprise_id: int | None = None) -> bool:
    return enterprise_id is None or enterprise_service.is_system_enterprise(db, int(enterprise_id))


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _split(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(item.strip() for item in re.split(r"[；;|]", raw) if item.strip())


def list_datasets(db: Session, enterprise_id: int | None = None) -> list[dict]:
    builtins = []
    if _can_access_builtin_datasets(db, enterprise_id):
        builtins = [_with_builtin_meta(summarize_full_dataset())]
    query = db.query(EvaluationDataset)
    if enterprise_id is not None:
        query = query.filter(EvaluationDataset.enterprise_id == enterprise_id)
    custom = query.order_by(EvaluationDataset.created_at.desc()).all()
    return builtins + [serialize_dataset(row, case_count=_case_count(db, row.version, enterprise_id)) for row in custom]


def get_dataset_summary(db: Session, dataset_version: str, enterprise_id: int | None = None) -> dict:
    if dataset_version == FULL_DATASET_VERSION and _can_access_builtin_datasets(db, enterprise_id):
        return _with_builtin_meta(summarize_full_dataset())
    dataset = _get_custom_dataset(db, dataset_version, enterprise_id)
    return serialize_dataset(dataset, case_count=_case_count(db, dataset.version, enterprise_id))


def load_cases(db: Session, dataset_version: str, enterprise_id: int | None = None) -> list[EvaluationCase]:
    if dataset_version == FULL_DATASET_VERSION and _can_access_builtin_datasets(db, enterprise_id):
        return get_builtin_cases(dataset_version)
    dataset = _get_custom_dataset(db, dataset_version, enterprise_id)
    if dataset.status != "approved":
        raise ValueError("评测题集尚未审核通过，不能运行评测")
    rows = db.query(EvaluationDatasetCase).filter(
        EvaluationDatasetCase.dataset_version == dataset_version,
        EvaluationDatasetCase.enterprise_id == enterprise_id,
    ).order_by(EvaluationDatasetCase.case_id).all()
    return [_case_from_model(row) for row in rows]


def list_cases(db: Session, dataset_version: str, enterprise_id: int | None = None) -> list[dict]:
    if dataset_version == FULL_DATASET_VERSION and _can_access_builtin_datasets(db, enterprise_id):
        return [_case_to_dict(case) for case in get_builtin_cases(dataset_version)]
    _get_custom_dataset(db, dataset_version, enterprise_id)
    rows = db.query(EvaluationDatasetCase).filter(
        EvaluationDatasetCase.dataset_version == dataset_version,
        EvaluationDatasetCase.enterprise_id == enterprise_id,
    ).order_by(EvaluationDatasetCase.case_id).all()
    return [_model_to_dict(row) for row in rows]


# ---- 上传题集的格式约定 ----
# 只接受能被解析成结构化题目的文件；解析不出来就直接报错，
# 不静默降级成一条草稿题（那样用户以为上传成功了，实际只拿到一道题）。
UPLOAD_REQUIRED_COLUMNS = ("case_id", "question")
UPLOAD_OPTIONAL_COLUMNS = (
    "category",
    "difficulty",
    "expected_doc",
    "expected_section",
    "expected_clause",
    "expected_keywords",
    "standard_answer",
    "answer_points",
    "required_citations",
    "evaluation_focus",
    "negative_case",
)
UPLOAD_ALLOWED_SUFFIXES = (".csv", ".md", ".markdown", ".txt")
UPLOAD_TEMPLATE_ROW = {
    "case_id": "A1",
    "category": "A",
    "difficulty": "基础",
    "question": "题目原文，必填。",
    "expected_doc": "期望命中的文档名（不含扩展名，可留空）",
    "expected_section": "期望命中的章节名（可留空）",
    "expected_clause": "期望命中的条款号（可留空）",
    "expected_keywords": "关键词1；关键词2（仅用于展示，不参与判分）",
    "standard_answer": "标准答案，供审核人参考。",
    "answer_points": "要点一；要点二（多个要点用中文分号分隔，答案要点覆盖率按它计算）",
    "required_citations": "文档名#章节名（多条用中文分号分隔；留空则自动按文档名+章节名生成）",
    "evaluation_focus": "这道题想考察什么。",
    "negative_case": "false",
}


def build_upload_template() -> str:
    """生成上传模板 CSV：表头齐全 + 一行示例。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(UPLOAD_REQUIRED_COLUMNS) + list(UPLOAD_OPTIONAL_COLUMNS)
    )
    writer.writeheader()
    writer.writerow(UPLOAD_TEMPLATE_ROW)
    return buffer.getvalue()


def _upload_format_help() -> str:
    required = "、".join(UPLOAD_REQUIRED_COLUMNS)
    optional = "、".join(UPLOAD_OPTIONAL_COLUMNS)
    return (
        "题集文件必须是 CSV，或带表头的 Markdown 表格（列名写在表头行）。"
        f"必需列：{required}；可选列：{optional}。"
        "同一格里要写多个值（多条引用、多个要点）时用中文分号「；」分隔。"
        "可以先点「下载模板」，照列填好再上传。"
    )


async def create_uploaded_dataset(db: Session, file: UploadFile, actor: dict) -> dict:
    filename = file.filename or "未命名文件"
    suffix = Path(filename).suffix.lower()
    if suffix not in UPLOAD_ALLOWED_SUFFIXES:
        raise ValueError(
            f"不支持的文件类型「{suffix or '无扩展名'}」，目前只接受 "
            f"{'、'.join(UPLOAD_ALLOWED_SUFFIXES)}。{_upload_format_help()}"
        )
    raw = await file.read()
    text = _decode_bytes(raw)
    rows = _parse_structured_cases(text)
    if not rows:
        raise ValueError(
            f"没有从「{filename}」里解析出任何题目。{_upload_format_help()}"
        )
    dataset = EvaluationDataset(
        enterprise_id=actor.get("enterprise_id"),
        version=_new_version("uploaded"),
        name=f"上传题集 · {filename}",
        source_type="uploaded",
        status="draft",
        description="由管理员上传文件生成的待审核评测题集。",
        created_by=int(actor.get("sub", 0)),
        created_by_name=actor.get("display_name", ""),
    )
    db.add(dataset)
    db.flush()
    _replace_cases(db, dataset.version, rows, enterprise_id=actor.get("enterprise_id"))
    db.commit()
    db.refresh(dataset)
    return serialize_dataset(dataset, case_count=len(rows))


def approve_dataset(
    db: Session,
    dataset_version: str,
    enterprise_id: int | None = None,
    actor: dict | None = None,
) -> dict:
    dataset = _get_custom_dataset(db, dataset_version, enterprise_id)
    if _case_count(db, dataset.version, enterprise_id) == 0:
        raise ValueError("题集没有题目，不能审核通过")
    dataset.status = "approved"
    dataset.reviewed_by = (actor or {}).get("display_name", "") if actor else dataset.reviewed_by
    dataset.reviewed_at = datetime.now()
    dataset.updated_at = datetime.now()
    db.query(EvaluationDatasetCase).filter(
        EvaluationDatasetCase.dataset_version == dataset.version,
        EvaluationDatasetCase.enterprise_id == enterprise_id,
    ).update({"review_status": "human_reviewed"})
    db.commit()
    db.refresh(dataset)
    return serialize_dataset(dataset, case_count=_case_count(db, dataset.version, enterprise_id))


def delete_dataset(db: Session, dataset_version: str, enterprise_id: int | None = None) -> dict:
    if dataset_version == FULL_DATASET_VERSION and _can_access_builtin_datasets(db, enterprise_id):
        raise ValueError("内置评测集不能删除")
    dataset = _get_custom_dataset(db, dataset_version, enterprise_id)
    db.query(EvaluationDatasetCase).filter(
        EvaluationDatasetCase.dataset_version == dataset.version,
        EvaluationDatasetCase.enterprise_id == enterprise_id,
    ).delete()
    db.delete(dataset)
    db.commit()
    return {"deleted": True, "version": dataset_version}


def update_case(db: Session, dataset_version: str, case_id: str, payload: dict, enterprise_id: int | None = None) -> dict:
    dataset = _get_custom_dataset(db, dataset_version, enterprise_id)
    row = db.query(EvaluationDatasetCase).filter(
        EvaluationDatasetCase.dataset_version == dataset.version,
        EvaluationDatasetCase.case_id == case_id,
        EvaluationDatasetCase.enterprise_id == enterprise_id,
    ).first()
    if not row:
        raise LookupError("题目不存在")
    for key in ("category", "difficulty", "question", "expected_doc", "expected_section", "expected_clause", "standard_answer", "evaluation_focus"):
        if key in payload:
            setattr(row, key, payload[key] or "")
    for key, column in (
        ("expected_keywords", "expected_keywords_json"),
        ("answer_points", "answer_points_json"),
        ("required_citations", "required_citations_json"),
    ):
        if key in payload:
            value = payload[key]
            setattr(row, column, _json(value if isinstance(value, list) else list(_split(str(value)))))
    if "negative_case" in payload:
        row.negative_case = 1 if payload["negative_case"] else 0
    dataset.status = "draft"
    dataset.reviewed_by = ""
    dataset.reviewed_at = None
    row.review_status = "pending_review"
    dataset.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return _model_to_dict(row)


def build_download(db: Session, dataset_version: str, fmt: str, enterprise_id: int | None = None) -> tuple[str, str, bytes]:
    fmt = (fmt or "csv").lower()
    if fmt not in SUPPORTED_DOWNLOAD_FORMATS:
        raise ValueError("format 仅支持 csv、xlsx 或 md")
    summary = get_dataset_summary(db, dataset_version, enterprise_id)
    cases = list_cases(db, dataset_version, enterprise_id)
    base_name = f"{summary['name']}.{('md' if fmt == 'markdown' else fmt)}"
    if fmt == "csv":
        return base_name, "text/csv; charset=utf-8", _cases_to_csv(cases)
    if fmt in {"md", "markdown"}:
        return base_name, "text/markdown; charset=utf-8", _cases_to_md(summary, cases).encode("utf-8")
    return base_name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _cases_to_xlsx(cases)


def serialize_dataset(dataset: EvaluationDataset, *, case_count: int) -> dict:
    categories = _summarize_custom_categories(dataset.version, case_count) if case_count == 0 else None
    review_status = "human_reviewed" if dataset.status == "approved" and dataset.reviewed_at else "pending_review"
    return {
        "id": dataset.id,
        "version": dataset.version,
        "name": dataset.name,
        "source_type": dataset.source_type,
        "status": dataset.status,
        "review_status": review_status,
        "reviewed_by": dataset.reviewed_by,
        "reviewed_at": dataset.reviewed_at,
        "review_notes": dataset.review_notes or "",
        "generation_policy": _loads(dataset.generation_policy, {}),
        "case_count": case_count,
        "categories": categories or {},
        "recommended_kb_name": dataset.recommended_kb_name,
        "description": dataset.description,
        "download_formats": ["csv", "xlsx", "md"],
        "created_by_name": dataset.created_by_name,
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
    }


def _with_builtin_meta(summary: dict) -> dict:
    summary = dict(summary)
    summary.update({
        "source_type": "builtin",
        "status": "approved",
        "review_status": "baseline",
        "reviewed_by": "system",
        "reviewed_at": None,
        "review_notes": "",
        "generation_policy": {"purpose": "baseline"},
        "description": "系统内置可复现评测题集。",
        "download_formats": summary.get("download_formats", ["csv", "md"]),
    })
    return summary


def _get_custom_dataset(db: Session, dataset_version: str, enterprise_id: int | None = None) -> EvaluationDataset:
    query = db.query(EvaluationDataset).filter(EvaluationDataset.version == dataset_version)
    if enterprise_id is not None:
        query = query.filter(EvaluationDataset.enterprise_id == enterprise_id)
    dataset = query.first()
    if not dataset:
        raise ValueError(f"未知评测集版本: {dataset_version}")
    return dataset


def _case_count(db: Session, dataset_version: str, enterprise_id: int | None = None) -> int:
    query = db.query(EvaluationDatasetCase).filter(EvaluationDatasetCase.dataset_version == dataset_version)
    if enterprise_id is not None:
        query = query.filter(EvaluationDatasetCase.enterprise_id == enterprise_id)
    return query.count()


def _case_from_model(row: EvaluationDatasetCase) -> EvaluationCase:
    return EvaluationCase(
        case_id=row.case_id,
        category=row.category,
        question=row.question,
        expected_doc=row.expected_doc,
        expected_section=row.expected_section,
        expected_clause=row.expected_clause,
        expected_keywords=tuple(_loads(row.expected_keywords_json, [])),
        answer_points=tuple(_loads(row.answer_points_json, []) or _split(row.standard_answer)),
        difficulty=row.difficulty,
        negative_case=bool(row.negative_case),
        required_citations=tuple(_loads(row.required_citations_json, [])),
    )


def _case_to_dict(case: EvaluationCase) -> dict:
    return {
        "case_id": case.case_id,
        "category": case.category,
        "difficulty": case.difficulty,
        "question": case.question,
        "expected_doc": case.expected_doc,
        "expected_section": case.expected_section,
        "expected_clause": case.expected_clause,
        "expected_keywords": list(case.expected_keywords),
        "standard_answer": "；".join(case.answer_points),
        "answer_points": list(case.answer_points),
        "required_citations": list(case.required_citations) or ([case.expected_doc] if case.expected_doc else []),
        "evaluation_focus": "检验RAG检索、回答和引用是否被标准答案支撑。",
        "negative_case": case.negative_case,
        "evidence_text": "",
        "evidence_hash": "",
        "source_chunk_id": 0,
        "generation_confidence": 0,
        "review_status": "baseline",
    }


def _model_to_dict(row: EvaluationDatasetCase) -> dict:
    return {
        "case_id": row.case_id,
        "category": row.category,
        "difficulty": row.difficulty,
        "question": row.question,
        "expected_doc": row.expected_doc,
        "expected_section": row.expected_section,
        "expected_clause": row.expected_clause,
        "expected_keywords": _loads(row.expected_keywords_json, []),
        "standard_answer": row.standard_answer,
        "answer_points": _loads(row.answer_points_json, []),
        "required_citations": _loads(row.required_citations_json, []),
        "evaluation_focus": row.evaluation_focus,
        "negative_case": bool(row.negative_case),
        "evidence_text": row.evidence_text or "",
        "evidence_hash": row.evidence_hash or "",
        "source_chunk_id": row.source_chunk_id or 0,
        "generation_confidence": row.generation_confidence or 0,
        "review_status": row.review_status or "pending_review",
    }


def _replace_cases(db: Session, dataset_version: str, rows: list[dict], enterprise_id: int | None = None) -> None:
    db.query(EvaluationDatasetCase).filter(
        EvaluationDatasetCase.dataset_version == dataset_version,
        EvaluationDatasetCase.enterprise_id == enterprise_id,
    ).delete()
    for index, row in enumerate(rows, start=1):
        db.add(EvaluationDatasetCase(
            enterprise_id=enterprise_id,
            dataset_version=dataset_version,
            case_id=row.get("case_id") or f"CUSTOM-{index:03d}",
            category=(row.get("category") or "A")[:1],
            difficulty=row.get("difficulty") or "中等",
            question=row.get("question") or "请根据知识库回答该问题。",
            expected_doc=row.get("expected_doc") or None,
            expected_section=row.get("expected_section") or None,
            expected_clause=row.get("expected_clause") or None,
            expected_keywords_json=_json(_split(row.get("expected_keywords"))),
            standard_answer=row.get("standard_answer") or "需由审核人在后台补充标准答案。",
            answer_points_json=_json(_split(row.get("answer_points") or row.get("standard_answer"))),
            required_citations_json=_json(_split(row.get("required_citations") or row.get("expected_doc"))),
            evaluation_focus=row.get("evaluation_focus") or "检验回答是否有知识库依据、是否覆盖答案要点、是否正确引用来源。",
            negative_case=1 if str(row.get("negative_case", "")).lower() == "true" or (row.get("category") or "").startswith("F") else 0,
            evidence_text=row.get("evidence_text") or "",
            evidence_hash=row.get("evidence_hash") or "",
            source_chunk_id=int(row.get("source_chunk_id") or 0),
            generation_confidence=int(row.get("generation_confidence") or 0),
            review_status=row.get("review_status") or "pending_review",
        ))


def _parse_structured_cases(text: str) -> list[dict]:
    if "case_id" not in text or "question" not in text:
        return []
    markdown_rows = _parse_markdown_table_cases(text)
    if markdown_rows:
        return markdown_rows
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except csv.Error:
        return []
    return [row for row in rows if row.get("question")]


def _parse_markdown_table_cases(text: str) -> list[dict]:
    rows: list[dict] = []
    headers: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "case_id" not in stripped and not headers:
            continue
        cells = [_unescape_md_cell(cell.strip()) for cell in stripped.strip("|").split("|")]
        if not headers:
            if "case_id" not in cells or "question" not in cells:
                continue
            headers = cells
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if cells == headers:
            continue
        if len(cells) != len(headers):
            continue
        raw = dict(zip(headers, cells))
        if not raw.get("case_id") or not raw.get("question"):
            continue
        rows.append(_normalize_markdown_case(raw))
    return rows


def _normalize_markdown_case(raw: dict[str, str]) -> dict[str, str]:
    source = raw.get("expected_source", "")
    expected_doc, expected_section, expected_clause = _split_expected_source(source)
    return {
        "case_id": raw.get("case_id", ""),
        "category": raw.get("category", ""),
        "difficulty": raw.get("difficulty", ""),
        "question": raw.get("question", ""),
        "expected_doc": raw.get("expected_doc") or expected_doc,
        "expected_section": raw.get("expected_section") or expected_section,
        "expected_clause": raw.get("expected_clause") or expected_clause,
        "expected_keywords": raw.get("expected_keywords", ""),
        "standard_answer": raw.get("standard_answer", ""),
        "answer_points": raw.get("answer_points", ""),
        "required_citations": raw.get("required_citations", ""),
        "evaluation_focus": raw.get("evaluation_focus", ""),
        "negative_case": raw.get("negative_case", ""),
        "evidence_text": raw.get("evidence_text", ""),
        "evidence_hash": raw.get("evidence_hash", ""),
        "source_chunk_id": raw.get("source_chunk_id", ""),
        "generation_confidence": raw.get("generation_confidence", ""),
        "review_status": raw.get("review_status", ""),
    }


def _split_expected_source(source: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in source.split("/") if part.strip()]
    if len(parts) >= 3 and parts[1] != "未覆盖":
        return parts[0], parts[1], parts[2]
    return (parts[0], "", "") if parts else ("", "", "")


def _unescape_md_cell(value: str) -> str:
    return value.replace("\\|", "|").replace("<br>", "\n")
def _evidence_row_from_chunk(item: dict) -> dict:
    raw_text = item.get("text") or ""
    text = re.sub(r"\s+", " ", raw_text).strip()
    source, section, clause = _extract_structured_source(raw_text, item.get("source") or "知识库文档")
    return {
        "source": source,
        "section": section,
        "clause": clause,
        "text": text,
        "chunk_id": int(item.get("chunk_id") or 0),
        "evidence_hash": _hash_text(text),
    }
def _draft_question(kb_name: str, category: str, index: int, source: str, text: str, secondary: dict | None = None) -> str:
    label = DEFAULT_CATEGORY_LABELS[category]
    if category == "F":
        return f"在「{kb_name}」相关知识库中，是否存在第 {index} 个外部福利或承诺政策？请在无依据时明确拒答。"
    if category == "C" and secondary:
        return f"请结合「{source}」和「{secondary['source']}」的证据，回答一道{label}题，并分别说明引用依据。"
    topic = text[:72].rstrip("，。；; ")
    return f"根据「{source}」中“{topic}”相关内容，回答一道{label}题，并说明依据。"


def _draft_standard_answer(category: str, text: str, secondary: dict | None = None) -> str:
    if category == "F":
        return "知识库没有提供该外部政策依据时，应明确说明无依据，不能编造金额、时限、审批人或制度编号。"
    if category == "C" and secondary:
        return f"需同时依据两份证据作答。主证据：{text[:120]}；辅助证据：{secondary.get('text', '')[:120]}"
    return "草稿答案依据所选知识库证据生成，管理员需要核对后审核通过：" + text[:180]


def _secondary_evidence(evidence_rows: list[dict], primary: dict) -> dict | None:
    for row in evidence_rows:
        if row is not primary and row.get("source") != primary.get("source"):
            return row
    for row in evidence_rows:
        if row is not primary:
            return row
    return None


def _required_citation(source: str, section: str, clause: str) -> str:
    return "#".join(item for item in (source, section, clause) if item)


def _extract_structured_source(text: str, fallback_source: str) -> tuple[str, str, str]:
    first_line = (text or "").splitlines()[0] if text else ""
    match = re.search(r"来源[:：]\s*(.+?)\s*>\s*(.+?)\s*>\s*([A-Z]{2,4}-\d{2}-\d{3})", first_line)
    if match:
        return match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
    return fallback_source, _guess_section(text), _extract_clause(text)


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _extract_clause(text: str) -> str:
    match = re.search(r"[A-Z]{2,4}-\d{2}-\d{3}", text)
    return match.group(0) if match else ""


def _guess_section(text: str) -> str:
    match = re.search(r"(?:第[一二三四五六七八九十]+章\s*)?([\u4e00-\u9fa5A-Za-z0-9]{2,18}(?:管理|制度|流程|要求|规范|审批|服务|安全|报销|绩效|培训|合同|付款|支持))", text)
    return match.group(1) if match else "待审核章节"


def _keywords_from_text(text: str) -> list[str]:
    tokens = re.findall(r"[A-Z]{2}-\d{2}-\d{3}|[\u4e00-\u9fa5]{2,8}|[A-Za-z]{3,}", text)
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
        if len(seen) >= 5:
            break
    return seen


def _decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _new_version(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"


def _cases_to_csv(cases: list[dict]) -> bytes:
    fields = [
        "case_id", "category", "difficulty", "question", "expected_doc", "expected_section",
        "expected_clause", "expected_keywords", "standard_answer", "answer_points",
        "required_citations", "evaluation_focus", "negative_case", "evidence_text",
        "evidence_hash", "source_chunk_id", "generation_confidence", "review_status",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for case in cases:
        row = dict(case)
        for key in ("expected_keywords", "answer_points", "required_citations"):
            if isinstance(row.get(key), list):
                row[key] = "；".join(row[key])
        row["negative_case"] = "true" if row.get("negative_case") else "false"
        writer.writerow({field: row.get(field, "") for field in fields})
    return output.getvalue().encode("utf-8-sig")


def _cases_to_md(summary: dict, cases: list[dict]) -> str:
    lines = [
        f"# {summary['name']} 评测题集",
        "",
        "企业规模知识库评测题集" if summary["version"] == FULL_DATASET_VERSION else "自定义 RAG 评测题集",
        "",
        f"- 版本：{summary['version']}",
        f"- 题集来源：{summary.get('source_type', '')}",
        f"- 状态：{summary['status']}",
        f"- 审核状态：{summary.get('review_status', '')}",
        f"- 审核人：{summary.get('reviewed_by') or '-'}",
        f"- 题目数：{len(cases)}",
        "",
        "| 题号 | 题型 | 问题 | 期望来源 | 标准答案 | 证据快照 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        evidence_snapshot = "；".join(
            item for item in (
                case.get("evidence_hash") or "",
                (case.get("evidence_text") or "")[:180],
            )
            if item
        )
        lines.append(
            f"| {case['case_id']} | {case['category']} | {_md(case['question'])} | "
            f"{_md(case.get('expected_doc') or '未覆盖')} | {_md(case.get('standard_answer') or '')} | "
            f"{_md(evidence_snapshot)} |"
        )
    return "\n".join(lines)


def _cases_to_xlsx(cases: list[dict]) -> bytes:
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ValueError("服务器未安装 openpyxl，暂不能导出 xlsx") from exc
    wb = Workbook()
    ws = wb.active
    ws.title = "评测题集"
    fields = [
        "case_id", "category", "difficulty", "question", "expected_doc", "expected_section",
        "expected_clause", "standard_answer", "answer_points", "required_citations",
        "evaluation_focus", "negative_case", "evidence_text", "evidence_hash",
        "source_chunk_id", "generation_confidence", "review_status",
    ]
    ws.append(fields)
    for case in cases:
        ws.append([case.get(field, "") for field in fields])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _summarize_custom_categories(dataset_version: str, case_count: int) -> dict:
    if case_count:
        return {}
    return {key: {"label": label, "case_count": 0} for key, label in CATEGORY_LABELS_360.items()}


def _md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")

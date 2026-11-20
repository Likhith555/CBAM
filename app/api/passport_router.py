"""Passport API: review status workflow, history, versions and server-side PDF."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import PassportVersion
from app.modules.calculation.engine import EngineInputError, IncompleteInputError
from app.modules.calculation.workspace_calc import latest_result, rerun_version, run_workspace_calculation
from app.modules.gaps.register import (
    INCOMPLETE,
    WorkspaceNotFound,
    compute_gaps,
    current_gaps,
    get_workspace,
    load_inputs,
)
from app.modules.reporting.pdf_generator import generate_passport_pdf
from app.modules.review.status import (
    STATUS_WORDING,
    TRANSITIONS,
    TransitionError,
    current_version,
    history,
    sync_passport,
    transition,
)
from app.modules.validation.rules import workspace_documents

router = APIRouter(prefix="/passports", tags=["Passport Review Workflow"])


class TransitionRequest(BaseModel):
    to: str
    note: str = ""
    actor: str
    role: str = Field(..., description="exporter / reviewer / verifier (prototype 'acting as' selector)")
    extra_fields: Dict[str, Any] = Field(default_factory=dict)


def _history_rows(db: Session, workspace_id: str):
    return [
        {"id": h.id, "version_number": h.version_number, "from": h.from_status, "to": h.to_status,
         "actor": h.actor, "role": h.role, "note": h.note,
         "extra": json.loads(h.extra_json) if h.extra_json else None, "timestamp": h.timestamp}
        for h in history(db, workspace_id)
    ]


def _version_dict(v: PassportVersion) -> Dict[str, Any]:
    return {
        "version_number": v.version_number, "status": v.status, "input_hash": v.input_hash,
        "factor_set_version": v.factor_set_version, "mc_seed": v.mc_seed, "has_result": bool(v.result_json),
        "verifier_details": json.loads(v.verifier_details_json) if v.verifier_details_json else None,
        "created_at": v.created_at, "status_wording": STATUS_WORDING.get(v.status),
    }


def _load(db: Session, workspace_id: str) -> PassportVersion:
    try:
        get_workspace(db, workspace_id)
    except WorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return sync_passport(db, workspace_id)


@router.get("/{workspace_id}")
def get_passport(workspace_id: str, db: Session = Depends(get_db)):
    version = _load(db, workspace_id)
    db.commit()
    allowed_next = [to for (frm, to), _ in TRANSITIONS.items() if frm == version.status]
    return {**_version_dict(version), "allowed_next": allowed_next,
            "required_role": {to: role for (frm, to), role in TRANSITIONS.items() if frm == version.status},
            "result": latest_result(db, workspace_id)}


@router.post("/{workspace_id}/transition")
def post_transition(workspace_id: str, body: TransitionRequest, db: Session = Depends(get_db)):
    _load(db, workspace_id)
    try:
        version = transition(db, workspace_id, body.to, body.actor, body.role, body.note, body.extra_fields)
    except TransitionError as exc:
        db.commit()
        raise HTTPException(status_code=409, detail={"message": str(exc), "problems": exc.problems})
    db.commit()
    return {**_version_dict(version), "history": _history_rows(db, workspace_id)}


@router.get("/{workspace_id}/history")
def get_history(workspace_id: str, db: Session = Depends(get_db)):
    return _history_rows(db, workspace_id)


@router.get("/{workspace_id}/versions")
def get_versions(workspace_id: str, db: Session = Depends(get_db)):
    rows = db.query(PassportVersion).filter_by(workspace_id=workspace_id).order_by(PassportVersion.version_number).all()
    return [_version_dict(v) for v in rows]


@router.post("/{workspace_id}/versions/{version_number}/rerun")
def rerun(workspace_id: str, version_number: int, db: Session = Depends(get_db)):
    try:
        payload = rerun_version(db, workspace_id, version_number)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    version = db.query(PassportVersion).filter_by(workspace_id=workspace_id, version_number=version_number).first()
    stored = json.loads(version.result_json) if version.result_json else None
    payload["matches_stored"] = (
        stored is not None and stored["result"]["see_tco2_per_t"] == payload["result"]["see_tco2_per_t"]
    )
    return payload


def build_pdf_payload(db: Session, workspace_id: str) -> Dict[str, Any]:
    ws = get_workspace(db, workspace_id)
    version = sync_passport(db, workspace_id)
    compute_gaps(db, workspace_id)
    gaps = current_gaps(db, workspace_id)
    result = latest_result(db, workspace_id)
    if result is None and ws.calc_status != INCOMPLETE:
        try:
            result = run_workspace_calculation(db, workspace_id)
        except (IncompleteInputError, EngineInputError):
            result = None
    documents = {}
    for doc in workspace_documents(db, workspace_id):
        documents[doc.document_hash] = {
            "filename": doc.filename, "file_size_bytes": doc.file_size_bytes,
            "extracted_fields": [
                {"field_name": f.field_name, "extracted_value": f.raw_extracted_value, "unit": f.raw_unit,
                 "evidence_page": f.evidence_page, "evidence_snippet": f.evidence_snippet}
                for f in doc.extracted_fields
            ],
        }
    overrides = {}
    for doc in workspace_documents(db, workspace_id):
        for f in doc.extracted_fields:
            if f.was_overridden:
                overrides[f"{doc.document_hash}_{f.field_name}"] = {
                    "override_value": f.user_override_value, "override_unit": f.user_override_unit,
                    "override_reason": f.override_reason,
                }
    return {
        "workspace": {
            "company_name": ws.company_name, "installation_name": ws.installation_name,
            "installation_id": ws.id, "cn_code": ws.cn_code, "country": "India",
            "reporting_period": f"{ws.reporting_start or '?'} to {ws.reporting_end or '?'}",
            "target_product_family": load_inputs(ws).production.product_family or "",
        },
        "review_status": version.status,
        "passport_version": version.version_number,
        "status_wording": STATUS_WORDING.get(version.status),
        "verifier_details": json.loads(version.verifier_details_json) if version.verifier_details_json else None,
        "calc_status": ws.calc_status if result is None else "Ready",
        "gaps": [json.loads(g.model_dump_json()) for g in gaps if g.status == "open"],
        "priority1_result": result,
        "status_history": _history_rows(db, workspace_id),
        "uploaded_documents": documents,
        "overrides": overrides,
    }


@router.get("/{workspace_id}/pdf")
def get_pdf(workspace_id: str, db: Session = Depends(get_db)):
    _load(db, workspace_id)
    payload = build_pdf_payload(db, workspace_id)
    db.commit()
    pdf = generate_passport_pdf(payload)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = f"CBAM_Passport_{workspace_id}_v{payload['passport_version']}_{stamp}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={name}"})

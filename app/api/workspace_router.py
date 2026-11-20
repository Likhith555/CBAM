"""Workspace API: inputs, validation findings, data gaps, fallbacks and calculation."""

import datetime
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Assumption, ValidationFinding
from app.modules.audit.log import list_audit, write_audit
from app.modules.calculation.engine import EngineInputError, IncompleteInputError
from app.modules.calculation.workspace_calc import assumption_rows, latest_result, run_workspace_calculation
from app.modules.gaps.fallbacks import (
    FallbackError,
    FallbackNotAllowed,
    apply_fallback,
    reapply_for_version,
    withdraw_assumption,
)
from app.modules.gaps.register import (
    WorkspaceNotFound,
    compute_gaps,
    get_or_create_workspace,
    get_workspace,
    summary,
)
from app.modules.registry.factors import list_versions
from app.modules.review.status import current_version, sync_passport
from app.modules.validation.rules import (
    Finding,
    FindingError,
    list_findings,
    resolve_finding,
    validate_workspace,
    waive_finding,
)
from app.schemas.workspace import WorkspaceInputs

router = APIRouter(tags=["Workspaces, Validation & Data Gaps"])


class WorkspaceProfile(BaseModel):
    company_name: Optional[str] = None
    installation_name: Optional[str] = None
    cn_code: Optional[str] = None
    reporting_start: Optional[datetime.date] = None
    reporting_end: Optional[datetime.date] = None
    inputs: Optional[WorkspaceInputs] = None
    actor: str = "system"


class ReasonRequest(BaseModel):
    reason: str
    actor: str


class ResolveRequest(BaseModel):
    action: str = "resolve"  # resolve / keep_this / keep_other / distinct
    actor: str
    note: str = ""


class CalculateRequest(BaseModel):
    seed: Optional[int] = None
    iterations: Optional[int] = None
    actor: str = "system"


class FactorSetChange(BaseModel):
    version: str
    actor: str


def _not_found(exc: Exception):
    raise HTTPException(status_code=404, detail=str(exc))


def workspace_state(db: Session, workspace_id: str) -> Dict[str, Any]:
    ws = get_workspace(db, workspace_id)
    state = summary(db, workspace_id)
    version = current_version(db, workspace_id)
    findings = list_findings(db, workspace_id)
    state.update({
        "company_name": ws.company_name,
        "installation_name": ws.installation_name,
        "cn_code": ws.cn_code,
        "reporting_start": ws.reporting_start,
        "reporting_end": ws.reporting_end,
        "open_errors": sum(1 for f in findings if f.status == "open" and f.severity == "error"),
        "open_warnings": sum(1 for f in findings if f.status == "open" and f.severity == "warning"),
        "passport": None if version is None else {
            "version_number": version.version_number,
            "status": version.status,
            "input_hash": version.input_hash,
            "has_result": bool(version.result_json),
        },
    })
    return state


def _after_input_change(db: Session, workspace_id: str, actor: str, reason: str) -> None:
    compute_gaps(db, workspace_id, actor=actor)
    sync_passport(db, workspace_id, actor=actor, reason=reason)


@router.put("/workspaces/{workspace_id}")
def upsert_workspace(workspace_id: str, profile: WorkspaceProfile, db: Session = Depends(get_db)):
    ws = get_or_create_workspace(db, workspace_id)
    changes = {}
    for field in ("company_name", "installation_name", "cn_code", "reporting_start", "reporting_end"):
        value = getattr(profile, field)
        if value is not None and getattr(ws, field) != value:
            changes[field] = [getattr(ws, field), value]
            setattr(ws, field, value)
    if profile.inputs is not None:
        new_json = profile.inputs.model_dump_json()
        if new_json != ws.inputs_json:
            changes["inputs"] = True
            ws.inputs_json = new_json
    if changes:
        write_audit(db, workspace_id=workspace_id, entity_type="workspace", entity_id=workspace_id,
                    action="updated", actor=profile.actor,
                    old_value={k: v[0] for k, v in changes.items() if isinstance(v, list)},
                    new_value={k: (v[1] if isinstance(v, list) else "changed") for k, v in changes.items()})
    _after_input_change(db, workspace_id, profile.actor, "Workspace profile or inputs changed.")
    db.commit()
    return workspace_state(db, workspace_id)


@router.put("/workspaces/{workspace_id}/inputs")
def put_inputs(workspace_id: str, inputs: WorkspaceInputs, actor: str = "system", db: Session = Depends(get_db)):
    ws = get_or_create_workspace(db, workspace_id)
    new_json = inputs.model_dump_json()
    if new_json != ws.inputs_json:
        old = json.loads(ws.inputs_json) if ws.inputs_json else None
        ws.inputs_json = new_json
        write_audit(db, workspace_id=workspace_id, entity_type="workspace", entity_id=workspace_id,
                    action="inputs_updated", actor=actor, old_value=old, new_value=json.loads(new_json))
    _after_input_change(db, workspace_id, actor, "Workspace inputs changed.")
    db.commit()
    return workspace_state(db, workspace_id)


@router.get("/workspaces/{workspace_id}")
def get_workspace_state(workspace_id: str, db: Session = Depends(get_db)):
    try:
        return workspace_state(db, workspace_id)
    except WorkspaceNotFound as exc:
        _not_found(exc)


@router.post("/workspaces/{workspace_id}/factor-set")
def change_factor_set(workspace_id: str, body: FactorSetChange, db: Session = Depends(get_db)):
    try:
        ws = get_workspace(db, workspace_id)
    except WorkspaceNotFound as exc:
        _not_found(exc)
    if body.version not in list_versions(db):
        raise HTTPException(status_code=404, detail=f"Factor set {body.version} does not exist.")
    old = ws.factor_set_version
    if old != body.version:
        ws.factor_set_version = body.version
        write_audit(db, workspace_id=workspace_id, entity_type="workspace", entity_id=workspace_id,
                    action="factor_set_changed", actor=body.actor, old_value=old, new_value=body.version)
        reapply_for_version(db, workspace_id, body.version, body.actor)
        _after_input_change(db, workspace_id, body.actor, f"Factor set changed from {old} to {body.version}.")
    db.commit()
    return workspace_state(db, workspace_id)


# ------------------------------ Validation ------------------------------


@router.post("/workspaces/{workspace_id}/validate", response_model=List[Finding])
def run_validation(workspace_id: str, actor: str = "system", db: Session = Depends(get_db)):
    get_or_create_workspace(db, workspace_id)
    findings = validate_workspace(db, workspace_id, actor=actor)
    db.commit()
    return findings


@router.get("/workspaces/{workspace_id}/findings", response_model=List[Finding])
def get_findings(workspace_id: str, db: Session = Depends(get_db)):
    return list_findings(db, workspace_id)


@router.post("/findings/{finding_id}/waive", response_model=Finding)
def waive(finding_id: int, body: ReasonRequest, db: Session = Depends(get_db)):
    try:
        row = waive_finding(db, finding_id, body.reason, body.actor)
    except LookupError as exc:
        _not_found(exc)
    except FindingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return Finding.model_validate(row)


@router.post("/findings/{finding_id}/resolve", response_model=Finding)
def resolve(finding_id: int, body: ResolveRequest, db: Session = Depends(get_db)):
    try:
        row = resolve_finding(db, finding_id, body.action, body.actor, body.note)
    except LookupError as exc:
        _not_found(exc)
    except FindingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    sync_passport(db, row.workspace_id, actor=body.actor, reason="A document was excluded or a finding resolved.")
    db.commit()
    return Finding.model_validate(db.get(ValidationFinding, finding_id))


# ------------------------------ Gaps & fallbacks ------------------------------


@router.get("/workspaces/{workspace_id}/gaps")
def get_gaps(workspace_id: str, db: Session = Depends(get_db)):
    try:
        compute_gaps(db, workspace_id)
    except WorkspaceNotFound as exc:
        _not_found(exc)
    db.commit()
    return summary(db, workspace_id)


@router.post("/workspaces/{workspace_id}/gaps/{input_key}/apply-fallback")
def apply_gap_fallback(workspace_id: str, input_key: str, body: ReasonRequest, db: Session = Depends(get_db)):
    try:
        assumption = apply_fallback(db, workspace_id, input_key, body.reason, body.actor)
    except WorkspaceNotFound as exc:
        _not_found(exc)
    except FallbackNotAllowed as exc:
        db.commit()  # keep the audit row for the rejected attempt
        raise HTTPException(status_code=409, detail=str(exc))
    except FallbackError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    sync_passport(db, workspace_id, actor=body.actor, reason=f"Fallback applied for {input_key}.")
    db.commit()
    state = summary(db, workspace_id)
    state["assumption_id"] = assumption.id
    return state


@router.get("/workspaces/{workspace_id}/assumptions")
def get_assumptions(workspace_id: str, include_withdrawn: bool = False, db: Session = Depends(get_db)):
    query = db.query(Assumption).filter_by(workspace_id=workspace_id)
    if not include_withdrawn:
        query = query.filter_by(status="active")
    return assumption_rows(db, query.order_by(Assumption.id).all())


@router.post("/workspaces/{workspace_id}/assumptions/{assumption_id}/withdraw")
def withdraw(workspace_id: str, assumption_id: int, body: ReasonRequest, db: Session = Depends(get_db)):
    try:
        withdraw_assumption(db, workspace_id, assumption_id, body.actor, body.reason)
    except FallbackError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    sync_passport(db, workspace_id, actor=body.actor, reason="Assumption withdrawn.")
    db.commit()
    return summary(db, workspace_id)


# ------------------------------ Calculation ------------------------------


@router.post("/workspaces/{workspace_id}/calculate")
def calculate_workspace(workspace_id: str, body: Optional[CalculateRequest] = None, db: Session = Depends(get_db)):
    body = body or CalculateRequest()
    try:
        payload = run_workspace_calculation(db, workspace_id, seed=body.seed, iterations=body.iterations,
                                            actor=body.actor)
    except WorkspaceNotFound as exc:
        _not_found(exc)
    except IncompleteInputError as exc:
        db.commit()
        gaps = [json.loads(g.model_dump_json()) if hasattr(g, "model_dump_json") else {"input_key": str(g)}
                for g in exc.gaps]
        return JSONResponse(status_code=422, content={
            "detail": str(exc), "calc_status": get_workspace(db, workspace_id).calc_status, "gaps": gaps,
        })
    except EngineInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return payload


@router.get("/workspaces/{workspace_id}/result")
def get_result(workspace_id: str, db: Session = Depends(get_db)):
    payload = latest_result(db, workspace_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="No calculation stored for the current passport version.")
    return payload


@router.get("/workspaces/{workspace_id}/audit")
def get_audit(workspace_id: str, entity_type: Optional[str] = None, db: Session = Depends(get_db)):
    return [
        {"id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id, "action": r.action, "actor": r.actor,
         "old_value": r.old_value, "new_value": r.new_value, "reason": r.reason, "timestamp": r.timestamp}
        for r in list_audit(db, workspace_id, entity_type)
    ]

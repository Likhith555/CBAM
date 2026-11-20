"""Review status workflow and numbered passport snapshots (Task 4).

Statuses: Draft -> User-Confirmed -> Expert-Reviewed -> Externally Verified.
No step can be skipped. Any change to inputs, overrides, assumptions or the
factor-set version creates a new passport version in Draft.

Roles are a simple "acting as" selector with a typed name for the prototype;
real authentication is future work. "Externally Verified" is recorded by the
user from a document they hold; this tool does not check it.
"""

import hashlib
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import Assumption, DocumentRecord, ExtractedFieldAudit, PassportVersion, StatusHistory
from app.modules.audit.log import write_audit
from app.modules.gaps.register import INCOMPLETE, compute_gaps, get_workspace, load_inputs
from app.modules.validation.rules import open_errors, validate_workspace, workspace_documents

DRAFT = "Draft"
USER_CONFIRMED = "User-Confirmed"
EXPERT_REVIEWED = "Expert-Reviewed"
EXTERNALLY_VERIFIED = "Externally Verified"
STATUSES = [DRAFT, USER_CONFIRMED, EXPERT_REVIEWED, EXTERNALLY_VERIFIED]

TRANSITIONS: Dict[tuple, str] = {
    (DRAFT, USER_CONFIRMED): "exporter",
    (USER_CONFIRMED, EXPERT_REVIEWED): "reviewer",
    (EXPERT_REVIEWED, EXTERNALLY_VERIFIED): "verifier",
}

REQUIRED_EXTRA = {
    EXPERT_REVIEWED: ["reviewer_name", "organisation"],
    EXTERNALLY_VERIFIED: ["verifier_body", "accreditation_ref", "statement_ref", "date"],
}

STATUS_WORDING = {
    DRAFT: "Not reviewed. Not for submission.",
    USER_CONFIRMED: "Confirmed by the exporter. Not independently reviewed.",
    EXPERT_REVIEWED: "Reviewed by a named expert. Not externally verified.",
    EXTERNALLY_VERIFIED: "Recorded by the user; not verified by this tool.",
}


class TransitionError(Exception):
    """Invalid transition or unmet precondition (HTTP 409)."""

    def __init__(self, message: str, problems: Optional[List[str]] = None):
        super().__init__(message)
        self.problems = problems or []


def _document_field_state(db: Session, workspace_id: str) -> List[Any]:
    state = []
    for doc in workspace_documents(db, workspace_id):
        for field in sorted(doc.extracted_fields, key=lambda f: f.field_name):
            state.append([doc.document_hash, field.field_name, field.effective_value, field.effective_unit])
    return state


def _assumption_state(assumptions: List[Assumption]) -> List[Any]:
    return sorted([a.input_key, a.value_used, a.unit, a.factor_id] for a in assumptions)


def input_hash(db: Session, workspace_id: str) -> str:
    ws = get_workspace(db, workspace_id)
    assumptions = db.query(Assumption).filter_by(workspace_id=workspace_id, status="active").all()
    payload = {
        "inputs": json.loads(load_inputs(ws).model_dump_json()),
        "assumptions": _assumption_state(assumptions),
        "factor_set_version": ws.factor_set_version,
        "documents": _document_field_state(db, workspace_id),
        "reporting_window": [str(ws.reporting_start), str(ws.reporting_end)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def current_version(db: Session, workspace_id: str) -> Optional[PassportVersion]:
    return (
        db.query(PassportVersion)
        .filter_by(workspace_id=workspace_id)
        .order_by(PassportVersion.version_number.desc())
        .first()
    )


def _snapshot(db: Session, workspace_id: str) -> Dict[str, str]:
    ws = get_workspace(db, workspace_id)
    assumptions = db.query(Assumption).filter_by(workspace_id=workspace_id, status="active").all()
    return {
        "inputs_snapshot_json": load_inputs(ws).model_dump_json(),
        "assumptions_snapshot_json": json.dumps([
            {"id": a.id, "input_key": a.input_key, "value_used": a.value_used, "unit": a.unit,
             "factor_id": a.factor_id, "factor_set_version": a.factor_set_version, "reason": a.reason,
             "applied_by": a.applied_by, "applied_at": str(a.applied_at)}
            for a in assumptions
        ]),
    }


def sync_passport(db: Session, workspace_id: str, actor: str = "system", reason: str = "Inputs changed.") -> PassportVersion:
    """Creates v1, or a new Draft version when the input hash differs from the stored one."""
    ws = get_workspace(db, workspace_id)
    digest = input_hash(db, workspace_id)
    latest = current_version(db, workspace_id)
    if latest is not None and latest.input_hash == digest:
        return latest

    number = 1 if latest is None else latest.version_number + 1
    version = PassportVersion(workspace_id=workspace_id, version_number=number, status=DRAFT, input_hash=digest,
                              factor_set_version=ws.factor_set_version, **_snapshot(db, workspace_id))
    db.add(version)
    db.flush()
    from_status = None if latest is None else latest.status
    note = "Passport created." if latest is None else (
        f"Automatic reset: {reason} Input hash changed from {latest.input_hash[:12]} to {digest[:12]}."
    )
    db.add(StatusHistory(workspace_id=workspace_id, passport_version_id=version.id, version_number=number,
                         from_status=from_status, to_status=DRAFT, actor="system", role="system", note=note))
    write_audit(db, workspace_id=workspace_id, entity_type="passport", entity_id=version.id,
                action="version_created" if latest is None else "status_reset", actor=actor,
                old_value=None if latest is None else {"version": latest.version_number, "status": latest.status},
                new_value={"version": number, "status": DRAFT, "input_hash": digest}, reason=note)
    db.flush()
    return version


def _all_fields_reviewed(db: Session, workspace_id: str) -> List[str]:
    pending = []
    for doc in workspace_documents(db, workspace_id):
        for field in doc.extracted_fields:
            if not field.reviewed:
                pending.append(f"{doc.filename}: {field.field_name}")
    return pending


def check_preconditions(db: Session, workspace_id: str, to_status: str, extra: Dict[str, Any],
                        declaration: bool, version: PassportVersion) -> List[str]:
    problems: List[str] = []
    if to_status == USER_CONFIRMED:
        validate_workspace(db, workspace_id)
        errors = open_errors(db, workspace_id)
        if errors:
            problems.append(f"{len(errors)} open validation error(s) must be resolved or waived.")
        ws = get_workspace(db, workspace_id)
        compute_gaps(db, workspace_id)
        if ws.calc_status == INCOMPLETE:
            problems.append("Calculation status is Incomplete: required inputs are missing.")
        if not version.result_json:
            problems.append("Run the calculation for this passport version first.")
        pending = _all_fields_reviewed(db, workspace_id)
        if pending:
            problems.append(f"{len(pending)} extracted field(s) are not reviewed yet: " + "; ".join(pending[:5]))
        if not declaration:
            problems.append("Tick the declaration checkbox to confirm the inputs are complete and correct.")
    for name in REQUIRED_EXTRA.get(to_status, []):
        if not str(extra.get(name) or "").strip():
            problems.append(f"'{name}' is required for {to_status}.")
    return problems


def transition(db: Session, workspace_id: str, to_status: str, actor: str, role: str,
               note: str = "", extra_fields: Optional[Dict[str, Any]] = None) -> PassportVersion:
    extra_fields = dict(extra_fields or {})
    declaration = bool(extra_fields.pop("declaration", False))
    if not (actor or "").strip():
        raise TransitionError("Enter the name of the person acting.")
    version = sync_passport(db, workspace_id)
    from_status = version.status
    if to_status not in STATUSES:
        raise TransitionError(f"Unknown status '{to_status}'.")
    required_role = TRANSITIONS.get((from_status, to_status))
    if required_role is None:
        raise TransitionError(f"Transition {from_status} -> {to_status} is not allowed. Steps cannot be skipped.")
    if role != required_role:
        raise TransitionError(f"{from_status} -> {to_status} must be performed by the {required_role} (acting as {role}).")
    problems = check_preconditions(db, workspace_id, to_status, extra_fields, declaration, version)
    if to_status == EXPERT_REVIEWED and not (note or "").strip():
        problems.append("A reviewer note is required for Expert-Reviewed.")
    if problems:
        raise TransitionError("Preconditions are not met.", problems)

    version.status = to_status
    if to_status == EXTERNALLY_VERIFIED:
        version.verifier_details_json = json.dumps(extra_fields)
    if declaration:
        extra_fields["declaration"] = True
    db.add(StatusHistory(workspace_id=workspace_id, passport_version_id=version.id,
                         version_number=version.version_number, from_status=from_status, to_status=to_status,
                         actor=actor.strip(), role=role, note=note, extra_json=json.dumps(extra_fields) if extra_fields else None))
    write_audit(db, workspace_id=workspace_id, entity_type="passport", entity_id=version.id, action="status_changed",
                actor=actor, old_value=from_status, new_value=to_status, reason=note or None)
    db.flush()
    return version


def history(db: Session, workspace_id: str) -> List[StatusHistory]:
    return db.query(StatusHistory).filter_by(workspace_id=workspace_id).order_by(StatusHistory.id).all()


def approve_field(db: Session, field: ExtractedFieldAudit, actor: str) -> ExtractedFieldAudit:
    """Marks a field reviewed. Blocked while validation errors on it are open."""
    if not (actor or "").strip():
        raise TransitionError("Enter the reviewer's name.")
    doc = db.query(DocumentRecord).filter_by(document_hash=field.document_hash).first()
    workspace_id = doc.workspace_id if doc else None
    if workspace_id:
        validate_workspace(db, workspace_id)
        errors = open_errors(db, workspace_id, document_id=doc.id, field_id=field.id)
        if errors:
            raise TransitionError("Approval is blocked by open validation errors.", [e.message for e in errors])
    from app.modules.gaps.register import utc_now

    old = bool(field.reviewed)
    field.reviewed = True
    field.reviewed_by = actor.strip()
    field.reviewed_at = utc_now()
    write_audit(db, workspace_id=workspace_id, entity_type="field", entity_id=field.id, action="approved",
                actor=actor, old_value=old, new_value=True)
    db.flush()
    return field

"""Validation checks run before any number reaches the calculation (Task 1).

Rules
-----
V1_UNIT       unit consistency and conversion to the canonical unit per field type
V2_OVERLAP    overlapping billing periods, coverage gaps and periods outside the window
V3_DUPLICATE  exact duplicates (rejected at upload, HTTP 409) and near duplicates (warning)

``validate_workspace(db, workspace_id)`` runs all rules, reconciles the results
with stored findings (so waived / resolved findings keep their status), and
writes an audit row for every change.
"""

import datetime
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import DocumentRecord, ExtractedFieldAudit, ValidationFinding, Workspace
from app.modules.audit.log import write_audit
from app.modules.calculation.unit_converter import (
    CANONICAL_UNITS,
    FUEL_FORMS,
    UnitError,
    canonical_factor,
)

Severity = Literal["error", "warning"]
NEAR_DUPLICATE_TOLERANCE = 0.005  # 0.5 %


class Finding(BaseModel):
    id: Optional[int] = None
    rule_id: Literal["V1_UNIT", "V2_OVERLAP", "V3_DUPLICATE"]
    severity: Severity
    document_id: Optional[int] = None
    field_id: Optional[int] = None
    related_document_id: Optional[int] = None
    message: str
    suggested_fix: Optional[str] = None
    status: Literal["open", "resolved", "waived"] = "open"
    resolution_note: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime.datetime] = None
    fingerprint: Optional[str] = None

    model_config = {"from_attributes": True}


class FindingError(ValueError):
    pass


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------------
# V1 unit consistency
# ---------------------------------------------------------------------


def infer_field_type(field_name: str, category: Optional[str], unit: str, fuel_key: Optional[str] = None) -> Optional[str]:
    """Maps an extracted field to a field type with one canonical unit."""
    name = (field_name or "").lower()
    cat = (category or "").lower()
    u = (unit or "").lower()
    if "see" in name or "co2" in u or "emission_factor" in name:
        return "see"
    if "electric" in name or cat == "scope2_electricity":
        return "electricity"
    if cat in {"production", "precursor"} or "production" in name or "precursor" in name or "wire_rod" in name:
        return "mass"
    if cat == "scope1_fuel" or "fuel" in name or "oil" in name or "diesel" in name or "gas" in name:
        for slug, form in FUEL_FORMS.items():
            if slug in name:
                return form
        if fuel_key in FUEL_FORMS:
            return FUEL_FORMS[fuel_key]
        for form in ("liquid_fuel", "gas", "mass"):
            try:
                canonical_factor(unit, form)
                return form
            except UnitError:
                continue
        return "liquid_fuel"
    return None


def effective_value_unit(field: ExtractedFieldAudit) -> Tuple[Optional[float], str]:
    raw = field.effective_value if field.effective_value is not None else field.raw_extracted_value
    unit = field.effective_unit or field.raw_unit or ""
    try:
        return float(raw), unit
    except (TypeError, ValueError):
        return None, unit


def check_unit(value: float, unit: str, field_type: str) -> Tuple[float, str]:
    """Returns the converted value and the canonical unit, or raises ``UnitError``."""
    return value * canonical_factor(unit, field_type), CANONICAL_UNITS[field_type]


def _rule_v1(db: Session, documents: List[DocumentRecord], fuel_key: Optional[str]) -> List[Finding]:
    findings = []
    for doc in documents:
        for field in doc.extracted_fields:
            value, unit = effective_value_unit(field)
            field_type = infer_field_type(field.field_name, field.field_category, unit, fuel_key)
            field.field_type = field_type
            if field_type is None or value is None:
                field.canonical_value = None
                field.canonical_unit = None
                continue
            try:
                converted, canonical = check_unit(value, unit, field_type)
                field.canonical_value = converted
                field.canonical_unit = canonical
            except UnitError as exc:
                field.canonical_value = None
                field.canonical_unit = None
                findings.append(Finding(
                    rule_id="V1_UNIT", severity="error", document_id=doc.id, field_id=field.id,
                    message=f"{field.field_name} in {doc.filename}: {exc}",
                    suggested_fix=(
                        f"Correct the unit on the Review step to a {field_type.replace('_', ' ')} unit "
                        f"(canonical {CANONICAL_UNITS[field_type]}), with a justification."
                    ),
                    fingerprint=f"V1:{field.id}:{unit.strip().lower()}",
                ))
    return findings


# ---------------------------------------------------------------------
# V2 billing-period overlap
# ---------------------------------------------------------------------


def periods_overlap(start_a: datetime.date, end_a: datetime.date, start_b: datetime.date, end_b: datetime.date) -> bool:
    """End dates are inclusive. Assumes start_a <= start_b."""
    return start_b <= end_a


def overlap_days(end_a: datetime.date, start_b: datetime.date, end_b: datetime.date) -> int:
    return (min(end_a, end_b) - start_b).days + 1


def _group_key(doc: DocumentRecord) -> Tuple[str, str, str]:
    return (
        (doc.supplier or "").strip().lower(),
        (doc.account_number or "").strip().lower(),
        (doc.utility_type or "").strip().lower(),
    )


def _rule_v2(documents: List[DocumentRecord], window: Tuple[Optional[datetime.date], Optional[datetime.date]]) -> List[Finding]:
    findings: List[Finding] = []
    groups: Dict[Tuple[str, str, str], List[DocumentRecord]] = {}
    for doc in documents:
        if doc.period_start and doc.period_end:
            groups.setdefault(_group_key(doc), []).append(doc)

    win_start, win_end = window
    one_day = datetime.timedelta(days=1)
    for key, docs in groups.items():
        docs.sort(key=lambda d: (d.period_start, d.period_end, d.id))
        label = " / ".join(part for part in key if part) or "ungrouped records"
        latest = docs[0]
        for doc in docs[1:]:
            if periods_overlap(latest.period_start, latest.period_end, doc.period_start, doc.period_end):
                days = overlap_days(latest.period_end, doc.period_start, doc.period_end)
                findings.append(Finding(
                    rule_id="V2_OVERLAP", severity="error", document_id=doc.id, related_document_id=latest.id,
                    message=(
                        f"Billing periods overlap by {days} day(s) for {label}: {latest.filename} "
                        f"({latest.period_start} to {latest.period_end}) and {doc.filename} "
                        f"({doc.period_start} to {doc.period_end}). End dates are inclusive."
                    ),
                    suggested_fix="Check the bill dates; correct the period or exclude the duplicate bill.",
                    fingerprint=f"V2O:{latest.id}:{doc.id}:{latest.period_end}:{doc.period_start}",
                ))
            elif doc.period_start > latest.period_end + one_day:
                start_gap = latest.period_end + one_day
                end_gap = doc.period_start - one_day
                if (win_start is None or end_gap >= win_start) and (win_end is None or start_gap <= win_end):
                    findings.append(Finding(
                        rule_id="V2_OVERLAP", severity="warning", document_id=doc.id, related_document_id=latest.id,
                        message=f"Coverage gap for {label}: no record from {start_gap} to {end_gap}.",
                        suggested_fix="Upload the missing bill or note why the period has no consumption.",
                        fingerprint=f"V2G:{latest.id}:{doc.id}:{start_gap}:{end_gap}",
                    ))
            if doc.period_end > latest.period_end:
                latest = doc

        if win_start and win_end:
            first, last = docs[0], max(docs, key=lambda d: d.period_end)
            if first.period_start > win_start and first.period_start <= win_end:
                findings.append(Finding(
                    rule_id="V2_OVERLAP", severity="warning", document_id=first.id,
                    message=f"Coverage gap for {label}: no record from {win_start} to {first.period_start - one_day}.",
                    suggested_fix="Upload the bill covering the start of the reporting window.",
                    fingerprint=f"V2S:{first.id}:{win_start}:{first.period_start}",
                ))
            if last.period_end < win_end and last.period_end >= win_start:
                findings.append(Finding(
                    rule_id="V2_OVERLAP", severity="warning", document_id=last.id,
                    message=f"Coverage gap for {label}: no record from {last.period_end + one_day} to {win_end}.",
                    suggested_fix="Upload the bill covering the end of the reporting window.",
                    fingerprint=f"V2E:{last.id}:{last.period_end}:{win_end}",
                ))
            for doc in docs:
                if doc.period_end < win_start or doc.period_start > win_end:
                    findings.append(Finding(
                        rule_id="V2_OVERLAP", severity="warning", document_id=doc.id,
                        message=(f"{doc.filename} ({doc.period_start} to {doc.period_end}) falls fully outside "
                                 f"the reporting window {win_start} to {win_end}."),
                        suggested_fix="Exclude the record or correct its billing period.",
                        fingerprint=f"V2W:{doc.id}:full:{doc.period_start}:{doc.period_end}",
                    ))
                elif doc.period_start < win_start or doc.period_end > win_end:
                    findings.append(Finding(
                        rule_id="V2_OVERLAP", severity="warning", document_id=doc.id,
                        message=(f"{doc.filename} ({doc.period_start} to {doc.period_end}) falls partly outside "
                                 f"the reporting window {win_start} to {win_end}."),
                        suggested_fix="Pro-rate the quantity to the window, with a justification on the Review step.",
                        fingerprint=f"V2W:{doc.id}:part:{doc.period_start}:{doc.period_end}",
                    ))
    return findings


# ---------------------------------------------------------------------
# V3 duplicates
# ---------------------------------------------------------------------


def find_exact_duplicate(db: Session, document_hash: str) -> Optional[DocumentRecord]:
    return db.query(DocumentRecord).filter_by(document_hash=document_hash).first()


def primary_quantity(doc: DocumentRecord) -> Optional[float]:
    for field in doc.extracted_fields:
        if field.canonical_value is not None and field.field_type not in (None, "see"):
            return field.canonical_value
    return None


def quantities_within(a: Optional[float], b: Optional[float], tolerance: float = NEAR_DUPLICATE_TOLERANCE) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    reference = max(abs(a), abs(b))
    return reference > 0 and abs(a - b) / reference <= tolerance


def _rule_v3(documents: List[DocumentRecord]) -> List[Finding]:
    findings = []
    keyed: Dict[Tuple[str, str, datetime.date], List[DocumentRecord]] = {}
    for doc in documents:
        if doc.supplier and doc.bill_number and doc.bill_date:
            keyed.setdefault((doc.supplier.strip().lower(), doc.bill_number.strip().lower(), doc.bill_date), []).append(doc)
    for (_, bill_number, bill_date), docs in keyed.items():
        docs.sort(key=lambda d: d.id)
        for i, first in enumerate(docs):
            for second in docs[i + 1:]:
                if first.document_hash == second.document_hash:
                    continue
                if quantities_within(primary_quantity(first), primary_quantity(second)):
                    findings.append(Finding(
                        rule_id="V3_DUPLICATE", severity="warning", document_id=second.id,
                        related_document_id=first.id,
                        message=(
                            f"Possible duplicate: {second.filename} and {first.filename} share supplier, bill number "
                            f"{bill_number.upper()} and date {bill_date}, with quantities within 0.5% but different file hashes "
                            "(for example a rescan)."
                        ),
                        suggested_fix="Keep one of the two records, or mark both as distinct bills.",
                        fingerprint=f"V3N:{first.id}:{second.id}",
                    ))
    return findings


# ---------------------------------------------------------------------
# Runner and lifecycle
# ---------------------------------------------------------------------


def workspace_documents(db: Session, workspace_id: str, include_excluded: bool = False) -> List[DocumentRecord]:
    query = db.query(DocumentRecord).filter(DocumentRecord.workspace_id == workspace_id)
    docs = query.order_by(DocumentRecord.id).all()
    return docs if include_excluded else [doc for doc in docs if not doc.is_excluded]


def _first_fuel_key(ws: Optional[Workspace]) -> Optional[str]:
    if ws is None or not ws.inputs_json:
        return None
    from app.modules.gaps.register import load_inputs

    inputs = load_inputs(ws)
    return inputs.fuels[0].fuel_key if inputs.fuels else None


def run_rules(db: Session, workspace_id: str) -> List[Finding]:
    ws = db.get(Workspace, workspace_id)
    documents = workspace_documents(db, workspace_id)
    window = (ws.reporting_start, ws.reporting_end) if ws else (None, None)
    findings = _rule_v1(db, documents, _first_fuel_key(ws))
    findings += _rule_v2(documents, window)
    findings += _rule_v3(documents)
    return findings


def validate_workspace(db: Session, workspace_id: str, actor: str = "system") -> List[Finding]:
    """Runs V1-V3 and reconciles with stored findings. Returns every stored finding."""
    produced = {f.fingerprint: f for f in run_rules(db, workspace_id)}
    stored = {row.fingerprint: row for row in db.query(ValidationFinding).filter_by(workspace_id=workspace_id).all()}

    for fp, finding in produced.items():
        row = stored.get(fp)
        if row is None:
            row = ValidationFinding(workspace_id=workspace_id, fingerprint=fp, status="open",
                                    **finding.model_dump(exclude={"id", "status", "fingerprint", "resolution_note",
                                                                  "resolved_by", "resolved_at"}))
            db.add(row)
            db.flush()
            write_audit(db, workspace_id=workspace_id, entity_type="finding", entity_id=row.id, action="opened",
                        actor=actor, new_value={"rule_id": row.rule_id, "severity": row.severity},
                        reason=row.message)
        else:
            row.message = finding.message
            row.suggested_fix = finding.suggested_fix
            if row.status == "resolved" and row.resolved_by == "system":
                row.status = "open"
                row.resolved_at = None
                row.resolved_by = None
                write_audit(db, workspace_id=workspace_id, entity_type="finding", entity_id=row.id,
                            action="reopened", actor=actor, old_value="resolved", new_value="open",
                            reason="Condition is present again.")

    for fp, row in stored.items():
        if fp not in produced and row.status == "open":
            row.status = "resolved"
            row.resolved_by = "system"
            row.resolved_at = utc_now()
            row.resolution_note = "Condition no longer present."
            write_audit(db, workspace_id=workspace_id, entity_type="finding", entity_id=row.id, action="resolved",
                        actor="system", old_value="open", new_value="resolved", reason=row.resolution_note)
    db.flush()
    return list_findings(db, workspace_id)


def list_findings(db: Session, workspace_id: str) -> List[Finding]:
    rows = db.query(ValidationFinding).filter_by(workspace_id=workspace_id).order_by(ValidationFinding.id).all()
    return [Finding.model_validate(row) for row in rows]


def open_errors(db: Session, workspace_id: str, document_id: Optional[int] = None, field_id: Optional[int] = None):
    query = db.query(ValidationFinding).filter_by(workspace_id=workspace_id, status="open", severity="error")
    rows = query.all()
    if field_id is not None:
        rows = [r for r in rows if r.field_id == field_id or (r.field_id is None and r.document_id == document_id)]
    elif document_id is not None:
        rows = [r for r in rows if r.document_id == document_id]
    return rows


def waive_finding(db: Session, finding_id: int, reason: str, reviewer: str) -> ValidationFinding:
    row = db.get(ValidationFinding, finding_id)
    if row is None:
        raise LookupError(f"Finding #{finding_id} not found.")
    if not (reason or "").strip() or not (reviewer or "").strip():
        raise FindingError("Waiving a finding needs a justification and the reviewer's name.")
    if row.status != "open":
        raise FindingError(f"Finding #{finding_id} is already {row.status}.")
    row.status = "waived"
    row.resolution_note = reason.strip()
    row.resolved_by = reviewer.strip()
    row.resolved_at = utc_now()
    write_audit(db, workspace_id=row.workspace_id, entity_type="finding", entity_id=row.id, action="waived",
                actor=reviewer, old_value="open", new_value="waived", reason=reason)
    db.flush()
    return row


def resolve_finding(db: Session, finding_id: int, action: str, actor: str, note: str = "") -> ValidationFinding:
    """User resolution. For near duplicates: keep_this / keep_other / distinct."""
    row = db.get(ValidationFinding, finding_id)
    if row is None:
        raise LookupError(f"Finding #{finding_id} not found.")
    if not (actor or "").strip():
        raise FindingError("The name of the person resolving the finding is required.")
    if row.status != "open":
        raise FindingError(f"Finding #{finding_id} is already {row.status}.")
    if action in {"keep_this", "keep_other"}:
        if row.related_document_id is None:
            raise FindingError("This finding has no second document to choose between.")
        drop_id = row.related_document_id if action == "keep_this" else row.document_id
        doc = db.get(DocumentRecord, drop_id)
        exclude_document(db, doc, actor, f"Duplicate resolution on finding #{row.id}: {note}".strip())
        note = f"Excluded {doc.filename}. {note}".strip()
    elif action == "distinct":
        note = f"Marked as distinct documents. {note}".strip()
    elif action != "resolve":
        raise FindingError(f"Unknown resolution action '{action}'.")
    if not note:
        raise FindingError("A note is required to resolve a finding.")
    row.status = "resolved"
    row.resolution_note = note
    row.resolved_by = actor.strip()
    row.resolved_at = utc_now()
    write_audit(db, workspace_id=row.workspace_id, entity_type="finding", entity_id=row.id, action="resolved",
                actor=actor, old_value="open", new_value="resolved", reason=note)
    db.flush()
    return row


def exclude_document(db: Session, doc: DocumentRecord, actor: str, reason: str) -> None:
    if doc.is_excluded:
        return
    doc.is_excluded = True
    write_audit(db, workspace_id=doc.workspace_id, entity_type="document", entity_id=doc.document_hash,
                action="excluded", actor=actor, old_value=False, new_value=True, reason=reason)
    db.flush()

"""Data-gap register and Incomplete status (Task 2).

``compute_gaps(db, workspace_id)`` compares the workspace inputs with the list
of required inputs for the steel-fastener pilot and keeps one ``DataGap`` row
per missing input. Every status change is written to the audit log.

Status rule: the workspace is **Incomplete** if any gap is ``open`` and has no
fallback; otherwise it is **Ready**. Open gaps that *do* have a fallback do
not make the workspace Incomplete, but the engine still refuses to run until
the user applies the fallback explicitly (Task 3) or enters a value.
"""

import datetime
import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Assumption, DataGap, Workspace
from app.modules.audit.log import write_audit
from app.modules.registry.factors import fallback_for, latest_version
from app.schemas.workspace import WorkspaceInputs

INCOMPLETE = "Incomplete"
READY = "Ready"

# Required-inputs list for the steel-fastener pilot (to confirm with the mentor).
FUEL_FACTOR_LABELS = {"ncv": "net calorific value", "ef": "emission factor", "of": "oxidation factor"}


class Gap(BaseModel):
    id: int
    input_key: str
    description: str
    fallback_available: bool
    fallback_allowed_flag: Optional[bool] = None
    status: str
    created_at: datetime.datetime
    resolved_at: Optional[datetime.datetime] = None
    resolved_by: Optional[str] = None

    model_config = {"from_attributes": True}


@dataclass
class Requirement:
    input_key: str
    description: str
    present: bool
    fallback_key: Optional[str] = None


class WorkspaceNotFound(LookupError):
    pass


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def get_workspace(db: Session, workspace_id: str) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise WorkspaceNotFound(f"Workspace '{workspace_id}' does not exist.")
    return ws


def get_or_create_workspace(db: Session, workspace_id: str) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        ws = Workspace(id=workspace_id, factor_set_version=latest_version(db), calc_status=INCOMPLETE)
        db.add(ws)
        db.flush()
        write_audit(db, workspace_id=workspace_id, entity_type="workspace", entity_id=workspace_id,
                    action="created", new_value={"factor_set_version": ws.factor_set_version})
    return ws


def load_inputs(ws: Workspace) -> WorkspaceInputs:
    if not ws.inputs_json:
        return WorkspaceInputs()
    return WorkspaceInputs.model_validate_json(ws.inputs_json)


def required_inputs(inputs: WorkspaceInputs) -> List[Requirement]:
    reqs: List[Requirement] = []
    prod = inputs.production
    reqs.append(Requirement("production.target_quantity", "Net production of the target product (t)", prod.target.present))
    if prod.utility_coverage != "dedicated":
        reqs.append(Requirement(
            "production.total_installation",
            "Total installation production (t) - needed because utilities are shared",
            prod.total_installation.present,
        ))

    if inputs.uses_fuel:
        if not inputs.fuels:
            reqs.append(Requirement("fuel.records", "Fuel quantity: add a fuel record, or declare that no fuel is used", False))
        for fuel in inputs.fuels:
            label = fuel.name or fuel.fuel_key.replace("_", " ")
            reqs.append(Requirement(f"fuel.{fuel.fuel_key}.quantity", f"Quantity of {label}", fuel.quantity.present))
            for attr, words in FUEL_FACTOR_LABELS.items():
                value = getattr(fuel, "oxidation_factor" if attr == "of" else attr)
                key = f"fuel.{fuel.fuel_key}.{attr}"
                reqs.append(Requirement(key, f"{label.capitalize()} {words}", value.present, fallback_key=key))

    reqs.append(Requirement("electricity.consumed", "Electricity consumed (MWh)", inputs.electricity.consumed.present))
    reqs.append(Requirement(
        "electricity.grid_ef", "Grid emission factor (tCO2/MWh)", inputs.electricity.grid_ef.present,
        fallback_key="electricity.grid_ef",
    ))

    if not inputs.precursors:
        reqs.append(Requirement("precursor.records", "Precursor quantity (t): add the supplier record", False))
    for prec in inputs.precursors:
        label = prec.name or prec.key.replace("_", " ")
        reqs.append(Requirement(f"precursor.{prec.key}.quantity", f"Quantity of precursor {label} (t)", prec.quantity.present))
        reqs.append(Requirement(
            f"precursor.{prec.key}.supplier_see", f"Supplier SEE for {label} (tCO2/t)", prec.supplier_see.present,
            fallback_key=f"precursor.{prec.key}.default_see",
        ))

    proc = inputs.process
    declared = (
        (proc.mode == "none" and bool((proc.reason or "").strip()))
        or (proc.mode == "sources" and bool(proc.sources) and all(_process_source_complete(s) for s in proc.sources))
    )
    reqs.append(Requirement(
        "process.declaration",
        "Process emissions: list the sources, or declare 'No process emissions' with a written reason",
        declared,
    ))
    return reqs


def _process_source_complete(src) -> bool:
    if src.method == "direct":
        return src.direct_tco2 is not None and src.direct_tco2 >= 0 and bool((src.evidence_ref or "").strip())
    return src.activity_quantity is not None and bool(src.activity_unit) and bool(src.factor_key)


def _active_assumptions(db: Session, workspace_id: str) -> Dict[str, Assumption]:
    rows = db.query(Assumption).filter_by(workspace_id=workspace_id, status="active").all()
    return {row.input_key: row for row in rows}


def _set_gap_status(db: Session, gap: DataGap, status: str, actor: str, reason: str) -> None:
    if gap.status == status:
        return
    old = gap.status
    gap.status = status
    if status == "open":
        gap.resolved_at = None
        gap.resolved_by = None
    else:
        gap.resolved_at = utc_now()
        gap.resolved_by = actor
    write_audit(db, workspace_id=gap.workspace_id, entity_type="gap", entity_id=gap.input_key,
                action="status_changed", actor=actor, old_value=old, new_value=status, reason=reason)


def compute_gaps(db: Session, workspace_id: str, actor: str = "system") -> List[Gap]:
    """Re-derives the gap list from the current inputs and returns the current gaps."""
    ws = get_workspace(db, workspace_id)
    inputs = load_inputs(ws)
    version = ws.factor_set_version or latest_version(db)
    assumptions = _active_assumptions(db, workspace_id)
    existing = {row.input_key: row for row in db.query(DataGap).filter_by(workspace_id=workspace_id).all()}
    current_keys = set()

    for req in required_inputs(inputs):
        current_keys.add(req.input_key)
        gap = existing.get(req.input_key)
        assumption = assumptions.get(req.input_key)

        if req.present:
            if assumption is not None:
                assumption.status = "withdrawn"
                write_audit(db, workspace_id=workspace_id, entity_type="assumption", entity_id=assumption.id,
                            action="withdrawn", actor=actor, old_value="active", new_value="withdrawn",
                            reason="A measured value was entered for this input.")
            if gap is not None:
                _set_gap_status(db, gap, "filled_by_user", actor, "Value provided in the workspace inputs.")
            continue

        fallback = fallback_for(db, req.fallback_key, version) if (req.fallback_key and version) else None
        available = bool(fallback is not None and fallback.allowed)
        if gap is None:
            gap = DataGap(workspace_id=workspace_id, input_key=req.input_key, description=req.description,
                          fallback_available=available, status="open")
            db.add(gap)
            db.flush()
            write_audit(db, workspace_id=workspace_id, entity_type="gap", entity_id=req.input_key,
                        action="opened", actor=actor, new_value={"fallback_available": available},
                        reason=req.description)
        else:
            gap.description = req.description
            if gap.fallback_available != available:
                write_audit(db, workspace_id=workspace_id, entity_type="gap", entity_id=req.input_key,
                            action="fallback_availability_changed", actor=actor,
                            old_value=gap.fallback_available, new_value=available)
                gap.fallback_available = available

        if assumption is not None:
            _set_gap_status(db, gap, "fallback_applied", actor, f"Assumption #{assumption.id} is active.")
        elif gap.status != "waived":
            _set_gap_status(db, gap, "open", actor, "Required input is missing.")

    # Inputs that are no longer required (e.g. a fuel was removed) close their gaps.
    for key, gap in existing.items():
        if key not in current_keys and gap.status == "open":
            _set_gap_status(db, gap, "filled_by_user", actor, "Input is no longer required.")
        if key not in current_keys and key in assumptions:
            assumptions[key].status = "withdrawn"
            write_audit(db, workspace_id=workspace_id, entity_type="assumption", entity_id=assumptions[key].id,
                        action="withdrawn", actor=actor, old_value="active", new_value="withdrawn",
                        reason="Input is no longer required.")

    db.flush()
    new_status = calc_status_from_rows(
        [row for row in db.query(DataGap).filter_by(workspace_id=workspace_id).all() if row.input_key in current_keys]
    )
    if ws.calc_status != new_status:
        write_audit(db, workspace_id=workspace_id, entity_type="workspace", entity_id=workspace_id,
                    action="calc_status_changed", actor=actor, old_value=ws.calc_status, new_value=new_status)
        ws.calc_status = new_status
    db.flush()
    return current_gaps(db, workspace_id)


def calc_status_from_rows(rows) -> str:
    blocking = [row for row in rows if row.status == "open" and not row.fallback_available]
    return INCOMPLETE if blocking else READY


def current_gaps(db: Session, workspace_id: str) -> List[Gap]:
    ws = get_workspace(db, workspace_id)
    keys = {req.input_key for req in required_inputs(load_inputs(ws))}
    version = ws.factor_set_version or latest_version(db)
    out = []
    for row in db.query(DataGap).filter_by(workspace_id=workspace_id).order_by(DataGap.id).all():
        if row.input_key not in keys or row.status == "filled_by_user":
            continue
        gap = Gap.model_validate(row)
        req_fallback = _fallback_key(row.input_key)
        if req_fallback and version:
            fb = fallback_for(db, req_fallback, version)
            gap.fallback_allowed_flag = None if fb is None else bool(fb.allowed)
        out.append(gap)
    return out


def _fallback_key(input_key: str) -> Optional[str]:
    if input_key.startswith("fuel.") and input_key.rsplit(".", 1)[-1] in FUEL_FACTOR_LABELS:
        return input_key
    if input_key == "electricity.grid_ef":
        return input_key
    if input_key.startswith("precursor.") and input_key.endswith(".supplier_see"):
        return input_key[: -len(".supplier_see")] + ".default_see"
    return None


def fallback_key_for(input_key: str) -> Optional[str]:
    return _fallback_key(input_key)


def blocking_gaps(gaps: List[Gap]) -> List[Gap]:
    return [gap for gap in gaps if gap.status == "open"]


def summary(db: Session, workspace_id: str) -> dict:
    ws = get_workspace(db, workspace_id)
    gaps = current_gaps(db, workspace_id)
    return {
        "workspace_id": workspace_id,
        "calc_status": ws.calc_status,
        "factor_set_version": ws.factor_set_version,
        "gaps": [json.loads(gap.model_dump_json()) for gap in gaps],
        "open_gaps": sum(1 for gap in gaps if gap.status == "open"),
        "pending_fallbacks": sum(1 for gap in gaps if gap.status == "open" and gap.fallback_available),
    }

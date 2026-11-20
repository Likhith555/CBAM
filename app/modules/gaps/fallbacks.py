"""Fallback values with widened uncertainty (Task 3).

A fallback is never applied silently: the user calls ``apply_fallback`` with a
reason. That creates an ``Assumption`` row, marks the gap ``fallback_applied``
and writes audit rows. ``build_engine_inputs`` then resolves every engine
input either from a measured value or from an active assumption; inputs that
come from a fallback carry ``source_type='fallback'`` and the register's own
uncertainty distribution.
"""

from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import Assumption, DataGap, EmissionFactor
from app.modules.audit.log import write_audit
from app.modules.calculation.engine import IncompleteInputError
from app.modules.gaps.register import (
    blocking_gaps,
    compute_gaps,
    fallback_key_for,
    get_workspace,
    load_inputs,
)
from app.modules.registry.factors import fallback_for, get_factor, get_factor_by_id
from app.schemas.workspace import (
    EngineFuel,
    EngineInputs,
    EnginePrecursor,
    EngineProcessSource,
    EngineValue,
    SourcedValue,
    UncertaintySpec,
    WorkspaceInputs,
)


class FallbackError(ValueError):
    """The fallback cannot be applied (not allowed, missing, or no open gap)."""


class FallbackNotAllowed(FallbackError):
    pass


def active_assumptions(db: Session, workspace_id: str) -> List[Assumption]:
    return (
        db.query(Assumption)
        .filter_by(workspace_id=workspace_id, status="active")
        .order_by(Assumption.id)
        .all()
    )


def apply_fallback(db: Session, workspace_id: str, input_key: str, reason: str, actor: str) -> Assumption:
    ws = get_workspace(db, workspace_id)
    if not (reason or "").strip():
        raise FallbackError("A reason is required: explain why measured data is unavailable.")
    if not (actor or "").strip():
        raise FallbackError("The name of the person applying the fallback is required.")
    gap = db.query(DataGap).filter_by(workspace_id=workspace_id, input_key=input_key).first()
    if gap is None or gap.status != "open":
        raise FallbackError(f"There is no open gap for '{input_key}'.")
    key = fallback_key_for(input_key)
    row = fallback_for(db, key, ws.factor_set_version) if key else None
    if row is None:
        raise FallbackError(f"No fallback exists in the register for '{input_key}'.")
    if not row.allowed:
        write_audit(db, workspace_id=workspace_id, entity_type="gap", entity_id=input_key,
                    action="fallback_rejected", actor=actor, new_value={"factor_id": row.id},
                    reason="Register row is not allowed as a fallback; the gap stays open.")
        db.flush()
        raise FallbackNotAllowed(
            f"The register fallback for '{input_key}' (factor #{row.id}) has allowed = false and cannot be applied."
        )

    assumption = Assumption(
        workspace_id=workspace_id, input_key=input_key, value_used=row.value, unit=row.unit,
        factor_id=row.id, factor_set_version=row.factor_set_version, reason=reason.strip(),
        applied_by=actor.strip(), status="active",
    )
    db.add(assumption)
    db.flush()
    write_audit(db, workspace_id=workspace_id, entity_type="assumption", entity_id=assumption.id,
                action="fallback_applied", actor=actor, old_value=None,
                new_value={"input_key": input_key, "value": row.value, "unit": row.unit,
                           "factor_id": row.id, "factor_set_version": row.factor_set_version},
                reason=reason)
    compute_gaps(db, workspace_id, actor=actor)
    return assumption


def withdraw_assumption(db: Session, workspace_id: str, assumption_id: int, actor: str, reason: str) -> Assumption:
    row = db.get(Assumption, assumption_id)
    if row is None or row.workspace_id != workspace_id:
        raise FallbackError(f"Assumption #{assumption_id} not found.")
    if row.status != "active":
        raise FallbackError(f"Assumption #{assumption_id} is already {row.status}.")
    if not (reason or "").strip():
        raise FallbackError("A reason is required to withdraw an assumption.")
    row.status = "withdrawn"
    write_audit(db, workspace_id=workspace_id, entity_type="assumption", entity_id=row.id, action="withdrawn",
                actor=actor, old_value="active", new_value="withdrawn", reason=reason)
    db.flush()
    compute_gaps(db, workspace_id, actor=actor)
    return row


def reapply_for_version(db: Session, workspace_id: str, version: str, actor: str) -> None:
    """Moves active assumptions onto ``version``'s register rows (used when the factor set changes)."""
    for old in active_assumptions(db, workspace_id):
        key = fallback_key_for(old.input_key)
        row = fallback_for(db, key, version) if key else None
        old.status = "withdrawn"
        write_audit(db, workspace_id=workspace_id, entity_type="assumption", entity_id=old.id, action="withdrawn",
                    actor=actor, old_value="active", new_value="withdrawn",
                    reason=f"Factor set changed to {version}.")
        if row is not None and row.allowed:
            new = Assumption(workspace_id=workspace_id, input_key=old.input_key, value_used=row.value, unit=row.unit,
                             factor_id=row.id, factor_set_version=row.factor_set_version, reason=old.reason,
                             applied_by=actor, status="active")
            db.add(new)
            db.flush()
            write_audit(db, workspace_id=workspace_id, entity_type="assumption", entity_id=new.id,
                        action="fallback_applied", actor=actor,
                        new_value={"input_key": old.input_key, "value": row.value, "factor_id": row.id,
                                   "factor_set_version": row.factor_set_version},
                        reason=f"Re-applied on factor set {version}: {old.reason}")
    db.flush()


# ---------------------------------------------------------------------
# Resolution into engine inputs
# ---------------------------------------------------------------------


def _register_uncertainty(row: EmissionFactor) -> UncertaintySpec:
    if row.uncertainty_type == "triangular":
        return UncertaintySpec(kind="triangular", low=row.uncertainty_low, high=row.uncertainty_high)
    if row.uncertainty_type == "relative":
        return UncertaintySpec(kind="relative", rel=row.uncertainty_rel)
    return UncertaintySpec()


def _measured(sv: SourcedValue, default_unit: str) -> EngineValue:
    spec = UncertaintySpec(kind="relative", rel=sv.rel_uncertainty) if sv.rel_uncertainty is not None else UncertaintySpec()
    return EngineValue(value=float(sv.value), unit=sv.unit or default_unit, source_type="measured", uncertainty=spec)


def _from_assumption(db: Session, assumption: Assumption, version_override: Optional[str] = None) -> EngineValue:
    row = get_factor_by_id(db, assumption.factor_id)
    if version_override is not None:
        key = fallback_key_for(assumption.input_key)
        pinned = get_factor(db, key, version_override) if key else None
        if pinned is not None:
            row = pinned
    value = row.value if version_override is not None else assumption.value_used
    return EngineValue(value=value, unit=row.unit, source_type="fallback", factor_id=row.id,
                       factor_set_version=row.factor_set_version, uncertainty=_register_uncertainty(row))


def build_engine_inputs(
    db: Session,
    workspace_id: str,
    inputs: Optional[WorkspaceInputs] = None,
    assumptions: Optional[List[Assumption]] = None,
    factor_set_version: Optional[str] = None,
    check_gaps: bool = True,
) -> EngineInputs:
    """Resolves every value or raises ``IncompleteInputError`` with the open gaps."""
    ws = get_workspace(db, workspace_id)
    if check_gaps:
        gaps = compute_gaps(db, workspace_id)
        open_gaps = blocking_gaps(gaps)
        if open_gaps:
            raise IncompleteInputError(open_gaps)
    inputs = inputs if inputs is not None else load_inputs(ws)
    assumptions = assumptions if assumptions is not None else active_assumptions(db, workspace_id)
    by_key: Dict[str, Assumption] = {a.input_key: a for a in assumptions}
    version = factor_set_version or ws.factor_set_version
    missing: List[str] = []

    def resolve(sv: SourcedValue, key: str, default_unit: str) -> Optional[EngineValue]:
        if sv.present:
            return _measured(sv, default_unit)
        if key in by_key:
            return _from_assumption(db, by_key[key], factor_set_version)
        missing.append(key)
        return None

    prod = inputs.production
    target = resolve(prod.target, "production.target_quantity", "t")
    total = None
    if prod.utility_coverage != "dedicated":
        total = resolve(prod.total_installation, "production.total_installation", "t")
    elif prod.total_installation.present:
        total = _measured(prod.total_installation, "t")

    fuels = []
    if inputs.uses_fuel:
        for fuel in inputs.fuels:
            base = f"fuel.{fuel.fuel_key}"
            parts = {
                "quantity": resolve(fuel.quantity, f"{base}.quantity", "L"),
                "ncv": resolve(fuel.ncv, f"{base}.ncv", "GJ/L"),
                "ef": resolve(fuel.ef, f"{base}.ef", "tCO2/GJ"),
                "oxidation_factor": resolve(fuel.oxidation_factor, f"{base}.of", "fraction"),
            }
            if all(parts.values()):
                fuels.append(EngineFuel(key=fuel.fuel_key, name=fuel.name or fuel.fuel_key, is_shared=fuel.is_shared, **parts))

    electricity = resolve(inputs.electricity.consumed, "electricity.consumed", "MWh")
    grid = resolve(inputs.electricity.grid_ef, "electricity.grid_ef", "tCO2/MWh")

    precursors = []
    for prec in inputs.precursors:
        qty = resolve(prec.quantity, f"precursor.{prec.key}.quantity", "t")
        see = resolve(prec.supplier_see, f"precursor.{prec.key}.supplier_see", "tCO2/t")
        if qty and see:
            precursors.append(EnginePrecursor(key=prec.key, name=prec.name or prec.key, quantity=qty, see=see,
                                              directly_attributable=prec.directly_attributable))

    process_sources = []
    if inputs.process.mode == "sources":
        for src in inputs.process.sources:
            if src.method == "direct":
                spec = UncertaintySpec(kind="relative", rel=src.rel_uncertainty) if src.rel_uncertainty is not None else UncertaintySpec()
                process_sources.append(EngineProcessSource(
                    name=src.name, method="direct", evidence_ref=src.evidence_ref, is_shared=src.is_shared,
                    direct=EngineValue(value=float(src.direct_tco2), unit="tCO2", uncertainty=spec),
                ))
            else:
                row = get_factor(db, src.factor_key, version) if src.factor_key else None
                if row is None:
                    missing.append(f"process.{src.name}.factor")
                    continue
                process_sources.append(EngineProcessSource(
                    name=src.name, method="activity", evidence_ref=src.evidence_ref, is_shared=src.is_shared,
                    activity=EngineValue(value=float(src.activity_quantity), unit=src.activity_unit),
                    factor=EngineValue(value=row.value, unit=row.unit, source_type="fallback" if row.is_fallback else "measured",
                                       factor_id=row.id, factor_set_version=row.factor_set_version,
                                       uncertainty=_register_uncertainty(row)),
                ))

    if missing:
        raise IncompleteInputError(missing)

    return EngineInputs(
        target_production=target,
        total_installation_production=total,
        utility_coverage=prod.utility_coverage,
        includes_indirect=inputs.includes_indirect,
        fuels=fuels,
        process_sources=process_sources,
        electricity=electricity,
        grid_ef=grid,
        electricity_is_shared=inputs.electricity.is_shared,
        precursors=precursors,
    )

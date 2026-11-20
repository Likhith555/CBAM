"""Runs the Priority 1 engine for a server-side workspace and stores the result
on the current passport version, together with the factor ids, factor-set
version and Monte Carlo seed needed to reproduce it exactly."""

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Assumption, PassportVersion
from app.modules.audit.log import write_audit
from app.modules.calculation.engine import compute_emissions
from app.modules.gaps.fallbacks import build_engine_inputs
from app.modules.registry.factors import get_factor_by_id, to_read
from app.modules.review.status import current_version, sync_passport
from app.modules.uncertainty.monte_carlo import simulate_engine
from app.schemas.workspace import WorkspaceInputs


def _assemble(db: Session, engine_inputs, seed: int, iterations: int, version: PassportVersion,
              factor_set_version: Optional[str], assumptions: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = compute_emissions(engine_inputs)
    mc = simulate_engine(engine_inputs, iterations=iterations, seed=seed)
    factors = []
    for factor_id in result.factor_ids_used:
        row = get_factor_by_id(db, factor_id)
        if row is not None:
            factors.append(json.loads(to_read(row).model_dump_json()))
    return {
        "version_number": version.version_number,
        "factor_set_version": factor_set_version,
        "result": json.loads(result.model_dump_json()),
        "uncertainty": json.loads(mc.model_dump_json()),
        "mc_seed": seed,
        "factors_used": factors,
        "assumptions": assumptions,
        "engine_inputs": json.loads(engine_inputs.model_dump_json()),
    }


def _assumption_dicts(rows: List[Assumption]) -> List[Dict[str, Any]]:
    out = []
    for a in rows:
        out.append({
            "id": a.id, "input_key": a.input_key, "value_used": a.value_used, "unit": a.unit,
            "factor_id": a.factor_id, "factor_set_version": a.factor_set_version, "reason": a.reason,
            "applied_by": a.applied_by, "applied_at": str(a.applied_at),
        })
    return out


def assumption_rows(db: Session, rows: List[Assumption]) -> List[Dict[str, Any]]:
    out = []
    for item in _assumption_dicts(rows):
        factor = get_factor_by_id(db, item["factor_id"])
        if factor is not None:
            item.update({"source_title": factor.source_title, "publisher": factor.publisher,
                         "source_url": factor.source_url, "year": factor.year})
        out.append(item)
    return out


def run_workspace_calculation(db: Session, workspace_id: str, seed: Optional[int] = None,
                              iterations: Optional[int] = None, actor: str = "system") -> Dict[str, Any]:
    """Raises ``IncompleteInputError`` when required inputs are missing."""
    seed = settings.MC_DEFAULT_SEED if seed is None else seed
    iterations = iterations or settings.MC_DEFAULT_ITERATIONS
    version = sync_passport(db, workspace_id)
    engine_inputs = build_engine_inputs(db, workspace_id)
    active = db.query(Assumption).filter_by(workspace_id=workspace_id, status="active").order_by(Assumption.id).all()
    payload = _assemble(db, engine_inputs, seed, iterations, version, version.factor_set_version,
                        assumption_rows(db, active))
    version.result_json = json.dumps(payload)
    version.factor_ids_json = json.dumps(payload["result"]["factor_ids_used"])
    version.mc_seed = seed
    write_audit(db, workspace_id=workspace_id, entity_type="passport", entity_id=version.id,
                action="calculated", actor=actor,
                new_value={"see": payload["result"]["see_tco2_per_t"],
                           "data_quality_label": payload["result"]["data_quality_label"],
                           "factor_set_version": version.factor_set_version, "seed": seed})
    db.flush()
    return payload


def rerun_version(db: Session, workspace_id: str, version_number: int) -> Dict[str, Any]:
    """Re-runs a stored passport version from its own snapshot and pinned factor set."""
    version = (
        db.query(PassportVersion)
        .filter_by(workspace_id=workspace_id, version_number=version_number)
        .first()
    )
    if version is None:
        raise LookupError(f"Passport version {version_number} not found.")
    inputs = WorkspaceInputs.model_validate_json(version.inputs_snapshot_json or "{}")
    snap = json.loads(version.assumptions_snapshot_json or "[]")
    assumptions = [
        Assumption(id=a["id"], workspace_id=workspace_id, input_key=a["input_key"], value_used=a["value_used"],
                   unit=a["unit"], factor_id=a["factor_id"], factor_set_version=a["factor_set_version"],
                   reason=a["reason"], applied_by=a["applied_by"], status="active")
        for a in snap
    ]
    engine_inputs = build_engine_inputs(db, workspace_id, inputs=inputs, assumptions=assumptions,
                                        factor_set_version=version.factor_set_version, check_gaps=False)
    seed = version.mc_seed if version.mc_seed is not None else settings.MC_DEFAULT_SEED
    stored = json.loads(version.result_json) if version.result_json else {}
    iterations = (stored.get("uncertainty") or {}).get("iterations") or settings.MC_DEFAULT_ITERATIONS
    return _assemble(db, engine_inputs, seed, iterations, version,
                     version.factor_set_version, snap)


def latest_result(db: Session, workspace_id: str) -> Optional[Dict[str, Any]]:
    version = current_version(db, workspace_id)
    if version is None or not version.result_json:
        return None
    return json.loads(version.result_json)

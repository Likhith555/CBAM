"""Fallback values with widened uncertainty (Task 3)."""

import pytest

from app.db.models import Assumption, AuditLog, DataGap
from app.modules.calculation.workspace_calc import run_workspace_calculation
from app.modules.gaps.fallbacks import (
    FallbackError,
    FallbackNotAllowed,
    apply_fallback,
    build_engine_inputs,
    reapply_for_version,
    withdraw_assumption,
)
from app.modules.registry.factors import FactorSpec, create_factor_set
from tests.helpers import benchmark_inputs, set_inputs

CITATION = dict(source_title="Test default table", publisher="Test publisher",
                source_url="https://example.org/defaults.pdf", year=2025)


def _no_grid(db, workspace):
    data = benchmark_inputs()
    data["electricity"]["grid_ef"] = {"value": None}
    set_inputs(db, workspace, data)


def test_no_grid_factor_with_allowed_fallback(db, workspace):
    _no_grid(db, workspace)
    assumption = apply_fallback(db, workspace.id, "electricity.grid_ef", "No supplier-specific factor", "Harish")
    assert workspace.calc_status == "Ready"
    rows = db.query(Assumption).filter_by(workspace_id=workspace.id).all()
    assert len(rows) == 1
    assert (rows[0].value_used, rows[0].unit, rows[0].factor_set_version) == (0.716, "tCO2/MWh", "2026.1")
    assert rows[0].applied_by == "Harish" and rows[0].reason
    gap = db.query(DataGap).filter_by(input_key="electricity.grid_ef").one()
    assert gap.status == "fallback_applied"

    payload = run_workspace_calculation(db, workspace.id)
    assert payload["result"]["data_quality_label"] in {"B", "C"}
    assert payload["result"]["see_tco2_per_t"] == pytest.approx(2.28507552, rel=1e-9)
    assert payload["assumptions"][0]["input_key"] == "electricity.grid_ef"
    assert payload["assumptions"][0]["source_url"].startswith("https://cea.nic.in")
    assert [f["key"] for f in payload["factors_used"]] == ["electricity.grid_ef"]
    assert db.query(AuditLog).filter_by(entity_type="assumption", action="fallback_applied").count() == 1
    assert assumption.id == rows[0].id


def test_fallback_not_allowed_keeps_gap_open(db, workspace):
    create_factor_set(db, "2026.2", [FactorSpec(
        key="precursor.wire_rod.default_see", value=2.2, unit="tCO2/t", allowed=False, **CITATION)])
    workspace.factor_set_version = "2026.2"
    data = benchmark_inputs()
    data["precursors"][0]["supplier_see"] = {"value": None}
    gaps = set_inputs(db, workspace, data)
    assert gaps[0].fallback_available is False and gaps[0].fallback_allowed_flag is False
    with pytest.raises(FallbackNotAllowed):
        apply_fallback(db, workspace.id, "precursor.wire_rod.supplier_see", "No supplier data", "Harish")
    gap = db.query(DataGap).filter_by(input_key="precursor.wire_rod.supplier_see").one()
    assert gap.status == "open"
    assert workspace.calc_status == "Incomplete"
    assert db.query(Assumption).count() == 0
    assert db.query(AuditLog).filter_by(action="fallback_rejected").count() == 1


def test_apply_fallback_validation(db, workspace):
    _no_grid(db, workspace)
    with pytest.raises(FallbackError, match="reason"):
        apply_fallback(db, workspace.id, "electricity.grid_ef", " ", "Harish")
    with pytest.raises(FallbackError, match="name"):
        apply_fallback(db, workspace.id, "electricity.grid_ef", "why", "")
    with pytest.raises(FallbackError, match="no open gap"):
        apply_fallback(db, workspace.id, "fuel.furnace_oil.ncv", "why", "Harish")
    data = benchmark_inputs()
    data["production"]["target"] = {"value": None}
    set_inputs(db, workspace, data)
    with pytest.raises(FallbackError, match="No fallback exists"):
        apply_fallback(db, workspace.id, "production.target_quantity", "why", "Harish")


def test_measured_value_withdraws_assumption(db, workspace):
    _no_grid(db, workspace)
    apply_fallback(db, workspace.id, "electricity.grid_ef", "pending letter", "Harish")
    set_inputs(db, workspace, benchmark_inputs())
    row = db.query(Assumption).one()
    assert row.status == "withdrawn"
    assert db.query(DataGap).filter_by(input_key="electricity.grid_ef").one().status == "filled_by_user"


def test_withdraw_assumption_reopens_gap(db, workspace):
    _no_grid(db, workspace)
    a = apply_fallback(db, workspace.id, "electricity.grid_ef", "pending letter", "Harish")
    with pytest.raises(FallbackError, match="reason"):
        withdraw_assumption(db, workspace.id, a.id, "Harish", "")
    withdraw_assumption(db, workspace.id, a.id, "Harish", "Letter received")
    assert db.query(DataGap).filter_by(input_key="electricity.grid_ef").one().status == "open"
    with pytest.raises(FallbackError, match="already"):
        withdraw_assumption(db, workspace.id, a.id, "Harish", "again")
    with pytest.raises(FallbackError, match="not found"):
        withdraw_assumption(db, workspace.id, 999, "Harish", "x")


def test_reapply_on_new_factor_set(db, workspace):
    _no_grid(db, workspace)
    apply_fallback(db, workspace.id, "electricity.grid_ef", "No supplier factor", "Harish")
    create_factor_set(db, "2026.2", [FactorSpec(key="electricity.grid_ef", value=0.727, unit="tCO2/MWh",
                                                uncertainty_type="relative", uncertainty_rel=0.1, **CITATION)])
    workspace.factor_set_version = "2026.2"
    reapply_for_version(db, workspace.id, "2026.2", "Harish")
    active = db.query(Assumption).filter_by(status="active").one()
    assert active.value_used == 0.727 and active.factor_set_version == "2026.2"
    assert active.reason == "No supplier factor"


def test_fallback_inputs_carry_register_uncertainty(db, workspace):
    data = benchmark_inputs()
    data["fuels"][0]["oxidation_factor"] = {"value": None}
    data["electricity"]["grid_ef"] = {"value": None}
    set_inputs(db, workspace, data)
    apply_fallback(db, workspace.id, "fuel.furnace_oil.of", "Default oxidation", "Harish")
    apply_fallback(db, workspace.id, "electricity.grid_ef", "Default grid", "Harish")
    inputs = build_engine_inputs(db, workspace.id)
    assert inputs.grid_ef.source_type == "fallback"
    assert inputs.grid_ef.uncertainty.kind == "relative" and inputs.grid_ef.uncertainty.rel == 0.10
    of = inputs.fuels[0].oxidation_factor
    assert of.uncertainty.kind == "triangular" and (of.uncertainty.low, of.uncertainty.high) == (0.98, 1.0)
    assert inputs.electricity.source_type == "measured"


def test_process_sources_resolve_register_factor(db, workspace):
    create_factor_set(db, "2026.2", [FactorSpec(key="process.lime.ef", value=0.75, unit="tCO2/t",
                                                uncertainty_type="relative", uncertainty_rel=0.2, **CITATION)])
    workspace.factor_set_version = "2026.2"
    data = benchmark_inputs()
    data["process"] = {"mode": "sources", "sources": [
        {"name": "Lime", "method": "activity", "activity_quantity": 4.0, "activity_unit": "t", "factor_key": "process.lime.ef"},
        {"name": "Pickling acid", "method": "direct", "direct_tco2": 1.0, "evidence_ref": "Report 4", "rel_uncertainty": 0.1},
    ]}
    set_inputs(db, workspace, data)
    payload = run_workspace_calculation(db, workspace.id)
    assert payload["result"]["process_raw_tco2"] == pytest.approx(4.0)
    assert "process.lime.ef" in [f["key"] for f in payload["factors_used"]]

    data["process"]["sources"][0]["factor_key"] = "process.unknown"
    set_inputs(db, workspace, data)
    from app.modules.calculation.engine import IncompleteInputError
    with pytest.raises(IncompleteInputError):
        build_engine_inputs(db, workspace.id)

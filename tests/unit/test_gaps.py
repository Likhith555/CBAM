"""Data-gap register and Incomplete status (Task 2)."""

import pytest

from app.db.models import AuditLog, DataGap
from app.modules.calculation.engine import IncompleteInputError
from app.modules.calculation.workspace_calc import run_workspace_calculation
from app.modules.gaps.register import (
    INCOMPLETE,
    READY,
    WorkspaceNotFound,
    compute_gaps,
    fallback_key_for,
    get_workspace,
    required_inputs,
    summary,
)
from app.schemas.workspace import WorkspaceInputs
from tests.helpers import benchmark_inputs, set_inputs


def test_fully_measured_benchmark_is_ready(db, workspace):
    assert compute_gaps(db, workspace.id) == []
    assert workspace.calc_status == READY


def test_missing_production_is_incomplete_and_engine_refuses(db, workspace):
    data = benchmark_inputs()
    data["production"]["target"] = {"value": None}
    gaps = set_inputs(db, workspace, data)
    assert [g.input_key for g in gaps] == ["production.target_quantity"]
    assert gaps[0].fallback_available is False and gaps[0].status == "open"
    assert workspace.calc_status == INCOMPLETE
    with pytest.raises(IncompleteInputError):
        run_workspace_calculation(db, workspace.id)


def test_missing_grid_factor_with_fallback_does_not_block(db, workspace):
    data = benchmark_inputs()
    data["electricity"]["grid_ef"] = {"value": None}
    gaps = set_inputs(db, workspace, data)
    assert [(g.input_key, g.fallback_available) for g in gaps] == [("electricity.grid_ef", True)]
    assert workspace.calc_status == READY
    # Ready, but still not silently filled: the engine waits for an explicit "Apply fallback".
    with pytest.raises(IncompleteInputError):
        run_workspace_calculation(db, workspace.id)
    assert summary(db, workspace.id)["pending_fallbacks"] == 1


def test_filling_a_gap_updates_status_and_is_audited(db, workspace):
    data = benchmark_inputs()
    data["production"]["total_installation"] = {"value": None}
    set_inputs(db, workspace, data)
    assert workspace.calc_status == INCOMPLETE
    gaps = set_inputs(db, workspace, benchmark_inputs())
    assert gaps == []
    assert workspace.calc_status == READY
    row = db.query(DataGap).filter_by(workspace_id=workspace.id, input_key="production.total_installation").one()
    assert row.status == "filled_by_user" and row.resolved_at is not None
    actions = [(r.action, r.old_value, r.new_value) for r in db.query(AuditLog).filter_by(entity_type="gap")]
    assert ("opened", None, '{"fallback_available": false}') in actions
    assert ("status_changed", "open", "filled_by_user") in actions
    ws_actions = [(r.old_value, r.new_value) for r in db.query(AuditLog).filter_by(action="calc_status_changed")]
    assert (READY, INCOMPLETE) in ws_actions and (INCOMPLETE, READY) in ws_actions


def test_dedicated_utilities_do_not_need_installation_total(db, workspace):
    data = benchmark_inputs()
    data["production"]["utility_coverage"] = "dedicated"
    data["production"]["total_installation"] = {"value": None}
    assert set_inputs(db, workspace, data) == []


def test_required_list_for_empty_workspace():
    keys = [r.input_key for r in required_inputs(WorkspaceInputs())]
    assert keys == [
        "production.target_quantity", "production.total_installation", "fuel.records",
        "electricity.consumed", "electricity.grid_ef", "precursor.records", "process.declaration",
    ]
    no_fuel = WorkspaceInputs(uses_fuel=False)
    assert "fuel.records" not in [r.input_key for r in required_inputs(no_fuel)]


def test_process_declaration_rules(db, workspace):
    data = benchmark_inputs()
    data["process"] = {"mode": "none", "reason": "  "}
    assert [g.input_key for g in set_inputs(db, workspace, data)] == ["process.declaration"]
    data["process"] = {"mode": "sources", "sources": [{"name": "Pickling", "method": "direct", "direct_tco2": 2.0}]}
    assert [g.input_key for g in set_inputs(db, workspace, data)] == ["process.declaration"]  # evidence missing
    data["process"]["sources"][0]["evidence_ref"] = "Lab report 17"
    assert set_inputs(db, workspace, data) == []
    data["process"] = {"mode": "sources", "sources": [
        {"name": "Lime", "method": "activity", "activity_quantity": 3, "activity_unit": "t", "factor_key": "x"}]}
    assert set_inputs(db, workspace, data) == []


def test_removed_inputs_close_their_gaps(db, workspace):
    data = benchmark_inputs()
    data["fuels"][0]["quantity"] = {"value": None}
    set_inputs(db, workspace, data)
    data = benchmark_inputs(uses_fuel=False, fuels=[])
    assert set_inputs(db, workspace, data) == []
    row = db.query(DataGap).filter_by(input_key="fuel.furnace_oil.quantity").one()
    assert row.status == "filled_by_user"


def test_precursor_supplier_see_has_no_fallback_without_register_default(db, workspace):
    data = benchmark_inputs()
    data["precursors"][0]["supplier_see"] = {"value": None}
    gaps = set_inputs(db, workspace, data)
    assert [(g.input_key, g.fallback_available) for g in gaps] == [("precursor.wire_rod.supplier_see", False)]
    assert gaps[0].fallback_allowed_flag is None
    assert workspace.calc_status == INCOMPLETE


def test_fallback_key_mapping():
    assert fallback_key_for("fuel.diesel.ncv") == "fuel.diesel.ncv"
    assert fallback_key_for("electricity.grid_ef") == "electricity.grid_ef"
    assert fallback_key_for("precursor.wire_rod.supplier_see") == "precursor.wire_rod.default_see"
    assert fallback_key_for("production.target_quantity") is None


def test_unknown_workspace(db):
    with pytest.raises(WorkspaceNotFound):
        get_workspace(db, "nope")

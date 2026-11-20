"""Versioned emission-factor register (Task 5)."""

import pytest
from pydantic import ValidationError

from app.db.models import EmissionFactor, FactorSet, ImmutableFactorError
from app.modules.calculation.workspace_calc import rerun_version, run_workspace_calculation
from app.modules.gaps.fallbacks import apply_fallback
from app.modules.registry.factors import (
    FactorSetError,
    FactorSpec,
    create_factor_set,
    fallback_for,
    get_factor,
    latest_version,
    list_versions,
    resolve_factors,
)
from tests.helpers import benchmark_inputs, set_inputs

CEA_V20 = dict(
    source_title="CO2 Baseline Database for the Indian Power Sector, User Guide Version 20.0 (Dec 2024), Annexure-I, FY 2023-24",
    publisher="Central Electricity Authority, Ministry of Power, Government of India",
    source_url="https://cea.nic.in/wp-content/uploads/2021/03/User_Guide_Version_20.0.pdf",
    year=2024,
)


def grid_2026_2(**kw):
    data = dict(key="electricity.grid_ef", value=0.727, unit="tCO2/MWh", uncertainty_type="relative",
                uncertainty_rel=0.10, **CEA_V20)
    data.update(kw)
    return FactorSpec(**data)


def test_seed_has_citations(db):
    factors = resolve_factors(db, "2026.1")
    assert {"fuel.furnace_oil.ncv", "fuel.furnace_oil.ef", "electricity.grid_ef",
            "fuel.natural_gas.ef", "fuel.diesel.ef"} <= set(factors)
    for row in factors.values():
        assert row.source_title and row.publisher and row.year
        assert row.source_url.startswith("https://")
    assert factors["fuel.furnace_oil.ncv"].value == 0.0402
    assert factors["fuel.furnace_oil.ef"].value == 0.0774
    assert factors["electricity.grid_ef"].value == 0.716
    assert "precursor.wire_rod.supplier_see" not in factors


def test_update_in_place_is_rejected(db):
    row = get_factor(db, "electricity.grid_ef", "2026.1")
    row.value = 0.9
    with pytest.raises(ImmutableFactorError):
        db.flush()
    db.rollback()


def test_delete_is_rejected(db):
    row = get_factor(db, "electricity.grid_ef", "2026.1")
    db.delete(row)
    with pytest.raises(ImmutableFactorError):
        db.flush()
    db.rollback()
    factor_set = db.get(FactorSet, "2026.1")
    factor_set.notes = "edited"
    with pytest.raises(ImmutableFactorError):
        db.flush()
    db.rollback()


def test_status_can_only_move_to_superseded(db):
    row = get_factor(db, "electricity.grid_ef", "2026.1")
    row.status = "retired"
    with pytest.raises(ImmutableFactorError):
        db.flush()
    db.rollback()


def test_new_set_supersedes_and_versions_resolve(db):
    create_factor_set(db, "2026.2", [grid_2026_2()], notes="CEA v20")
    db.commit()
    assert list_versions(db) == ["2026.1", "2026.2"]
    assert latest_version(db) == "2026.2"
    old = get_factor(db, "electricity.grid_ef", "2026.1")
    new = get_factor(db, "electricity.grid_ef", "2026.2")
    assert old.value == 0.716 and old.status == "superseded"
    assert new.value == 0.727 and new.status == "active"
    # unchanged keys are inherited from the older set
    assert get_factor(db, "fuel.furnace_oil.ef", "2026.2").factor_set_version == "2026.1"
    assert get_factor(db, "electricity.grid_ef", "2025.9") is None


def test_factor_set_rules(db):
    with pytest.raises(FactorSetError, match="already exists"):
        create_factor_set(db, "2026.1", [grid_2026_2()])
    with pytest.raises(FactorSetError, match="newer"):
        create_factor_set(db, "2025.1", [grid_2026_2()])
    with pytest.raises(FactorSetError, match="twice"):
        create_factor_set(db, "2026.3", [grid_2026_2(), grid_2026_2()])


def test_factor_spec_needs_citation_and_valid_uncertainty():
    with pytest.raises(ValidationError):
        FactorSpec(key="x.y", value=1, unit="t", source_title="", publisher="p", source_url="https://a.b", year=2020)
    with pytest.raises(ValidationError):
        grid_2026_2(source_url="not a url")
    with pytest.raises(ValidationError):
        grid_2026_2(uncertainty_type="relative", uncertainty_rel=None)
    with pytest.raises(ValidationError):
        grid_2026_2(uncertainty_type="triangular", uncertainty_low=0.8, uncertainty_high=0.9)
    with pytest.raises(ValidationError):
        grid_2026_2(uncertainty_type="triangular", uncertainty_low=None, uncertainty_high=None)
    with pytest.raises(ValidationError):
        grid_2026_2(value=0.7, uncertainty_type="triangular", uncertainty_low=0.7, uncertainty_high=0.7)


def test_fallback_for_ignores_non_fallback_rows(db):
    create_factor_set(db, "2026.2", [grid_2026_2(key="process.lime.ef", unit="tCO2/t", is_fallback=False)])
    assert fallback_for(db, "process.lime.ef", "2026.2") is None
    assert fallback_for(db, "electricity.grid_ef", "2026.2") is not None


def test_passport_on_2026_1_unchanged_after_2026_2(db, workspace):
    data = benchmark_inputs()
    data["electricity"]["grid_ef"] = {"value": None}
    set_inputs(db, workspace, data)
    apply_fallback(db, workspace.id, "electricity.grid_ef", "No supplier-specific factor", "Harish")
    first = run_workspace_calculation(db, workspace.id)
    db.commit()
    assert first["factor_set_version"] == "2026.1"

    create_factor_set(db, "2026.2", [grid_2026_2()])
    db.commit()
    again = rerun_version(db, workspace.id, first["version_number"])
    assert again["result"]["see_tco2_per_t"] == first["result"]["see_tco2_per_t"]
    assert again["uncertainty"] == first["uncertainty"]
    # The live workspace is still pinned to 2026.1, so a fresh run gives the same SEE as well.
    assert run_workspace_calculation(db, workspace.id)["result"]["see_tco2_per_t"] == first["result"]["see_tco2_per_t"]
    used = {f["key"]: f for f in first["factors_used"]}
    assert used["electricity.grid_ef"]["factor_set_version"] == "2026.1"
    assert db.query(EmissionFactor).count() == 14

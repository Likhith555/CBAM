"""Engine test vectors (Task 6 / Task 7). Benchmark: 500 t product, 625 t installation, alpha = 0.8."""

import pytest

from app.modules.calculation.engine import (
    EngineInputError,
    IncompleteInputError,
    compute_emissions,
    data_quality_label,
)
from app.modules.gaps.fallbacks import build_engine_inputs
from app.schemas.workspace import EngineProcessSource, EngineValue
from tests.helpers import benchmark_engine_inputs, benchmark_inputs, ev, set_inputs

REL = 1e-9


def test_benchmark_of_1():
    r = compute_emissions(benchmark_engine_inputs())
    assert r.alpha == pytest.approx(0.8, rel=REL)
    assert r.scope1_raw_tco2 == pytest.approx(46.6722, rel=REL)
    assert r.scope1_allocated_tco2 == pytest.approx(37.33776, rel=REL)
    assert r.scope2_allocated_tco2 == pytest.approx(143.2, rel=REL)
    assert r.precursor_tco2 == pytest.approx(962.0, rel=REL)
    assert r.total_tco2 == pytest.approx(1142.53776, rel=REL)
    assert r.see_tco2_per_t == pytest.approx(2.28507552, rel=REL)
    assert r.data_quality_label == "A"
    assert r.process_raw_tco2 == 0.0


def test_benchmark_of_099():
    r = compute_emissions(benchmark_engine_inputs(of=0.99))
    assert r.scope1_raw_tco2 == pytest.approx(46.205478, rel=REL)
    assert r.scope1_allocated_tco2 == pytest.approx(36.9643824, rel=REL)
    assert r.total_tco2 == pytest.approx(1142.1643824, rel=REL)
    assert r.see_tco2_per_t == pytest.approx(2.2843287648, rel=REL)


def test_dedicated_utilities_alpha_1():
    r = compute_emissions(benchmark_engine_inputs(dedicated=True, total_installation_production=None))
    assert r.alpha == 1.0
    assert r.total_tco2 == pytest.approx(1187.6722, rel=REL)
    assert r.see_tco2_per_t == pytest.approx(2.3753444, rel=REL)


def test_breakdown_per_fuel_and_process():
    process = [
        EngineProcessSource(name="Pickling", method="direct", direct=ev(10.0, "tCO2")),
        EngineProcessSource(name="Lime", method="activity", activity=ev(20.0, "t"),
                            factor=ev(0.5, "tCO2/t", source_type="fallback", factor_id=99)),
    ]
    r = compute_emissions(benchmark_engine_inputs(process_sources=process))
    assert r.process_raw_tco2 == pytest.approx(20.0, rel=REL)
    assert r.scope1_raw_tco2 == pytest.approx(66.6722, rel=REL)
    # alpha applies to the combined fuel + process total
    assert r.scope1_allocated_tco2 == pytest.approx(66.6722 * 0.8, rel=REL)
    assert [t.name for t in r.process] == ["Pickling", "Lime"]
    assert r.process[1].fallback_based is True
    assert 99 in r.factor_ids_used
    assert r.fuels[0].raw_tco2 == pytest.approx(46.6722, rel=REL)


def test_unit_conversions_in_engine():
    inputs = benchmark_engine_inputs()
    inputs.fuels[0].quantity = ev(15.0, "kL")
    inputs.electricity = ev(250.0, "MWh")
    assert compute_emissions(inputs).see_tco2_per_t == pytest.approx(2.28507552, rel=REL)


@pytest.mark.parametrize("target", [0.0])
def test_zero_production_raises(target):
    with pytest.raises(EngineInputError, match="greater than 0"):
        compute_emissions(benchmark_engine_inputs(target_production=ev(target, "t")))


def test_negative_quantity_raises():
    inputs = benchmark_engine_inputs()
    inputs.fuels[0].quantity = ev(-1.0, "L")
    with pytest.raises(EngineInputError, match="negative"):
        compute_emissions(inputs)


@pytest.mark.parametrize("of", [1.01, -0.1])
def test_oxidation_factor_out_of_range(of):
    with pytest.raises(EngineInputError, match="between 0 and 1"):
        compute_emissions(benchmark_engine_inputs(of=of))


def test_unit_mismatch_q_vs_ncv():
    inputs = benchmark_engine_inputs()
    inputs.fuels[0].ncv = ev(40.4, "GJ/t")  # quantity is in litres
    with pytest.raises(EngineInputError, match="does not match"):
        compute_emissions(inputs)


def test_bad_factor_units_and_shapes():
    inputs = benchmark_engine_inputs()
    inputs.fuels[0].ef = ev(0.0774, "tCO2/L")
    with pytest.raises(EngineInputError, match="tCO2/GJ"):
        compute_emissions(inputs)
    inputs = benchmark_engine_inputs()
    inputs.grid_ef = ev(0.716, "tCO2/L")
    with pytest.raises(EngineInputError, match="tCO2/MWh"):
        compute_emissions(inputs)
    with pytest.raises(EngineInputError, match="grid emission factor"):
        compute_emissions(benchmark_engine_inputs(grid_ef=None))
    with pytest.raises(EngineInputError, match="Installation production|Total installation"):
        compute_emissions(benchmark_engine_inputs(total_installation_production=None))
    with pytest.raises(EngineInputError, match="cannot exceed"):
        compute_emissions(benchmark_engine_inputs(total_installation_production=ev(400.0, "t")))
    with pytest.raises(EngineInputError, match="direct"):
        compute_emissions(benchmark_engine_inputs(process_sources=[EngineProcessSource(name="x", method="direct")]))
    with pytest.raises(EngineInputError, match="activity"):
        compute_emissions(benchmark_engine_inputs(process_sources=[EngineProcessSource(name="x", method="activity")]))
    inputs = benchmark_engine_inputs()
    inputs.fuels[0].key = "mystery"
    inputs.fuels[0].quantity = ev(1.0, "kWh")
    with pytest.raises(EngineInputError, match="physical form"):
        compute_emissions(inputs)
    inputs = benchmark_engine_inputs()
    inputs.fuels[0].ncv = ev(0.0, "GJ/L")
    with pytest.raises(EngineInputError, match="NCV"):
        compute_emissions(inputs)
    inputs = benchmark_engine_inputs()
    inputs.fuels[0].ef = ev(-1.0, "tCO2/GJ")
    with pytest.raises(EngineInputError, match="negative"):
        compute_emissions(inputs)


def test_scope2_not_counted_when_cn_excludes_indirect():
    r = compute_emissions(benchmark_engine_inputs(includes_indirect=False))
    assert r.scope2_allocated_tco2 == pytest.approx(143.2, rel=REL)
    assert r.scope2_counted_tco2 == 0.0
    assert r.total_tco2 == pytest.approx(37.33776 + 962.0, rel=REL)
    assert any("not counted" in n for n in r.notes)


def test_missing_required_input_raises_incomplete(db, workspace):
    data = benchmark_inputs()
    data["production"]["target"] = {"value": None}
    set_inputs(db, workspace, data)
    with pytest.raises(IncompleteInputError) as info:
        build_engine_inputs(db, workspace.id)
    assert "production.target_quantity" in [g.input_key for g in info.value.gaps]
    # also raised when gap checking is skipped but a value is still missing
    with pytest.raises(IncompleteInputError):
        build_engine_inputs(db, workspace.id, check_gaps=False)


@pytest.mark.parametrize("field,factor", [
    ("fuel", [1.0, 1.1, 1.5, 3.0]),
    ("electricity", [1.0, 1.2, 2.0, 5.0]),
    ("precursor", [1.0, 1.01, 1.3, 2.0]),
])
def test_property_see_never_decreases(field, factor):
    previous = None
    for multiplier in factor:
        inputs = benchmark_engine_inputs()
        if field == "fuel":
            inputs.fuels[0].quantity = ev(15000.0 * multiplier, "L")
        elif field == "electricity":
            inputs.electricity = ev(250000.0 * multiplier, "kWh")
        else:
            inputs.precursors[0].quantity = ev(520.0 * multiplier, "t")
        see = compute_emissions(inputs).see_tco2_per_t
        if previous is not None:
            assert see >= previous
        previous = see


def test_data_quality_label_thresholds():
    assert data_quality_label(False, 0.0) == "A"
    assert data_quality_label(True, 0.1999) == "B"
    assert data_quality_label(True, 0.20) == "C"
    assert data_quality_label(True, 0.5, threshold=0.6) == "B"


def test_fallback_share_and_label():
    r = compute_emissions(benchmark_engine_inputs(grid_fallback=True))
    assert r.fallback_tco2 == pytest.approx(143.2, rel=REL)
    assert r.fallback_share == pytest.approx(143.2 / 1142.53776, rel=REL)
    assert r.data_quality_label == "B"
    assert r.factor_ids_used == [1]


def test_engine_value_defaults():
    value = EngineValue(value=1.0, unit="t")
    assert value.source_type == "measured" and value.uncertainty.kind == "none"

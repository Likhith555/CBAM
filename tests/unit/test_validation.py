"""Validation checks V1-V3 (Task 1)."""

import datetime as dt

import pytest

from app.db.models import AuditLog, DocumentRecord
from app.modules.calculation.unit_converter import UnitError, canonical_factor, ncv_to_canonical
from app.modules.validation.rules import (
    FindingError,
    check_unit,
    infer_field_type,
    list_findings,
    open_errors,
    periods_overlap,
    quantities_within,
    resolve_finding,
    validate_workspace,
    waive_finding,
)
from tests.helpers import make_doc

D = dt.date


# ------------------------------ V1 ------------------------------

def test_v1_conversions_pass():
    assert check_unit(250000, "kWh", "electricity") == (250.0, "MWh")
    assert check_unit(15, "kL", "liquid_fuel") == (15000.0, "L")
    value, unit = check_unit(520000, "kg", "mass")
    assert value == pytest.approx(520.0) and unit == "t"
    assert check_unit(1200, "Nm3", "gas") == (1200.0, "m3")
    assert check_unit(1.85, "tCO2e/t", "see") == (1.85, "tCO2/t")
    assert ncv_to_canonical(40.2, "GJ/kL", "liquid_fuel") == pytest.approx(0.0402)


def test_v1_electricity_in_litres_is_error():
    with pytest.raises(UnitError, match="does not fit"):
        check_unit(100, "litres", "electricity")
    with pytest.raises(UnitError, match="Unknown unit"):
        check_unit(100, "barrels-ish", "mass")
    with pytest.raises(UnitError, match="Unknown field type"):
        canonical_factor("t", "plasma")
    with pytest.raises(UnitError, match="energy per quantity"):
        ncv_to_canonical(1, "GJ", "liquid_fuel")
    with pytest.raises(UnitError, match="not recognised"):
        ncv_to_canonical(1, "furlongs/L", "liquid_fuel")


def test_infer_field_type():
    assert infer_field_type("electricity_quantity", "scope2_electricity", "kWh") == "electricity"
    assert infer_field_type("furnace_oil_quantity", "scope1_fuel", "L") == "liquid_fuel"
    assert infer_field_type("fuel_quantity", "scope1_fuel", "m3", "natural_gas") == "gas"
    assert infer_field_type("fuel_quantity", "scope1_fuel", "kg") == "mass"
    assert infer_field_type("fuel_quantity", "scope1_fuel", "zz") == "liquid_fuel"
    assert infer_field_type("precursor_see", "precursor", "tCO2/t") == "see"
    assert infer_field_type("production_quantity", "production", "t") == "mass"
    assert infer_field_type("invoice_total", None, "INR") is None


def test_v1_findings_and_canonical_values(db, workspace):
    good = make_doc(db, workspace.id, "power.pdf", [("electricity_quantity", 250000, "kWh", "scope2_electricity")])
    bad = make_doc(db, workspace.id, "power2.pdf", [("electricity_quantity", 100, "litres", "scope2_electricity")])
    findings = validate_workspace(db, workspace.id)
    field = good.extracted_fields[0]
    assert field.canonical_value == 250.0 and field.canonical_unit == "MWh"
    assert field.raw_extracted_value == "250000" and field.raw_unit == "kWh"  # original kept
    v1 = [f for f in findings if f.rule_id == "V1_UNIT"]
    assert len(v1) == 1 and v1[0].severity == "error" and v1[0].field_id == bad.extracted_fields[0].id
    assert v1[0].suggested_fix
    assert open_errors(db, workspace.id, document_id=bad.id, field_id=bad.extracted_fields[0].id)

    # Correcting the unit auto-resolves the finding and keeps an audit trail.
    bad.extracted_fields[0].effective_unit = "kWh"
    findings = validate_workspace(db, workspace.id)
    assert [f.status for f in findings if f.rule_id == "V1_UNIT"] == ["resolved"]
    actions = {r.action for r in db.query(AuditLog).filter_by(entity_type="finding")}
    assert {"opened", "resolved"} <= actions


# ------------------------------ V2 ------------------------------

def test_periods_overlap_inclusive_end():
    assert periods_overlap(D(2026, 10, 1), D(2026, 10, 31), D(2026, 10, 31), D(2026, 11, 30))
    assert not periods_overlap(D(2026, 10, 1), D(2026, 10, 31), D(2026, 11, 1), D(2026, 11, 30))


def _bill(db, ws, name, start, end, **kw):
    meta = dict(supplier="DHBVN", account_number="C-1001", utility_type="electricity",
                period_start=start, period_end=end)
    meta.update(kw)
    return make_doc(db, ws, name, [("electricity_quantity", 1000, "kWh", "scope2_electricity")], **meta)


def test_v2_one_day_overlap_is_error(db, workspace):
    _bill(db, workspace.id, "oct.pdf", D(2026, 10, 1), D(2026, 10, 31))
    nov = _bill(db, workspace.id, "nov.pdf", D(2026, 10, 31), D(2026, 11, 30))
    findings = [f for f in validate_workspace(db, workspace.id) if f.rule_id == "V2_OVERLAP"]
    assert len(findings) == 1
    assert findings[0].severity == "error" and findings[0].document_id == nov.id
    assert "1 day" in findings[0].message


def test_v2_adjacent_periods_do_not_overlap(db, workspace):
    _bill(db, workspace.id, "oct.pdf", D(2026, 10, 1), D(2026, 10, 31))
    _bill(db, workspace.id, "nov.pdf", D(2026, 11, 1), D(2026, 11, 30))
    _bill(db, workspace.id, "other-meter.pdf", D(2026, 10, 15), D(2026, 11, 14), account_number="C-2002")
    assert [f for f in validate_workspace(db, workspace.id) if f.rule_id == "V2_OVERLAP"] == []


def test_v2_window_gaps_and_outside_periods(db, workspace):
    workspace.reporting_start = D(2026, 1, 1)
    workspace.reporting_end = D(2026, 12, 31)
    _bill(db, workspace.id, "feb.pdf", D(2026, 2, 1), D(2026, 2, 28))
    _bill(db, workspace.id, "apr.pdf", D(2026, 4, 1), D(2026, 4, 30))
    _bill(db, workspace.id, "dec-jan.pdf", D(2026, 12, 15), D(2027, 1, 14))
    _bill(db, workspace.id, "2025.pdf", D(2025, 6, 1), D(2025, 6, 30), account_number="OLD")
    messages = [f.message for f in validate_workspace(db, workspace.id) if f.rule_id == "V2_OVERLAP"]
    assert all(f.severity == "warning" for f in list_findings(db, workspace.id) if f.rule_id == "V2_OVERLAP")
    assert any("2026-03-01 to 2026-03-31" in m for m in messages)  # gap inside the window
    assert any("2026-01-01 to 2026-01-31" in m for m in messages)  # window start not covered
    assert any("partly outside" in m for m in messages)
    assert any("fully outside" in m for m in messages)


# ------------------------------ V3 ------------------------------

def test_quantities_within_half_percent():
    assert quantities_within(100.0, 100.4)
    assert not quantities_within(100.0, 101.0)
    assert not quantities_within(None, 1.0)
    assert quantities_within(0.0, 0.0)


def test_v3_near_duplicate_warning(db, workspace):
    meta = dict(supplier="IOCL", bill_number="INV-778", bill_date=D(2026, 10, 5), utility_type="fuel")
    first = make_doc(db, workspace.id, "fo.pdf", [("furnace_oil_quantity", 15000, "L", "scope1_fuel")], **meta)
    second = make_doc(db, workspace.id, "fo-rescan.pdf", [("furnace_oil_quantity", 15040, "L", "scope1_fuel")], **meta)
    make_doc(db, workspace.id, "fo-other.pdf", [("furnace_oil_quantity", 18000, "L", "scope1_fuel")], **meta)
    v3 = [f for f in validate_workspace(db, workspace.id) if f.rule_id == "V3_DUPLICATE"]
    assert len(v3) == 1
    assert v3[0].severity == "warning"
    assert (v3[0].document_id, v3[0].related_document_id) == (second.id, first.id)

    # The user keeps the first one: the rescan is excluded (never deleted).
    resolved = resolve_finding(db, v3[0].id, "keep_other", "Harish", "Rescan of the same invoice")
    assert resolved.status == "resolved"
    assert db.get(DocumentRecord, second.id).is_excluded is True
    assert db.query(DocumentRecord).count() == 3


def test_v3_mark_distinct_stays_resolved(db, workspace):
    meta = dict(supplier="IOCL", bill_number="INV-9", bill_date=D(2026, 10, 5))
    make_doc(db, workspace.id, "a.pdf", [("furnace_oil_quantity", 100, "L", "scope1_fuel")], **meta)
    make_doc(db, workspace.id, "b.pdf", [("furnace_oil_quantity", 100.2, "L", "scope1_fuel")], **meta)
    finding = [f for f in validate_workspace(db, workspace.id) if f.rule_id == "V3_DUPLICATE"][0]
    resolve_finding(db, finding.id, "distinct", "Harish", "Two deliveries on the same day")
    again = [f for f in validate_workspace(db, workspace.id) if f.rule_id == "V3_DUPLICATE"]
    assert again[0].status == "resolved"


def test_resolution_errors(db, workspace):
    make_doc(db, workspace.id, "x.pdf", [("electricity_quantity", 1, "litres", "scope2_electricity")])
    finding = validate_workspace(db, workspace.id)[0]
    with pytest.raises(FindingError, match="second document"):
        resolve_finding(db, finding.id, "keep_this", "H", "")
    with pytest.raises(FindingError, match="Unknown"):
        resolve_finding(db, finding.id, "explode", "H", "x")
    with pytest.raises(FindingError, match="note"):
        resolve_finding(db, finding.id, "resolve", "H", "")
    with pytest.raises(FindingError, match="name"):
        resolve_finding(db, finding.id, "resolve", "", "x")
    with pytest.raises(LookupError):
        resolve_finding(db, 9999, "resolve", "H", "x")
    resolve_finding(db, finding.id, "resolve", "H", "Checked with the utility")
    with pytest.raises(FindingError, match="already"):
        resolve_finding(db, finding.id, "resolve", "H", "x")


# ------------------------------ Waiving ------------------------------

def test_waive_needs_reason_and_name_and_is_audited(db, workspace):
    make_doc(db, workspace.id, "x.pdf", [("electricity_quantity", 1, "litres", "scope2_electricity")])
    finding = validate_workspace(db, workspace.id)[0]
    with pytest.raises(FindingError):
        waive_finding(db, finding.id, "", "Reviewer")
    with pytest.raises(FindingError):
        waive_finding(db, finding.id, "Unit printed wrongly on the bill", "")
    with pytest.raises(LookupError):
        waive_finding(db, 12345, "r", "n")
    row = waive_finding(db, finding.id, "Unit printed wrongly on the bill; value is kWh", "A. Reviewer")
    assert row.status == "waived" and row.resolved_by == "A. Reviewer"
    audit = db.query(AuditLog).filter_by(entity_type="finding", action="waived").one()
    assert audit.actor == "A. Reviewer" and audit.old_value == "open" and audit.new_value == "waived"
    assert "kWh" in audit.reason
    # A waived finding stays waived on re-validation and no longer counts as an open error.
    validate_workspace(db, workspace.id)
    assert open_errors(db, workspace.id) == []
    with pytest.raises(FindingError, match="already"):
        waive_finding(db, finding.id, "again", "A. Reviewer")

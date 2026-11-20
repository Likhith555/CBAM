"""Review status workflow (Task 4)."""

import pytest

from app.db.models import AuditLog, PassportVersion, StatusHistory
from app.modules.calculation.workspace_calc import run_workspace_calculation
from app.modules.review.status import (
    DRAFT,
    EXPERT_REVIEWED,
    EXTERNALLY_VERIFIED,
    USER_CONFIRMED,
    TransitionError,
    approve_field,
    current_version,
    history,
    sync_passport,
    transition,
)
from app.modules.validation.rules import validate_workspace, waive_finding
from tests.helpers import benchmark_inputs, make_doc, set_inputs

EXPERT = {"reviewer_name": "Dr. A. Rao", "organisation": "Steel Carbon Advisory"}
VERIFIER = {"verifier_body": "TUV Example", "accreditation_ref": "DAkkS D-VS-0001", "statement_ref": "VS-2026-17",
            "date": "2026-11-20"}


def _ready_passport(db, workspace):
    sync_passport(db, workspace.id)
    run_workspace_calculation(db, workspace.id)
    return current_version(db, workspace.id)


def _confirm(db, workspace):
    return transition(db, workspace.id, USER_CONFIRMED, "Harish", "exporter", "All inputs checked",
                      {"declaration": True})


def test_every_valid_transition_passes(db, workspace):
    _ready_passport(db, workspace)
    assert _confirm(db, workspace).status == USER_CONFIRMED
    v = transition(db, workspace.id, EXPERT_REVIEWED, "Dr. A. Rao", "reviewer", "Factors and allocation checked", EXPERT)
    assert v.status == EXPERT_REVIEWED
    v = transition(db, workspace.id, EXTERNALLY_VERIFIED, "Harish", "verifier", "Statement on file", VERIFIER)
    assert v.status == EXTERNALLY_VERIFIED
    assert "TUV Example" in v.verifier_details_json
    rows = history(db, workspace.id)
    assert [(h.from_status, h.to_status) for h in rows] == [
        (None, DRAFT), (DRAFT, USER_CONFIRMED), (USER_CONFIRMED, EXPERT_REVIEWED),
        (EXPERT_REVIEWED, EXTERNALLY_VERIFIED)]
    assert all(h.timestamp is not None and h.actor for h in rows)


def test_skipping_steps_is_rejected(db, workspace):
    _ready_passport(db, workspace)
    with pytest.raises(TransitionError, match="not allowed"):
        transition(db, workspace.id, EXTERNALLY_VERIFIED, "Harish", "verifier", "", VERIFIER)
    with pytest.raises(TransitionError, match="Unknown"):
        transition(db, workspace.id, "Approved", "Harish", "exporter")
    with pytest.raises(TransitionError, match="must be performed by the exporter"):
        transition(db, workspace.id, USER_CONFIRMED, "Harish", "reviewer", "", {"declaration": True})
    with pytest.raises(TransitionError, match="name"):
        transition(db, workspace.id, USER_CONFIRMED, " ", "exporter")


def test_user_confirmed_preconditions(db, workspace):
    sync_passport(db, workspace.id)
    with pytest.raises(TransitionError) as info:
        transition(db, workspace.id, USER_CONFIRMED, "Harish", "exporter", "", {})
    problems = " ".join(info.value.problems)
    assert "Run the calculation" in problems and "declaration" in problems

    doc = make_doc(db, workspace.id, "bill.pdf", [("electricity_quantity", 10, "litres", "scope2_electricity")])
    run_workspace_calculation(db, workspace.id)
    with pytest.raises(TransitionError) as info:
        _confirm(db, workspace)
    problems = " ".join(info.value.problems)
    assert "open validation error" in problems and "not reviewed" in problems

    field = doc.extracted_fields[0]
    with pytest.raises(TransitionError, match="blocked"):
        approve_field(db, field, "Harish")
    finding = [f for f in validate_workspace(db, workspace.id) if f.status == "open"][0]
    waive_finding(db, finding.id, "Bill prints litres but the meter reads kWh", "Harish")
    approve_field(db, field, "Harish")
    assert field.reviewed and field.reviewed_by == "Harish"
    with pytest.raises(TransitionError, match="reviewer"):
        approve_field(db, field, "")
    assert _confirm(db, workspace).status == USER_CONFIRMED


def test_incomplete_blocks_confirmation(db, workspace):
    data = benchmark_inputs()
    data["production"]["target"] = {"value": None}
    set_inputs(db, workspace, data)
    sync_passport(db, workspace.id)
    with pytest.raises(TransitionError) as info:
        _confirm(db, workspace)
    assert any("Incomplete" in p for p in info.value.problems)


def test_expert_and_verifier_need_their_fields(db, workspace):
    _ready_passport(db, workspace)
    _confirm(db, workspace)
    with pytest.raises(TransitionError) as info:
        transition(db, workspace.id, EXPERT_REVIEWED, "Rao", "reviewer", "", {"reviewer_name": "Rao"})
    assert any("organisation" in p for p in info.value.problems)
    assert any("note" in p for p in info.value.problems)
    transition(db, workspace.id, EXPERT_REVIEWED, "Rao", "reviewer", "ok", EXPERT)
    with pytest.raises(TransitionError) as info:
        transition(db, workspace.id, EXTERNALLY_VERIFIED, "H", "verifier", "", {"verifier_body": "X"})
    assert len(info.value.problems) == 3


def test_editing_input_after_confirmation_resets_to_draft(db, workspace):
    _ready_passport(db, workspace)
    confirmed = _confirm(db, workspace)
    data = benchmark_inputs()
    data["fuels"][0]["quantity"]["value"] = 16000.0
    set_inputs(db, workspace, data)
    new = sync_passport(db, workspace.id)
    assert new.status == DRAFT
    assert new.version_number == confirmed.version_number + 1
    assert new.input_hash != confirmed.input_hash
    last = db.query(StatusHistory).order_by(StatusHistory.id.desc()).first()
    assert (last.from_status, last.to_status, last.actor) == (USER_CONFIRMED, DRAFT, "system")
    assert "Automatic reset" in last.note
    assert db.query(AuditLog).filter_by(action="status_reset").count() == 1
    # The confirmed snapshot is kept untouched.
    old = db.query(PassportVersion).filter_by(version_number=confirmed.version_number).one()
    assert old.status == USER_CONFIRMED and old.result_json


def test_override_on_document_field_resets(db, workspace):
    doc = make_doc(db, workspace.id, "bill.pdf", [("electricity_quantity", 250000, "kWh", "scope2_electricity")])
    approve_field(db, doc.extracted_fields[0], "Harish")
    _ready_passport(db, workspace)
    _confirm(db, workspace)
    doc.extracted_fields[0].effective_value = "260000"
    assert sync_passport(db, workspace.id).status == DRAFT


def test_unchanged_inputs_keep_version(db, workspace):
    first = sync_passport(db, workspace.id)
    assert sync_passport(db, workspace.id).id == first.id

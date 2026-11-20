"""Smoke test verifying Streamlit views and state management logic."""

from unittest.mock import patch
import pytest
from app.modules.calculation.engine import calculate_product_see
from app.modules.document_ai.extractor import run_document_ai_extraction
from frontend.views.dashboard import gather_effective_inputs


def test_gather_effective_inputs_defaults():
    # With an empty session there are no actual records, so the calculation is blocked with a checklist.
    with patch("streamlit.session_state", {}):
        with pytest.raises(ValueError, match="actual inputs are provided"):
            gather_effective_inputs()


def test_full_pipeline_mock_run():
    # 1. Mock document extraction
    doc_text = "DHBVN Power Bill - Active Energy Consumption: 250,000 kWh\nFurnace Oil: 15,000 litres\nPrecursor: 520 tonnes\nProduction: 500 tonnes"
    import fitz

    pdf = fitz.open()
    pdf.new_page().insert_text((50, 80), doc_text)
    res = run_document_ai_extraction(pdf.write(), "plant_records.pdf")
    pdf.close()
    assert len(res.extracted_fields) >= 3

    # 2. Gather inputs and calculate
    from app.db.database import SessionLocal
    from app.db.seed import seed_database

    seed_database()
    fields = [f.model_dump() for f in res.extracted_fields]
    fields.append({"field_name": "precursor_see", "extracted_value": 1.85, "unit": "tCO2/t", "confidence_score": 0.95})
    with patch("streamlit.session_state", {
        "workspace": {"company_name": "Apex", "cn_code": "7318 15 82", "target_product_family": "Steel Fasteners"},
        "uploaded_documents": {res.document_hash: {"extracted_fields": fields}},
        "field_overrides": {},
        "allocation_data": {"target_production_tonnes": 500.0, "total_facility_production_tonnes": 625.0,
                            "apply_allocation_to_shared_energy": True, "fuel_allocation_basis": "shared",
                            "electricity_allocation_basis": "shared"},
        "selected_fuel_factor": "Furnace Oil",
        "selected_grid_factor": "Indian National Grid (CEA v19 Baseline)",
        "precursor_cn_code": "7228 30",
        "precursor_material_name": "Steel wire rod",
        "precursor_country": "India",
    }):
        req = gather_effective_inputs()
        with SessionLocal() as db:
            calc_result = calculate_product_see(req, db=db)
        assert calc_result.specific_embedded_emissions_see > 0
        assert calc_result.allocation_factor_alpha == 0.8

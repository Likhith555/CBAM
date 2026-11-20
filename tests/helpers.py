"""Benchmark data shared by the test suite (500 t product, 625 t installation, alpha 0.8)."""

import copy

from app.schemas.workspace import (
    EngineFuel,
    EngineInputs,
    EnginePrecursor,
    EngineValue,
    UncertaintySpec,
    WorkspaceInputs,
)

BENCHMARK_INPUTS = {
    "cn_code": "7318 15 82",
    "includes_indirect": True,
    "production": {
        "product_family": "Steel Fasteners",
        "target": {"value": 500.0, "unit": "t"},
        "total_installation": {"value": 625.0, "unit": "t"},
        "utility_coverage": "shared",
    },
    "uses_fuel": True,
    "fuels": [{
        "fuel_key": "furnace_oil", "name": "Furnace oil",
        "quantity": {"value": 15000.0, "unit": "L"},
        "ncv": {"value": 0.0402, "unit": "GJ/L", "evidence_ref": "lab certificate"},
        "ef": {"value": 0.0774, "unit": "tCO2/GJ", "evidence_ref": "lab certificate"},
        "oxidation_factor": {"value": 1.0, "unit": "fraction", "evidence_ref": "lab certificate"},
    }],
    "electricity": {
        "consumed": {"value": 250000.0, "unit": "kWh"},
        "grid_ef": {"value": 0.716, "unit": "tCO2/MWh", "evidence_ref": "supplier factor letter"},
    },
    "precursors": [{
        "key": "wire_rod", "name": "Steel wire rod", "cn_code": "7213 91",
        "quantity": {"value": 520.0, "unit": "t"},
        "supplier_see": {"value": 1.85, "unit": "tCO2/t", "evidence_ref": "supplier declaration"},
    }],
    "process": {"mode": "none", "reason": "Cold forging and heat treatment with electric furnaces only."},
}


def benchmark_inputs(**changes) -> dict:
    data = copy.deepcopy(BENCHMARK_INPUTS)
    for path, value in changes.items():
        node = data
        parts = path.split("__")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() else node[part]
        node[parts[-1]] = value
    return data


def ev(value, unit, **kw) -> EngineValue:
    return EngineValue(value=value, unit=unit, **kw)


def benchmark_engine_inputs(of=1.0, dedicated=False, grid_fallback=False, **overrides) -> EngineInputs:
    grid = (
        ev(0.716, "tCO2/MWh", source_type="fallback", factor_id=1,
           uncertainty=UncertaintySpec(kind="relative", rel=0.10))
        if grid_fallback else ev(0.716, "tCO2/MWh")
    )
    data = dict(
        target_production=ev(500.0, "t"),
        total_installation_production=ev(625.0, "t"),
        utility_coverage="dedicated" if dedicated else "shared",
        fuels=[EngineFuel(key="furnace_oil", name="Furnace oil", quantity=ev(15000.0, "L"),
                          ncv=ev(0.0402, "GJ/L"), ef=ev(0.0774, "tCO2/GJ"), oxidation_factor=ev(of, "fraction"))],
        electricity=ev(250000.0, "kWh"),
        grid_ef=grid,
        precursors=[EnginePrecursor(key="wire_rod", name="Wire rod", quantity=ev(520.0, "t"), see=ev(1.85, "tCO2/t"))],
    )
    data.update(overrides)
    return EngineInputs(**data)


def set_inputs(db, ws, data: dict):
    from app.modules.gaps.register import compute_gaps

    ws.inputs_json = WorkspaceInputs.model_validate(data).model_dump_json()
    gaps = compute_gaps(db, ws.id)
    db.commit()
    return gaps


_doc_counter = [0]


def make_doc(db, workspace_id, filename, fields=(), **meta):
    """Creates a document record with extracted fields directly in the DB."""
    import hashlib

    from app.db.models import DocumentRecord, ExtractedFieldAudit

    _doc_counter[0] += 1
    digest = hashlib.sha256(f"{filename}-{_doc_counter[0]}".encode()).hexdigest()
    doc = DocumentRecord(document_hash=digest, filename=filename, workspace_id=workspace_id,
                         page_count=1, file_size_bytes=100, **meta)
    db.add(doc)
    db.flush()
    for name, value, unit, category in fields:
        db.add(ExtractedFieldAudit(document_hash=digest, field_name=name, field_category=category,
                                   raw_extracted_value=str(value), raw_unit=unit, effective_value=str(value),
                                   effective_unit=unit, evidence_snippet=f"{value} {unit}"))
    db.flush()
    db.refresh(doc)
    return doc

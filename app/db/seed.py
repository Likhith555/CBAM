"""Initial database seeder for CBAM CN codes and standard emission factors."""

from sqlalchemy.orm import Session

from app.db.database import SessionLocal, init_db
from app.db.models import CBAMDefaultValues, CNCodeRegistry, FactorSet
from app.modules.registry.factors import FactorSpec, create_factor_set

IPCC_TITLE = (
    "2006 IPCC Guidelines for National Greenhouse Gas Inventories, Vol. 2 Energy, "
    "Ch. 1 Introduction, Table 1.2 (default NCVs) and Table 1.4 (default CO2 emission factors)"
)
IPCC_PUBLISHER = "IPCC National Greenhouse Gas Inventories Programme (IGES, Japan)"
IPCC_URL = "https://www.ipcc-nggip.iges.or.jp/public/2006gl/pdf/2_Volume2/V2_1_Ch1_Introduction.pdf"
CEA_V19_TITLE = (
    "CO2 Baseline Database for the Indian Power Sector, User Guide Version 19.0 (Dec 2023), "
    "Annexure-I: weighted average emission factor incl. renewable energy, FY 2022-23"
)
CEA_PUBLISHER = "Central Electricity Authority, Ministry of Power, Government of India"
CEA_V19_URL = "https://cea.nic.in/wp-content/uploads/baseline/2024/01/User_Guide__Version_19.0.pdf"
PLACEHOLDER_UNC = "Uncertainty is a placeholder pending mentor approval (Task 3)."


def _fuel_specs(fuel: str, label: str, ncv: float, ncv_unit: str, ef: float, ncv_note: str):
    return [
        FactorSpec(
            key=f"fuel.{fuel}.ncv", value=ncv, unit=ncv_unit,
            source_title=IPCC_TITLE, publisher=IPCC_PUBLISHER, source_url=IPCC_URL, year=2006,
            uncertainty_type="relative", uncertainty_rel=0.05,
            notes=f"{label}: {ncv_note} {PLACEHOLDER_UNC}",
        ),
        FactorSpec(
            key=f"fuel.{fuel}.ef", value=ef, unit="tCO2/GJ",
            source_title=IPCC_TITLE, publisher=IPCC_PUBLISHER, source_url=IPCC_URL, year=2006,
            uncertainty_type="relative", uncertainty_rel=0.03,
            notes=f"{label}: IPCC default CO2 EF {ef * 1e6:,.0f} kg/TJ (Table 1.4). {PLACEHOLDER_UNC}",
        ),
        FactorSpec(
            key=f"fuel.{fuel}.of", value=1.0, unit="fraction",
            source_title=IPCC_TITLE, publisher=IPCC_PUBLISHER, source_url=IPCC_URL, year=2006,
            uncertainty_type="triangular", uncertainty_low=0.98, uncertainty_high=1.0,
            notes=f"{label}: IPCC 2006 Tier 1 assumes full oxidation (fraction of carbon oxidised = 1). {PLACEHOLDER_UNC}",
        ),
    ]


def register_2026_1():
    """Seed values from the benchmark, each with its citation (factor set 2026.1)."""
    specs = []
    specs += _fuel_specs(
        "furnace_oil", "Furnace oil (residual fuel oil)", 0.0402, "GJ/L", 0.0774,
        "IPCC NCV 40.4 TJ/Gg converted per litre at the benchmark density of 0.995 kg/L. "
        "TO VERIFY: typical furnace oil is 0.94-0.98 kg/L; replace with the supplier density.",
    )
    specs += _fuel_specs(
        "diesel", "Diesel / HSD (gas/diesel oil)", 0.0357, "GJ/L", 0.0741,
        "IPCC NCV 43.0 TJ/Gg converted per litre at an assumed density of 0.83 kg/L.",
    )
    specs += _fuel_specs(
        "natural_gas", "Natural gas", 0.0385, "GJ/m3", 0.0561,
        "IPCC NCV 48.0 TJ/Gg converted per standard m3 at an assumed density of ~0.80 kg/m3. "
        "Replace with the supplier gas analysis where available.",
    )
    specs += _fuel_specs(
        "lpg", "Liquefied petroleum gas", 47.3, "GJ/t", 0.0631,
        "IPCC NCV 47.3 TJ/Gg (= GJ/t); no density conversion needed.",
    )
    specs.append(
        FactorSpec(
            key="electricity.grid_ef", value=0.716, unit="tCO2/MWh",
            source_title=CEA_V19_TITLE, publisher=CEA_PUBLISHER, source_url=CEA_V19_URL, year=2023,
            uncertainty_type="relative", uncertainty_rel=0.10,
            notes="National average grid factor; use a supplier- or PPA-specific factor where one is documented. "
            + PLACEHOLDER_UNC,
        )
    )
    return specs


def seed_factor_register(db: Session) -> None:
    if db.get(FactorSet, "2026.1") is None:
        create_factor_set(
            db, "2026.1", register_2026_1(),
            notes="Benchmark seed values. Precursor supplier SEE is document-backed input, not a register factor.",
        )


def seed_session(db: Session) -> None:
    """Seeds reference data into an existing session. Idempotent."""
    # Remove previously shipped benchmark values that are not approved primary data.
    db.query(CBAMDefaultValues).filter(
        CBAMDefaultValues.item_name.in_([
            "EU CBAM Default - India Iron & Steel Grid",
            "Steel Wire Rod (DRI-EAF Route - Indian Benchmark)",
            "Steel Wire Rod (BF-BOF Primary Route)",
            "EU CBAM Default - Alloy Steel Wire Rod (India)",
        ])
    ).delete(synchronize_session=False)

    # 1. Seed Pilot CN Codes (Steel Fasteners & Precursors)
    cn_codes = [
        CNCodeRegistry(
            cn_code="7318 15 82",
            description="Other screws and bolts, whether or not with their nuts or washers, of steel, without heads or with cold formed heads",
            sector="Iron and Steel",
            includes_indirect_emissions=True,  # delta_CN = 1
            requires_precursors=True,
            default_unit="tonne",
            notes="Primary pilot CN code for Indian cold-forging fastener manufacturers."
        ),
        CNCodeRegistry(
            cn_code="7318 15 95",
            description="Other screws and bolts, of steel, with heads, tensile strength < 800 MPa",
            sector="Iron and Steel",
            includes_indirect_emissions=True,
            requires_precursors=True,
            default_unit="tonne",
            notes="Pilot CN code for standard mild steel bolts."
        ),
        CNCodeRegistry(
            cn_code="7318 16 00",
            description="Nuts, of steel",
            sector="Iron and Steel",
            includes_indirect_emissions=True,
            requires_precursors=True,
            default_unit="tonne",
            notes="Hex nuts and locking nuts."
        ),
        CNCodeRegistry(
            cn_code="7228 30",
            description="Precursor: Other bars and rods of other alloy steel; wire rod",
            sector="Iron and Steel (Precursor)",
            includes_indirect_emissions=True,
            requires_precursors=False,
            default_unit="tonne",
            notes="Raw material wire rod used in cold heading fasteners."
        ),
    ]

    for item in cn_codes:
        existing = db.query(CNCodeRegistry).filter_by(cn_code=item.cn_code).first()
        if not existing:
            db.add(item)

    # 2. Seed Standard Default Emission & Calorific Factors
    default_factors = [
        # Scope 1 - Fuels
        CBAMDefaultValues(
            category="fuel",
            item_name="Furnace Oil",
            unit="litre",
            ncv_value=0.0402,  # GJ / litre
            ncv_unit="GJ/litre",
            ef_value=0.0774,  # tCO2 / GJ
            ef_unit="tCO2/GJ",
            oxidation_factor=1.0,
            geographic_scope="India / Global",
            source_reference="IPCC 2006 Guidelines for National GHG Inventories, Vol 2"
        ),
        CBAMDefaultValues(
            category="fuel",
            item_name="Diesel (HSD)",
            unit="litre",
            ncv_value=0.0357,  # GJ / litre
            ncv_unit="GJ/litre",
            ef_value=0.0741,  # tCO2 / GJ
            ef_unit="tCO2/GJ",
            oxidation_factor=1.0,
            geographic_scope="India / Global",
            source_reference="IPCC 2006 Guidelines, Vol 2 Energy"
        ),
        CBAMDefaultValues(
            category="fuel",
            item_name="Natural Gas",
            unit="Nm3",
            ncv_value=0.0385,  # GJ / Nm3
            ncv_unit="GJ/Nm3",
            ef_value=0.0561,  # tCO2 / GJ
            ef_unit="tCO2/GJ",
            oxidation_factor=1.0,
            geographic_scope="India / Global",
            source_reference="IPCC 2006 Guidelines, Vol 2 Energy"
        ),
        CBAMDefaultValues(
            category="fuel",
            item_name="LPG",
            unit="kg",
            ncv_value=0.0461,  # GJ / kg
            ncv_unit="GJ/kg",
            ef_value=0.0631,  # tCO2 / GJ
            ef_unit="tCO2/GJ",
            oxidation_factor=1.0,
            geographic_scope="India / Global",
            source_reference="IPCC 2006 Guidelines, Vol 2 Energy"
        ),

        # Scope 2 - Electricity
        CBAMDefaultValues(
            category="electricity",
            item_name="Indian National Grid (CEA v19 Baseline)",
            unit="kWh",
            ncv_value=None,
            ncv_unit=None,
            ef_value=0.716,  # tCO2 / MWh (0.000716 tCO2/kWh)
            ef_unit="tCO2/MWh",
            oxidation_factor=1.0,
            geographic_scope="India (National Average)",
            source_reference="Central Electricity Authority (CEA) CO2 Baseline Database v19 (2024)"
        ),
    ]

    for item in default_factors:
        existing = db.query(CBAMDefaultValues).filter_by(
            category=item.category,
            item_name=item.item_name
        ).first()
        if not existing:
            db.add(item)

    seed_factor_register(db)
    db.commit()


def seed_database():
    init_db()
    db = SessionLocal()
    try:
        seed_session(db)
        print("Database initialized and successfully seeded with pilot CN codes and default factors.")
    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()

"""Physical unit conversion and normalization service for deterministic carbon accounting."""

import re
from typing import Dict

# Normalization alias mappings (lowercase)
MASS_CONVERSIONS_TO_TONNES: Dict[str, float] = {
    "tonne": 1.0,
    "tonnes": 1.0,
    "metric ton": 1.0,
    "metric tons": 1.0,
    "t": 1.0,
    "mt": 1.0,
    "kg": 0.001,
    "kilogram": 0.001,
    "kilograms": 0.001,
    "g": 1e-6,
    "gram": 1e-6,
    "grams": 1e-6,
    "lb": 0.00045359237,
    "lbs": 0.00045359237,
    "pound": 0.00045359237,
    "pounds": 0.00045359237,
    "quintal": 0.1,
    "quintals": 0.1,
}

ELECTRICITY_CONVERSIONS_TO_MWH: Dict[str, float] = {
    "mwh": 1.0,
    "megawatt-hour": 1.0,
    "megawatt-hours": 1.0,
    "kwh": 0.001,
    "kilowatt-hour": 0.001,
    "kilowatt-hours": 0.001,
    "units": 0.001,
    "unit": 0.001,
    "gwh": 1000.0,
    "gigawatt-hour": 1000.0,
    "gigawatt-hours": 1000.0,
    "wh": 1e-6,
    "watt-hour": 1e-6,
    "watt-hours": 1e-6,
}

ENERGY_CONVERSIONS_TO_GJ: Dict[str, float] = {
    "gj": 1.0,
    "gigajoule": 1.0,
    "gigajoules": 1.0,
    "mj": 0.001,
    "megajoule": 0.001,
    "megajoules": 0.001,
    "tj": 1000.0,
    "terajoule": 1000.0,
    "terajoules": 1000.0,
    "kwh": 0.0036,
    "mwh": 3.6,
    "mmbtu": 1.05505585,
    "btu": 1.05505585e-6,
    "kcal": 4.184e-6,
    "gcal": 4.184,
}

VOLUME_CONVERSIONS_TO_LITRES: Dict[str, float] = {
    "litre": 1.0,
    "litres": 1.0,
    "liter": 1.0,
    "liters": 1.0,
    "l": 1.0,
    "m3": 1000.0,
    "nm3": 1000.0,
    "cubic meter": 1000.0,
    "cubic meters": 1000.0,
    "cubic metre": 1000.0,
    "cubic metres": 1000.0,
    "kl": 1000.0,
    "kilolitre": 1000.0,
    "kilolitres": 1000.0,
    "gal": 3.78541,
    "gallon": 3.78541,
    "gallons": 3.78541,
    "bbl": 158.987,
    "barrel": 158.987,
    "barrels": 158.987,
}


def clean_unit(unit_str: str) -> str:
    """Cleans and standardizes raw unit string."""
    if not unit_str:
        return ""
    # Strip spaces, special punctuation, and lowercase
    u = unit_str.strip().lower()
    u = re.sub(r'[\/\\_\-\.]', '', u)
    # Common replacements
    if u in ["tons", "ton", "tonnes", "tonne", "mt", "t"]:
        return "tonne"
    if u in ["kwh", "units", "unit"]:
        return "kwh"
    if u in ["mwh"]:
        return "mwh"
    if u in ["litre", "litres", "liter", "liters", "l"]:
        return "litre"
    if u in ["m3", "nm3"]:
        return "m3"
    return u


def to_metric_tonnes(quantity: float, unit: str) -> float:
    """Converts a mass quantity to standard metric tonnes."""
    if quantity < 0:
        raise ValueError(f"Quantity cannot be negative: {quantity}")
    u = unit.strip().lower()
    factor = MASS_CONVERSIONS_TO_TONNES.get(u)
    if factor is None:
        # Try cleaned unit
        clean_u = clean_unit(u)
        factor = MASS_CONVERSIONS_TO_TONNES.get(clean_u)
    if factor is None:
        raise ValueError(f"Unsupported mass unit: '{unit}'. Expected one of: {list(MASS_CONVERSIONS_TO_TONNES.keys())}")
    return quantity * factor


def to_megawatt_hours(quantity: float, unit: str) -> float:
    """Converts an electrical energy quantity to Megawatt-hours (MWh)."""
    if quantity < 0:
        raise ValueError(f"Electrical consumption cannot be negative: {quantity}")
    u = unit.strip().lower()
    factor = ELECTRICITY_CONVERSIONS_TO_MWH.get(u)
    if factor is None:
        clean_u = clean_unit(u)
        factor = ELECTRICITY_CONVERSIONS_TO_MWH.get(clean_u)
    if factor is None:
        raise ValueError(f"Unsupported electricity unit: '{unit}'. Expected one of: {list(ELECTRICITY_CONVERSIONS_TO_MWH.keys())}")
    return quantity * factor


def to_energy_gigajoules(quantity: float, unit: str, ncv_gj_per_unit: float = None) -> float:
    """
    Converts fuel consumption to energy content in Gigajoules (GJ).
    If unit is an energy unit (GJ, MJ, TJ, MMBTU), converts directly.
    If unit is a physical unit (litres, kg, m3, tonnes), multiplies by ncv_gj_per_unit.
    """
    if quantity < 0:
        raise ValueError(f"Fuel quantity cannot be negative: {quantity}")
    u = unit.strip().lower()

    # Direct energy unit conversion
    if u in ENERGY_CONVERSIONS_TO_GJ:
        return quantity * ENERGY_CONVERSIONS_TO_GJ[u]

    # Physical fuel quantity: requires NCV
    if ncv_gj_per_unit is None or ncv_gj_per_unit <= 0:
        raise ValueError(f"Net Calorific Value (NCV) is required to convert physical unit '{unit}' to Gigajoules.")

    return quantity * ncv_gj_per_unit


# ---------------------------------------------------------------------
# Canonical units per field type (validation rule V1 and the engine)
# ---------------------------------------------------------------------

class UnitError(ValueError):
    """Unknown unit, or a unit that does not fit the field."""


CANONICAL_UNITS: Dict[str, str] = {
    "electricity": "MWh",
    "liquid_fuel": "L",
    "gas": "m3",
    "mass": "t",
    "see": "tCO2/t",
}

_LIQUID_TO_L: Dict[str, float] = {
    "l": 1.0, "litre": 1.0, "litres": 1.0, "liter": 1.0, "liters": 1.0, "ltr": 1.0, "ltrs": 1.0,
    "kl": 1000.0, "kilolitre": 1000.0, "kilolitres": 1000.0, "kiloliter": 1000.0,
    "m3": 1000.0, "cubic metre": 1000.0, "cubic meter": 1000.0,
    "ml": 0.001,
}
_GAS_TO_M3: Dict[str, float] = {
    "m3": 1.0, "nm3": 1.0, "scm": 1.0, "sm3": 1.0, "cubic metre": 1.0, "cubic meter": 1.0,
    "mmscm": 1_000_000.0, "thousand m3": 1000.0,
}
_MASS_TO_T: Dict[str, float] = {
    "t": 1.0, "tonne": 1.0, "tonnes": 1.0, "mt": 1.0, "ton": 1.0, "tons": 1.0, "metric ton": 1.0,
    "kg": 0.001, "kilogram": 0.001, "kilograms": 0.001, "g": 1e-6,
    "quintal": 0.1, "quintals": 0.1,
}
_ELEC_TO_MWH: Dict[str, float] = {
    "mwh": 1.0, "kwh": 0.001, "units": 0.001, "unit": 0.001, "gwh": 1000.0, "wh": 1e-6,
}
_SEE_TO_T_PER_T: Dict[str, float] = {
    "tco2/t": 1.0, "tco2e/t": 1.0, "t co2/t": 1.0, "tco2/tonne": 1.0, "tco2e/tonne": 1.0,
    "kgco2/kg": 1.0, "kgco2/t": 0.001, "kgco2e/t": 0.001,
}
_TABLES = {
    "electricity": _ELEC_TO_MWH,
    "liquid_fuel": _LIQUID_TO_L,
    "gas": _GAS_TO_M3,
    "mass": _MASS_TO_T,
    "see": _SEE_TO_T_PER_T,
}

# Fuel register slug -> physical form, so the canonical unit is known.
FUEL_FORMS: Dict[str, str] = {
    "furnace_oil": "liquid_fuel",
    "diesel": "liquid_fuel",
    "natural_gas": "gas",
    "lpg": "mass",
}


def _norm(unit: str) -> str:
    u = (unit or "").strip().lower().replace("³", "3").replace("₂", "2")
    u = re.sub(r"\s+", " ", u)
    u = u.replace(".", "")
    return u


def canonical_factor(unit: str, field_type: str) -> float:
    """Multiplier that converts one ``unit`` into the canonical unit of ``field_type``."""
    if field_type not in _TABLES:
        raise UnitError(f"Unknown field type '{field_type}'.")
    u = _norm(unit)
    table = _TABLES[field_type]
    if u in table:
        return table[u]
    compact = u.replace(" ", "")
    if compact in table:
        return table[compact]
    for other_type, other in _TABLES.items():
        if other_type != field_type and (u in other or compact in other):
            raise UnitError(
                f"Unit '{unit}' is a {other_type.replace('_', ' ')} unit and does not fit a "
                f"{field_type.replace('_', ' ')} field (expected {CANONICAL_UNITS[field_type]})."
            )
    raise UnitError(f"Unknown unit '{unit}' for a {field_type.replace('_', ' ')} field.")


def to_canonical(value: float, unit: str, field_type: str) -> float:
    return value * canonical_factor(unit, field_type)


def ncv_to_canonical(value: float, ncv_unit: str, fuel_form: str) -> float:
    """Converts an NCV such as 'GJ/kL' to GJ per canonical unit of the fuel form."""
    u = _norm(ncv_unit)
    if "/" not in u:
        raise UnitError(f"NCV unit '{ncv_unit}' must be energy per quantity, e.g. GJ/L.")
    energy, per = (part.strip() for part in u.split("/", 1))
    if energy not in ENERGY_CONVERSIONS_TO_GJ:
        raise UnitError(f"NCV energy unit '{energy}' is not recognised.")
    try:
        per_factor = canonical_factor(per, fuel_form)
    except UnitError as exc:
        raise UnitError(
            f"NCV unit '{ncv_unit}' does not match the fuel quantity "
            f"(expected GJ/{CANONICAL_UNITS[fuel_form]}): {exc}"
        ) from exc
    return value * ENERGY_CONVERSIONS_TO_GJ[energy] / per_factor

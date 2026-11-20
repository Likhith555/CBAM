"""Deterministic CBAM Specific Embedded Emissions (SEE) Calculation Engine.

STRICT PRINCIPLE: AI NEVER PERFORMS EMISSIONS ARITHMETIC.
All calculations are 100% deterministic, testable, and auditable.

Every emission factor and CN classification is resolved from the seeded
reference tables. The engine contains no fallback factor values of its own:
if a required factor is missing it raises, rather than inventing data.
"""

from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.modules.calculation.factors import (
    find_cn_code,
    resolve_electricity_factor,
    resolve_fuel_factors,
    resolve_precursor_default,
)
from app.modules.calculation.unit_converter import (
    to_energy_gigajoules,
    to_megawatt_hours,
    to_metric_tonnes,
)
from app.schemas.calculation import (
    DataQualityRating,
    DeterministicCalculationRequest,
    DeterministicCalculationResult,
    ElectricityConsumptionItem,
    FuelCombustionItem,
    PrecursorMaterialItem,
)


def compute_mass_allocation_factor(
    target_production_tonnes: float,
    total_facility_production_tonnes: float,
) -> float:
    """
    Calculates multi-product mass allocation factor alpha_p:
    alpha_p = Net Target Production / Total Facility Production
    """
    if target_production_tonnes <= 0:
        raise ValueError(f"Target production quantity must be greater than 0, got {target_production_tonnes}")
    if total_facility_production_tonnes <= 0:
        raise ValueError(f"Total facility production quantity must be greater than 0, got {total_facility_production_tonnes}")
    if target_production_tonnes > total_facility_production_tonnes:
        raise ValueError(
            f"Target production quantity ({target_production_tonnes} t) cannot exceed "
            f"total facility production quantity ({total_facility_production_tonnes} t)."
        )

    return target_production_tonnes / total_facility_production_tonnes


def determine_delta_cn(cn_code: str, db: Session) -> bool:
    """
    Determines whether the CN code requires Scope 2 indirect emissions (delta_CN = 1)
    using the CBAM CN code registry.
    """
    if db is None:
        raise ValueError("A database session is required to resolve the CBAM CN classification.")
    entry = find_cn_code(db, cn_code)
    if entry is None:
        raise ValueError(
            f"CN code '{cn_code}' is not registered as a CBAM-covered good. "
            "Select a CN code from the reference registry."
        )
    return bool(entry.includes_indirect_emissions)


def _resolve_request_factors(
    request: DeterministicCalculationRequest,
    db: Session,
) -> Tuple[DeterministicCalculationRequest, List[str]]:
    """Fills missing factor values from the reference database, or raises."""
    if db is None:
        raise ValueError("A database session is required to resolve emission factors.")

    notes: List[str] = []

    fuels: List[FuelCombustionItem] = []
    for fuel in request.fuels:
        ncv = fuel.ncv
        ef = fuel.ef
        if ncv is None or ncv <= 0 or ef is None or ef <= 0:
            row = resolve_fuel_factors(db, fuel.fuel_name)
            if row is None or row.ncv_value is None or row.ef_value is None:
                raise ValueError(
                    f"No reference NCV/EF found for fuel '{fuel.fuel_name}'. "
                    "Select a fuel from the reference factor list."
                )
            if ncv is None or ncv <= 0:
                ncv = row.ncv_value
                notes.append(f"Resolved NCV for {fuel.fuel_name} from '{row.item_name}': {ncv} {row.ncv_unit}.")
            if ef is None or ef <= 0:
                ef = row.ef_value
                notes.append(f"Resolved EF for {fuel.fuel_name} from '{row.item_name}': {ef} {row.ef_unit}.")
        fuels.append(fuel.model_copy(update={"ncv": ncv, "ef": ef}))

    electricity = request.electricity
    if electricity is not None and (electricity.grid_ef_tco2_per_mwh is None or electricity.grid_ef_tco2_per_mwh <= 0):
        row = resolve_electricity_factor(db, electricity.source_name)
        if row is None or row.ef_value is None:
            raise ValueError(
                f"No reference electricity factor matched '{electricity.source_name}'. "
                "Select an electricity factor from the reference list."
            )
        electricity = electricity.model_copy(update={"grid_ef_tco2_per_mwh": row.ef_value})
        notes.append(
            f"Resolved grid EF for {electricity.source_name} from '{row.item_name}': {row.ef_value} {row.ef_unit}."
        )

    precursors: List[PrecursorMaterialItem] = []
    for prec in request.precursors:
        is_fallback = prec.is_fallback or prec.source_type == "eu_default"
        if not is_fallback:
            if prec.embedded_see_tco2_per_tonne is None or prec.embedded_see_tco2_per_tonne <= 0:
                raise ValueError(
                    f"Supplier SEE is required for precursor '{prec.material_name}' (CN {prec.cn_code}). "
                    "Upload supplier emissions data or explicitly mark an approved official fallback."
                )
            precursors.append(prec)
            continue

        row = resolve_precursor_default(db, prec.cn_code)
        if row is None or row.ef_value is None:
            raise ValueError(
                f"No EU default precursor factor is registered for CN code '{prec.cn_code}'. "
                "Provide a supplier-declared SEE or select a registered default."
            )
        markup_pct = prec.fallback_markup_pct
        if markup_pct is None:
            markup_pct = row.markup_pct
        if markup_pct is None:
            raise ValueError(
                f"Reference precursor factor '{row.item_name}' has no regulatory markup percentage configured."
            )
        see_val = row.ef_value * (1.0 + (markup_pct / 100.0))
        notes.append(
            f"Precursor '{prec.material_name}' (CN {prec.cn_code}): applied reference default SEE of "
            f"{row.ef_value} tCO2/t from '{row.item_name}' with +{markup_pct}% penalty markup -> {see_val:.4f} tCO2/t."
        )
        precursors.append(
            prec.model_copy(
                update={
                    "embedded_see_tco2_per_tonne": see_val,
                    "fallback_markup_pct": markup_pct,
                    "is_fallback": True,
                }
            )
        )

    resolved = request.model_copy(
        update={
            "fuels": fuels,
            "electricity": electricity,
            "precursors": precursors,
        }
    )
    return resolved, notes


def calculate_fuel_emissions(
    fuel: FuelCombustionItem,
    allocation_factor_alpha: float,
    apply_allocation: bool = True,
) -> Tuple[float, float, List[str]]:
    """
    Calculates direct emissions from a single fuel source.
    Direct = Quantity * NCV * EF * OF
    Returns: (gross_emissions_tco2, allocated_emissions_tco2, notes)
    """
    if fuel.ncv is None or fuel.ncv <= 0:
        raise ValueError(f"Missing Net Calorific Value (NCV) for fuel '{fuel.fuel_name}'.")
    if fuel.ef is None or fuel.ef <= 0:
        raise ValueError(f"Missing Emission Factor (EF) for fuel '{fuel.fuel_name}'.")

    of = fuel.oxidation_factor if fuel.oxidation_factor is not None else 1.0

    energy_gj = to_energy_gigajoules(fuel.quantity, fuel.unit, fuel.ncv)
    gross_tco2 = energy_gj * fuel.ef * of

    if apply_allocation and fuel.is_shared_utility:
        allocated_tco2 = gross_tco2 * allocation_factor_alpha
    else:
        allocated_tco2 = gross_tco2

    return gross_tco2, allocated_tco2, []


def calculate_electricity_emissions(
    electricity_item: ElectricityConsumptionItem,
    allocation_factor_alpha: float,
    apply_allocation: bool = True,
) -> Tuple[float, float, List[str]]:
    """
    Calculates indirect emissions from grid / PPA electricity consumption.
    Indirect = Consumed MWh * Grid EF (tCO2/MWh)
    Returns: (gross_emissions_tco2, allocated_emissions_tco2, notes)
    """
    if not electricity_item or electricity_item.consumed_quantity <= 0:
        return 0.0, 0.0, []

    grid_ef = electricity_item.grid_ef_tco2_per_mwh
    if grid_ef is None or grid_ef <= 0:
        raise ValueError(
            f"Missing grid emission factor for electricity source '{electricity_item.source_name}'."
        )

    mwh = to_megawatt_hours(electricity_item.consumed_quantity, electricity_item.unit)
    gross_tco2 = mwh * grid_ef

    if apply_allocation and electricity_item.is_shared_utility:
        allocated_tco2 = gross_tco2 * allocation_factor_alpha
    else:
        allocated_tco2 = gross_tco2

    return gross_tco2, allocated_tco2, []


def calculate_precursor_emissions(
    precursor: PrecursorMaterialItem,
) -> Tuple[float, bool, List[str]]:
    """
    Calculates embedded emissions from a precursor material.
    Emissions = Mass (tonnes) * Embedded SEE (tCO2/tonne)
    Returns: (precursor_tco2, is_fallback_used, notes)
    """
    is_fallback = precursor.is_fallback or precursor.source_type == "eu_default"
    see_val = precursor.embedded_see_tco2_per_tonne
    if see_val is None or see_val <= 0:
        raise ValueError(
            f"Missing embedded SEE for precursor '{precursor.material_name}' (CN {precursor.cn_code})."
        )

    mass_tonnes = to_metric_tonnes(precursor.consumed_quantity, precursor.unit)
    precursor_tco2 = mass_tonnes * see_val
    return precursor_tco2, is_fallback, []


def calculate_product_see(
    request: DeterministicCalculationRequest,
    db: Optional[Session] = None,
) -> DeterministicCalculationResult:
    """
    Master deterministic calculation service.
    Follows official CBAM formula for specific embedded emissions:

    SEE = [Allocated Scope 1 + (delta_CN * Allocated Scope 2) + Precursor Emissions] / Net Target Production (tonnes)
    """
    resolved_request, fallback_notes = _resolve_request_factors(request, db)
    any_fallback_used = any(
        prec.is_fallback or prec.source_type == "eu_default" for prec in resolved_request.precursors
    )

    # 1. Net Target and Total Facility Production (tonnes)
    target_tonnes = to_metric_tonnes(
        resolved_request.production.target_production_quantity,
        resolved_request.production.target_production_unit,
    )
    total_facility_tonnes = to_metric_tonnes(
        resolved_request.production.total_facility_production_quantity,
        resolved_request.production.total_facility_production_unit,
    )

    # 2. Multi-Product Mass Allocation Factor (alpha_p)
    alpha_p = compute_mass_allocation_factor(target_tonnes, total_facility_tonnes)

    # 3. Delta CN Configuration (Scope 2 inclusion flag)
    delta_cn = determine_delta_cn(resolved_request.cn_code, db)

    # 4. Scope 1 Combustion & Process Emissions
    facility_gross_scope1 = 0.0
    allocated_scope1 = 0.0

    for fuel in resolved_request.fuels:
        gross_f, alloc_f, _ = calculate_fuel_emissions(
            fuel,
            alpha_p,
            apply_allocation=resolved_request.apply_allocation_to_shared_energy,
        )
        facility_gross_scope1 += gross_f
        allocated_scope1 += alloc_f

    # Process Emissions (if any)
    if resolved_request.process_emissions_tco2 > 0:
        facility_gross_scope1 += resolved_request.process_emissions_tco2
        allocated_scope1 += (
            resolved_request.process_emissions_tco2 * alpha_p
            if resolved_request.apply_allocation_to_shared_energy
            else resolved_request.process_emissions_tco2
        )

    # 5. Scope 2 Grid Electricity Emissions
    facility_gross_scope2 = 0.0
    allocated_scope2 = 0.0

    if resolved_request.electricity:
        gross_e, alloc_e, _ = calculate_electricity_emissions(
            resolved_request.electricity,
            alpha_p,
            apply_allocation=resolved_request.apply_allocation_to_shared_energy,
        )
        facility_gross_scope2 = gross_e
        allocated_scope2 = alloc_e

    # 6. Precursor Material Emissions
    total_precursor_emissions = 0.0
    for prec in resolved_request.precursors:
        prec_emissions, _, _ = calculate_precursor_emissions(prec)
        total_precursor_emissions += prec_emissions

    # 7. Total Embedded Emissions (tCO2)
    indirect_contribution = allocated_scope2 if delta_cn else 0.0
    total_embedded_emissions = allocated_scope1 + indirect_contribution + total_precursor_emissions

    # 8. Specific Embedded Emissions (SEE in tCO2 / tonne)
    see = total_embedded_emissions / target_tonnes

    # 9. Data Quality Rating
    if any_fallback_used:
        data_quality = DataQualityRating.FALLBACK_DEFAULT
    else:
        has_low_conf = any(f.confidence_score < 0.90 for f in resolved_request.fuels)
        if resolved_request.electricity and resolved_request.electricity.confidence_score < 0.90:
            has_low_conf = True
        if any(p.confidence_score < 0.90 for p in resolved_request.precursors):
            has_low_conf = True

        data_quality = DataQualityRating.HYBRID_ESTIMATED if has_low_conf else DataQualityRating.ACTUAL_PRIMARY

    # 10. LaTeX Formula representation for auditable reporting
    formula_latex = (
        r"\text{SEE} = \frac{\alpha_p \cdot \text{Dir} + \delta_{\text{CN}} \cdot \alpha_p \cdot \text{Ind} "
        r"+ \sum (M_{\text{prec}} \cdot \text{SEE}_{\text{prec}})}{\text{Production (t)}} = "
        rf"\frac{{{allocated_scope1:.4f} + ({1 if delta_cn else 0} \times {allocated_scope2:.4f}) + {total_precursor_emissions:.4f}}}{{{target_tonnes:.2f}}} = {see:.6f}"
        + r"\text{ tCO}_2/\text{t}"
    )

    # 11. Uncertainty Quantification (Monte Carlo simulation)
    uncertainty_result = None
    uncertainty_low = None
    uncertainty_high = None

    if resolved_request.run_monte_carlo:
        from app.modules.uncertainty.monte_carlo import run_monte_carlo_simulation
        uncertainty_result = run_monte_carlo_simulation(
            request=resolved_request,
            deterministic_see=see,
            alpha_p=alpha_p,
            delta_cn=delta_cn,
            target_tonnes=target_tonnes,
            iterations=resolved_request.monte_carlo_iterations,
        )
        uncertainty_low = uncertainty_result.p5
        uncertainty_high = uncertainty_result.p95

    # 12. Input Snapshot Freeze
    input_snapshot = {
        "reporting_period_id": resolved_request.reporting_period_id,
        "cn_code": resolved_request.cn_code,
        "delta_cn": delta_cn,
        "alpha_p": alpha_p,
        "target_production_tonnes": target_tonnes,
        "total_facility_production_tonnes": total_facility_tonnes,
        "fuel_factors": [
            {
                "fuel_name": fuel.fuel_name,
                "quantity": fuel.quantity,
                "unit": fuel.unit,
                "ncv_gj_per_unit": fuel.ncv,
                "ef_tco2_per_gj": fuel.ef,
                "oxidation_factor": fuel.oxidation_factor,
                "is_shared_utility": fuel.is_shared_utility,
            }
            for fuel in resolved_request.fuels
        ],
        "electricity_factor": (
            {
                "source_name": resolved_request.electricity.source_name,
                "consumed_quantity": resolved_request.electricity.consumed_quantity,
                "unit": resolved_request.electricity.unit,
                "grid_ef_tco2_per_mwh": resolved_request.electricity.grid_ef_tco2_per_mwh,
                "is_shared_utility": resolved_request.electricity.is_shared_utility,
            }
            if resolved_request.electricity
            else None
        ),
        "precursor_factors": [
            {
                "material_name": prec.material_name,
                "cn_code": prec.cn_code,
                "consumed_quantity": prec.consumed_quantity,
                "unit": prec.unit,
                "embedded_see_tco2_per_tonne": prec.embedded_see_tco2_per_tonne,
                "source_type": prec.source_type,
                "is_fallback": prec.is_fallback,
                "fallback_markup_pct": prec.fallback_markup_pct,
            }
            for prec in resolved_request.precursors
        ],
        "gross_scope1_tco2": facility_gross_scope1,
        "allocated_scope1_tco2": allocated_scope1,
        "gross_scope2_tco2": facility_gross_scope2,
        "allocated_scope2_tco2": allocated_scope2,
        "precursor_emissions_tco2": total_precursor_emissions,
        "total_embedded_emissions_tco2": total_embedded_emissions,
        "see_tco2_per_tonne": see,
        "data_quality_rating": data_quality.value,
        "fallback_notes": fallback_notes,
    }

    return DeterministicCalculationResult(
        reporting_period_id=resolved_request.reporting_period_id,
        target_cn_code=resolved_request.cn_code,
        target_product_family=resolved_request.production.target_product_family,
        allocation_factor_alpha=alpha_p,
        includes_indirect_delta_cn=delta_cn,
        data_quality_rating=data_quality,
        facility_gross_scope1_tco2=round(facility_gross_scope1, 6),
        allocated_scope1_direct_tco2=round(allocated_scope1, 6),
        facility_gross_scope2_tco2=round(facility_gross_scope2, 6),
        allocated_scope2_indirect_tco2=round(allocated_scope2, 6),
        precursor_emissions_tco2=round(total_precursor_emissions, 6),
        total_embedded_emissions_tco2=round(total_embedded_emissions, 6),
        net_target_production_tonnes=round(target_tonnes, 4),
        specific_embedded_emissions_see=round(see, 6),
        see_unit="tCO2/t",
        uncertainty=uncertainty_result,
        uncertainty_low_see=round(uncertainty_low, 6) if uncertainty_low is not None else None,
        uncertainty_high_see=round(uncertainty_high, 6) if uncertainty_high is not None else None,
        confidence_interval_pct=95.0,
        formula_latex=formula_latex,
        fallback_notes=fallback_notes,
        input_snapshot=input_snapshot,
    )


# =====================================================================
# Priority 1 engine core (Tasks 3 and 6)
#
#   Direct    = sum_i (Q_i x NCV_i x EF_i x OF_i) + Process
#   Allocated = alpha x (shared fuel + shared process) + unshared terms
#   SEE       = (Allocated direct + delta_CN x Allocated indirect + Precursors) / Production
#
# Everything is computed in floating point; rounding is for display only.
# The same evaluation function is used for the deterministic result and for
# every Monte Carlo draw, so the two can never disagree on the formula.
# =====================================================================

from typing import Any, Callable, Dict  # noqa: E402

from app.config import settings  # noqa: E402
from app.modules.calculation.unit_converter import (  # noqa: E402
    FUEL_FORMS,
    UnitError,
    canonical_factor,
    ncv_to_canonical,
)
from app.schemas.workspace import (  # noqa: E402
    EngineInputs,
    EngineResult,
    EngineValue,
    TermBreakdown,
)


class EngineInputError(ValueError):
    """A resolved input is out of range or has an incompatible unit."""


class IncompleteInputError(Exception):
    """Raised when the engine is called on a workspace with open blocking gaps."""

    def __init__(self, gaps: List[Any]):
        self.gaps = gaps
        keys = ", ".join(getattr(g, "input_key", str(g)) for g in gaps)
        super().__init__(f"Calculation refused: required inputs are missing ({keys}).")


_EF_UNITS = {"tco2/gj": 1.0, "kgco2/gj": 0.001, "kgco2/tj": 1e-6, "tco2/tj": 0.001}
_GRID_UNITS = {"tco2/mwh": 1.0, "kgco2/kwh": 1.0, "kgco2/mwh": 0.001, "gco2/kwh": 0.001}

Sampler = Callable[[EngineValue, str], Any]


def _unit_key(unit: str) -> str:
    return (unit or "").strip().lower().replace(" ", "").replace("₂", "2").replace("e/", "/")


def _fuel_form(fuel_key: str, quantity_unit: str) -> str:
    if fuel_key in FUEL_FORMS:
        return FUEL_FORMS[fuel_key]
    for form in ("liquid_fuel", "mass", "gas"):
        try:
            canonical_factor(quantity_unit, form)
            return form
        except UnitError:
            continue
    raise EngineInputError(f"Cannot tell the physical form of fuel '{fuel_key}' from unit '{quantity_unit}'.")


def _validate_engine_inputs(inputs: EngineInputs) -> None:
    def non_negative(ev: Optional[EngineValue], label: str):
        if ev is not None and ev.value < 0:
            raise EngineInputError(f"{label} cannot be negative (got {ev.value}).")

    non_negative(inputs.target_production, "Target production")
    non_negative(inputs.total_installation_production, "Installation production")
    for fuel in inputs.fuels:
        non_negative(fuel.quantity, f"Quantity of {fuel.name}")
        if fuel.ncv.value <= 0:
            raise EngineInputError(f"NCV of {fuel.name} must be greater than 0.")
        if fuel.ef.value < 0:
            raise EngineInputError(f"Emission factor of {fuel.name} cannot be negative.")
        of = fuel.oxidation_factor.value
        if not (0.0 <= of <= 1.0):
            raise EngineInputError(f"Oxidation factor of {fuel.name} must be between 0 and 1 (got {of}).")
    non_negative(inputs.electricity, "Electricity consumed")
    non_negative(inputs.grid_ef, "Grid emission factor")
    for prec in inputs.precursors:
        non_negative(prec.quantity, f"Quantity of {prec.name}")
        non_negative(prec.see, f"Supplier SEE of {prec.name}")
    for src in inputs.process_sources:
        non_negative(src.activity, f"Activity of process source {src.name}")
        non_negative(src.factor, f"Factor of process source {src.name}")
        non_negative(src.direct, f"Direct emissions of process source {src.name}")


def _evaluate(inputs: EngineInputs, sample: Sampler) -> Dict[str, Any]:
    """Evaluates the formula. ``sample`` returns a float or a numpy array per input."""
    try:
        target = sample(inputs.target_production, "production") * canonical_factor(
            inputs.target_production.unit, "mass"
        )
        if inputs.utility_coverage == "dedicated":
            alpha = 1.0
        else:
            if inputs.total_installation_production is None:
                raise EngineInputError("Total installation production is required for a shared installation.")
            total = sample(inputs.total_installation_production, "production") * canonical_factor(
                inputs.total_installation_production.unit, "mass"
            )
            alpha = target / total

        fuel_terms = []
        for fuel in inputs.fuels:
            form = _fuel_form(fuel.key, fuel.quantity.unit)
            q = sample(fuel.quantity, "fuel") * canonical_factor(fuel.quantity.unit, form)
            ncv = sample(fuel.ncv, "factor") * ncv_to_canonical(1.0, fuel.ncv.unit, form)
            ef_scale = _EF_UNITS.get(_unit_key(fuel.ef.unit))
            if ef_scale is None:
                raise EngineInputError(f"Emission factor unit '{fuel.ef.unit}' must be tCO2/GJ.")
            ef = sample(fuel.ef, "factor") * ef_scale
            of = sample(fuel.oxidation_factor, "of")
            raw = q * ncv * ef * of
            fallback = any(v.source_type == "fallback" for v in (fuel.quantity, fuel.ncv, fuel.ef, fuel.oxidation_factor))
            fuel_terms.append((fuel.key, fuel.name, raw, fuel.is_shared, fallback))

        process_terms = []
        for src in inputs.process_sources:
            if src.method == "direct":
                if src.direct is None:
                    raise EngineInputError(f"Process source {src.name} needs a direct tCO2 value.")
                raw = sample(src.direct, "process_direct")
                fallback = src.direct.source_type == "fallback"
            else:
                if src.activity is None or src.factor is None:
                    raise EngineInputError(f"Process source {src.name} needs activity data and a factor.")
                raw = sample(src.activity, "process_activity") * sample(src.factor, "factor")
                fallback = src.factor.source_type == "fallback"
            process_terms.append((src.name, src.name, raw, src.is_shared, fallback))

        scope2_raw = 0.0
        scope2_fallback = False
        if inputs.electricity is not None:
            if inputs.grid_ef is None:
                raise EngineInputError("A grid emission factor is required when electricity is consumed.")
            grid_scale = _GRID_UNITS.get(_unit_key(inputs.grid_ef.unit))
            if grid_scale is None:
                raise EngineInputError(f"Grid factor unit '{inputs.grid_ef.unit}' must be tCO2/MWh.")
            mwh = sample(inputs.electricity, "electricity") * canonical_factor(inputs.electricity.unit, "electricity")
            scope2_raw = mwh * sample(inputs.grid_ef, "factor") * grid_scale
            scope2_fallback = "fallback" in (inputs.electricity.source_type, inputs.grid_ef.source_type)

        precursor_terms = []
        for prec in inputs.precursors:
            qty = sample(prec.quantity, "precursor") * canonical_factor(prec.quantity.unit, "mass")
            see = sample(prec.see, "factor") * canonical_factor(prec.see.unit, "see")
            precursor_terms.append((prec.key, prec.name, qty * see, False, prec.see.source_type == "fallback"))
    except UnitError as exc:
        raise EngineInputError(str(exc)) from exc

    def allocate(raw, shared):
        return raw * alpha if shared else raw

    fuel_raw = sum((t[2] for t in fuel_terms), 0.0)
    process_raw = sum((t[2] for t in process_terms), 0.0)
    scope1_alloc = sum((allocate(t[2], t[3]) for t in fuel_terms + process_terms), 0.0)
    scope2_alloc = allocate(scope2_raw, inputs.electricity_is_shared)
    scope2_counted = scope2_alloc if inputs.includes_indirect else 0.0 * scope2_alloc
    precursor_total = sum((t[2] for t in precursor_terms), 0.0)
    total = scope1_alloc + scope2_counted + precursor_total
    fallback_total = (
        sum((allocate(t[2], t[3]) for t in fuel_terms + process_terms if t[4]), 0.0)
        + (scope2_counted if scope2_fallback else 0.0)
        + sum((t[2] for t in precursor_terms if t[4]), 0.0)
    )
    return {
        "alpha": alpha,
        "target": target,
        "fuel_terms": fuel_terms,
        "process_terms": process_terms,
        "precursor_terms": precursor_terms,
        "fuel_raw": fuel_raw,
        "process_raw": process_raw,
        "scope1_raw": fuel_raw + process_raw,
        "scope1_alloc": scope1_alloc,
        "scope2_raw": scope2_raw,
        "scope2_alloc": scope2_alloc,
        "scope2_counted": scope2_counted,
        "scope2_fallback": scope2_fallback and inputs.includes_indirect,
        "precursor_total": precursor_total,
        "total": total,
        "fallback_total": fallback_total,
        "see": total / target,
        "allocate": allocate,
    }


def data_quality_label(any_fallback_counted: bool, fallback_share: float, threshold: Optional[float] = None) -> str:
    """A = all measured, B = fallbacks under the threshold, C = at or above it."""
    threshold = settings.DQ_C_THRESHOLD if threshold is None else threshold
    if not any_fallback_counted:
        return "A"
    return "B" if fallback_share < threshold else "C"


def compute_emissions(inputs: EngineInputs) -> EngineResult:
    """Deterministic Priority 1 engine. Returns the full breakdown."""
    _validate_engine_inputs(inputs)
    if inputs.target_production.value <= 0:
        raise EngineInputError("Target production quantity must be greater than 0.")
    if inputs.utility_coverage == "shared":
        total = inputs.total_installation_production
        if total is None or total.value <= 0:
            raise EngineInputError("Total installation production must be greater than 0 for a shared installation.")

    r = _evaluate(inputs, lambda ev, role: ev.value)
    if r["alpha"] > 1.0 + 1e-12:
        raise EngineInputError("Target production cannot exceed total installation production.")

    allocate = r["allocate"]

    def breakdown(terms):
        return [
            TermBreakdown(key=k, name=n or k, raw_tco2=raw, allocated_tco2=allocate(raw, shared), fallback_based=fb)
            for k, n, raw, shared, fb in terms
        ]

    any_fallback = (
        any(t[4] for t in r["fuel_terms"] + r["process_terms"] + r["precursor_terms"]) or r["scope2_fallback"]
    )
    share = r["fallback_total"] / r["total"] if r["total"] > 0 else 0.0

    factor_ids: List[int] = []
    values: List[Optional[EngineValue]] = [inputs.target_production, inputs.total_installation_production,
                                          inputs.electricity, inputs.grid_ef]
    for fuel in inputs.fuels:
        values += [fuel.quantity, fuel.ncv, fuel.ef, fuel.oxidation_factor]
    for src in inputs.process_sources:
        values += [src.activity, src.factor, src.direct]
    for prec in inputs.precursors:
        values += [prec.quantity, prec.see]
    for ev in values:
        if ev is not None and ev.factor_id is not None and ev.factor_id not in factor_ids:
            factor_ids.append(ev.factor_id)

    notes = []
    if inputs.precursors and all(p.directly_attributable for p in inputs.precursors):
        notes.append("Precursor emissions are not allocated: the precursors are directly attributable to the target product.")
    if not inputs.includes_indirect:
        notes.append("Indirect (Scope 2) emissions are calculated but not counted for this CN code.")

    return EngineResult(
        alpha=r["alpha"],
        fuels=breakdown(r["fuel_terms"]),
        process=breakdown(r["process_terms"]),
        precursors=[
            TermBreakdown(key=k, name=n or k, raw_tco2=raw, allocated_tco2=raw, fallback_based=fb)
            for k, n, raw, _, fb in r["precursor_terms"]
        ],
        scope1_fuel_raw_tco2=r["fuel_raw"],
        process_raw_tco2=r["process_raw"],
        scope1_raw_tco2=r["scope1_raw"],
        scope1_allocated_tco2=r["scope1_alloc"],
        scope2_raw_tco2=r["scope2_raw"],
        scope2_allocated_tco2=r["scope2_alloc"],
        scope2_counted_tco2=r["scope2_counted"],
        precursor_tco2=r["precursor_total"],
        total_tco2=r["total"],
        target_production_t=r["target"],
        see_tco2_per_t=r["see"],
        fallback_tco2=r["fallback_total"],
        fallback_share=share,
        data_quality_label=data_quality_label(any_fallback, share),
        factor_ids_used=factor_ids,
        notes=notes,
    )

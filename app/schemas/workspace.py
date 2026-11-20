"""Pydantic v2 models for workspace inputs and the Priority 1 engine.

``WorkspaceInputs`` is what the user has provided (values may be missing).
``EngineInputs`` is the fully resolved input set the deterministic engine runs
on: every value is present and carries its source type and uncertainty.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

SourceType = Literal["measured", "fallback"]


class SourcedValue(BaseModel):
    """A user-provided value with its unit and evidence reference."""

    value: Optional[float] = None
    unit: Optional[str] = None
    evidence_ref: Optional[str] = None
    rel_uncertainty: Optional[float] = Field(default=None, ge=0, le=1)

    @property
    def present(self) -> bool:
        return self.value is not None


class FuelInput(BaseModel):
    fuel_key: str = Field(..., description="Register slug, e.g. furnace_oil")
    name: Optional[str] = None
    quantity: SourcedValue = Field(default_factory=SourcedValue)
    ncv: SourcedValue = Field(default_factory=SourcedValue)
    ef: SourcedValue = Field(default_factory=SourcedValue)
    oxidation_factor: SourcedValue = Field(default_factory=SourcedValue)
    is_shared: bool = True


class ProcessSource(BaseModel):
    name: str
    method: Literal["activity", "direct"]
    activity_quantity: Optional[float] = None
    activity_unit: Optional[str] = None
    factor_key: Optional[str] = Field(default=None, description="Register key of the process factor")
    direct_tco2: Optional[float] = None
    evidence_ref: Optional[str] = None
    rel_uncertainty: Optional[float] = Field(default=None, ge=0, le=1)
    is_shared: bool = True


class ProcessDeclaration(BaseModel):
    """Process emissions must be declared: either a list of sources or 'none' with a reason."""

    mode: Optional[Literal["none", "sources"]] = None
    reason: Optional[str] = None
    sources: List[ProcessSource] = Field(default_factory=list)


class ElectricityInput(BaseModel):
    consumed: SourcedValue = Field(default_factory=SourcedValue)
    grid_ef: SourcedValue = Field(default_factory=SourcedValue)
    is_shared: bool = True


class PrecursorInput(BaseModel):
    key: str = Field(..., description="Slug used in gap keys, e.g. wire_rod")
    name: Optional[str] = None
    cn_code: Optional[str] = None
    country_of_origin: Optional[str] = None
    quantity: SourcedValue = Field(default_factory=SourcedValue)
    supplier_see: SourcedValue = Field(default_factory=SourcedValue)
    directly_attributable: bool = True


class ProductionInput(BaseModel):
    product_family: Optional[str] = None
    target: SourcedValue = Field(default_factory=SourcedValue)
    total_installation: SourcedValue = Field(default_factory=SourcedValue)
    utility_coverage: Literal["shared", "dedicated"] = "shared"


class WorkspaceInputs(BaseModel):
    cn_code: Optional[str] = None
    includes_indirect: bool = True
    production: ProductionInput = Field(default_factory=ProductionInput)
    uses_fuel: bool = True
    fuels: List[FuelInput] = Field(default_factory=list)
    electricity: ElectricityInput = Field(default_factory=ElectricityInput)
    precursors: List[PrecursorInput] = Field(default_factory=list)
    process: ProcessDeclaration = Field(default_factory=ProcessDeclaration)


# ---------------------------------------------------------------------
# Resolved engine inputs
# ---------------------------------------------------------------------


class UncertaintySpec(BaseModel):
    kind: Literal["none", "relative", "triangular"] = "none"
    rel: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None


class EngineValue(BaseModel):
    value: float
    unit: str
    source_type: SourceType = "measured"
    factor_id: Optional[int] = None
    factor_set_version: Optional[str] = None
    uncertainty: UncertaintySpec = Field(default_factory=UncertaintySpec)


class EngineFuel(BaseModel):
    key: str
    name: str
    quantity: EngineValue
    ncv: EngineValue
    ef: EngineValue
    oxidation_factor: EngineValue
    is_shared: bool = True


class EngineProcessSource(BaseModel):
    name: str
    method: Literal["activity", "direct"]
    activity: Optional[EngineValue] = None
    factor: Optional[EngineValue] = None
    direct: Optional[EngineValue] = None
    evidence_ref: Optional[str] = None
    is_shared: bool = True


class EnginePrecursor(BaseModel):
    key: str
    name: str
    quantity: EngineValue
    see: EngineValue
    directly_attributable: bool = True


class EngineInputs(BaseModel):
    target_production: EngineValue
    total_installation_production: Optional[EngineValue] = None
    utility_coverage: Literal["shared", "dedicated"] = "shared"
    includes_indirect: bool = True
    fuels: List[EngineFuel] = Field(default_factory=list)
    process_sources: List[EngineProcessSource] = Field(default_factory=list)
    electricity: Optional[EngineValue] = None
    grid_ef: Optional[EngineValue] = None
    electricity_is_shared: bool = True
    precursors: List[EnginePrecursor] = Field(default_factory=list)


class TermBreakdown(BaseModel):
    key: str
    name: str
    raw_tco2: float
    allocated_tco2: float
    fallback_based: bool


class EngineResult(BaseModel):
    alpha: float
    fuels: List[TermBreakdown]
    process: List[TermBreakdown]
    precursors: List[TermBreakdown]
    scope1_fuel_raw_tco2: float
    process_raw_tco2: float
    scope1_raw_tco2: float
    scope1_allocated_tco2: float
    scope2_raw_tco2: float
    scope2_allocated_tco2: float
    scope2_counted_tco2: float
    precursor_tco2: float
    total_tco2: float
    target_production_t: float
    see_tco2_per_t: float
    fallback_tco2: float
    fallback_share: float
    data_quality_label: Literal["A", "B", "C"]
    factor_ids_used: List[int] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

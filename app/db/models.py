import datetime
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship, synonym
from app.db.database import Base


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


class Company(Base):
    """Company / Enterprise representing the exporting entity."""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    legal_name = Column(String(255), nullable=False, unique=True)
    country = Column(String(100), default="India")
    eori_number = Column(String(50), nullable=True)
    registration_number = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    contact_email = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    # Relationships
    installations = relationship("Installation", back_populates="company", cascade="all, delete-orphan")


class Installation(Base):
    """Specific manufacturing site / plant where emissions occur."""
    __tablename__ = "installations"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String(255), nullable=False)
    un_locode = Column(String(10), nullable=True)
    location = Column(String(255), nullable=False)
    country_code = Column(String(5), default="IN")
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    # Relationships
    company = relationship("Company", back_populates="installations")
    reporting_periods = relationship("ReportingPeriod", back_populates="installation", cascade="all, delete-orphan")


class CNCodeRegistry(Base):
    """Registry of EU CBAM covered 8-digit Combined Nomenclature (CN) codes."""
    __tablename__ = "cn_code_registry"

    cn_code = Column(String(20), primary_key=True, index=True)  # e.g. "7318 15 82"
    description = Column(String(500), nullable=False)
    sector = Column(String(100), default="Iron and Steel")
    includes_indirect_emissions = Column(Boolean, default=True)  # delta_CN
    requires_precursors = Column(Boolean, default=True)
    default_unit = Column(String(20), default="tonne")
    notes = Column(Text, nullable=True)

    # Relationships
    reporting_periods = relationship("ReportingPeriod", back_populates="cn_registry")


class CBAMDefaultValues(Base):
    """Standardized and versioned official default values for fuels, grid, and precursors."""
    __tablename__ = "cbam_default_values"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(50), nullable=False, index=True)  # 'fuel', 'electricity', 'precursor', 'process'
    item_name = Column(String(100), nullable=False)  # e.g. 'Diesel', 'Furnace Oil', 'Natural Gas', 'Indian Grid (CEA)', 'Crude Steel'
    unit = Column(String(30), nullable=False)  # 'litre', 'kg', 'Nm3', 'kWh', 'tonne'
    ncv_value = Column(Float, nullable=True)  # Net Calorific Value (e.g. GJ/tonne, MJ/kg)
    ncv_unit = Column(String(30), nullable=True)
    ef_value = Column(Float, nullable=False)  # Emission factor (e.g. tCO2/GJ, tCO2/MWh, tCO2/t)
    ef_unit = Column(String(30), nullable=False)
    oxidation_factor = Column(Float, default=1.0)
    geographic_scope = Column(String(100), default="India / Global")
    source_reference = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    cn_code = Column(String(20), nullable=True, index=True)  # Precursor defaults are keyed to a CN code
    markup_pct = Column(Float, nullable=True)  # Regulatory penalty markup applied to unverified defaults


class ReportingPeriod(Base):
    """A distinct compliance reporting period (e.g., Year 2025/2026) for an installation."""
    __tablename__ = "reporting_periods"

    id = Column(Integer, primary_key=True, index=True)
    installation_id = Column(Integer, ForeignKey("installations.id"), nullable=False)
    year = Column(Integer, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    target_cn_code = Column(String(20), ForeignKey("cn_code_registry.cn_code"), nullable=False)
    status = Column(String(50), default="Draft")  # 'Draft', 'User-Confirmed', 'Expert-Reviewed', 'Externally Verified'
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    # Relationships
    installation = relationship("Installation", back_populates="reporting_periods")
    cn_registry = relationship("CNCodeRegistry", back_populates="reporting_periods")
    uploaded_files = relationship("UploadedFile", back_populates="reporting_period", cascade="all, delete-orphan")
    production_data = relationship("ProductionData", back_populates="reporting_period", uselist=False, cascade="all, delete-orphan")
    calculation_results = relationship("CalculationResult", back_populates="reporting_period", cascade="all, delete-orphan")
    audit_trail = relationship("AuditTrail", back_populates="reporting_period", cascade="all, delete-orphan")


class DocumentRecord(Base):
    """Document metadata record indexed by cryptographic SHA-256 hash."""
    __tablename__ = "document_records"

    id = Column(Integer, primary_key=True, index=True)
    document_hash = Column(String(64), unique=True, index=True, nullable=False)
    filename = Column(String(255), nullable=False)
    workspace_id = Column(String(100), nullable=False, index=True)
    page_count = Column(Integer, default=1)
    file_size_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    # Billing metadata used by validation rules V2 (overlap) and V3 (near duplicate).
    # Values are pre-filled from the document text where possible and editable by the user.
    supplier = Column(String(255), nullable=True)
    account_number = Column(String(100), nullable=True)  # meter / consumer number
    utility_type = Column(String(50), nullable=True)  # electricity / fuel / precursor / production
    bill_number = Column(String(100), nullable=True)
    bill_date = Column(Date, nullable=True)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)  # inclusive
    is_excluded = Column(Boolean, default=False)  # soft removal; the record is never deleted

    # Relationships
    extracted_fields = relationship("ExtractedFieldAudit", back_populates="document_record", cascade="all, delete-orphan")


class UploadedFile(Base):
    """File uploaded by the user with cryptographic SHA-256 hash for tamper evidence."""
    __tablename__ = "uploaded_files"

    id = Column(Integer, primary_key=True, index=True)
    reporting_period_id = Column(Integer, ForeignKey("reporting_periods.id"), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    file_type = Column(String(100), nullable=False)
    sha256_hash = Column(String(64), nullable=False, index=True)
    doc_category = Column(String(50), nullable=True)
    upload_timestamp = Column(DateTime, default=utc_now)

    # Relationships
    reporting_period = relationship("ReportingPeriod", back_populates="uploaded_files")


class ExtractedFieldAudit(Base):
    """Audit ledger entry for Document AI extracted field, preserving raw and user-override values."""
    __tablename__ = "extracted_fields_audit"

    id = Column(Integer, primary_key=True, index=True)
    document_hash = Column(String(64), ForeignKey("document_records.document_hash"), nullable=True, index=True)
    reporting_period_id = Column(Integer, ForeignKey("reporting_periods.id"), nullable=True)
    field_name = Column(String(100), nullable=False)
    field_category = Column(String(50), nullable=True, default="scope1_fuel")

    # Raw Model Extraction
    raw_extracted_value = Column(String(100), nullable=True)
    raw_unit = Column(String(30), nullable=True)
    raw_extracted_unit = synonym("raw_unit")
    confidence_score = Column(Float, default=0.85)
    evidence_page = Column(Integer, default=1)
    evidence_snippet = Column(Text, nullable=True)

    # Effective / Override Values
    effective_value = Column(String(100), nullable=True)
    effective_unit = Column(String(30), nullable=True)
    was_overridden = Column(Boolean, default=False)
    user_override_value = Column(String(100), nullable=True)
    user_override_unit = Column(String(30), nullable=True)
    modified_by_user_id = Column(String(100), nullable=True)
    override_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    # Unit normalisation (rule V1). The original value/unit columns above are never overwritten.
    field_type = Column(String(30), nullable=True)  # electricity / liquid_fuel / gas / mass / see
    canonical_value = Column(Float, nullable=True)
    canonical_unit = Column(String(30), nullable=True)

    # Field-level review (precondition for Draft -> User-Confirmed)
    reviewed = Column(Boolean, default=False)
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    # Relationships
    document_record = relationship("DocumentRecord", back_populates="extracted_fields")


class ProductionData(Base):
    """Net production and shared facility multi-product allocation parameters."""
    __tablename__ = "production_data"

    id = Column(Integer, primary_key=True, index=True)
    reporting_period_id = Column(Integer, ForeignKey("reporting_periods.id"), unique=True, nullable=False)
    target_product_family = Column(String(255), nullable=False)
    target_production_tonnes = Column(Float, nullable=False)
    total_facility_production_tonnes = Column(Float, nullable=False)
    allocation_factor_alpha = Column(Float, nullable=False)
    scrap_tonnes = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    # Relationships
    reporting_period = relationship("ReportingPeriod", back_populates="production_data")


class CalculationResult(Base):
    """Deterministic calculation output with complete transparency freeze and uncertainty bounds."""
    __tablename__ = "calculation_results"

    id = Column(Integer, primary_key=True, index=True)
    reporting_period_id = Column(Integer, ForeignKey("reporting_periods.id"), nullable=False)
    scope1_direct_tco2 = Column(Float, nullable=False)
    scope2_indirect_tco2 = Column(Float, nullable=False)
    precursor_emissions_tco2 = Column(Float, nullable=False)
    total_embedded_emissions_tco2 = Column(Float, nullable=False)
    specific_embedded_emissions_see = Column(Float, nullable=False)
    see_unit = Column(String(20), default="tCO2/t")
    includes_indirect = Column(Boolean, nullable=False)

    # Uncertainty Quantification
    uncertainty_low_see = Column(Float, nullable=True)
    uncertainty_high_see = Column(Float, nullable=True)
    confidence_interval_pct = Column(Float, default=95.0)

    # Audit & Replicability
    formula_applied = Column(Text, nullable=False)
    input_snapshot_json = Column(Text, nullable=False)
    status = Column(String(50), default="Calculated")
    calculation_timestamp = Column(DateTime, default=utc_now)

    # Relationships
    reporting_period = relationship("ReportingPeriod", back_populates="calculation_results")


class AuditTrail(Base):
    """Immutable audit trail for all operations performed in the reporting workspace."""
    __tablename__ = "audit_trail"

    id = Column(Integer, primary_key=True, index=True)
    reporting_period_id = Column(Integer, ForeignKey("reporting_periods.id"), nullable=True)
    action = Column(String(100), nullable=False)
    performed_by = Column(String(100), default="System")
    timestamp = Column(DateTime, default=utc_now)
    details_json = Column(Text, nullable=True)

    # Relationships
    reporting_period = relationship("ReportingPeriod", back_populates="audit_trail")


# =====================================================================
# Priority 1 additions: factor register, workspaces, validation, gaps,
# assumptions, passport versions and the generic audit log.
# =====================================================================


class FactorSet(Base):
    """An immutable, versioned set of emission factors (Task 5)."""
    __tablename__ = "factor_set"

    version = Column(String(20), primary_key=True)  # e.g. "2026.1"
    created_at = Column(DateTime, default=utc_now)
    checksum = Column(String(64), nullable=False)
    notes = Column(Text, nullable=True)

    factors = relationship("EmissionFactor", back_populates="factor_set")


class EmissionFactor(Base):
    """A single cited factor. Rows are never edited or deleted in place."""
    __tablename__ = "emission_factor"

    id = Column(Integer, primary_key=True, index=True)
    factor_set_version = Column(String(20), ForeignKey("factor_set.version"), nullable=False, index=True)
    key = Column(String(120), nullable=False, index=True)  # e.g. fuel.furnace_oil.ef
    value = Column(Float, nullable=False)
    unit = Column(String(30), nullable=False)
    source_title = Column(String(500), nullable=False)
    publisher = Column(String(255), nullable=False)
    source_url = Column(String(500), nullable=False)
    year = Column(Integer, nullable=False)
    valid_from = Column(Date, nullable=True)
    valid_to = Column(Date, nullable=True)
    is_fallback = Column(Boolean, default=True)
    allowed = Column(Boolean, default=True)  # may be applied as a fallback
    uncertainty_type = Column(String(20), nullable=True)  # "relative" or "triangular"
    uncertainty_rel = Column(Float, nullable=True)  # +/- fraction at 95 %
    uncertainty_low = Column(Float, nullable=True)
    uncertainty_high = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(20), default="active")  # active / superseded

    factor_set = relationship("FactorSet", back_populates="factors")


class Workspace(Base):
    """Server-side workspace: the reviewed inputs a passport is calculated from."""
    __tablename__ = "workspaces"

    id = Column(String(100), primary_key=True)  # same id used for DocumentRecord.workspace_id
    company_name = Column(String(255), nullable=True)
    installation_name = Column(String(255), nullable=True)
    cn_code = Column(String(20), nullable=True)
    reporting_start = Column(Date, nullable=True)
    reporting_end = Column(Date, nullable=True)
    factor_set_version = Column(String(20), nullable=True)
    inputs_json = Column(Text, nullable=True)
    calc_status = Column(String(20), default="Incomplete")  # Incomplete / Ready
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class ValidationFinding(Base):
    """Result of a validation rule (Task 1)."""
    __tablename__ = "validation_findings"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(String(100), nullable=False, index=True)
    fingerprint = Column(String(255), nullable=False, index=True)
    rule_id = Column(String(20), nullable=False)  # V1_UNIT / V2_OVERLAP / V3_DUPLICATE
    severity = Column(String(10), nullable=False)  # error / warning
    document_id = Column(Integer, nullable=True)
    field_id = Column(Integer, nullable=True)
    related_document_id = Column(Integer, nullable=True)
    message = Column(Text, nullable=False)
    suggested_fix = Column(Text, nullable=True)
    status = Column(String(10), default="open")  # open / resolved / waived
    resolution_note = Column(Text, nullable=True)
    resolved_by = Column(String(100), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)


class DataGap(Base):
    """A required input that is missing (Task 2)."""
    __tablename__ = "data_gaps"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(String(100), nullable=False, index=True)
    input_key = Column(String(120), nullable=False)
    description = Column(Text, nullable=False)
    fallback_available = Column(Boolean, default=False)
    status = Column(String(20), default="open")  # open / filled_by_user / fallback_applied / waived
    created_at = Column(DateTime, default=utc_now)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(100), nullable=True)


class Assumption(Base):
    """A fallback value explicitly applied by a user (Task 3)."""
    __tablename__ = "assumptions"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(String(100), nullable=False, index=True)
    input_key = Column(String(120), nullable=False)
    value_used = Column(Float, nullable=False)
    unit = Column(String(30), nullable=False)
    factor_id = Column(Integer, ForeignKey("emission_factor.id"), nullable=False)
    factor_set_version = Column(String(20), nullable=False)
    reason = Column(Text, nullable=False)
    applied_by = Column(String(100), nullable=False)
    applied_at = Column(DateTime, default=utc_now)
    status = Column(String(20), default="active")  # active / withdrawn


class PassportVersion(Base):
    """A numbered passport snapshot carrying one review status (Task 4)."""
    __tablename__ = "passport_version"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(String(100), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    status = Column(String(30), default="Draft")
    input_hash = Column(String(64), nullable=False)
    factor_set_version = Column(String(20), nullable=True)
    inputs_snapshot_json = Column(Text, nullable=True)
    assumptions_snapshot_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    factor_ids_json = Column(Text, nullable=True)
    mc_seed = Column(Integer, nullable=True)
    verifier_details_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)


class StatusHistory(Base):
    __tablename__ = "status_history"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(String(100), nullable=False, index=True)
    passport_version_id = Column(Integer, ForeignKey("passport_version.id"), nullable=True)
    version_number = Column(Integer, nullable=True)
    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=False)
    actor = Column(String(100), nullable=False)
    role = Column(String(30), nullable=False)
    note = Column(Text, nullable=True)
    extra_json = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=utc_now)


class AuditLog(Base):
    """Generic audit row: who, when, old value, new value, reason."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(String(100), nullable=True, index=True)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(120), nullable=True)
    action = Column(String(60), nullable=False)
    actor = Column(String(100), default="system")
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=utc_now)


# ---------------------------------------------------------------------
# Register immutability guard (Task 5): factor rows may only change
# status (active -> superseded); any other update or any delete is refused.
# ---------------------------------------------------------------------
from sqlalchemy import event, inspect as sa_inspect  # noqa: E402
from sqlalchemy.orm import Session as _Session  # noqa: E402


class ImmutableFactorError(Exception):
    """Raised when code tries to edit or delete a register row in place."""


_MUTABLE_FACTOR_COLUMNS = {"status"}


@event.listens_for(_Session, "before_flush")
def _guard_factor_register(session, flush_context, instances):
    for obj in session.dirty:
        if isinstance(obj, (EmissionFactor, FactorSet)) and session.is_modified(obj):
            state = sa_inspect(obj)
            changed = {attr.key for attr in state.attrs if attr.history.has_changes()}
            allowed = _MUTABLE_FACTOR_COLUMNS if isinstance(obj, EmissionFactor) else set()
            if changed - allowed:
                raise ImmutableFactorError(
                    f"{type(obj).__name__} rows are immutable; attempted to change {sorted(changed - allowed)}. "
                    "Add a new row in a new factor_set instead."
                )
            if isinstance(obj, EmissionFactor) and "status" in changed:
                old = state.attrs.status.history.deleted
                if obj.status != "superseded" or (old and old[0] != "active"):
                    raise ImmutableFactorError("A factor can only move from 'active' to 'superseded'.")
    for obj in session.deleted:
        if isinstance(obj, (EmissionFactor, FactorSet)):
            raise ImmutableFactorError(f"{type(obj).__name__} rows cannot be deleted.")

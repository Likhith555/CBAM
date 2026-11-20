"""Versioned emission-factor register (Task 5).

Rules
-----
* Factor rows are never edited or deleted in place (enforced by a SQLAlchemy
  ``before_flush`` guard in ``app.db.models``).
* A new value is added as a row in a *new* factor set; the previous active row
  for the same key is marked ``superseded``.
* A factor set may contain only the keys that changed. Looking up a key for
  version V returns the row from the newest set whose version is <= V, so a
  passport pinned to ``2026.1`` resolves exactly the same rows after
  ``2026.2`` is added.
"""

import datetime
import hashlib
import json
from typing import Dict, Iterable, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, HttpUrl, model_validator
from sqlalchemy.orm import Session

from app.db.models import EmissionFactor, FactorSet


class FactorSpec(BaseModel):
    """Input model for a new register row. Every row needs a real citation."""

    key: str = Field(..., min_length=3)
    value: float
    unit: str
    source_title: str = Field(..., min_length=3)
    publisher: str = Field(..., min_length=2)
    source_url: HttpUrl
    year: int = Field(..., ge=1990, le=2100)
    valid_from: Optional[datetime.date] = None
    valid_to: Optional[datetime.date] = None
    is_fallback: bool = True
    allowed: bool = True
    uncertainty_type: Optional[Literal["relative", "triangular"]] = None
    uncertainty_rel: Optional[float] = Field(default=None, ge=0, le=1)
    uncertainty_low: Optional[float] = None
    uncertainty_high: Optional[float] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_uncertainty(self):
        if self.uncertainty_type == "relative" and self.uncertainty_rel is None:
            raise ValueError("relative uncertainty needs uncertainty_rel")
        if self.uncertainty_type == "triangular":
            if self.uncertainty_low is None or self.uncertainty_high is None:
                raise ValueError("triangular uncertainty needs low and high")
            if not (self.uncertainty_low <= self.value <= self.uncertainty_high):
                raise ValueError("triangular uncertainty needs low <= value <= high")
            if self.uncertainty_low == self.uncertainty_high:
                raise ValueError("triangular uncertainty needs low < high")
        return self


class FactorRead(BaseModel):
    id: int
    factor_set_version: str
    key: str
    value: float
    unit: str
    source_title: str
    publisher: str
    source_url: str
    year: int
    valid_from: Optional[datetime.date] = None
    valid_to: Optional[datetime.date] = None
    is_fallback: bool
    allowed: bool
    uncertainty_type: Optional[str] = None
    uncertainty_rel: Optional[float] = None
    uncertainty_low: Optional[float] = None
    uncertainty_high: Optional[float] = None
    notes: Optional[str] = None
    status: str

    model_config = {"from_attributes": True}


class FactorSetError(ValueError):
    pass


def _version_key(version: str) -> Tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError as exc:  # pragma: no cover - guarded by create_factor_set
        raise FactorSetError(f"Factor set version '{version}' must look like 2026.1") from exc


def _checksum(specs: Iterable[FactorSpec]) -> str:
    payload = [
        spec.model_dump(mode="json")
        for spec in sorted(specs, key=lambda item: item.key)
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def list_versions(db: Session) -> List[str]:
    return sorted((row.version for row in db.query(FactorSet).all()), key=_version_key)


def latest_version(db: Session) -> Optional[str]:
    versions = list_versions(db)
    return versions[-1] if versions else None


def create_factor_set(
    db: Session,
    version: str,
    factors: List[FactorSpec],
    notes: Optional[str] = None,
) -> FactorSet:
    """Adds a new factor set. Older active rows for the same keys become superseded."""
    _version_key(version)
    if db.get(FactorSet, version) is not None:
        raise FactorSetError(f"Factor set {version} already exists; versions are immutable.")
    existing = list_versions(db)
    if existing and _version_key(version) <= _version_key(existing[-1]):
        raise FactorSetError(f"New factor set {version} must be newer than {existing[-1]}.")
    keys = [spec.key for spec in factors]
    if len(keys) != len(set(keys)):
        raise FactorSetError("A factor set cannot contain the same key twice.")

    factor_set = FactorSet(version=version, checksum=_checksum(factors), notes=notes)
    db.add(factor_set)

    for row in db.query(EmissionFactor).filter(
        EmissionFactor.key.in_(keys), EmissionFactor.status == "active"
    ):
        row.status = "superseded"

    for spec in factors:
        data = spec.model_dump()
        data["source_url"] = str(spec.source_url)
        db.add(EmissionFactor(factor_set_version=version, status="active", **data))
    db.flush()
    return factor_set


def get_factor(db: Session, key: str, version: str) -> Optional[EmissionFactor]:
    """Returns the row for ``key`` as it stood at factor-set ``version``."""
    target = _version_key(version)
    candidates = db.query(EmissionFactor).filter(EmissionFactor.key == key).all()
    eligible = [row for row in candidates if _version_key(row.factor_set_version) <= target]
    if not eligible:
        return None
    return max(eligible, key=lambda row: _version_key(row.factor_set_version))


def resolve_factors(db: Session, version: str) -> Dict[str, EmissionFactor]:
    """All keys as they stood at ``version``."""
    target = _version_key(version)
    resolved: Dict[str, EmissionFactor] = {}
    for row in db.query(EmissionFactor).all():
        if _version_key(row.factor_set_version) > target:
            continue
        current = resolved.get(row.key)
        if current is None or _version_key(row.factor_set_version) > _version_key(current.factor_set_version):
            resolved[row.key] = row
    return dict(sorted(resolved.items()))


def get_factor_by_id(db: Session, factor_id: int) -> Optional[EmissionFactor]:
    return db.get(EmissionFactor, factor_id)


def fallback_for(db: Session, key: str, version: str) -> Optional[EmissionFactor]:
    """The register row that may serve as a fallback for ``key`` (allowed or not)."""
    row = get_factor(db, key, version)
    if row is None or not row.is_fallback:
        return None
    return row


def to_read(row: EmissionFactor) -> FactorRead:
    return FactorRead.model_validate(row)

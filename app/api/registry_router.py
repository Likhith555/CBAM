"""Emission-factor register API (Task 5). Factors are append-only."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import FactorSet
from app.modules.registry.factors import (
    FactorRead,
    FactorSetError,
    FactorSpec,
    create_factor_set,
    latest_version,
    list_versions,
    resolve_factors,
    to_read,
)

router = APIRouter(prefix="/registry", tags=["Emission-Factor Register"])


class FactorSetCreate(BaseModel):
    version: str
    notes: Optional[str] = None
    factors: List[FactorSpec]


@router.get("/factor-sets")
def get_factor_sets(db: Session = Depends(get_db)):
    rows = {row.version: row for row in db.query(FactorSet).all()}
    return [
        {"version": v, "created_at": rows[v].created_at, "checksum": rows[v].checksum, "notes": rows[v].notes,
         "factor_count": len(rows[v].factors)}
        for v in list_versions(db)
    ]


@router.get("/factors", response_model=List[FactorRead])
def get_factors(version: Optional[str] = None, db: Session = Depends(get_db)):
    version = version or latest_version(db)
    if version is None:
        return []
    return [to_read(row) for row in resolve_factors(db, version).values()]


@router.post("/factor-sets", status_code=201)
def post_factor_set(body: FactorSetCreate, db: Session = Depends(get_db)):
    try:
        factor_set = create_factor_set(db, body.version, body.factors, body.notes)
    except FactorSetError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return {"version": factor_set.version, "checksum": factor_set.checksum}


@router.api_route("/factors/{factor_id}", methods=["PUT", "PATCH", "DELETE"])
def reject_in_place_change(factor_id: int):
    raise HTTPException(
        status_code=409,
        detail="Register factors are immutable. Add a new factor set with the changed row instead.",
    )

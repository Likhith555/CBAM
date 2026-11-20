import datetime
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import DocumentRecord, ExtractedFieldAudit
from app.modules.audit.log import write_audit
from app.modules.document_ai.extractor import mock_llm_schema_extraction
from app.modules.document_ai.metadata import extract_billing_metadata
from app.modules.document_ai.parser import compute_sha256, parse_document
from app.modules.validation.rules import exclude_document, find_exact_duplicate
from app.schemas.document import (
    DocumentExtractionResult,
    DocumentRecordRead,
    ExtractedFieldAuditRead,
    ExtractedFieldCandidate,
    HumanOverrideRequest,
)


class DocumentMetadataUpdate(BaseModel):
    supplier: Optional[str] = None
    account_number: Optional[str] = None
    utility_type: Optional[str] = None
    bill_number: Optional[str] = None
    bill_date: Optional[datetime.date] = None
    period_start: Optional[datetime.date] = None
    period_end: Optional[datetime.date] = None
    actor: str
    reason: Optional[str] = None


class ActorRequest(BaseModel):
    actor: str
    reason: Optional[str] = None


METADATA_FIELDS = ["supplier", "account_number", "utility_type", "bill_number", "bill_date", "period_start", "period_end"]


def _sync_workspace(db: Session, workspace_id: str, actor: str, reason: str) -> None:
    """Re-runs gaps and the passport hash check after document changes (status resets to Draft)."""
    from app.db.models import Workspace
    from app.modules.review.status import sync_passport

    if db.get(Workspace, workspace_id) is not None:
        sync_passport(db, workspace_id, actor=actor, reason=reason)

router = APIRouter(prefix="/documents", tags=["Document AI & Evidence Ledger"])


@router.post("/upload", response_model=DocumentExtractionResult, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    workspace_id: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    1. Ingests multi-format factory records (PDF, PNG, JPG, CSV, XLSX).
    2. Computes immutable SHA-256 cryptographic digest.
    3. Runs schema-constrained Document AI extraction with verbatim evidence snippets.
    4. Persists document record and audit ledger entries.
    """
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Rule V3 (exact duplicate): same SHA-256 hash -> reject with a link to the existing document.
    digest = compute_sha256(contents)
    existing = find_exact_duplicate(db, digest)
    if existing is not None:
        write_audit(db, workspace_id=workspace_id, entity_type="document", entity_id=digest,
                    action="duplicate_upload_rejected", actor="system",
                    new_value={"filename": file.filename, "existing_document_id": existing.id},
                    reason="V3_DUPLICATE exact match (same SHA-256).")
        db.commit()
        return JSONResponse(status_code=409, content={
            "detail": (f"Duplicate document: '{file.filename}' has the same SHA-256 hash as "
                       f"'{existing.filename}', uploaded on {existing.created_at:%Y-%m-%d}."),
            "rule_id": "V3_DUPLICATE",
            "existing_document": {
                "id": existing.id, "document_hash": existing.document_hash, "filename": existing.filename,
                "workspace_id": existing.workspace_id,
                "url": f"/api/v1/documents/{existing.document_hash}",
            },
        })

    try:
        sha256_hash, pages_data = parse_document(contents, file.filename)
        fields = mock_llm_schema_extraction(pages_data, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document parsing error: {str(e)}")
    extraction_result = DocumentExtractionResult(
        document_hash=sha256_hash, filename=file.filename, page_count=len(pages_data), extracted_fields=fields,
    )

    # Secure local file persistence using hash
    safe_ext = Path(file.filename).suffix
    save_path = settings.UPLOAD_DIR / f"{extraction_result.document_hash}{safe_ext}"
    if not save_path.exists():
        save_path.write_bytes(contents)

    metadata = extract_billing_metadata(pages_data, [f.field_category or "" for f in fields])
    doc_record = DocumentRecord(
        document_hash=extraction_result.document_hash,
        filename=file.filename,
        workspace_id=workspace_id,
        page_count=extraction_result.page_count,
        file_size_bytes=len(contents),
        **metadata,
    )
    db.add(doc_record)
    db.flush()
    write_audit(db, workspace_id=workspace_id, entity_type="document", entity_id=digest, action="uploaded",
                actor="system", new_value={"filename": file.filename, "metadata": metadata})

    for candidate in extraction_result.extracted_fields:
        db.add(ExtractedFieldAudit(
            document_hash=extraction_result.document_hash,
            field_name=candidate.field_name,
            field_category=candidate.field_category or "scope1_fuel",
            raw_extracted_value=str(candidate.extracted_value),
            raw_unit=candidate.unit,
            confidence_score=candidate.confidence_score,
            evidence_page=candidate.evidence_page,
            evidence_snippet=candidate.evidence_snippet,
            effective_value=str(candidate.extracted_value),
            effective_unit=candidate.unit,
            was_overridden=False,
        ))

    _sync_workspace(db, workspace_id, "system", f"Document {file.filename} uploaded.")
    db.commit()
    return extraction_result


@router.post("/override")
def override_field_value(
    request: HumanOverrideRequest,
    db: Session = Depends(get_db)
):
    """
    Human-in-the-loop override endpoint:
    Logs user corrections while preserving the raw machine extraction for third-party verifier audits.
    """
    audit_entry = db.query(ExtractedFieldAudit).filter_by(
        document_hash=request.document_hash,
        field_name=request.field_name
    ).first()

    if not audit_entry:
        raise HTTPException(
            status_code=404,
            detail=f"Extracted field '{request.field_name}' not found for document {request.document_hash[:8]}..."
        )

    # Log human correction while keeping raw extraction intact
    audit_entry.user_override_value = str(request.override_value)
    audit_entry.user_override_unit = request.override_unit or audit_entry.raw_unit
    audit_entry.effective_value = str(request.override_value)
    audit_entry.effective_unit = request.override_unit or audit_entry.raw_unit
    audit_entry.was_overridden = True
    audit_entry.modified_by_user_id = request.user_id
    audit_entry.override_reason = request.override_reason

    doc = db.query(DocumentRecord).filter_by(document_hash=request.document_hash).first()
    workspace_id = doc.workspace_id if doc else None
    write_audit(db, workspace_id=workspace_id, entity_type="field", entity_id=audit_entry.id,
                action="overridden", actor=request.user_id,
                old_value={"raw": [audit_entry.raw_extracted_value, audit_entry.raw_unit]},
                new_value={"effective": [audit_entry.effective_value, audit_entry.effective_unit]},
                reason=request.override_reason)
    if audit_entry.reviewed:
        audit_entry.reviewed = False
        write_audit(db, workspace_id=workspace_id, entity_type="field", entity_id=audit_entry.id,
                    action="review_reset", actor=request.user_id, old_value=True, new_value=False,
                    reason="Value changed after approval; the field must be approved again.")
    if workspace_id:
        _sync_workspace(db, workspace_id, request.user_id, f"Field {request.field_name} overridden.")
    db.commit()

    return {
        "status": "success",
        "message": f"Field '{request.field_name}' successfully updated.",
        "field_name": request.field_name,
        "raw_value": audit_entry.raw_extracted_value,
        "effective_value": audit_entry.effective_value,
        "effective_unit": audit_entry.effective_unit,
        "was_overridden": True,
        "modified_by": audit_entry.modified_by_user_id,
        "override_reason": audit_entry.override_reason,
    }


@router.get("/{document_hash}/fields", response_model=List[ExtractedFieldAuditRead])
def get_document_fields(
    document_hash: str,
    db: Session = Depends(get_db)
):
    """Returns all audit ledger fields extracted for a given document."""
    fields = db.query(ExtractedFieldAudit).filter_by(document_hash=document_hash).all()
    if not fields:
        raise HTTPException(status_code=404, detail="No extracted fields found for this document hash.")
    return fields


@router.get("/workspace/{workspace_id}", response_model=List[DocumentRecordRead])
def list_workspace_documents(
    workspace_id: str,
    db: Session = Depends(get_db)
):
    """Lists all files ingested in a workspace."""
    docs = db.query(DocumentRecord).filter_by(workspace_id=workspace_id).all()
    return docs


def _doc_or_404(db: Session, document_hash: str) -> DocumentRecord:
    doc = db.query(DocumentRecord).filter_by(document_hash=document_hash).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@router.get("/{document_hash}", response_model=DocumentExtractionResult)
def get_document(document_hash: str, db: Session = Depends(get_db)):
    """The stored extraction for a document (used to load an existing record after a 409)."""
    doc = _doc_or_404(db, document_hash)
    return DocumentExtractionResult(
        document_hash=doc.document_hash, filename=doc.filename, page_count=doc.page_count,
        extracted_fields=[
            ExtractedFieldCandidate(
                field_name=f.field_name, extracted_value=float(f.raw_extracted_value), unit=f.raw_unit or "",
                confidence_score=f.confidence_score, evidence_page=f.evidence_page or 1,
                evidence_snippet=f.evidence_snippet or "", field_category=f.field_category,
            )
            for f in doc.extracted_fields
        ],
    )


@router.patch("/{document_hash}/metadata", response_model=DocumentRecordRead)
def update_metadata(document_hash: str, body: DocumentMetadataUpdate, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, document_hash)
    changes = {}
    for name in METADATA_FIELDS:
        if name in body.model_fields_set:
            new = getattr(body, name)
            if isinstance(new, str):
                new = new.strip() or None
            old = getattr(doc, name)
            if old != new:
                changes[name] = (old, new)
                setattr(doc, name, new)
    if changes:
        write_audit(db, workspace_id=doc.workspace_id, entity_type="document", entity_id=document_hash,
                    action="metadata_updated", actor=body.actor,
                    old_value={k: v[0] for k, v in changes.items()},
                    new_value={k: v[1] for k, v in changes.items()}, reason=body.reason)
        _sync_workspace(db, doc.workspace_id, body.actor, "Document billing metadata changed.")
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/{document_hash}/exclude", response_model=DocumentRecordRead)
def exclude(document_hash: str, body: ActorRequest, db: Session = Depends(get_db)):
    """Soft-removes a document from the workspace. The record and file are kept."""
    doc = _doc_or_404(db, document_hash)
    exclude_document(db, doc, body.actor, body.reason or "Removed from the workspace by the user.")
    _sync_workspace(db, doc.workspace_id, body.actor, f"Document {doc.filename} excluded.")
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/{document_hash}/include", response_model=DocumentRecordRead)
def include(document_hash: str, body: ActorRequest, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, document_hash)
    if doc.is_excluded:
        doc.is_excluded = False
        write_audit(db, workspace_id=doc.workspace_id, entity_type="document", entity_id=document_hash,
                    action="included", actor=body.actor, old_value=True, new_value=False, reason=body.reason)
        _sync_workspace(db, doc.workspace_id, body.actor, f"Document {doc.filename} re-included.")
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/fields/{field_id}/approve", response_model=ExtractedFieldAuditRead)
def approve(field_id: int, body: ActorRequest, db: Session = Depends(get_db)):
    """Marks a field reviewed. Blocked (HTTP 409) while validation errors on it are open."""
    from app.modules.review.status import TransitionError, approve_field

    field = db.get(ExtractedFieldAudit, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="Field not found.")
    try:
        approve_field(db, field, body.actor)
    except TransitionError as exc:
        db.commit()
        raise HTTPException(status_code=409, detail={"message": str(exc), "problems": exc.problems})
    db.commit()
    db.refresh(field)
    return field

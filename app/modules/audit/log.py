"""Generic audit-log writer.

Every validation result, gap, fallback, assumption and status change calls
``write_audit`` so a verifier can see who did what, when, the old and new
value, and why.
"""

import json
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def _dump(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, sort_keys=True)


def write_audit(
    db: Session,
    *,
    workspace_id: Optional[str],
    entity_type: str,
    entity_id: Any,
    action: str,
    actor: str = "system",
    old_value: Any = None,
    new_value: Any = None,
    reason: Optional[str] = None,
) -> AuditLog:
    row = AuditLog(
        workspace_id=workspace_id,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        action=action,
        actor=actor or "system",
        old_value=_dump(old_value),
        new_value=_dump(new_value),
        reason=reason,
    )
    db.add(row)
    return row


def list_audit(db: Session, workspace_id: str, entity_type: Optional[str] = None) -> List[AuditLog]:
    query = db.query(AuditLog).filter(AuditLog.workspace_id == workspace_id)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    return query.order_by(AuditLog.id).all()

"""Audit logging for administrator and system actions. Never log student data."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditLog


def audit(db: Session, actor: str, action: str, entity: str | None = None,
          entity_id: int | None = None, **details) -> None:
    db.add(AuditLog(actor=actor, action=action, entity=entity, entity_id=entity_id, details=details))

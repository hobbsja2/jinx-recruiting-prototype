from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import IntakeInvitation


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _valid_token(token: str) -> bool:
    return bool(token) and len(token) <= 200


def invitation_days() -> int:
    try:
        return max(1, min(int(os.environ.get("INTAKE_INVITATION_DAYS", "14")), 30))
    except ValueError:
        return 14


def create_invitation(db: Session, player_id: int, recipients: str) -> tuple[IntakeInvitation, str]:
    token = secrets.token_urlsafe(32)
    invitation = IntakeInvitation(
        token_hash=_digest(token),
        player_id=player_id,
        recipients=recipients[:500],
        expires_at=datetime.utcnow() + timedelta(days=invitation_days()),
    )
    db.add(invitation)
    db.flush()
    return invitation, token


def active_invitation(db: Session, token: str) -> IntakeInvitation | None:
    if not _valid_token(token):
        return None
    return db.scalar(
        select(IntakeInvitation).where(
            IntakeInvitation.token_hash == _digest(token),
            IntakeInvitation.used_at.is_(None),
            IntakeInvitation.expires_at > func.current_timestamp(),
        )
    )


def claim_invitation(db: Session, token: str) -> IntakeInvitation | None:
    """Atomically consume a valid invitation inside the caller's transaction."""
    if not _valid_token(token):
        return None
    statement = (
        update(IntakeInvitation)
        .where(
            IntakeInvitation.token_hash == _digest(token),
            IntakeInvitation.used_at.is_(None),
            IntakeInvitation.expires_at > func.current_timestamp(),
        )
        .values(used_at=func.current_timestamp())
        .returning(IntakeInvitation)
        .execution_options(synchronize_session=False)
    )
    return db.execute(statement).scalar_one_or_none()

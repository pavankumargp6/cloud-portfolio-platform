"""Business logic for backup records and restore verifications."""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.backups.restore import get_backend as get_restore_backend
from app.models.application import Application
from app.models.backup import BackupRecord, BackupVerification

logger = logging.getLogger(__name__)


def record_backup(
    db: Session,
    application_id: int,
    backup_type: str,
    location: str,
    size_bytes: Optional[int] = None,
) -> BackupRecord:
    """Register a new backup record for an application."""
    app = db.get(Application, application_id)
    if not app:
        raise ValueError(f"Application {application_id} not found")

    record = BackupRecord(
        application_id=application_id,
        backup_type=backup_type,
        location=location,
        size_bytes=size_bytes,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def verify_backup(db: Session, backup_id: int) -> BackupVerification:
    """Trigger an actual restore test and record the result.

    Uses the RestoreBackend abstraction. If the restore fails, the exception
    is caught and recorded in the verification table.
    """
    record = db.get(BackupRecord, backup_id)
    if not record:
        raise ValueError(f"Backup {backup_id} not found")

    backend = get_restore_backend()
    
    status = "success"
    error_msg = None
    start_time = time.monotonic()

    try:
        backend.verify_restore(record.location)
    except Exception as exc:
        logger.error("Backup %d restore verification failed: %s", backup_id, exc)
        status = "failed"
        error_msg = str(exc)[:1000]  # cap at 1000 chars for DB
    finally:
        duration_ms = int((time.monotonic() - start_time) * 1000)

    verification = BackupVerification(
        backup_id=backup_id,
        status=status,
        duration_ms=duration_ms,
        error_message=error_msg,
    )
    db.add(verification)
    db.commit()
    db.refresh(verification)

    return verification


def list_backups(
    db: Session,
    application_id: int,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[BackupRecord], int]:
    """Return a paginated list of backups for an application."""
    base = select(BackupRecord).where(BackupRecord.application_id == application_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    
    rows = db.scalars(
        base.order_by(BackupRecord.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def get_latest_verification(
    db: Session, backup_id: int
) -> Optional[BackupVerification]:
    """Get the most recent verification attempt for a backup."""
    return db.scalar(
        select(BackupVerification)
        .where(BackupVerification.backup_id == backup_id)
        .order_by(BackupVerification.verified_at.desc())
        .limit(1)
    )

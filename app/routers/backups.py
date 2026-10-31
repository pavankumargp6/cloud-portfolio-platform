"""Backup HTTP endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.backup import BackupCreate, BackupRead, BackupVerificationRead
from app.schemas.pagination import Page
from app.services import backups as svc

router = APIRouter(tags=["backups"])


@router.post(
    "/applications/{application_id}/backups",
    response_model=BackupRead,
    status_code=201,
)
def create_backup(
    application_id: int,
    payload: BackupCreate,
    db: Session = Depends(get_db),
):
    try:
        return svc.record_backup(
            db,
            application_id=application_id,
            backup_type=payload.backup_type,
            location=payload.location,
            size_bytes=payload.size_bytes,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Application not found")


@router.get(
    "/applications/{application_id}/backups",
    response_model=Page[BackupRead],
)
def list_backups(
    application_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    records, total = svc.list_backups(db, application_id, limit, offset)

    # Attach latest verification to each record
    items = []
    for r in records:
        item = BackupRead.model_validate(r)
        latest = svc.get_latest_verification(db, r.id)
        if latest:
            item.latest_verification = BackupVerificationRead.model_validate(latest)
        items.append(item)

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post(
    "/backups/{backup_id}/verify",
    response_model=BackupVerificationRead,
)
def verify_backup(
    backup_id: int,
    db: Session = Depends(get_db),
):
    try:
        return svc.verify_backup(db, backup_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Backup not found")

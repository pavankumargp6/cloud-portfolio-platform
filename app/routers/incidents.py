"""Incident HTTP endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import require_api_user
from app.db import get_db
from app.models.user import User
from app.schemas.incident import (
    IncidentAcknowledge,
    IncidentDetailRead,
    IncidentPage,
    IncidentRead,
    IncidentResolve,
)
from app.services import incidents as svc

router = APIRouter(prefix="/incidents", tags=["incidents"])
app_router = APIRouter(prefix="/applications", tags=["incidents"])


@router.get("", response_model=IncidentPage)
def list_incidents(
    status: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_api_user),
):
    rows, total = svc.list_incidents(
        db, status=status, limit=limit, offset=offset
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@app_router.get("/{application_id}/incidents", response_model=IncidentPage)
def list_application_incidents(
    application_id: int,
    status: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_api_user),
):
    rows, total = svc.list_incidents(
        db,
        application_id=application_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/{incident_id}", response_model=IncidentDetailRead)
def get_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_api_user),
):
    try:
        return svc.get_incident_with_events(db, incident_id)
    except svc.IncidentNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")


@router.patch("/{incident_id}/acknowledge", response_model=IncidentRead)
def acknowledge_incident(
    incident_id: int,
    payload: IncidentAcknowledge,
    db: Session = Depends(get_db),
    user: User = Depends(require_api_user),
):
    try:
        return svc.acknowledge_incident(db, incident_id, by=user.email)
    except svc.IncidentNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")
    except svc.InvalidIncidentStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "invalid_state", "message": str(exc)},
        )


@router.patch("/{incident_id}/resolve", response_model=IncidentRead)
def resolve_incident(
    incident_id: int,
    payload: IncidentResolve,
    db: Session = Depends(get_db),
    user: User = Depends(require_api_user),
):
    try:
        return svc.resolve_incident(
            db, incident_id, by=user.email, notes=payload.notes
        )
    except svc.IncidentNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")
    except svc.InvalidIncidentStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "invalid_state", "message": str(exc)},
        )

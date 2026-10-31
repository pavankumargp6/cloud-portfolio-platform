"""Monitoring HTTP endpoints — thin wrappers around app.services.monitoring.

All business logic lives in the service. This router only:
  - validates HTTP query/body via Pydantic
  - maps domain exceptions to FastAPI HTTPException with the exact
    status codes and detail shapes that the 20 monitoring API tests expect
  - converts MonitoringSnapshot dataclass to MonitoringConfigRead
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.monitoring import (
    CheckResultPage,
    CheckResultRead,
    MonitoringConfigRead,
    MonitoringConfigUpdate,
)
from app.services import monitoring as svc

router = APIRouter(tags=["monitoring"])


def _to_read(snapshot: svc.MonitoringSnapshot) -> MonitoringConfigRead:
    return MonitoringConfigRead(**snapshot.as_dict())


@router.get(
    "/applications/{application_id}/monitoring",
    response_model=MonitoringConfigRead,
)
def get_monitoring(application_id: int, db: Session = Depends(get_db)):
    try:
        return _to_read(svc.get_snapshot(db, application_id))
    except svc.ApplicationNotFoundError:
        raise HTTPException(status_code=404, detail="Application not found")


@router.patch(
    "/applications/{application_id}/monitoring",
    response_model=MonitoringConfigRead,
)
def patch_monitoring(
    application_id: int,
    payload: MonitoringConfigUpdate,
    db: Session = Depends(get_db),
):
    updates = payload.model_dump(exclude_unset=True)
    try:
        snapshot = svc.update_config(db, application_id, updates)
    except svc.ApplicationNotFoundError:
        raise HTTPException(status_code=404, detail="Application not found")
    except svc.SSRFBlockedError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": exc.message, "url": exc.url},
        )
    return _to_read(snapshot)


@router.get(
    "/applications/{application_id}/checks",
    response_model=CheckResultPage,
)
def list_checks(
    application_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    try:
        rows, total = svc.list_checks(db, application_id, limit, offset)
    except svc.ApplicationNotFoundError:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get(
    "/applications/{application_id}/checks/latest",
    response_model=list[CheckResultRead],
)
def list_latest_checks(
    application_id: int,
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        return svc.list_latest_checks(db, application_id, limit)
    except svc.ApplicationNotFoundError:
        raise HTTPException(status_code=404, detail="Application not found")


@router.post(
    "/applications/{application_id}/checks/run-now",
    status_code=202,
)
def run_check_now(application_id: int, db: Session = Depends(get_db)):
    try:
        svc.request_immediate_check(db, application_id)
    except svc.ApplicationNotFoundError:
        raise HTTPException(status_code=404, detail="Application not found")
    except svc.MonitoringDisabledError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "monitoring_disabled",
                "message": "Monitoring is not enabled for this application",
            },
        )
    except svc.CooldownActiveError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "cooldown_active",
                "message": str(exc),
                "retry_after": exc.retry_after,
            },
            headers={"Retry-After": str(exc.retry_after)},
        )
    return {"queued": True}

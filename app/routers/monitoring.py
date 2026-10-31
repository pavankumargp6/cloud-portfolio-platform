"""Monitoring endpoints: config, history, run-now.

PATCH calls resolve_and_validate on enabled=true — early warning.
The authoritative SSRF gate is per-probe inside the worker (Phase 7):
even if the URL changes to something private after enabling, the worker
will record `blocked_ssrf` and never actually connect.
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.application import Application
from app.models.check_result import CheckResult
from app.models.monitoring import ApplicationHealthState, ApplicationMonitoring
from app.monitoring.errors import SSRFError
from app.monitoring.ssrf import resolve_and_validate
from app.monitoring.state import HealthState, apply_threshold_change
from app.schemas.monitoring import (
    CheckResultPage,
    CheckResultRead,
    MonitoringConfigRead,
    MonitoringConfigUpdate,
)

router = APIRouter(tags=["monitoring"])
logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 300
DEFAULT_THRESHOLD = 3
RUN_NOW_COOLDOWN_SECONDS = 30
RUN_NOW_QUEUE_GUARD_SECONDS = 5
ENABLE_GRACE_SECONDS = 5


def _require_application(db: Session, application_id: int) -> Application:
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def _build_read_response(app: Application, db: Session) -> MonitoringConfigRead:
    mon = db.get(ApplicationMonitoring, app.id)
    state = db.get(ApplicationHealthState, app.id)
    latest_check = db.scalar(
        select(CheckResult)
        .where(CheckResult.application_id == app.id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(1)
    )

    cleartext_warning = urlparse(app.base_url).scheme == "http"

    if mon is None:
        enabled, interval, threshold = False, DEFAULT_INTERVAL, DEFAULT_THRESHOLD
        last_checked, next_check = None, None
    else:
        enabled = mon.enabled
        interval = mon.interval_seconds
        threshold = mon.failure_threshold
        last_checked = mon.last_checked_at
        next_check = mon.next_check_at

    if state is None:
        status, cf = "unknown", 0
        last_success = last_failure = last_result = None
    else:
        status = state.current_status
        cf = state.consecutive_failures
        last_success = state.last_success_at
        last_failure = state.last_failure_at
        last_result = state.last_result_at

    return MonitoringConfigRead(
        application_id=app.id,
        enabled=enabled,
        interval_seconds=interval,
        failure_threshold=threshold,
        last_checked_at=last_checked,
        next_check_at=next_check,
        current_status=status,
        consecutive_failures=cf,
        last_success_at=last_success,
        last_failure_at=last_failure,
        last_result_at=last_result,
        last_ssl_expires_at=latest_check.ssl_expires_at if latest_check else None,
        last_ssl_issuer=latest_check.ssl_issuer if latest_check else None,
        cleartext_warning=cleartext_warning,
    )


@router.get(
    "/applications/{application_id}/monitoring",
    response_model=MonitoringConfigRead,
)
def get_monitoring(application_id: int, db: Session = Depends(get_db)):
    app = _require_application(db, application_id)
    return _build_read_response(app, db)


@router.patch(
    "/applications/{application_id}/monitoring",
    response_model=MonitoringConfigRead,
)
def patch_monitoring(
    application_id: int,
    payload: MonitoringConfigUpdate,
    db: Session = Depends(get_db),
):
    app = _require_application(db, application_id)
    updates = payload.model_dump(exclude_unset=True)

    if updates.get("enabled") is True:
        try:
            resolve_and_validate(app.base_url)
        except SSRFError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc), "url": exc.url},
            )

    mon = db.get(ApplicationMonitoring, application_id)
    was_enabled = mon.enabled if mon else False
    if mon is None:
        mon = ApplicationMonitoring(
            application_id=application_id,
            enabled=False,
            interval_seconds=DEFAULT_INTERVAL,
            failure_threshold=DEFAULT_THRESHOLD,
        )
        db.add(mon)
        db.flush()

    if "interval_seconds" in updates:
        mon.interval_seconds = updates["interval_seconds"]
    if "failure_threshold" in updates:
        mon.failure_threshold = updates["failure_threshold"]
    if "enabled" in updates:
        mon.enabled = updates["enabled"]
        if updates["enabled"] and not was_enabled:
            mon.next_check_at = datetime.now(timezone.utc) + timedelta(
                seconds=ENABLE_GRACE_SECONDS
            )
        elif not updates["enabled"] and was_enabled:
            mon.next_check_at = None

    if "failure_threshold" in updates:
        state = db.get(ApplicationHealthState, application_id)
        if state is None:
            state = ApplicationHealthState(application_id=application_id)
            db.add(state)
            db.flush()
        current = HealthState(state.current_status, state.consecutive_failures)

        def log_transition(f: str, t: str) -> None:
            logger.info(
                "application %d state transition %s -> %s (threshold change)",
                application_id,
                f,
                t,
            )

        new_state = apply_threshold_change(
            current, mon.failure_threshold, on_transition=log_transition
        )
        if new_state.status != current.status:
            state.current_status = new_state.status

    db.commit()
    db.refresh(mon)
    return _build_read_response(app, db)


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
    _require_application(db, application_id)
    total = db.scalar(
        select(func.count())
        .select_from(CheckResult)
        .where(CheckResult.application_id == application_id)
    ) or 0
    rows = db.scalars(
        select(CheckResult)
        .where(CheckResult.application_id == application_id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
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
    _require_application(db, application_id)
    rows = db.scalars(
        select(CheckResult)
        .where(CheckResult.application_id == application_id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(limit)
    ).all()
    return list(rows)


@router.post(
    "/applications/{application_id}/checks/run-now",
    status_code=202,
)
def run_check_now(application_id: int, db: Session = Depends(get_db)):
    _require_application(db, application_id)

    now = datetime.now(timezone.utc)
    cooldown_cutoff = now - timedelta(seconds=RUN_NOW_COOLDOWN_SECONDS)
    queue_cutoff = now + timedelta(seconds=RUN_NOW_QUEUE_GUARD_SECONDS)

    stmt = (
        update(ApplicationMonitoring)
        .where(
            ApplicationMonitoring.application_id == application_id,
            ApplicationMonitoring.enabled.is_(True),
            or_(
                ApplicationMonitoring.last_checked_at.is_(None),
                ApplicationMonitoring.last_checked_at < cooldown_cutoff,
            ),
            or_(
                ApplicationMonitoring.next_check_at.is_(None),
                ApplicationMonitoring.next_check_at > queue_cutoff,
            ),
        )
        .values(next_check_at=now)
    )
    result = db.execute(stmt)
    db.commit()

    if result.rowcount == 1:
        return {"queued": True}

    mon = db.get(ApplicationMonitoring, application_id)
    if mon is None or not mon.enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "monitoring_disabled",
                "message": "Monitoring is not enabled for this application",
            },
        )

    candidates = []
    if mon.last_checked_at is not None:
        candidates.append(
            mon.last_checked_at + timedelta(seconds=RUN_NOW_COOLDOWN_SECONDS)
        )
    if mon.next_check_at is not None:
        candidates.append(mon.next_check_at)
    retry_after = RUN_NOW_COOLDOWN_SECONDS
    if candidates:
        earliest = max(candidates)
        delta = (earliest - now).total_seconds()
        retry_after = max(1, int(delta) + 1)

    raise HTTPException(
        status_code=429,
        detail={
            "code": "cooldown_active",
            "message": (
                f"A check is already scheduled or recently completed; "
                f"retry in {retry_after}s"
            ),
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )

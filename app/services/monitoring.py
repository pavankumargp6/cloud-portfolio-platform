"""Monitoring business logic — no FastAPI HTTP concerns.

This module is the shared seam between the JSON API router and the dashboard
HTMX routes. Functions take a DB session and raise domain exceptions; the
caller decides what HTTP status code to emit.

Domain exceptions:
    ApplicationNotFoundError  -> router maps to 404
    MonitoringDisabledError   -> router maps to 409
    SSRFBlockedError          -> router maps to 422 with {code, message, url}
    CooldownActiveError       -> router maps to 429 with Retry-After header
"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.check_result import CheckResult
from app.models.monitoring import ApplicationHealthState, ApplicationMonitoring
from app.monitoring.errors import SSRFError
from app.monitoring.ssrf import resolve_and_validate
from app.monitoring.state import HealthState, apply_threshold_change
from app.services.incidents import handle_transition as handle_incidents

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 300
DEFAULT_THRESHOLD = 3
RUN_NOW_COOLDOWN_SECONDS = 30
RUN_NOW_QUEUE_GUARD_SECONDS = 5
ENABLE_GRACE_SECONDS = 5


# --- Domain exceptions ---
class MonitoringError(Exception):
    """Base for all monitoring domain errors."""


class ApplicationNotFoundError(MonitoringError):
    def __init__(self, application_id: int):
        self.application_id = application_id
        super().__init__(f"Application {application_id} not found")


class MonitoringDisabledError(MonitoringError):
    def __init__(self, application_id: int):
        self.application_id = application_id
        super().__init__(f"Monitoring is not enabled for application {application_id}")


class CooldownActiveError(MonitoringError):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"A check is already scheduled or recently completed; retry in {retry_after}s")


class SSRFBlockedError(MonitoringError):
    def __init__(self, code: str, message: str, url: Optional[str] = None):
        self.code = code
        self.message = message
        self.url = url
        super().__init__(f"{code}: {message}")


# --- Read model ---
@dataclass(frozen=True)
class MonitoringSnapshot:
    application_id: int
    enabled: bool
    interval_seconds: int
    failure_threshold: int
    last_checked_at: Optional[datetime]
    next_check_at: Optional[datetime]
    current_status: str
    consecutive_failures: int
    last_success_at: Optional[datetime]
    last_failure_at: Optional[datetime]
    last_result_at: Optional[datetime]
    last_ssl_expires_at: Optional[datetime]
    last_ssl_issuer: Optional[str]
    cleartext_warning: bool

    def as_dict(self) -> dict:
        return asdict(self)


# --- Helpers ---
def _require_application(db: Session, application_id: int) -> Application:
    app = db.get(Application, application_id)
    if app is None:
        raise ApplicationNotFoundError(application_id)
    return app


# --- Public API ---
def get_snapshot(db: Session, application_id: int) -> MonitoringSnapshot:
    """Return the current config + health + latest-SSL snapshot."""
    app = _require_application(db, application_id)

    mon = db.get(ApplicationMonitoring, application_id)
    state = db.get(ApplicationHealthState, application_id)
    latest_check = db.scalar(
        select(CheckResult)
        .where(CheckResult.application_id == application_id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(1)
    )

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

    return MonitoringSnapshot(
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
        cleartext_warning=urlparse(app.base_url).scheme == "http",
    )


def update_config(
    db: Session, application_id: int, updates: dict
) -> MonitoringSnapshot:
    """Apply config updates. Raises SSRFBlockedError if enabling an unsafe URL."""
    app = _require_application(db, application_id)

    if updates.get("enabled") is True:
        try:
            resolve_and_validate(app.base_url)
        except SSRFError as exc:
            raise SSRFBlockedError(exc.code, str(exc), exc.url) from exc

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

        def threshold_transition_hook(f: str, t: str) -> None:
            logger.info(
                "application %d state transition %s -> %s (threshold change)",
                application_id, f, t,
            )
            handle_incidents(db, application_id, f, t, commit=False)

        new_state = apply_threshold_change(
            current, mon.failure_threshold, on_transition=threshold_transition_hook
        )
        if new_state.status != current.status:
            state.current_status = new_state.status

    db.commit()
    db.refresh(mon)
    return get_snapshot(db, application_id)


def list_checks(
    db: Session, application_id: int, limit: int, offset: int
) -> tuple[list[CheckResult], int]:
    """Return (page_of_rows, total_count). Raises ApplicationNotFoundError."""
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
    return list(rows), total


def list_latest_checks(
    db: Session, application_id: int, limit: int
) -> list[CheckResult]:
    _require_application(db, application_id)
    return list(
        db.scalars(
            select(CheckResult)
            .where(CheckResult.application_id == application_id)
            .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
            .limit(limit)
        ).all()
    )


def request_immediate_check(db: Session, application_id: int) -> None:
    """Queue the next check for `now` if cooldown allows.

    Raises:
        ApplicationNotFoundError
        MonitoringDisabledError (monitoring row missing OR enabled=False)
        CooldownActiveError (rowcount=0 for cooldown/queue-guard reasons)
    """
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
        return

    mon = db.get(ApplicationMonitoring, application_id)
    if mon is None or not mon.enabled:
        raise MonitoringDisabledError(application_id)

    # Compute a sensible retry_after. Two conditions can block:
    #   (1) last_checked within cooldown  -> wait until last+cooldown
    #   (2) next_check within queue guard -> wait for it to complete; return
    #       the max of the two so a subsequent click never surprises.
    candidates: list[datetime] = []
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

    raise CooldownActiveError(retry_after)

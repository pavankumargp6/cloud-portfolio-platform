"""Worker scheduler: reserve a due app, record its outcome, reschedule.

`pick_due` uses SELECT ... FOR UPDATE SKIP LOCKED so multiple workers can
safely coexist. `record_outcome` writes the check_result and applies the
state machine. `reschedule` sets `last_checked_at` and `next_check_at`.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.check_result import CheckResult
from app.models.monitoring import ApplicationHealthState, ApplicationMonitoring
from app.monitoring.probe import ProbeOutcome
from app.monitoring.state import (
    HealthState,
    apply_result,
    outcome_to_kind,
    TransitionCallback,
)


def pick_due(db: Session, limit: int = 1):
    """Return [(monitoring, application), ...] of due rows, locked."""
    stmt = (
        select(ApplicationMonitoring, Application)
        .join(Application, Application.id == ApplicationMonitoring.application_id)
        .where(
            ApplicationMonitoring.enabled.is_(True),
            Application.status != "archived",
            ApplicationMonitoring.next_check_at.is_not(None),
            ApplicationMonitoring.next_check_at <= func.now(),
        )
        .order_by(ApplicationMonitoring.next_check_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True, of=ApplicationMonitoring)
    )
    return db.execute(stmt).all()


def record_outcome(
    db: Session,
    application_id: int,
    outcome: ProbeOutcome,
    on_transition: Optional[TransitionCallback] = None,
    checked_at: Optional[datetime] = None,
) -> CheckResult:
    """Write the check_result and update application_health_state atomically."""
    when = checked_at or datetime.now(timezone.utc)

    check = CheckResult(
        application_id=application_id,
        checked_at=when,
        status=outcome.status,
        http_status=outcome.http_status,
        latency_ms=outcome.latency_ms,
        failure_reason=outcome.failure_reason,
        ssl_expires_at=outcome.ssl_expires_at,
        ssl_issuer=outcome.ssl_issuer,
        final_url=outcome.final_url,
        redirect_count=outcome.redirect_count,
    )
    db.add(check)

    state = db.get(ApplicationHealthState, application_id)
    if state is None:
        state = ApplicationHealthState(
            application_id=application_id,
            current_status="unknown",
            consecutive_failures=0,
        )
        db.add(state)
        db.flush()

    mon = db.get(ApplicationMonitoring, application_id)
    threshold = mon.failure_threshold if mon else 3

    kind = outcome_to_kind(outcome.status)
    current = HealthState(state.current_status, state.consecutive_failures)
    new_state = apply_result(
        current, kind, threshold, on_transition=on_transition
    )

    if new_state.status != current.status:
        state.current_status = new_state.status
    state.consecutive_failures = new_state.consecutive_failures
    if kind == "success":
        state.last_success_at = when
    elif kind == "failure":
        state.last_failure_at = when
    state.last_result_at = when

    return check


def reschedule(
    db: Session,
    application_id: int,
    checked_at: Optional[datetime] = None,
) -> None:
    """Set last_checked_at=checked_at and next_check_at=checked_at+interval."""
    mon = db.get(ApplicationMonitoring, application_id)
    if mon is None or not mon.enabled:
        return
    when = checked_at or datetime.now(timezone.utc)
    mon.last_checked_at = when
    mon.next_check_at = when + timedelta(seconds=mon.interval_seconds)

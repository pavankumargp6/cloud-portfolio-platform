"""Incident business logic — no FastAPI, no HTTP.

Domain exceptions:
    IncidentNotFoundError     -> router maps to 404
    InvalidIncidentStateError -> router maps to 409
    AlreadyOpenError          -> not a router error; used by worker's handle_transition

`handle_transition(db, application_id, from_status, to_status)` is the
single seam that opens / resolves incidents. It is called from:
  - app/worker/loop.py after a probe result is recorded
  - app/services/monitoring.py after a threshold change triggers down
Both call sites already have `db` and `application_id` in scope.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentEvent
from app.services import notifications as notif_svc

logger = logging.getLogger(__name__)


# --- Domain exceptions ---
class IncidentError(Exception):
    """Base for all incident domain errors."""


class IncidentNotFoundError(IncidentError):
    def __init__(self, incident_id: int):
        self.incident_id = incident_id
        super().__init__(f"Incident {incident_id} not found")


class InvalidIncidentStateError(IncidentError):
    def __init__(self, current_status: str, requested_action: str):
        self.current_status = current_status
        self.requested_action = requested_action
        super().__init__(
            f"Cannot {requested_action} an incident in status {current_status!r}"
        )


class AlreadyOpenError(IncidentError):
    def __init__(self, application_id: int, existing_incident_id: int):
        self.application_id = application_id
        self.existing_incident_id = existing_incident_id
        super().__init__(
            f"Application {application_id} already has open incident {existing_incident_id}"
        )


# --- Helpers ---
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _find_open_incident(db: Session, application_id: int) -> Optional[Incident]:
    return db.scalar(
        select(Incident)
        .where(
            Incident.application_id == application_id,
            Incident.status.in_(["open", "acknowledged"]),
        )
        .order_by(Incident.started_at.desc())
        .limit(1)
    )


def _add_event(
    db: Session,
    incident: Incident,
    event_type: str,
    details: Optional[str] = None,
) -> IncidentEvent:
    event = IncidentEvent(
        incident_id=incident.id,
        event_type=event_type,
        details=details,
    )
    db.add(event)
    return event


# --- Public API ---
def open_incident(
    db: Session,
    application_id: int,
    *,
    reason: Optional[str] = None,
    started_at: Optional[datetime] = None,
    commit: bool = True,
) -> Incident:
    """Open a new incident for the application.

    Raises AlreadyOpenError if an open or acknowledged incident already exists.
    The caller (handle_transition) catches this and treats it as a no-op.
    """
    existing = _find_open_incident(db, application_id)
    if existing is not None:
        raise AlreadyOpenError(application_id, existing.id)

    incident = Incident(
        application_id=application_id,
        status="open",
        started_at=started_at or _now(),
    )
    db.add(incident)
    db.flush()
    _add_event(db, incident, "opened", details=reason)
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(incident)
    logger.info(
        "opened incident %d for application %d (reason=%r)",
        incident.id, application_id, reason,
    )
    notif_svc.notify_incident_opened(db, incident)
    return incident


def acknowledge_incident(
    db: Session,
    incident_id: int,
    *,
    by: Optional[str] = None,
    commit: bool = True,
) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(incident_id)
    if incident.status != "open":
        raise InvalidIncidentStateError(incident.status, "acknowledge")

    incident.status = "acknowledged"
    incident.acknowledged_at = _now()
    incident.acknowledged_by = by
    _add_event(
        db, incident, "acknowledged", details=f"by {by}" if by else None
    )
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(incident)
    return incident


def resolve_incident(
    db: Session,
    incident_id: int,
    *,
    by: Optional[str] = None,
    notes: Optional[str] = None,
    resolved_at: Optional[datetime] = None,
    commit: bool = True,
) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(incident_id)
    if incident.status == "resolved":
        raise InvalidIncidentStateError(incident.status, "resolve")

    incident.status = "resolved"
    incident.resolved_at = resolved_at or _now()
    incident.resolved_by = by
    incident.resolution_notes = notes
    _add_event(
        db,
        incident,
        "resolved",
        details=notes or (f"by {by}" if by else None),
    )
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(incident)
    logger.info(
        "resolved incident %d (by=%r, has_notes=%s)",
        incident.id, by, notes is not None,
    )
    notif_svc.notify_incident_resolved(db, incident)
    return incident


def handle_transition(
    db: Session,
    application_id: int,
    from_status: str,
    to_status: str,
    *,
    commit: bool = True,
) -> None:
    """React to a health-state transition. Called from worker and dashboard.

    - to 'down' (from anything else) -> open incident (idempotent, log if exists)
    - 'down' -> 'up'                 -> resolve the open incident, if any
    - any other transition           -> no-op
    """
    if to_status == "down":
        try:
            open_incident(
                db,
                application_id,
                reason=f"State transition {from_status} -> {to_status}",
                commit=commit,
            )
        except AlreadyOpenError as exc:
            logger.warning(
                "app %d already has open incident %d; skipping",
                exc.application_id, exc.existing_incident_id,
            )
        return

    if from_status == "down" and to_status == "up":
        existing = _find_open_incident(db, application_id)
        if existing is None:
            logger.info(
                "recovery for app %d but no open incident to resolve",
                application_id,
            )
            return
        try:
            resolve_incident(
                db,
                existing.id,
                by="system",
                notes="Auto-resolved on recovery",
                commit=commit,
            )
        except InvalidIncidentStateError:
            # Possible race: someone else resolved between _find and resolve
            logger.info(
                "app %d recovered but incident %d was already resolved",
                application_id, existing.id,
            )
        return


def list_incidents(
    db: Session,
    *,
    application_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Incident], int]:
    from sqlalchemy import func

    base = select(Incident)
    if application_id is not None:
        base = base.where(Incident.application_id == application_id)
    if status is not None:
        base = base.where(Incident.status == status)

    total = db.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = db.scalars(
        base.order_by(Incident.started_at.desc(), Incident.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def get_incident_with_events(db: Session, incident_id: int) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(incident_id)
    return incident

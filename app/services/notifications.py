"""Business logic for sending incident notifications."""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.client import Client
from app.models.incident import Incident
from app.models.notification import NotificationLog
from app.notifications.email import send_email

logger = logging.getLogger(__name__)


def notify_incident_opened(db: Session, incident: Incident) -> None:
    """Send an alert that an incident was opened."""
    # Fetch the app and client to find the recipient
    app = db.get(Application, incident.application_id)
    if not app:
        return

    client = db.get(Client, app.client_id)
    recipient = client.contact_email if client and client.contact_email else "ops@agency.local"

    subject = f"[CRITICAL] Down: {app.name}"
    body = (
        f"Application: {app.name}\n"
        f"URL: {app.base_url}\n"
        f"Environment: {app.environment.value}\n"
        f"Incident ID: {incident.id}\n"
        f"Started At: {incident.started_at}\n\n"
        f"Please investigate immediately."
    )

    log = NotificationLog(
        incident_id=incident.id,
        notification_type="incident_opened",
        recipient=recipient,
    )

    try:
        send_email(recipient, subject, body)
        log.status = "sent"
        db.add(log)
        db.commit()
    except Exception as exc:
        logger.exception("Failed to send email for incident %d", incident.id)
        log.status = "failed"
        log.error_message = str(exc)[:500]
        db.add(log)
        db.commit()


def notify_incident_resolved(db: Session, incident: Incident) -> None:
    """Send an alert that an incident was resolved."""
    app = db.get(Application, incident.application_id)
    if not app:
        return

    client = db.get(Client, app.client_id)
    recipient = client.contact_email if client and client.contact_email else "ops@agency.local"

    subject = f"[RESOLVED] Up: {app.name}"
    body = (
        f"Application: {app.name}\n"
        f"URL: {app.base_url}\n"
        f"Incident ID: {incident.id}\n"
        f"Resolved At: {incident.resolved_at}\n"
        f"Resolved By: {incident.resolved_by or 'System'}\n"
    )

    log = NotificationLog(
        incident_id=incident.id,
        notification_type="incident_resolved",
        recipient=recipient,
    )

    try:
        send_email(recipient, subject, body)
        log.status = "sent"
        db.add(log)
        db.commit()
    except Exception as exc:
        logger.exception("Failed to send email for incident %d", incident.id)
        log.status = "failed"
        log.error_message = str(exc)[:500]
        db.add(log)
        db.commit()

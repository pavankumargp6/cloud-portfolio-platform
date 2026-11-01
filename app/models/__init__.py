"""Model package.

Importing this package registers every model with SQLAlchemy's declarative
registry, so string relationship references (e.g. `Mapped[list["Application"]]`
inside `Client`) always resolve regardless of which model a caller imports
first.
"""

from app.models.agency import Agency
from app.models.application import Application
from app.models.backup import BackupRecord, BackupVerification
from app.models.base import Base
from app.models.check_result import CheckResult
from app.models.client import Client
from app.models.incident import Incident, IncidentEvent
from app.models.monitoring import ApplicationHealthState, ApplicationMonitoring
from app.models.note import Note
from app.models.notification import NotificationLog

__all__ = [
    "Agency",
    "Application",
    "BackupRecord",
    "BackupVerification",
    "Base",
    "CheckResult",
    "Client",
    "Incident",
    "IncidentEvent",
    "ApplicationHealthState",
    "ApplicationMonitoring",
    "Note",
    "NotificationLog",
]

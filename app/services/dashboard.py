from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.client import Client
from app.models.enums import Environment


def get_dashboard_stats(db: Session) -> dict:
    total_clients = db.scalar(select(func.count()).select_from(Client)) or 0
    total_applications = db.scalar(select(func.count()).select_from(Application)) or 0

    status_rows = db.execute(
        select(Application.status, func.count()).group_by(Application.status)
    ).all()
    env_rows = db.execute(
        select(Application.environment, func.count()).group_by(Application.environment)
    ).all()

    def key(value):
        return value.value if hasattr(value, "value") else str(value)

    status_counts = {key(k): v for k, v in status_rows}
    env_counts = {key(k): v for k, v in env_rows}

    return {
        "total_clients": total_clients,
        "total_applications": total_applications,
        "active": status_counts.get("active", 0),
        "maintenance": status_counts.get("maintenance", 0),
        "archived": status_counts.get("archived", 0),
        "by_environment": {e.value: env_counts.get(e.value, 0) for e in Environment},
    }

"""Business logic for generating client reports."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.backup import BackupRecord, BackupVerification
from app.models.check_result import CheckResult
from app.models.client import Client
from app.models.incident import Incident


def generate_client_report(db: Session, client_id: int, days: int = 30) -> dict:
    """Aggregate data for a client report over the last `days` days."""
    client = db.get(Client, client_id)
    if not client:
        raise ValueError("Client not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    apps = db.scalars(
        select(Application).where(Application.client_id == client_id)
    ).all()

    app_reports = []
    for app in apps:
        # 1. Uptime & Latency
        check_stats = db.execute(
            select(
                func.count(CheckResult.id).label("total_checks"),
                func.count(CheckResult.id).filter(CheckResult.status == "up").label("up_checks"),
                func.avg(CheckResult.latency_ms).filter(CheckResult.status == "up").label("avg_latency")
            )
            .where(
                CheckResult.application_id == app.id,
                CheckResult.checked_at >= cutoff
            )
        ).one()

        total_checks = check_stats.total_checks or 0
        up_checks = check_stats.up_checks or 0
        uptime_pct = (up_checks / total_checks * 100) if total_checks > 0 else 0.0
        avg_latency = int(check_stats.avg_latency) if check_stats.avg_latency else 0

        # 2. Incidents
        incidents = db.scalars(
            select(Incident)
            .where(
                Incident.application_id == app.id,
                Incident.started_at >= cutoff
            )
            .order_by(Incident.started_at.desc())
        ).all()

        incident_data = []
        total_downtime = 0.0
        for inc in incidents:
            # Calculate duration (if unresolved, use current time)
            end_time = inc.resolved_at or datetime.now(timezone.utc)
            # Handle potential timezone mismatch if DB returns naive datetimes
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            start_time = inc.started_at
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
                
            duration_sec = (end_time - start_time).total_seconds()
            total_downtime += duration_sec

            incident_data.append({
                "id": inc.id,
                "status": inc.status,
                "started_at": start_time,
                "resolved_at": inc.resolved_at,
                "duration_minutes": round(duration_sec / 60, 1),
                "notes": inc.resolution_notes
            })

        # 3. Backups
        backups = db.scalars(
            select(BackupRecord)
            .where(
                BackupRecord.application_id == app.id,
                BackupRecord.created_at >= cutoff
            )
            .order_by(BackupRecord.created_at.desc())
        ).all()

        backup_data = []
        for bk in backups:
            latest_ver = db.scalar(
                select(BackupVerification)
                .where(BackupVerification.backup_id == bk.id)
                .order_by(BackupVerification.verified_at.desc())
                .limit(1)
            )
            backup_data.append({
                "id": bk.id,
                "location": bk.location,
                "created_at": bk.created_at,
                "verified": latest_ver.status == "success" if latest_ver else None
            })

        app_reports.append({
            "application": app,
            "total_checks": total_checks,
            "up_checks": up_checks,
            "uptime_pct": round(uptime_pct, 2),
            "avg_latency": avg_latency,
            "incidents": incident_data,
            "total_downtime_minutes": round(total_downtime / 60, 1),
            "backups": backup_data,
        })

    return {
        "client": client,
        "generated_at": datetime.now(timezone.utc),
        "period_days": days,
        "applications": app_reports,
    }

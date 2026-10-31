from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.application import Application
from app.models.client import Client
from app.models.enums import ApplicationStatus, Environment
from app.schemas.application import ApplicationCreate, ApplicationUpdate
from app.schemas.client import ClientCreate, ClientUpdate
from app.services.dashboard import get_dashboard_stats
from app.services import monitoring as svc

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

APP_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=APP_DIR / "templates")

ENVIRONMENTS = list(Environment)
STATUSES = list(ApplicationStatus)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _client_or_404(db: Session, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _application_or_404(db: Session, application_id: int) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request, db: Session = Depends(get_db)):
    stats = get_dashboard_stats(db)
    recent_clients = db.scalars(
        select(Client).order_by(Client.id.desc()).limit(5)
    ).all()
    recent_applications = db.scalars(
        select(Application).order_by(Application.id.desc()).limit(5)
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "recent_clients": recent_clients,
            "recent_applications": recent_applications,
            "nav": "dashboard",
        },
    )


@router.get("/clients", response_class=HTMLResponse)
def list_clients_page(request: Request, db: Session = Depends(get_db)):
    clients = db.scalars(select(Client).order_by(Client.id)).all()
    return templates.TemplateResponse(
        request,
        "clients/list.html",
        {"clients": clients, "nav": "clients"},
    )


@router.get("/clients/{client_id}", response_class=HTMLResponse)
def client_detail_page(
    client_id: int, request: Request, db: Session = Depends(get_db)
):
    client = _client_or_404(db, client_id)
    applications = db.scalars(
        select(Application)
        .where(Application.client_id == client_id)
        .order_by(Application.id)
    ).all()
    # Fetch monitoring state for each app (single query per app is fine for
    # a small number of applications per client; can be optimized later).
    monitoring = {app.id: svc.get_snapshot(db, app.id) for app in applications}
    return templates.TemplateResponse(
        request,
        "clients/detail.html",
        {
            "client": client,
            "applications": applications,
            "monitoring": monitoring,
            "nav": "clients",
            "environments": ENVIRONMENTS,
            "statuses": STATUSES,
        },
    )


@router.post("/clients")
def create_client_form(
    request: Request,
    name: str = Form(""),
    contact_email: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        payload = ClientCreate(
            name=name,
            contact_email=contact_email or None,
            notes=notes or None,
        )
    except Exception as exc:
        clients = db.scalars(select(Client).order_by(Client.id)).all()
        return templates.TemplateResponse(
            request,
            "clients/list.html",
            {"clients": clients, "nav": "clients", "error": str(exc)},
            status_code=422,
        )
    client = Client(
        name=payload.name,
        contact_email=payload.contact_email,
        notes=payload.notes,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return RedirectResponse(url="/dashboard/clients", status_code=303)


@router.post("/clients/{client_id}/edit")
def edit_client_form(
    client_id: int,
    request: Request,
    name: str = Form(""),
    contact_email: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    client = _client_or_404(db, client_id)
    try:
        payload = ClientUpdate(
            name=name,
            contact_email=contact_email or None,
            notes=notes or None,
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "partials/client_row.html",
            {"client": client, "row_error": str(exc)},
            status_code=422,
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(client, field, value)
    db.commit()
    db.refresh(client)
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "partials/client_row.html",
            {"client": client},
        )
    return RedirectResponse(url=f"/dashboard/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/applications")
def create_application_form(
    client_id: int,
    request: Request,
    name: str = Form(""),
    base_url: str = Form(""),
    environment: str = Form(""),
    status: str = Form("active"),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    client = _client_or_404(db, client_id)
    try:
        payload = ApplicationCreate(
            name=name,
            base_url=base_url,
            environment=environment,
            status=status,
            description=description or None,
        )
    except Exception as exc:
        applications = db.scalars(
            select(Application)
            .where(Application.client_id == client_id)
            .order_by(Application.id)
        ).all()
        return templates.TemplateResponse(
            request,
            "clients/detail.html",
            {
                "client": client,
                "applications": applications,
                "nav": "clients",
                "environments": ENVIRONMENTS,
                "statuses": STATUSES,
                "error": str(exc),
            },
            status_code=422,
        )
    application = Application(client_id=client_id, **payload.model_dump())
    db.add(application)
    db.commit()
    db.refresh(application)
    return RedirectResponse(url=f"/dashboard/clients/{client_id}", status_code=303)


@router.post("/applications/{application_id}/edit")
def edit_application_form(
    application_id: int,
    request: Request,
    name: str = Form(""),
    base_url: str = Form(""),
    environment: str = Form(""),
    status: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    application = _application_or_404(db, application_id)
    try:
        payload = ApplicationUpdate(
            name=name,
            base_url=base_url,
            environment=environment,
            status=status,
            description=description or None,
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "partials/application_row.html",
            {
                "application": application,
                "environments": ENVIRONMENTS,
                "statuses": STATUSES,
                "row_error": str(exc),
            },
            status_code=422,
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(application, field, value)
    db.commit()
    db.refresh(application)
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "partials/application_row.html",
            {
                "application": application,
                "environments": ENVIRONMENTS,
                "statuses": STATUSES,
            },
        )
    return RedirectResponse(
        url=f"/dashboard/clients/{application.client_id}", status_code=303
    )

# =============================================================================
# Phase 8.2 — Monitoring dashboard routes (HTMX)
# =============================================================================

@router.get("/applications/{application_id}/checks", response_class=HTMLResponse)
def application_checks_page(
    application_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    """Paginated history of checks for a single application."""
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")

    limit = 20
    offset = (page - 1) * limit
    rows, total = svc.list_checks(db, application_id, limit, offset)
    total_pages = (total + limit - 1) // limit if total > 0 else 1

    snap = svc.get_snapshot(db, application_id)
    return templates.TemplateResponse(
        request,
        "monitoring_checks.html",
        {
            "application": app,
            "snapshot": snap,
            "checks": rows,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "nav": "clients",
        },
    )


def _render_monitoring_panel(request: Request, db: Session, application_id: int, error: str = None):
    app = db.get(Application, application_id)
    snap = svc.get_snapshot(db, application_id)
    return templates.TemplateResponse(
        request,
        "partials/monitoring_panel.html",
        {"app": app, "monitoring": snap, "error": error},
    )


@router.post("/applications/{application_id}/monitoring/enable")
def enable_monitoring_htmx(application_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        svc.update_config(db, application_id, {"enabled": True})
    except svc.SSRFBlockedError as exc:
        # Return the panel with an error so HTMX can swap it in-place
        return _render_monitoring_panel(request, db, application_id, error=exc.message)
    return _render_monitoring_panel(request, db, application_id)


@router.post("/applications/{application_id}/monitoring/disable")
def disable_monitoring_htmx(application_id: int, request: Request, db: Session = Depends(get_db)):
    svc.update_config(db, application_id, {"enabled": False})
    return _render_monitoring_panel(request, db, application_id)


@router.post("/applications/{application_id}/monitoring/interval")
def update_interval_htmx(
    application_id: int,
    request: Request,
    interval_seconds: int = Form(...),
    db: Session = Depends(get_db),
):
    try:
        svc.update_config(db, application_id, {"interval_seconds": interval_seconds})
    except svc.MonitoringError as exc:
        return _render_monitoring_panel(request, db, application_id, error=str(exc))
    return _render_monitoring_panel(request, db, application_id)


@router.post("/applications/{application_id}/monitoring/run-now")
def run_now_htmx(application_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        svc.request_immediate_check(db, application_id)
        error = None
    except svc.CooldownActiveError as exc:
        error = f"Cooldown active. Retry in {exc.retry_after}s."
    except svc.MonitoringDisabledError:
        error = "Monitoring is disabled."
    except svc.ApplicationNotFoundError:
        raise HTTPException(status_code=404, detail="Application not found")
    return _render_monitoring_panel(request, db, application_id, error=error)

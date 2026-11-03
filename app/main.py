from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.dependencies import install_exception_handlers
from app.config import IS_PRODUCTION
from app.routers import applications, auth, backups, clients, dashboard, health, incidents, monitoring, notes

APP_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Agency Reliability Desk API",
    description="Agency application management (Milestone 1) with dashboard (Milestone 2)",
    version="2.0.0-m2",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

install_exception_handlers(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(notes.router)
app.include_router(clients.router)
app.include_router(applications.router)
app.include_router(dashboard.router)
app.include_router(monitoring.router)
app.include_router(incidents.router)
app.include_router(incidents.app_router)
app.include_router(backups.router)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

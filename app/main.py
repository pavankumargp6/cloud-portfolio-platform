from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import applications, clients, dashboard, health, monitoring, notes

APP_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Agency Reliability Desk API",
    description="Agency application management (Milestone 1) with dashboard (Milestone 2)",
    version="2.0.0-m2",
)

app.include_router(health.router)
app.include_router(notes.router)
app.include_router(clients.router)
app.include_router(applications.router)
app.include_router(dashboard.router)
app.include_router(monitoring.router)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

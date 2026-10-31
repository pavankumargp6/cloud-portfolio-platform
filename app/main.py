from fastapi import FastAPI

from app.routers import applications, clients, health, notes

app = FastAPI(
    title="Agency Reliability Desk API",
    description="Agency application management (Milestone 1)",
    version="2.0.0-m1",
)

app.include_router(health.router)
app.include_router(notes.router)
app.include_router(clients.router)
app.include_router(applications.router)

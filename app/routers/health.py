from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine

router = APIRouter(tags=["health"])


@router.get("/")
def home():
    return {
        "message": "Cloud Portfolio API is running",
        "service": "backend",
        "status": "healthy",
    }


@router.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

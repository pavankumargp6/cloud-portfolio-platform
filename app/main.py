import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)


def initialize_database():
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS notes (
                id BIGSERIAL PRIMARY KEY,
                content VARCHAR(500) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Cloud Portfolio API",
    description="A Dockerized microservices platform",
    version="1.0.0",
    lifespan=lifespan,
)


class NoteCreate(BaseModel):
    content: str = Field(min_length=1, max_length=500)


@app.get("/")
def home():
    return {
        "message": "Cloud Portfolio API is running",
        "service": "backend",
        "status": "healthy",
    }


@app.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from exc


@app.post("/notes")
def create_note(note: NoteCreate):
    with engine.begin() as connection:
        result = connection.execute(
            text("""
                INSERT INTO notes (content)
                VALUES (:content)
                RETURNING id, content, created_at
            """),
            {"content": note.content},
        )
        return dict(result.mappings().one())


@app.get("/notes")
def get_notes():
    with engine.connect() as connection:
        result = connection.execute(
            text("""
                SELECT id, content, created_at
                FROM notes
                ORDER BY id DESC
            """)
        )
        return {"notes": [dict(row) for row in result.mappings()]}

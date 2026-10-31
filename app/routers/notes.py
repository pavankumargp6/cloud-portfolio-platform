from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import engine

router = APIRouter(tags=["notes"])


class NoteCreate(BaseModel):
    content: str = Field(min_length=1, max_length=500)


@router.post("/notes")
def create_note(note: NoteCreate):
    with engine.begin() as connection:
        result = connection.execute(
            text(
                """
                INSERT INTO notes (content)
                VALUES (:content)
                RETURNING id, content, created_at
                """
            ),
            {"content": note.content},
        )
        return dict(result.mappings().one())


@router.get("/notes")
def get_notes():
    with engine.connect() as connection:
        result = connection.execute(
            text(
                """
                SELECT id, content, created_at
                FROM notes
                ORDER BY id DESC
                """
            )
        )
        return {"notes": [dict(row) for row in result.mappings()]}

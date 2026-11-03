"""Notes router — Phase 13.2.

Authentication is enforced, but notes are NOT tenant-isolated:
the `notes` table has no `agency_id` column (predates Phase 13.1).
Phase 13.3 will decide whether to add agency ownership to notes or
retire the endpoint from the multi-tenant product scope.

Acknowledging this explicitly so future readers don't assume multi-
tenant safety for this endpoint.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth.dependencies import require_api_user
from app.db import engine
from app.models.user import User

router = APIRouter(tags=["notes"])


class NoteCreate(BaseModel):
    content: str = Field(min_length=1, max_length=500)


@router.post("/notes")
def create_note(
    note: NoteCreate,
    user: User = Depends(require_api_user),
):
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
def get_notes(user: User = Depends(require_api_user)):
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

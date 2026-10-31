"""Schemas for the Incident API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class IncidentEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    details: Optional[str] = None
    created_at: datetime


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    status: str
    started_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_notes: Optional[str] = None


class IncidentDetailRead(IncidentRead):
    events: list[IncidentEventRead]


class IncidentPage(BaseModel):
    items: list[IncidentRead]
    total: int
    limit: int
    offset: int


class IncidentAcknowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: Optional[str] = Field(None, max_length=255)


class IncidentResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None

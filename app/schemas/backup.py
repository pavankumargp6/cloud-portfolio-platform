"""Schemas for the Backup API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BackupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backup_type: str = Field(min_length=1, max_length=50, description="e.g. 'database', 'filesystem'")
    location: str = Field(min_length=1, max_length=1024, description="S3 path, local path, etc.")
    size_bytes: Optional[int] = Field(default=None, ge=0)


class BackupVerificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    verified_at: datetime


class BackupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    backup_type: str
    location: str
    size_bytes: Optional[int] = None
    created_at: datetime
    latest_verification: Optional[BackupVerificationRead] = None

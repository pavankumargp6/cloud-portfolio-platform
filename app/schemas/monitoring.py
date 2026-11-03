"""Schemas for the monitoring API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MonitoringConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = None
    interval_seconds: Optional[int] = Field(default=None, ge=60, le=86400)
    failure_threshold: Optional[int] = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def _reject_explicit_null(self):
        for field in ("enabled", "interval_seconds", "failure_threshold"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class MonitoringConfigRead(BaseModel):
    application_id: int
    enabled: bool
    interval_seconds: int
    failure_threshold: int
    last_checked_at: Optional[datetime] = None
    next_check_at: Optional[datetime] = None
    current_status: str
    consecutive_failures: int
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    last_result_at: Optional[datetime] = None
    last_ssl_expires_at: Optional[datetime] = None
    last_ssl_issuer: Optional[str] = None
    cleartext_warning: bool


class CheckResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    checked_at: datetime
    status: str
    http_status: Optional[int] = None
    latency_ms: Optional[int] = None
    failure_reason: Optional[str] = None
    ssl_expires_at: Optional[datetime] = None
    ssl_issuer: Optional[str] = None
    final_url: Optional[str] = None
    redirect_count: int


class CheckResultPage(BaseModel):
    items: list[CheckResultRead]
    total: int
    limit: int
    offset: int

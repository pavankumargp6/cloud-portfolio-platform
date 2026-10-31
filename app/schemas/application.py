from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ApplicationStatus, Environment


def _validate_absolute_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("base_url must be an absolute http or https URL")
    if not parsed.netloc:
        raise ValueError("base_url must include a host")
    return value


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    base_url: str = Field(min_length=1, max_length=2048)
    environment: Environment
    status: ApplicationStatus = ApplicationStatus.active
    description: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        return _validate_absolute_http_url(value)


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    base_url: Optional[str] = Field(default=None, min_length=1, max_length=2048)
    environment: Optional[Environment] = None
    status: Optional[ApplicationStatus] = None
    description: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _validate_absolute_http_url(value)

    @model_validator(mode="after")
    def _reject_explicit_null_for_required(self):
        for required in ("name", "base_url", "environment", "status"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be null")
        return self


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    name: str
    base_url: str
    environment: Environment
    status: ApplicationStatus
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

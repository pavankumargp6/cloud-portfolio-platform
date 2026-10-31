from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    contact_email: Optional[EmailStr] = None
    notes: Optional[str] = None


class ClientUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    contact_email: Optional[EmailStr] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _reject_explicit_null_for_required(self):
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class ClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    contact_email: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

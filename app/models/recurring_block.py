from __future__ import annotations
from typing import Optional
from sqlmodel import SQLModel, Field


class RecurringBlock(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)
    reason: str = ""

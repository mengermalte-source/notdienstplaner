from __future__ import annotations

from datetime import date as Date
from typing import Optional
from sqlmodel import SQLModel, Field


class SpecialDayCategory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    weight: float = Field(default=2.0)
    color: str = "#ef4444"
    description: str = ""


class SpecialDay(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: Date = Field(index=True)
    category_id: int = Field(foreign_key="specialdaycategory.id")
    label: str = ""
    is_auto_imported: bool = False
    required_doctors: Optional[int] = Field(default=None)  # None = globaler Wert aus config

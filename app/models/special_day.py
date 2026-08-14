from __future__ import annotations

from datetime import date as Date
from typing import Optional
from sqlmodel import SQLModel, Field


class SpecialDayCategory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str                        # z.B. "Weihnachten", "Brückentag"
    weight: float = Field(default=2.0)  # Fairness-Gewichtung
    color: str = "#ef4444"           # Kalenderfarbe (hex)
    description: str = ""


class SpecialDay(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: Date = Field(index=True)
    category_id: int = Field(foreign_key="specialdaycategory.id")
    label: str = ""                  # optionale Beschriftung
    is_auto_imported: bool = False   # True = aus python-holidays importiert

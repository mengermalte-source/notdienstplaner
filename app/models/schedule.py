from __future__ import annotations

from datetime import date as Date, datetime
from typing import Optional
from enum import Enum
from sqlmodel import SQLModel, Field


class PlanStatus(str, Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class PlanningPeriod(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str                        # z.B. "Notdienstplan 2027"
    year: int
    start_date: Date
    end_date: Date
    status: PlanStatus = PlanStatus.draft
    wish_deadline: Optional[Date] = None  # bis wann können Ärzte Wünsche eingeben
    created_at: datetime = Field(default_factory=datetime.utcnow)
    published_at: Optional[datetime] = None
    notes: str = ""


class ShiftAssignment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    planning_period_id: int = Field(foreign_key="planningperiod.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    date: Date = Field(index=True)
    is_manual_override: bool = False   # True wenn Admin nachträglich geändert hat
    weighted_score: float = 1.0        # Fairness-Gewicht (1.0 normal, höher = Sonderbelastung)
    acknowledged_at: Optional[datetime] = None  # Arzt hat Dienst bestätigt
    is_substitute: bool = Field(default=False)  # True = Bereitschaftsdienst (Dez–Apr)

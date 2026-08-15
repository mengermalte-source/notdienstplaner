from __future__ import annotations
from datetime import date as Date, datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class VacationPeriod(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    planning_period_id: Optional[int] = Field(default=None, foreign_key="planningperiod.id")
    start_date: Date
    end_date: Date
    reason: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

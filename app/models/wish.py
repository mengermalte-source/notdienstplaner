from __future__ import annotations

from datetime import date as Date, datetime
from typing import Optional
from enum import Enum
from sqlmodel import SQLModel, Field


class WishType(str, Enum):
    positive = "positive"   # Arzt möchte an diesem Tag arbeiten
    negative = "negative"   # Arzt möchte NICHT an diesem Tag arbeiten


class WishPriority(str, Enum):
    soft = "soft"    # Wunsch, wird wenn möglich berücksichtigt
    hard = "hard"    # Muss berücksichtigt werden (z.B. Urlaub)


class WishEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    date: Date = Field(index=True)
    wish_type: str = "positive"
    priority: str = Field(default="soft")
    reason: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    planning_period_id: Optional[int] = Field(default=None, foreign_key="planningperiod.id")

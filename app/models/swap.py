from __future__ import annotations

from datetime import date as Date, datetime
from typing import Optional
from enum import Enum
from sqlmodel import SQLModel, Field


class SwapStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    admin_approved = "admin_approved"


class SwapRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    requester_id: int = Field(foreign_key="user.id")
    target_id: int = Field(foreign_key="user.id")
    requester_shift_date: Date
    target_shift_date: Date
    planning_period_id: int = Field(foreign_key="planningperiod.id")
    status: SwapStatus = SwapStatus.pending
    message: str = ""
    is_coverage_request: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None

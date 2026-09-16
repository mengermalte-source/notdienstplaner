from __future__ import annotations

from datetime import datetime
from typing import Optional
from enum import Enum
from sqlmodel import SQLModel, Field


class TransferStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    revoked = "revoked"


class ContingentTransfer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sender_id: int = Field(foreign_key="user.id")
    receiver_id: int = Field(foreign_key="user.id")
    percent: float
    status: TransferStatus = TransferStatus.pending
    message: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)  # noqa: DTZ003
    resolved_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoked_by_id: Optional[int] = Field(default=None, foreign_key="user.id")

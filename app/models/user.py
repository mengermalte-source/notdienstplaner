from __future__ import annotations

from datetime import datetime
from typing import Optional
from enum import Enum
from sqlmodel import SQLModel, Field, Relationship


class UserRole(str, Enum):
    doctor = "doctor"
    admin = "admin"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    full_name: str
    role: UserRole = UserRole.doctor
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    profile: DoctorProfile = Relationship(back_populates="user")


class DoctorProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True)
    credit_factor: float = Field(default=1.0, ge=0.0, le=1.0)     # Anrechnungsfaktor
    desired_shifts: Optional[int] = Field(default=None, ge=0)      # None = Minimum
    day_preference: str = Field(default="alle")                    # "alle"|"mittwoch"|"freitag"
    sub_carried_over_score: float = 0.0                            # Bereitschafts-Fairness
    part_time_factor: float = Field(default=1.0, ge=0.0, le=1.0)  # Legacy — nicht entfernen
    phone: str = ""
    notes: str = ""
    carried_over_score: float = 0.0

    user: User = Relationship(back_populates="profile")

from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Relationship
import enum


class UserRole(str, enum.Enum):
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
    part_time_factor: float = Field(default=1.0, ge=0.1, le=1.0)
    phone: str = ""
    notes: str = ""
    # Kumulierter Fairness-Score aus Vorjahren (wird jährlich übertragen)
    carried_over_score: float = 0.0

    user: User = Relationship(back_populates="profile")

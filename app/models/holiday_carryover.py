from __future__ import annotations
from datetime import date as Date
from typing import Optional
from sqlmodel import SQLModel, Field


class HolidayDutyCarryover(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    planning_period_id: int = Field(foreign_key="planningperiod.id", index=True)
    holiday_key: str = Field(index=True)  # "weihnachten"|"silvester"|"ostern"|"pfingsten"
    worked: bool = True  # Hat dieser Arzt in dieser Periode an diesem Feiertag gearbeitet?

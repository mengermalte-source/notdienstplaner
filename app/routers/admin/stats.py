from pathlib import Path
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from collections import Counter
from typing import Optional
from app.database import get_session
from app.deps import require_admin
from app.models.user import User, UserRole, DoctorProfile
from app.models.schedule import PlanningPeriod, ShiftAssignment
from app.models.special_day import SpecialDay, SpecialDayCategory
from app.services.fairness import compute_fairness_score

router = APIRouter(prefix="/admin/statistics", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "templates" if "admin" in str(Path(__file__)) else Path(__file__).parent.parent / "templates")


@router.get("", response_class=HTMLResponse)
async def stats_page(request: Request, period_id: Optional[int] = None,
                     session: AsyncSession = Depends(get_session),
                     user: User = Depends(require_admin)):
    periods = (await session.exec(
        select(PlanningPeriod).order_by(PlanningPeriod.year.desc()))).all()

    selected = None
    scores = {}
    duty_counts = {}

    if period_id or periods:
        selected = await session.get(PlanningPeriod, period_id) if period_id else periods[0]
        if selected:
            assignments = (await session.exec(
                select(ShiftAssignment).where(
                    ShiftAssignment.planning_period_id == selected.id))).all()
            sdays_raw = (await session.exec(
                select(SpecialDay, SpecialDayCategory).join(
                    SpecialDayCategory,
                    SpecialDay.category_id == SpecialDayCategory.id)
            )).all()

            class SDProxy:
                def __init__(self, d, w):
                    self.date = d
                    self.weight = w

            special_days = [SDProxy(sd.date, cat.weight) for sd, cat in sdays_raw]
            scores = compute_fairness_score(
                [(a.user_id, a.date) for a in assignments], special_days)
            duty_counts = Counter(a.user_id for a in assignments)

    doctors = (await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True))).all()
    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

    return templates.TemplateResponse("admin/statistics.html", {
        "request": request, "user": user, "periods": periods, "selected": selected,
        "doctors": doctors, "scores": scores, "duty_counts": duty_counts,
        "profiles": profiles,
    })

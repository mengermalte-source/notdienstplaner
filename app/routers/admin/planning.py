from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from datetime import date as date_type, datetime, timedelta
from typing import Optional
from app.database import get_session
from app.deps import require_admin
from app.models.user import User, UserRole, DoctorProfile
from app.models.schedule import PlanningPeriod, ShiftAssignment, PlanStatus
from app.models.wish import WishEntry
from app.models.special_day import SpecialDay, SpecialDayCategory
from app.services.algorithm import solve_schedule
from app.services.fairness import compute_fairness_score
from app.config import settings

router = APIRouter(prefix="/admin/planning", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "templates" if "admin" in str(Path(__file__)) else Path(__file__).parent.parent / "templates")


@router.get("", response_class=HTMLResponse)
async def planning_page(request: Request, session: AsyncSession = Depends(get_session),
                        admin: User = Depends(require_admin)):
    periods = (await session.exec(
        select(PlanningPeriod).order_by(PlanningPeriod.year.desc()))).all()
    return templates.TemplateResponse("admin/planning.html",
        {"request": request, "user": admin, "periods": periods, "period": None, "assignments": []})


@router.post("/create")
async def create_period(name: str = Form(...), year: int = Form(...),
                         start_date: str = Form(...), end_date: str = Form(...),
                         wish_deadline: str = Form(None),
                         session: AsyncSession = Depends(get_session)):
    period = PlanningPeriod(
        name=name, year=year,
        start_date=date_type.fromisoformat(start_date),
        end_date=date_type.fromisoformat(end_date),
        wish_deadline=date_type.fromisoformat(wish_deadline) if wish_deadline else None,
    )
    session.add(period)
    await session.commit()
    return RedirectResponse("/admin/planning", status_code=302)


@router.post("/{period_id}/run")
async def run_algorithm(period_id: int, request: Request,
                        coverage: str = Form("weekends"),
                        session: AsyncSession = Depends(get_session),
                        admin: User = Depends(require_admin)):
    period = await session.get(PlanningPeriod, period_id)
    if not period:
        return RedirectResponse("/admin/planning", status_code=302)

    result = await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True))
    doctors = result.all()

    if len(doctors) < settings.doctors_per_day:
        periods = (await session.exec(
            select(PlanningPeriod).order_by(PlanningPeriod.year.desc()))).all()
        return templates.TemplateResponse("admin/planning.html", {
            "request": request, "user": admin,
            "periods": periods, "period": None, "assignments": [],
            "error": f"Mindestens {settings.doctors_per_day} aktive Ärzte erforderlich. "
                     f"Aktuell: {len(doctors)}.",
        })

    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

    class DoctorWithFactor:
        def __init__(self, user, profile):
            self.id = user.id
            self.part_time_factor = profile.part_time_factor if profile else 1.0

    doctor_objs = [DoctorWithFactor(u, profiles.get(u.id)) for u in doctors]

    sdays_raw = (await session.exec(
        select(SpecialDay, SpecialDayCategory).join(
            SpecialDayCategory,
            SpecialDay.category_id == SpecialDayCategory.id
        ).where(
            SpecialDay.date >= period.start_date,
            SpecialDay.date <= period.end_date,
        )
    )).all()

    class SDay:
        def __init__(self, d, w):
            self.date = d
            self.weight = w

    special_days = [SDay(sd.date, cat.weight) for sd, cat in sdays_raw]
    special_dates = {sd.date for sd in special_days}

    all_days = [period.start_date + timedelta(days=i)
                for i in range((period.end_date - period.start_date).days + 1)]

    if coverage == "weekends":
        days = [d for d in all_days if d.weekday() >= 5]
    elif coverage == "weekends_holidays":
        days = [d for d in all_days if d.weekday() >= 5 or d in special_dates]
    else:
        days = all_days

    if not days:
        periods = (await session.exec(
            select(PlanningPeriod).order_by(PlanningPeriod.year.desc()))).all()
        return templates.TemplateResponse("admin/planning.html", {
            "request": request, "user": admin,
            "periods": periods, "period": None, "assignments": [],
            "error": "Keine zu planenden Tage gefunden. Bitte Zeitraum oder Abdeckung prüfen.",
        })

    wishes = (await session.exec(
        select(WishEntry).where(
            WishEntry.date >= period.start_date,
            WishEntry.date <= period.end_date,
        )
    )).all()

    assignments = solve_schedule(doctor_objs, days, wishes, special_days,
                                 settings.doctors_per_day)

    if assignments is None:
        periods = (await session.exec(
            select(PlanningPeriod).order_by(PlanningPeriod.year.desc()))).all()
        return templates.TemplateResponse("admin/planning.html", {
            "request": request, "user": admin,
            "periods": periods, "period": None, "assignments": [],
            "error": f"Kein gültiger Plan gefunden. Ärzte: {len(doctors)}, "
                     f"Tage: {len(days)}, Abdeckung: {coverage}. "
                     "Bitte Constraints oder Zeitraum prüfen.",
        })

    old = (await session.exec(
        select(ShiftAssignment).where(
            ShiftAssignment.planning_period_id == period_id))).all()
    for a in old:
        await session.delete(a)

    weight_by_date = {sd.date: sd.weight for sd in special_days}
    for user_id, day in assignments:
        session.add(ShiftAssignment(
            planning_period_id=period_id,
            user_id=user_id,
            date=day,
            weighted_score=weight_by_date.get(day, 1.0),
        ))
    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)


@router.get("/{period_id}", response_class=HTMLResponse)
async def period_detail(period_id: int, request: Request,
                        session: AsyncSession = Depends(get_session),
                        admin: User = Depends(require_admin)):
    period = await session.get(PlanningPeriod, period_id)
    assignments = (await session.exec(
        select(ShiftAssignment).where(
            ShiftAssignment.planning_period_id == period_id)
        .order_by(ShiftAssignment.date))).all()

    users = {u.id: u for u in (await session.exec(select(User))).all()}

    scores = compute_fairness_score(
        [(a.user_id, a.date) for a in assignments], []
    )
    return templates.TemplateResponse("admin/planning.html", {
        "request": request, "user": admin,
        "period": period,
        "assignments": assignments,
        "users": users,
        "scores": scores,
        "periods": [],
    })


@router.post("/{period_id}/publish")
async def publish_period(period_id: int, session: AsyncSession = Depends(get_session)):
    period = await session.get(PlanningPeriod, period_id)
    period.status = PlanStatus.published
    period.published_at = datetime.utcnow()
    session.add(period)
    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)

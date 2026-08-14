from calendar import monthcalendar
from collections import defaultdict
from datetime import date as date_type, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_session
from app.deps import require_admin
from app.models.schedule import PlanStatus, PlanningPeriod, ShiftAssignment
from app.models.special_day import SpecialDay, SpecialDayCategory
from app.models.user import DoctorProfile, User, UserRole
from app.models.wish import WishEntry, WishPriority, WishType
from app.services.algorithm import solve_schedule
from app.services.fairness import compute_fairness_score, compute_target_duties

router = APIRouter(prefix="/admin/planning", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(
    directory=Path(__file__).parent.parent.parent / "templates"
)

_MONTH_NAMES = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

_DOCTOR_COLORS = [
    "bg-blue-500", "bg-emerald-500", "bg-violet-500", "bg-amber-500", "bg-rose-500",
    "bg-cyan-500", "bg-orange-500", "bg-teal-500", "bg-pink-500", "bg-indigo-500",
    "bg-lime-600", "bg-red-500", "bg-sky-500", "bg-purple-500", "bg-yellow-500",
    "bg-green-600", "bg-fuchsia-500", "bg-slate-500", "bg-zinc-600", "bg-stone-500",
]


def _monday_of_week(d: date_type) -> date_type:
    return d - timedelta(days=d.weekday())


def _build_doctor_colors(users: dict) -> dict:
    colors = {}
    idx = 0
    for uid in sorted(users):
        u = users[uid]
        if u.role == UserRole.doctor:
            colors[uid] = _DOCTOR_COLORS[idx % len(_DOCTOR_COLORS)]
            idx += 1
    return colors


def _build_months(start: date_type, end: date_type) -> list:
    months = []
    cur = start.replace(day=1)
    end_month = end.replace(day=1)
    while cur <= end_month:
        y, m = cur.year, cur.month
        weeks = []
        for week in monthcalendar(y, m):
            row = [date_type(y, m, d) if d else None for d in week]
            weeks.append(row)
        months.append({"name": _MONTH_NAMES[m - 1], "year": y, "month": m, "weeks": weeks})
        cur = date_type(y + 1, 1, 1) if m == 12 else date_type(y, m + 1, 1)
    return months


# ---------------------------------------------------------------------------
# List / create period
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def planning_page(request: Request, session: AsyncSession = Depends(get_session),
                        admin: User = Depends(require_admin)):
    periods = (await session.exec(
        select(PlanningPeriod).order_by(PlanningPeriod.year.desc())
    )).all()
    return templates.TemplateResponse("admin/planning.html", {
        "request": request, "user": admin,
        "periods": periods, "period": None, "assignments": [],
    })


@router.post("/create")
async def create_period(
    name: str = Form(...), year: int = Form(...),
    start_date: str = Form(...), end_date: str = Form(...),
    wish_deadline: str = Form(None),
    session: AsyncSession = Depends(get_session),
):
    period = PlanningPeriod(
        name=name, year=year,
        start_date=date_type.fromisoformat(start_date),
        end_date=date_type.fromisoformat(end_date),
        wish_deadline=date_type.fromisoformat(wish_deadline) if wish_deadline else None,
    )
    session.add(period)
    await session.commit()
    return RedirectResponse("/admin/planning", status_code=302)


# ---------------------------------------------------------------------------
# Run algorithm
# ---------------------------------------------------------------------------

@router.post("/{period_id}/run")
async def run_algorithm(
    period_id: int, request: Request,
    coverage: str = Form("weekends"),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    period = await session.get(PlanningPeriod, period_id)
    if not period:
        return RedirectResponse("/admin/planning", status_code=302)

    result = await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True)
    )
    doctors = result.all()

    if len(doctors) < settings.doctors_per_day:
        periods = (await session.exec(
            select(PlanningPeriod).order_by(PlanningPeriod.year.desc())
        )).all()
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
            self.carried_over_score = profile.carried_over_score if profile else 0.0

    doctor_objs = [DoctorWithFactor(u, profiles.get(u.id)) for u in doctors]

    sdays_raw = (await session.exec(
        select(SpecialDay, SpecialDayCategory).join(
            SpecialDayCategory, SpecialDay.category_id == SpecialDayCategory.id
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

    all_days = [
        period.start_date + timedelta(days=i)
        for i in range((period.end_date - period.start_date).days + 1)
    ]

    if coverage == "weekends":
        days = [d for d in all_days if d.weekday() >= 5]
    elif coverage == "weekends_holidays":
        days = [d for d in all_days if d.weekday() >= 5 or d in special_dates]
    else:
        days = all_days

    if not days:
        periods = (await session.exec(
            select(PlanningPeriod).order_by(PlanningPeriod.year.desc())
        )).all()
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

    assignments = solve_schedule(
        doctor_objs, days, wishes, special_days, settings.doctors_per_day
    )

    if assignments is None:
        periods = (await session.exec(
            select(PlanningPeriod).order_by(PlanningPeriod.year.desc())
        )).all()
        return templates.TemplateResponse("admin/planning.html", {
            "request": request, "user": admin,
            "periods": periods, "period": None, "assignments": [],
            "error": f"Kein gültiger Plan gefunden. Ärzte: {len(doctors)}, "
                     f"Tage: {len(days)}, Abdeckung: {coverage}. "
                     "Bitte Constraints oder Zeitraum prüfen.",
        })

    old = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.planning_period_id == period_id)
    )).all()
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


# ---------------------------------------------------------------------------
# Period detail (table view)
# ---------------------------------------------------------------------------

@router.get("/{period_id}", response_class=HTMLResponse)
async def period_detail(
    period_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    period = await session.get(PlanningPeriod, period_id)
    assignments = (await session.exec(
        select(ShiftAssignment)
        .where(ShiftAssignment.planning_period_id == period_id)
        .order_by(ShiftAssignment.date)
    )).all()
    users = {u.id: u for u in (await session.exec(select(User))).all()}
    scores = compute_fairness_score([(a.user_id, a.date) for a in assignments], [])
    return templates.TemplateResponse("admin/planning.html", {
        "request": request, "user": admin,
        "period": period,
        "assignments": assignments,
        "users": users,
        "scores": scores,
        "periods": [],
    })


# ---------------------------------------------------------------------------
# Calendar view
# ---------------------------------------------------------------------------

@router.get("/{period_id}/calendar", response_class=HTMLResponse)
async def period_calendar(
    period_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    period = await session.get(PlanningPeriod, period_id)
    assignments = (await session.exec(
        select(ShiftAssignment)
        .where(ShiftAssignment.planning_period_id == period_id)
        .order_by(ShiftAssignment.date)
    )).all()

    users = {u.id: u for u in (await session.exec(select(User))).all()}

    duties_by_date: dict = defaultdict(list)
    for a in assignments:
        if a.user_id in users:
            duties_by_date[a.date].append(users[a.user_id])

    months = _build_months(period.start_date, period.end_date)
    doctor_colors = _build_doctor_colors(users)

    return templates.TemplateResponse("admin/calendar_view.html", {
        "request": request, "user": admin,
        "period": period,
        "months": months,
        "duties_by_date": dict(duties_by_date),
        "doctor_colors": doctor_colors,
    })


# ---------------------------------------------------------------------------
# Substitute finder
# ---------------------------------------------------------------------------

@router.get("/{period_id}/substitute", response_class=HTMLResponse)
async def find_substitute(
    period_id: int, request: Request,
    date: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    period = await session.get(PlanningPeriod, period_id)
    candidates = []
    target_date = None

    if date:
        target_date = date_type.fromisoformat(date)

        all_doctors = (await session.exec(
            select(User).where(User.role == UserRole.doctor, User.is_active == True)
        )).all()
        profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

        already_assigned = {
            a.user_id for a in (await session.exec(
                select(ShiftAssignment).where(
                    ShiftAssignment.planning_period_id == period_id,
                    ShiftAssignment.date == target_date,
                )
            )).all()
        }

        cannot_work = {
            w.user_id for w in (await session.exec(
                select(WishEntry).where(
                    WishEntry.date == target_date,
                    WishEntry.wish_type == WishType.negative,
                    WishEntry.priority == WishPriority.hard,
                )
            )).all()
        }

        all_period_assignments = (await session.exec(
            select(ShiftAssignment).where(
                ShiftAssignment.planning_period_id == period_id
            )
        )).all()
        shifts_by_doctor: dict = defaultdict(list)
        for a in all_period_assignments:
            if a.date != target_date:
                shifts_by_doctor[a.user_id].append(a.date)

        sdays_raw = (await session.exec(
            select(SpecialDay, SpecialDayCategory).join(
                SpecialDayCategory, SpecialDay.category_id == SpecialDayCategory.id)
        )).all()
        weight_by_date = {sd.date: cat.weight for sd, cat in sdays_raw}

        actual_scores: dict = defaultdict(float)
        for a in all_period_assignments:
            actual_scores[a.user_id] += weight_by_date.get(a.date, 1.0)

        mon_target = _monday_of_week(target_date)

        def violates_gap(doctor_id: int) -> bool:
            for existing in shifts_by_doctor[doctor_id]:
                gap = abs((_monday_of_week(existing) - mon_target).days) // 7
                if gap < 3:
                    return True
            return False

        for doc in all_doctors:
            if doc.id in already_assigned:
                status = "assigned"
            elif doc.id in cannot_work:
                status = "cannot"
            elif violates_gap(doc.id):
                status = "gap_rule"
            else:
                status = "available"

            profile = profiles.get(doc.id)
            candidates.append({
                "user": doc,
                "profile": profile,
                "status": status,
                "score": round(actual_scores.get(doc.id, 0.0), 1),
            })

        candidates.sort(key=lambda c: (0 if c["status"] == "available" else 1, c["score"]))

    return templates.TemplateResponse("admin/substitute.html", {
        "request": request, "user": admin,
        "period": period,
        "target_date": target_date,
        "candidates": candidates,
    })


# ---------------------------------------------------------------------------
# Publish (with fairness carryover)
# ---------------------------------------------------------------------------

@router.post("/{period_id}/publish")
async def publish_period(period_id: int, session: AsyncSession = Depends(get_session)):
    period = await session.get(PlanningPeriod, period_id)
    if not period:
        return RedirectResponse("/admin/planning", status_code=302)

    period.status = PlanStatus.published
    period.published_at = datetime.utcnow()
    session.add(period)

    all_assignments = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.planning_period_id == period_id)
    )).all()

    if all_assignments:
        sdays_raw = (await session.exec(
            select(SpecialDay, SpecialDayCategory).join(
                SpecialDayCategory, SpecialDay.category_id == SpecialDayCategory.id)
        )).all()
        weight_by_date = {sd.date: cat.weight for sd, cat in sdays_raw}

        doctors_raw = (await session.exec(
            select(User).where(User.role == UserRole.doctor, User.is_active == True)
        )).all()
        profiles_map = {
            p.user_id: p
            for p in (await session.exec(select(DoctorProfile))).all()
        }

        planned_days = len({a.date for a in all_assignments})
        total_factor = sum(
            (profiles_map[u.id].part_time_factor if u.id in profiles_map else 1.0)
            for u in doctors_raw
        ) or 1.0
        total_slots = planned_days * settings.doctors_per_day

        actual_scores: dict = defaultdict(float)
        for a in all_assignments:
            actual_scores[a.user_id] += weight_by_date.get(a.date, 1.0)

        for u in doctors_raw:
            profile = profiles_map.get(u.id)
            if not profile:
                continue
            fair_share = (profile.part_time_factor / total_factor) * total_slots
            delta = actual_scores.get(u.id, 0.0) - fair_share
            profile.carried_over_score = round(profile.carried_over_score + delta, 3)
            session.add(profile)

    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)

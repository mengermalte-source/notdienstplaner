import csv
import io
from calendar import monthcalendar
from collections import defaultdict
from datetime import date as date_type, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_session
from app.deps import require_admin
from app.models.schedule import PlanStatus, PlanningPeriod, ShiftAssignment
from app.models.holiday_carryover import HolidayDutyCarryover
from app.models.user import DoctorProfile, User, UserRole
from app.models.wish import WishEntry, WishPriority, WishType
from types import SimpleNamespace
from app.models.vacation import VacationPeriod
from app.services.algorithm import solve_schedule, solve_substitute_schedule, get_day_weight
from app.services.fairness import compute_fairness_score, compute_target_duties
from app.models.swap import SwapRequest, SwapStatus
from app.services.email import send_coverage_request

router = APIRouter(prefix="/admin/planning", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "templates")


def _get_key_holiday_dates(year: int) -> dict[str, set[date_type]]:
    return {
        "weihnachten": {date_type(year, 12, 24), date_type(year, 12, 25), date_type(year, 12, 26)},
        "silvester": {date_type(year, 12, 31), date_type(year + 1, 1, 1)},
    }

_MONTH_NAMES = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]
_WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

_DOCTOR_COLORS = [
    "bg-blue-500", "bg-emerald-500", "bg-violet-500", "bg-amber-500", "bg-rose-500",
    "bg-cyan-500", "bg-orange-500", "bg-teal-500", "bg-pink-500", "bg-indigo-500",
    "bg-lime-600", "bg-red-500", "bg-sky-500", "bg-purple-500", "bg-yellow-500",
    "bg-green-600", "bg-fuchsia-500", "bg-slate-500", "bg-zinc-600", "bg-stone-500",
]



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
    today = date_type.today()
    periods = (await session.exec(
        select(PlanningPeriod).order_by(PlanningPeriod.start_date.desc())
    )).all()
    return templates.TemplateResponse("admin/planning.html", {
        "request": request, "user": admin,
        "periods": periods, "period": None, "assignments": [], "today": today,
    })


# ---------------------------------------------------------------------------
# Delete period
# ---------------------------------------------------------------------------

@router.post("/{period_id}/delete")
async def delete_period(period_id: int, session: AsyncSession = Depends(get_session)):
    period = await session.get(PlanningPeriod, period_id)
    if not period:
        return RedirectResponse("/admin/planning", status_code=302)
    assignments = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.planning_period_id == period_id)
    )).all()
    for a in assignments:
        await session.delete(a)
    carryovers = (await session.exec(
        select(HolidayDutyCarryover).where(HolidayDutyCarryover.planning_period_id == period_id)
    )).all()
    for c in carryovers:
        await session.delete(c)
    await session.delete(period)
    await session.commit()
    return RedirectResponse("/admin/planning", status_code=302)


# ---------------------------------------------------------------------------
# Run algorithm
# ---------------------------------------------------------------------------

@router.post("/{period_id}/run")
async def run_algorithm(
    period_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    period = await session.get(PlanningPeriod, period_id)
    if not period:
        return RedirectResponse("/admin/planning", status_code=302)

    doctors = (await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True)
    )).all()

    if len(doctors) < 2:
        return RedirectResponse(
            f"/admin/planning/{period_id}?error=Mindestens+2+aktive+%C3%84rzte+erforderlich.+Aktuell:+{len(doctors)}.",
            status_code=302,
        )

    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

    class DoctorWithFactor:
        def __init__(self, user, profile):
            self.id = user.id
            self.credit_factor = profile.credit_factor if profile else 1.0
            self.carried_over_score = profile.carried_over_score if profile else 0.0
            self.desired_shifts = profile.desired_shifts if profile else None
            self.day_preference = profile.day_preference if profile else "alle"

    doctor_objs = [DoctorWithFactor(u, profiles.get(u.id)) for u in doctors]

    holiday_dates = set()

    # Urlaubszeiträume laden
    vacation_periods = (await session.exec(
        select(VacationPeriod).where(
            VacationPeriod.user_id.in_([u.id for u in doctors]),
            VacationPeriod.end_date >= period.start_date,
            VacationPeriod.start_date <= period.end_date,
        )
    )).all()

    # Tagesauswahl: Mi, Fr, Sa, So
    all_days = [
        period.start_date + timedelta(days=i)
        for i in range((period.end_date - period.start_date).days + 1)
    ]
    days = [
        d for d in all_days
        if d.weekday() in (2, 4, 5, 6)
    ]

    if not days:
        return RedirectResponse(
            f"/admin/planning/{period_id}?error=Keine+zu+planenden+Tage+gefunden.+Bitte+Zeitraum+pr%C3%BCfen.",
            status_code=302,
        )

    wishes = (await session.exec(
        select(WishEntry).where(
            WishEntry.date >= period.start_date,
            WishEntry.date <= period.end_date,
        )
    )).all()

    # Urlaubszeiträume → virtuelle Hard-Wishes
    days_set = set(days)
    vac_wishes = []
    for vp in vacation_periods:
        cur = vp.start_date
        while cur <= vp.end_date:
            if cur in days_set:
                vac_wishes.append(SimpleNamespace(
                    user_id=vp.user_id, date=cur,
                    wish_type="negative", priority="hard", is_vacation=True,
                ))
            cur += timedelta(days=1)

    all_wishes = list(wishes) + vac_wishes

    # Feiertagsübertrag aus früheren Perioden laden
    all_carryovers = (await session.exec(
        select(HolidayDutyCarryover).where(
            HolidayDutyCarryover.planning_period_id != period_id
        )
    )).all()
    penalty_map: dict[int, set[str]] = defaultdict(set)
    for c in all_carryovers:
        if c.worked:
            penalty_map[c.user_id].add(c.holiday_key)

    key_holiday_dates_for_period = _get_key_holiday_dates(period.year)

    assignments = solve_schedule(
        doctor_objs, days, all_wishes, holiday_dates,
        holiday_carryover_penalty=dict(penalty_map),
        key_holiday_dates=key_holiday_dates_for_period,
    )
    if assignments is None:
        assignments = solve_schedule(
            doctor_objs, days, all_wishes, holiday_dates,
            holiday_carryover_penalty=dict(penalty_map),
            key_holiday_dates=key_holiday_dates_for_period,
            strict_wishes=False,
        )

    if assignments is None:
        return RedirectResponse(
            f"/admin/planning/{period_id}?error=Kein+g%C3%BCltiger+Plan+gefunden.+"
            f"%C3%84rzte:+{len(doctors)},+Tage:+{len(days)}.+"
            "Zu+viele+Urlaubssperren+oder+zu+wenig+%C3%84rzte.",
            status_code=302,
        )

    old = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.planning_period_id == period_id)
    )).all()
    for a in old:
        await session.delete(a)

    for user_id, day in assignments:
        session.add(ShiftAssignment(
            planning_period_id=period_id,
            user_id=user_id,
            date=day,
            weighted_score=get_day_weight(day, holiday_dates),
        ))
    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)


# ---------------------------------------------------------------------------
# Run substitute algorithm (Bereitschaft Dez–Apr)
# ---------------------------------------------------------------------------

@router.post("/{period_id}/run-substitute")
async def run_substitute_algorithm(
    period_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    period = await session.get(PlanningPeriod, period_id)
    if not period:
        return RedirectResponse("/admin/planning", status_code=302)

    doctors = (await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True)
    )).all()
    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

    holiday_dates = set()

    all_days = [
        period.start_date + timedelta(days=i)
        for i in range((period.end_date - period.start_date).days + 1)
    ]
    # Nur Dez–Apr, gleiche Tagestypen wie Primärplan
    sub_days = [
        d for d in all_days
        if d.month in (12, 1, 2, 3, 4) and d.weekday() in (2, 4, 5, 6)
    ]

    if not sub_days:
        return RedirectResponse(
            f"/admin/planning/{period_id}?error=Keine+Bereitschaftstage",
            status_code=302,
        )

    primary_assignments = (await session.exec(
        select(ShiftAssignment).where(
            ShiftAssignment.planning_period_id == period_id,
            ShiftAssignment.is_substitute == False,
        )
    )).all()
    primary_set = {(a.user_id, a.date) for a in primary_assignments}

    wishes = (await session.exec(
        select(WishEntry).where(
            WishEntry.date >= period.start_date,
            WishEntry.date <= period.end_date,
        )
    )).all()

    class DoctorWithFactor:
        def __init__(self, user, profile):
            self.id = user.id
            self.credit_factor = profile.credit_factor if profile else 1.0
            # sub_carried_over_score für Bereitschafts-Fairness
            self.carried_over_score = profile.sub_carried_over_score if profile else 0.0
            self.desired_shifts = None
            self.day_preference = "alle"

    doctor_objs = [DoctorWithFactor(u, profiles.get(u.id)) for u in doctors]

    # Alte Bereitschaftsdienste löschen
    old_subs = (await session.exec(
        select(ShiftAssignment).where(
            ShiftAssignment.planning_period_id == period_id,
            ShiftAssignment.is_substitute == True,
        )
    )).all()
    for s in old_subs:
        await session.delete(s)

    sub_assignments = solve_substitute_schedule(
        doctor_objs, sub_days, primary_set, wishes, holiday_dates
    )

    if sub_assignments is None:
        await session.commit()  # commit deletions before redirect
        return RedirectResponse(
            f"/admin/planning/{period_id}?error=Kein+Bereitschaftsplan+gefunden",
            status_code=302,
        )

    for user_id, day in sub_assignments:
        session.add(ShiftAssignment(
            planning_period_id=period_id,
            user_id=user_id,
            date=day,
            weighted_score=get_day_weight(day, holiday_dates),
            is_substitute=True,
        ))
    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)


# ---------------------------------------------------------------------------
# Period detail (table view)
# ---------------------------------------------------------------------------

@router.get("/{period_id}", response_class=HTMLResponse)
async def period_detail(
    period_id: int, request: Request,
    error: Optional[str] = Query(None),
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
    doctors = [u for u in users.values()
               if u.role == UserRole.doctor and u.is_active]
    doctors.sort(key=lambda u: u.full_name)
    holiday_dates = set()
    scores = compute_fairness_score([(a.user_id, a.date) for a in assignments], holiday_dates)
    return templates.TemplateResponse("admin/planning.html", {
        "request": request, "user": admin,
        "period": period, "assignments": assignments,
        "users": users, "doctors": doctors, "scores": scores, "periods": [],
        "error": error,
    })


# ---------------------------------------------------------------------------
# Calendar view
# ---------------------------------------------------------------------------

@router.get("/{period_id}/calendar", response_class=HTMLResponse)
async def period_calendar(
    period_id: int, request: Request,
    month: Optional[str] = Query(None),
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

    all_months = _build_months(period.start_date, period.end_date)

    sel_year, sel_month = None, None
    if month:
        try:
            sel_year, sel_month = map(int, month.split("-"))
        except (ValueError, AttributeError):
            pass
    if sel_year is None:
        today = date_type.today()
        if period.start_date <= today <= period.end_date:
            sel_year, sel_month = today.year, today.month
        else:
            sel_year, sel_month = all_months[0]["year"], all_months[0]["month"]

    current_idx = next(
        (i for i, mo in enumerate(all_months) if mo["year"] == sel_year and mo["month"] == sel_month),
        0,
    )
    current_month = all_months[current_idx]

    def month_url(idx: int) -> Optional[str]:
        if 0 <= idx < len(all_months):
            mo = all_months[idx]
            return f"/admin/planning/{period_id}/calendar?month={mo['year']}-{mo['month']:02d}"
        return None

    return templates.TemplateResponse("admin/calendar_view.html", {
        "request": request, "user": admin,
        "period": period,
        "month": current_month,
        "all_months": all_months,
        "duties_by_date": dict(duties_by_date),
        "doctor_colors": _build_doctor_colors(users),
        "prev_url": month_url(current_idx - 1),
        "next_url": month_url(current_idx + 1),
    })


# ---------------------------------------------------------------------------
# QS — Qualitätssicherung
# ---------------------------------------------------------------------------

def _qa_compute_targets(doctors: list, service_days: list, holiday_dates: set) -> dict:
    from app.services.algorithm import get_day_coverage
    total_slots = sum(get_day_coverage(d, holiday_dates) for d in service_days)
    fixed = [doc for doc in doctors if doc.desired_shifts is not None]
    flex = [doc for doc in doctors if doc.desired_shifts is None]
    fixed_claimed = sum(doc.desired_shifts for doc in fixed)
    flex_slots = max(0, total_slots - fixed_claimed)
    total_flex_credit = sum(doc.credit_factor for doc in flex) or 1.0
    targets: dict = {}
    for doc in fixed:
        targets[doc.id] = float(doc.desired_shifts)
    for doc in flex:
        targets[doc.id] = (doc.credit_factor / total_flex_credit) * flex_slots
    return targets


@router.get("/{period_id}/qa", response_class=HTMLResponse)
async def period_qa(
    period_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    from collections import Counter
    period = await session.get(PlanningPeriod, period_id)
    assignments = (await session.exec(
        select(ShiftAssignment).where(
            ShiftAssignment.planning_period_id == period_id,
            ShiftAssignment.is_substitute == False,
        ).order_by(ShiftAssignment.date)
    )).all()

    if not assignments:
        return templates.TemplateResponse("admin/qa.html", {
            "request": request, "user": admin,
            "period": period, "checks": [], "passed": 0, "total": 0,
            "has_assignments": False,
        })

    doctors = (await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True)
    )).all()
    profiles_map = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}
    wishes = (await session.exec(
        select(WishEntry).where(
            WishEntry.date >= period.start_date,
            WishEntry.date <= period.end_date,
        )
    )).all()
    vacations = (await session.exec(
        select(VacationPeriod).where(
            VacationPeriod.end_date >= period.start_date,
            VacationPeriod.start_date <= period.end_date,
        )
    )).all()

    holiday_dates: set = set()
    users_map = {d.id: d for d in doctors}

    # Service days
    all_days = [
        period.start_date + timedelta(days=i)
        for i in range((period.end_date - period.start_date).days + 1)
    ]
    service_days = [d for d in all_days if d.weekday() in (2, 4, 5, 6) or d in holiday_dates]

    # Aggregates
    counts_per_day: Counter = Counter(a.date for a in assignments)
    counts_per_doctor: Counter = Counter(a.user_id for a in assignments)
    weighted_scores: dict[int, float] = {}
    for a in assignments:
        weighted_scores[a.user_id] = (
            weighted_scores.get(a.user_id, 0.0) + get_day_weight(a.date, holiday_dates)
        )

    # Doctor proxy objects for target computation
    class _Doc:
        def __init__(self, user, profile):
            self.id = user.id
            self.credit_factor = profile.credit_factor if profile else 1.0
            self.desired_shifts = profile.desired_shifts if profile else None
            self.day_preference = str(profile.day_preference) if profile else "alle"

    doc_objs = [_Doc(d, profiles_map.get(d.id)) for d in doctors]
    targets = _qa_compute_targets(doc_objs, service_days, holiday_dates)

    # Hard wishes and vacation blocks
    hard_negative = {
        (w.user_id, w.date) for w in wishes
        if w.wish_type == "negative" and w.priority == "hard"
    }
    assignment_set = {(a.user_id, a.date) for a in assignments}
    vacation_blocked: set = set()
    for vp in vacations:
        cur = vp.start_date
        while cur <= vp.end_date:
            if cur.weekday() in (2, 4, 5, 6):
                vacation_blocked.add((vp.user_id, cur))
            cur += timedelta(days=1)

    checks = []

    # --- 1. Tagesabdeckung vollständig ---
    coverage_errors = []
    for day in service_days:
        req = get_day_coverage(day, holiday_dates)
        got = counts_per_day.get(day, 0)
        if got != req:
            label = day.strftime("%d.%m.%Y") + f" ({['Mo','Di','Mi','Do','Fr','Sa','So'][day.weekday()]})"
            coverage_errors.append(f"{label}: {got} statt {req} Ärzte")
    checks.append({
        "id": "test_every_day_is_covered",
        "name": "Tagesabdeckung vollständig",
        "detail": "Mi/Fr = 1 Arzt, Sa/So/Feiertag = 2 Ärzte an jedem Diensttag",
        "passed": not coverage_errors,
        "errors": coverage_errors[:10],
        "error_count": len(coverage_errors),
    })

    # --- 2. „Kann nicht"-Wünsche eingehalten ---
    wish_errors = []
    for uid, d in hard_negative:
        if (uid, d) in assignment_set:
            name = users_map[uid].full_name if uid in users_map else f"Arzt {uid}"
            wish_errors.append(f"{name} am {d.strftime('%d.%m.%Y')}")
    checks.append({
        "id": "test_hard_wishes_respected",
        "name": "„Kann nicht"-Wünsche eingehalten",
        "detail": "Kein Arzt arbeitet an einem als „Kann nicht" gesperrten Tag",
        "passed": not wish_errors,
        "errors": wish_errors,
        "error_count": len(wish_errors),
    })

    # --- 3. Urlaubszeiträume eingehalten ---
    vac_errors = []
    for uid, d in vacation_blocked:
        if (uid, d) in assignment_set:
            name = users_map[uid].full_name if uid in users_map else f"Arzt {uid}"
            vac_errors.append(f"{name} am {d.strftime('%d.%m.%Y')}")
    checks.append({
        "id": "test_vacation_periods_respected",
        "name": "Urlaubszeiträume eingehalten",
        "detail": "Kein Dienst während eines eingetragenen Urlaubszeitraums",
        "passed": not vac_errors,
        "errors": vac_errors,
        "error_count": len(vac_errors),
    })

    # --- 4. Tagespräferenz eingehalten ---
    pref_errors = []
    for a in assignments:
        profile = profiles_map.get(a.user_id)
        if not profile:
            continue
        pref = str(profile.day_preference)
        name = users_map[a.user_id].full_name if a.user_id in users_map else f"Arzt {a.user_id}"
        if pref == "mittwoch" and a.date.weekday() != 2:
            pref_errors.append(f"{name}: {a.date.strftime('%d.%m.%Y')} ist kein Mittwoch")
        elif pref == "freitag" and a.date.weekday() != 4:
            pref_errors.append(f"{name}: {a.date.strftime('%d.%m.%Y')} ist kein Freitag")
    checks.append({
        "id": "test_day_preference_respected",
        "name": "Tagespräferenz eingehalten",
        "detail": "Ärzte mit Mittwoch- oder Freitag-Präferenz nur an diesen Tagen eingeplant",
        "passed": not pref_errors,
        "errors": pref_errors[:10],
        "error_count": len(pref_errors),
    })

    # --- 5. Anrechnungsfaktor 0 → keine Dienste ---
    zero_errors = []
    for doc in doc_objs:
        if doc.credit_factor == 0.0 and counts_per_doctor.get(doc.id, 0) > 0:
            name = users_map[doc.id].full_name if doc.id in users_map else f"Arzt {doc.id}"
            zero_errors.append(f"{name}: {counts_per_doctor[doc.id]} Dienste trotz Faktor 0")
    checks.append({
        "id": "test_zero_credit_doctor_gets_no_shifts",
        "name": "Anrechnungsfaktor 0 → keine Dienste",
        "detail": "Ärzte mit Anrechnungsfaktor 0 werden nicht eingeplant",
        "passed": not zero_errors,
        "errors": zero_errors,
        "error_count": len(zero_errors),
    })

    # --- 6. Obergrenze eingehalten (≤ 115 %+2) ---
    upper_errors = []
    for doc in doc_objs:
        t = targets.get(doc.id, 0.0)
        upper = int(t * 1.15) + 2
        got = counts_per_doctor.get(doc.id, 0)
        if got > upper:
            name = users_map[doc.id].full_name if doc.id in users_map else f"Arzt {doc.id}"
            upper_errors.append(f"{name}: {got} Dienste (Grenze {upper}, Ziel {t:.1f})")
    checks.append({
        "id": "test_upper_bound_not_exceeded",
        "name": "Obergrenze nicht überschritten (≤ 115 %+2)",
        "detail": "Kein Arzt erhält mehr als 115 % seiner Sollzahl + 2 Dienste",
        "passed": not upper_errors,
        "errors": upper_errors,
        "error_count": len(upper_errors),
    })

    # --- 7. Untergrenze eingehalten (≥ 70 %) ---
    lower_errors = []
    for doc in doc_objs:
        t = targets.get(doc.id, 0.0)
        if t >= 1.0:
            lower = max(1, int(t * 0.70))
            got = counts_per_doctor.get(doc.id, 0)
            if got < lower:
                name = users_map[doc.id].full_name if doc.id in users_map else f"Arzt {doc.id}"
                lower_errors.append(f"{name}: {got} Dienste (Minimum {lower}, Ziel {t:.1f})")
    checks.append({
        "id": "test_lower_bound_respected",
        "name": "Untergrenze eingehalten (≥ 70 %)",
        "detail": "Jeder Arzt mit Solldiensten erhält mindestens 70 % davon",
        "passed": not lower_errors,
        "errors": lower_errors,
        "error_count": len(lower_errors),
    })

    # --- 8. Alle aktiven Ärzte eingeplant ---
    eligible_ids = {d.id for d in doc_objs if targets.get(d.id, 0.0) >= 1.0}
    assigned_ids = set(counts_per_doctor.keys())
    missing_ids = eligible_ids - assigned_ids
    missing_errors = []
    for uid in missing_ids:
        name = users_map[uid].full_name if uid in users_map else f"Arzt {uid}"
        missing_errors.append(f"{name} hat 0 Dienste (Ziel {targets.get(uid, 0):.1f})")
    checks.append({
        "id": "test_all_eligible_doctors_receive_shifts",
        "name": "Alle berechtigten Ärzte eingeplant",
        "detail": "Kein Arzt mit Solldienst ≥ 1 bleibt ohne Dienst",
        "passed": not missing_errors,
        "errors": missing_errors,
        "error_count": len(missing_errors),
    })

    # --- 9. Fairness-Spreizung ---
    active_scores = [weighted_scores.get(d.id, 0.0) for d in doc_objs if targets.get(d.id, 0.0) >= 1.0]
    fair_errors = []
    if len(active_scores) >= 2:
        spread = max(active_scores) - min(active_scores)
        avg = sum(active_scores) / len(active_scores)
        # Threshold scales: allow 12 points absolute spread (test uses 8 for uniform doctors)
        if spread > 12.0:
            fair_errors.append(
                f"Spreizung {spread:.1f} Punkte (Ø {avg:.1f}) — Schwelle 12,0"
            )
    checks.append({
        "id": "test_fairness_spread_acceptable",
        "name": "Fairness-Spreizung akzeptabel (≤ 12 Punkte)",
        "detail": "Max−Min des gewichteten Scores über alle berechtigten Ärzte",
        "passed": not fair_errors,
        "errors": fair_errors,
        "error_count": len(fair_errors),
    })

    passed = sum(1 for c in checks if c["passed"])

    return templates.TemplateResponse("admin/qa.html", {
        "request": request, "user": admin,
        "period": period,
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "has_assignments": True,
    })


# ---------------------------------------------------------------------------
# Dienstbuch (journal)
# ---------------------------------------------------------------------------

@router.get("/{period_id}/journal", response_class=HTMLResponse)
async def period_journal(
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
    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

    confirmed = sum(1 for a in assignments if a.acknowledged_at)
    overridden = sum(1 for a in assignments if a.is_manual_override)

    return templates.TemplateResponse("admin/journal.html", {
        "request": request, "user": admin,
        "period": period, "assignments": assignments,
        "users": users, "profiles": profiles,
        "weekday_names": _WEEKDAY_NAMES,
        "confirmed": confirmed, "overridden": overridden,
        "total": len(assignments),
    })


# ---------------------------------------------------------------------------
# CSV-Export (KV-Abrechnung)
# ---------------------------------------------------------------------------

@router.get("/{period_id}/export.csv")
async def export_csv(
    period_id: int,
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
    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Datum", "Wochentag", "Arzt", "E-Mail", "Teilzeit", "Gewicht", "Bestätigt"])

    for a in assignments:
        u = users.get(a.user_id)
        if not u:
            continue
        profile = profiles.get(a.user_id)
        factor = f"{int((profile.part_time_factor if profile else 1.0) * 100)}%"
        confirmed = a.acknowledged_at.strftime("%d.%m.%Y") if a.acknowledged_at else "Nein"
        writer.writerow([
            a.date.strftime("%d.%m.%Y"),
            _WEEKDAY_NAMES[a.date.weekday()],
            u.full_name,
            u.email,
            factor,
            f"{a.weighted_score:.1f}".replace(".", ","),
            confirmed,
        ])

    output.seek(0)
    filename = f"notdienste_{period.name.replace(' ', '_')}.csv" if period else "notdienste.csv"
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Substitute finder (per period)
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
            select(ShiftAssignment).where(ShiftAssignment.planning_period_id == period_id)
        )).all()
        actual_scores: dict = defaultdict(float)
        for a in all_period_assignments:
            actual_scores[a.user_id] += a.weighted_score

        for doc in all_doctors:
            if doc.id in already_assigned:
                status = "assigned"
            elif doc.id in cannot_work:
                status = "cannot"
            else:
                status = "available"
            profile = profiles.get(doc.id)
            candidates.append({
                "user": doc, "profile": profile, "status": status,
                "score": round(actual_scores.get(doc.id, 0.0), 1),
            })
        candidates.sort(key=lambda c: (0 if c["status"] == "available" else 1, c["score"]))

    return templates.TemplateResponse("admin/substitute.html", {
        "request": request, "user": admin,
        "period": period, "target_date": target_date, "candidates": candidates,
    })


@router.post("/{period_id}/substitute/propose")
async def propose_coverage(
    period_id: int,
    target_date: str = Form(...),
    absent_doctor_id: int = Form(...),
    substitute_id: int = Form(...),
    message: str = Form(""),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    shift_date = date_type.fromisoformat(target_date)
    absent_doctor = await session.get(User, absent_doctor_id)
    substitute = await session.get(User, substitute_id)
    if not absent_doctor or not substitute:
        return RedirectResponse(
            f"/admin/planning/{period_id}/substitute?date={target_date}", status_code=302
        )
    session.add(SwapRequest(
        requester_id=absent_doctor_id,
        target_id=substitute_id,
        requester_shift_date=shift_date,
        target_shift_date=shift_date,
        planning_period_id=period_id,
        message=message,
        is_coverage_request=True,
    ))
    await session.commit()
    send_coverage_request(
        substitute.email, substitute.full_name,
        absent_doctor.full_name, shift_date, message,
    )
    return RedirectResponse(
        f"/admin/planning/{period_id}/substitute?date={target_date}", status_code=302
    )


# ---------------------------------------------------------------------------
# Manual override: Arzt auf einem Slot hart überschreiben
# ---------------------------------------------------------------------------

@router.post("/{period_id}/assignments/{assignment_id}/override")
async def override_assignment(
    period_id: int,
    assignment_id: int,
    new_user_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    a = await session.get(ShiftAssignment, assignment_id)
    if not a or a.planning_period_id != period_id:
        return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)
    a.user_id = new_user_id
    a.is_manual_override = True
    a.acknowledged_at = None
    a.weighted_score = get_day_weight(a.date, set())
    session.add(a)
    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)


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
    primary_assignments = [a for a in all_assignments if not a.is_substitute]
    sub_assignments = [a for a in all_assignments if a.is_substitute]

    if all_assignments:
        holiday_dates_all = set()

        doctors_raw = (await session.exec(
            select(User).where(User.role == UserRole.doctor, User.is_active == True)
        )).all()
        profiles_map = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

        # Primary fairness carryover (excludes substitutes)
        actual_scores: dict = compute_fairness_score(
            [(a.user_id, a.date) for a in primary_assignments],
            holiday_dates_all,
        )
        total_factor = sum(
            profiles_map[u.id].credit_factor if u.id in profiles_map else 1.0
            for u in doctors_raw
        ) or 1.0
        total_weighted = sum(
            get_day_weight(a.date, holiday_dates_all) for a in primary_assignments
        )

        # Substitute fairness carryover (sub_carried_over_score)
        sub_scores: dict = compute_fairness_score(
            [(a.user_id, a.date) for a in sub_assignments],
            holiday_dates_all,
        )
        total_sub_weighted = sum(
            get_day_weight(a.date, holiday_dates_all) for a in sub_assignments
        )

        for u in doctors_raw:
            profile = profiles_map.get(u.id)
            if not profile:
                continue
            fair_share = (profile.credit_factor / total_factor) * total_weighted
            profile.carried_over_score = round(
                profile.carried_over_score + (actual_scores.get(u.id, 0.0) - fair_share), 3
            )
            sub_fair_share = (profile.credit_factor / total_factor) * total_sub_weighted
            profile.sub_carried_over_score = round(
                profile.sub_carried_over_score + (sub_scores.get(u.id, 0.0) - sub_fair_share), 3
            )
            session.add(profile)

        # Feiertagshistorie speichern (idempotent: alte Zeilen erst löschen)
        old_carryovers = (await session.exec(
            select(HolidayDutyCarryover).where(
                HolidayDutyCarryover.planning_period_id == period_id
            )
        )).all()
        for old in old_carryovers:
            await session.delete(old)

        key_holidays = _get_key_holiday_dates(period.year)
        for key, date_set in key_holidays.items():
            for u in doctors_raw:
                worked = any(
                    a.user_id == u.id and a.date in date_set
                    for a in primary_assignments
                )
                session.add(HolidayDutyCarryover(
                    user_id=u.id,
                    planning_period_id=period_id,
                    holiday_key=key,
                    worked=worked,
                ))

    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)

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
from app.services.algorithm import solve_schedule, solve_substitute_schedule, get_day_weight, get_day_coverage
from app.services.fairness import compute_fairness_score, compute_target_duties
from app.models.swap import SwapRequest, SwapStatus

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
    cookie_pid = request.cookies.get("admin_period_id")
    if cookie_pid:
        try:
            period = await session.get(PlanningPeriod, int(cookie_pid))
            if period:
                return RedirectResponse(f"/admin/planning/{cookie_pid}", status_code=302)
        except (ValueError, TypeError):
            pass

    today = date_type.today()
    periods = (await session.exec(
        select(PlanningPeriod).order_by(PlanningPeriod.start_date.asc())
    )).all()
    active = next((p for p in periods if p.start_date <= today <= p.end_date), None)
    if not active:
        active = next((p for p in periods if p.start_date > today), None)
    if not active and periods:
        active = periods[-1]
    if active:
        return RedirectResponse(f"/admin/planning/{active.id}", status_code=302)

    return templates.TemplateResponse("admin/planning.html", {
        "request": request, "user": admin,
        "period": None, "assignments": [], "users": {}, "doctors": [],
        "scores": {}, "periods": [], "error": None,
        "qa_passed": None, "qa_total": None, "fairness_rows": [], "show_list": False, "show_fairness": False,
        "month": None, "all_months": [], "duties_by_date": {},
        "doctor_colors": {}, "prev_url": None, "next_url": None,
        "day_pressure": {},
    })


# ---------------------------------------------------------------------------
# Reset period (back to draft, delete all assignments)
# ---------------------------------------------------------------------------

@router.post("/{period_id}/reset")
async def reset_period(period_id: int, session: AsyncSession = Depends(get_session)):
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
    period.status = PlanStatus.draft
    period.published_at = None
    session.add(period)
    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)


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

    from app.models.recurring_block import RecurringBlock as RecurringBlockModel
    recurring_blocks = (await session.exec(
        select(RecurringBlockModel).where(
            RecurringBlockModel.user_id.in_([u.id for u in doctors])
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

    # Expand recurring annual blocks to service days in this period
    recurring_wishes = []
    for rb in recurring_blocks:
        for d in days:
            try:
                from datetime import date as _date
                if d.month == rb.month and d.day == rb.day:
                    recurring_wishes.append(SimpleNamespace(
                        user_id=rb.user_id, date=d,
                        wish_type="negative", priority="hard",
                    ))
            except Exception:
                pass

    all_wishes = list(wishes) + vac_wishes + recurring_wishes

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

    # Auto-run substitute (Bereitschaft) for Dec–Apr overlap
    all_days_period = [
        period.start_date + timedelta(days=i)
        for i in range((period.end_date - period.start_date).days + 1)
    ]
    sub_days = [
        d for d in all_days_period
        if d.month in (12, 1, 2, 3, 4) and d.weekday() in (2, 4, 5, 6)
    ]
    if sub_days:
        primary_set = set(assignments)

        class _SubDoc:
            def __init__(self, user, profile):
                self.id = user.id
                self.credit_factor = profile.credit_factor if profile else 1.0
                self.carried_over_score = profile.sub_carried_over_score if profile else 0.0
                self.desired_shifts = None
                self.day_preference = "alle"

        sub_doctor_objs = [_SubDoc(u, profiles.get(u.id)) for u in doctors]
        sub_assignments = solve_substitute_schedule(
            sub_doctor_objs, sub_days, primary_set, all_wishes, holiday_dates
        )
        if sub_assignments:
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
# Run substitute algorithm (Bereitschaft Dez–Apr) — kept for manual re-run
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
    view: Optional[str] = Query(None),
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
    doctors = [u for u in users.values()
               if u.role == UserRole.doctor and u.is_active]
    doctors.sort(key=lambda u: u.full_name)
    holiday_dates = set()
    scores = compute_fairness_score([(a.user_id, a.date) for a in assignments], holiday_dates)

    # Load wishes and vacations (used for both QA and heatmap)
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

    # QS-Zusammenfassung
    qa_passed: Optional[int] = None
    qa_total: Optional[int] = None
    if any(not a.is_substitute for a in assignments):
        profiles_map = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}
        from app.models.recurring_block import RecurringBlock as RecurringBlockModel
        recurring_blocks_qa = (await session.exec(
            select(RecurringBlockModel).where(
                RecurringBlockModel.user_id.in_([d.id for d in doctors])
            )
        )).all()
        checks = _run_qa_checks(assignments, period, doctors, profiles_map, wishes, vacations, recurring_blocks_qa)
        qa_passed = sum(1 for c in checks if c["passed"])
        qa_total = len(checks)

        # Fairness-Übersicht
        from collections import Counter as _Counter
        primary_assignments = [a for a in assignments if not a.is_substitute]
        total_credit = sum(
            profiles_map[d.id].credit_factor if d.id in profiles_map else 1.0
            for d in doctors
        ) or 1.0
        total_weighted = sum(get_day_weight(a.date, set()) for a in primary_assignments)
        shift_counts = _Counter(a.user_id for a in primary_assignments)
        w_scores_map: dict[int, float] = {}
        for a in primary_assignments:
            w_scores_map[a.user_id] = w_scores_map.get(a.user_id, 0.0) + get_day_weight(a.date, set())
        fairness_rows = []
        for doc in doctors:
            cf = profiles_map[doc.id].credit_factor if doc.id in profiles_map else 1.0
            if cf == 0.0:
                continue
            target_w = (cf / total_credit) * total_weighted
            actual_w = w_scores_map.get(doc.id, 0.0)
            delta = round(actual_w - target_w, 1)
            fairness_rows.append({
                "name": doc.full_name,
                "short": doc.full_name.split()[-1],
                "count": shift_counts.get(doc.id, 0),
                "score": round(actual_w, 1),
                "target": round(target_w, 1),
                "delta": delta,
            })
        fairness_rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    else:
        fairness_rows = []

    # Heatmap: count hard blocks per service day (Mi/Fr/Sa/So)
    _neg_by_date: dict[date_type, int] = defaultdict(int)
    for w in wishes:
        if w.wish_type == WishType.negative and w.priority == WishPriority.hard:
            _neg_by_date[w.date] += 1
    _vac_by_date: dict[date_type, int] = defaultdict(int)
    for vp in vacations:
        cur_v = vp.start_date
        while cur_v <= vp.end_date:
            _vac_by_date[cur_v] += 1
            cur_v += timedelta(days=1)
    day_pressure: dict[date_type, dict] = {}
    cur_p = period.start_date
    while cur_p <= period.end_date:
        if cur_p.weekday() in (2, 4, 5, 6):
            neg = _neg_by_date.get(cur_p, 0)
            vac = _vac_by_date.get(cur_p, 0)
            if neg + vac > 0:
                day_pressure[cur_p] = {"neg": neg, "vac": vac, "total": neg + vac}
        cur_p += timedelta(days=1)

    # Calendar data: include assignment_id for quick-edit
    duties_by_date: dict = defaultdict(list)
    for a in assignments:
        if a.user_id in users and not a.is_substitute:
            duties_by_date[a.date].append({"assignment_id": a.id, "user": users[a.user_id]})

    all_months = _build_months(period.start_date, period.end_date)
    sel_year, sel_month_num = None, None
    if month:
        try:
            sel_year, sel_month_num = map(int, month.split("-"))
        except (ValueError, AttributeError):
            pass
    if sel_year is None:
        today_d = date_type.today()
        if period.start_date <= today_d <= period.end_date:
            sel_year, sel_month_num = today_d.year, today_d.month
        else:
            sel_year, sel_month_num = all_months[0]["year"], all_months[0]["month"]
    current_idx = next(
        (i for i, mo in enumerate(all_months) if mo["year"] == sel_year and mo["month"] == sel_month_num),
        0,
    )
    current_month_data = all_months[current_idx]

    def month_url(idx: int) -> Optional[str]:
        if 0 <= idx < len(all_months):
            mo = all_months[idx]
            return f"/admin/planning/{period_id}?month={mo['year']}-{mo['month']:02d}"
        return None

    response = templates.TemplateResponse("admin/planning.html", {
        "request": request, "user": admin,
        "period": period, "assignments": assignments,
        "users": users, "doctors": doctors, "scores": scores, "periods": [],
        "error": error,
        "qa_passed": qa_passed,
        "qa_total": qa_total,
        "fairness_rows": fairness_rows,
        "show_list": (view == "list"),
        "show_fairness": (view == "fairness"),
        "month": current_month_data,
        "all_months": all_months,
        "duties_by_date": dict(duties_by_date),
        "doctor_colors": _build_doctor_colors(users),
        "prev_url": month_url(current_idx - 1),
        "next_url": month_url(current_idx + 1),
        "day_pressure": day_pressure,
    })
    response.set_cookie("admin_period_id", str(period_id), max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


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
    dest = f"/admin/planning/{period_id}"
    if month:
        dest += f"?month={month}"
    return RedirectResponse(dest, status_code=302)


# ---------------------------------------------------------------------------
# QS — Qualitätssicherung (shared logic)
# ---------------------------------------------------------------------------

def _qa_compute_targets(doctors: list, service_days: list, holiday_dates: set) -> dict:
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


def _run_qa_checks(assignments: list, period, doctors: list, profiles_map: dict,
                   wishes: list, vacations: list, recurring_blocks=None) -> list[dict]:
    from collections import Counter
    holiday_dates: set = set()
    users_map = {d.id: d for d in doctors}

    primary = [a for a in assignments if not a.is_substitute]

    all_days = [
        period.start_date + timedelta(days=i)
        for i in range((period.end_date - period.start_date).days + 1)
    ]
    service_days = [d for d in all_days if d.weekday() in (2, 4, 5, 6) or d in holiday_dates]

    counts_per_day: Counter = Counter(a.date for a in primary)
    counts_per_doctor: Counter = Counter(a.user_id for a in primary)
    weighted_scores: dict[int, float] = {}
    for a in primary:
        weighted_scores[a.user_id] = (
            weighted_scores.get(a.user_id, 0.0) + get_day_weight(a.date, holiday_dates)
        )

    class _Doc:
        def __init__(self, user, profile):
            self.id = user.id
            self.credit_factor = profile.credit_factor if profile else 1.0
            self.desired_shifts = profile.desired_shifts if profile else None
            self.day_preference = str(profile.day_preference) if profile else "alle"

    doc_objs = [_Doc(d, profiles_map.get(d.id)) for d in doctors]
    targets = _qa_compute_targets(doc_objs, service_days, holiday_dates)

    hard_negative = {
        (w.user_id, w.date) for w in wishes
        if w.wish_type == "negative" and w.priority == "hard"
    }
    assignment_set = {(a.user_id, a.date) for a in primary}
    vacation_blocked: set = set()
    for vp in vacations:
        cur = vp.start_date
        while cur <= vp.end_date:
            if cur.weekday() in (2, 4, 5, 6):
                vacation_blocked.add((vp.user_id, cur))
            cur += timedelta(days=1)

    # Expand recurring blocks for this period
    recurring_blocked: set = set()
    if recurring_blocks:
        all_period_days = [
            period.start_date + timedelta(days=i)
            for i in range((period.end_date - period.start_date).days + 1)
        ]
        for rb in recurring_blocks:
            for d in all_period_days:
                if d.month == rb.month and d.day == rb.day and d.weekday() in (2, 4, 5, 6):
                    recurring_blocked.add((rb.user_id, d))

    checks: list[dict] = []

    # 1. Tagesabdeckung
    coverage_errors = []
    for day in service_days:
        req = get_day_coverage(day, holiday_dates)
        got = counts_per_day.get(day, 0)
        if got != req:
            wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][day.weekday()]
            coverage_errors.append(f"{day.strftime('%d.%m.%Y')} ({wd}): {got} statt {req} Aerzte")
    checks.append({
        "id": "test_every_day_is_covered",
        "name": "Tagesabdeckung vollstaendig",
        "detail": "Mi/Fr = 1 Arzt, Sa/So/Feiertag = 2 Aerzte an jedem Diensttag",
        "passed": not coverage_errors,
        "errors": coverage_errors[:10],
        "error_count": len(coverage_errors),
    })

    # 2. Kann-nicht-Wuensche
    wish_errors = []
    for uid, d in hard_negative:
        if (uid, d) in assignment_set:
            name = users_map[uid].full_name if uid in users_map else f"Arzt {uid}"
            wish_errors.append(f"{name} am {d.strftime('%d.%m.%Y')}")
    checks.append({
        "id": "test_hard_wishes_respected",
        "name": "Kann-nicht-Wuensche eingehalten",
        "detail": "Kein Arzt arbeitet an einem als gesperrt markierten Tag",
        "passed": not wish_errors,
        "errors": wish_errors,
        "error_count": len(wish_errors),
    })

    # 3. Urlaubszeitraeume
    vac_errors = []
    for uid, d in vacation_blocked:
        if (uid, d) in assignment_set:
            name = users_map[uid].full_name if uid in users_map else f"Arzt {uid}"
            vac_errors.append(f"{name} am {d.strftime('%d.%m.%Y')}")
    checks.append({
        "id": "test_vacation_periods_respected",
        "name": "Urlaubszeitraeume eingehalten",
        "detail": "Kein Dienst waehrend eines eingetragenen Urlaubszeitraums",
        "passed": not vac_errors,
        "errors": vac_errors,
        "error_count": len(vac_errors),
    })

    # 4. Tagspraeferenz
    pref_errors = []
    for a in primary:
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
        "name": "Tagspraeferenz eingehalten",
        "detail": "Aerzte mit Mi- oder Fr-Praeferenz nur an diesen Tagen eingeplant",
        "passed": not pref_errors,
        "errors": pref_errors[:10],
        "error_count": len(pref_errors),
    })

    # 5. Anrechnungsfaktor 0
    zero_errors = []
    for doc in doc_objs:
        if doc.credit_factor == 0.0 and counts_per_doctor.get(doc.id, 0) > 0:
            name = users_map[doc.id].full_name if doc.id in users_map else f"Arzt {doc.id}"
            zero_errors.append(f"{name}: {counts_per_doctor[doc.id]} Dienste trotz Faktor 0")
    checks.append({
        "id": "test_zero_credit_doctor_gets_no_shifts",
        "name": "Anrechnungsfaktor 0 - keine Dienste",
        "detail": "Aerzte ohne Anrechnungsfaktor werden nicht eingeplant",
        "passed": not zero_errors,
        "errors": zero_errors,
        "error_count": len(zero_errors),
    })

    # 6. Obergrenze
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
        "name": "Obergrenze nicht ueberschritten (max. 115%+2)",
        "detail": "Kein Arzt erhaelt mehr als 115% seiner Sollzahl + 2 Dienste",
        "passed": not upper_errors,
        "errors": upper_errors,
        "error_count": len(upper_errors),
    })

    # 7. Untergrenze
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
        "name": "Untergrenze eingehalten (mind. 70%)",
        "detail": "Jeder Arzt mit Solldiensten erhaelt mindestens 70% davon",
        "passed": not lower_errors,
        "errors": lower_errors,
        "error_count": len(lower_errors),
    })

    # 8. Alle Aerzte eingeplant
    eligible_ids = {d.id for d in doc_objs if targets.get(d.id, 0.0) >= 1.0}
    missing_ids = eligible_ids - set(counts_per_doctor.keys())
    missing_errors = []
    for uid in missing_ids:
        name = users_map[uid].full_name if uid in users_map else f"Arzt {uid}"
        missing_errors.append(f"{name} hat 0 Dienste (Ziel {targets.get(uid, 0):.1f})")
    checks.append({
        "id": "test_all_eligible_doctors_receive_shifts",
        "name": "Alle berechtigten Aerzte eingeplant",
        "detail": "Kein Arzt mit Solldienst >= 1 bleibt ohne Dienst",
        "passed": not missing_errors,
        "errors": missing_errors,
        "error_count": len(missing_errors),
    })

    # 9. Fairness-Spreizung
    active_scores = [weighted_scores.get(d.id, 0.0) for d in doc_objs if targets.get(d.id, 0.0) >= 1.0]
    fair_errors = []
    if len(active_scores) >= 2:
        spread = max(active_scores) - min(active_scores)
        avg = sum(active_scores) / len(active_scores)
        if spread > 12.0:
            fair_errors.append(f"Spreizung {spread:.1f} Punkte (Durchschnitt {avg:.1f}) - Schwelle 12,0")
    checks.append({
        "id": "test_fairness_spread_acceptable",
        "name": "Fairness-Spreizung akzeptabel (max. 12 Punkte)",
        "detail": "Max-Min des gewichteten Scores ueber alle berechtigten Aerzte",
        "passed": not fair_errors,
        "errors": fair_errors,
        "error_count": len(fair_errors),
    })

    # 10. Recurring blocks respected
    recur_errors = []
    for uid, d in recurring_blocked:
        if (uid, d) in assignment_set:
            name = users_map[uid].full_name if uid in users_map else f"Arzt {uid}"
            recur_errors.append(f"{name} am {d.strftime('%d.%m.%Y')} (jaehrl. Sperre {d.day}.{d.month}.)")
    checks.append({
        "id": "test_recurring_blocks_respected",
        "name": "Jaehrliche Sperrtage eingehalten",
        "detail": "Kein Dienst an jahresuebergreifend gesperrten Daten",
        "passed": not recur_errors,
        "errors": recur_errors,
        "error_count": len(recur_errors),
    })

    return checks


@router.get("/{period_id}/qa", response_class=HTMLResponse)
async def period_qa(
    period_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    period = await session.get(PlanningPeriod, period_id)
    assignments = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.planning_period_id == period_id)
    )).all()

    if not [a for a in assignments if not a.is_substitute]:
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
    from app.models.recurring_block import RecurringBlock as RecurringBlockModel
    recurring_blocks_qa = (await session.exec(
        select(RecurringBlockModel).where(
            RecurringBlockModel.user_id.in_([d.id for d in doctors])
        )
    )).all()

    checks = _run_qa_checks(assignments, period, doctors, profiles_map, wishes, vacations, recurring_blocks_qa)
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
    a_date = a.date
    a.user_id = new_user_id
    a.is_manual_override = True
    a.acknowledged_at = None
    a.weighted_score = get_day_weight(a_date, set())
    session.add(a)
    await session.commit()
    return RedirectResponse(
        f"/admin/planning/{period_id}?month={a_date.year}-{a_date.month:02d}",
        status_code=302,
    )


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

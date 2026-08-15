from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from datetime import date as date_type, date as Date, datetime
from collections import defaultdict
from app.database import get_session
from app.deps import get_current_user
from app.models.user import User, DoctorProfile, UserRole
from app.models.wish import WishEntry, WishType, WishPriority
from app.models.vacation import VacationPeriod
from app.models.schedule import ShiftAssignment, PlanningPeriod, PlanStatus
from app.models.special_day import SpecialDay, SpecialDayCategory
from app.services.auth import hash_password, verify_password
from app.services.ical import build_ical
from app.services.fairness import compute_fairness_score

router = APIRouter(prefix="/me")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    wishes = (await session.exec(
        select(WishEntry).where(WishEntry.user_id == user.id)
        .order_by(WishEntry.date))).all()
    assignments = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.user_id == user.id)
        .order_by(ShiftAssignment.date))).all()
    return templates.TemplateResponse("doctor/dashboard.html",
        {"request": request, "user": user, "wishes": wishes, "assignments": assignments})


@router.get("/wishes", response_class=HTMLResponse)
async def wishes_page(request: Request, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    wishes = (await session.exec(
        select(WishEntry).where(WishEntry.user_id == user.id)
        .order_by(WishEntry.date))).all()
    vacations = (await session.exec(
        select(VacationPeriod).where(VacationPeriod.user_id == user.id)
        .order_by(VacationPeriod.start_date)
    )).all()
    return templates.TemplateResponse("doctor/wishes.html",
        {"request": request, "user": user, "wishes": wishes,
         "wish_types": WishType, "priorities": WishPriority,
         "vacations": vacations})


@router.post("/wishes")
async def create_wish(user: User = Depends(get_current_user),
                      date: str = Form(...), kind: str = Form(...),
                      reason: str = Form(""),
                      session: AsyncSession = Depends(get_session)):
    d = date_type.fromisoformat(date)
    existing = (await session.exec(
        select(WishEntry).where(WishEntry.user_id == user.id,
                                WishEntry.date == d))).first()
    if existing:
        raise HTTPException(status_code=400, detail="Für dieses Datum existiert bereits ein Wunsch")
    wish_type, priority = {
        "cannot":     (WishType.negative, WishPriority.hard),
        "prefer_not": (WishType.negative, WishPriority.soft),
        "prefer":     (WishType.positive,  WishPriority.soft),
    }.get(kind, (WishType.negative, WishPriority.soft))
    session.add(WishEntry(user_id=user.id, date=d, wish_type=wish_type,
                           priority=priority, reason=reason))
    await session.commit()
    return RedirectResponse("/me/wishes", status_code=302)


@router.post("/wishes/{wish_id}/delete")
async def delete_wish(wish_id: int, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    wish = await session.get(WishEntry, wish_id)
    if not wish or wish.user_id != user.id:
        raise HTTPException(status_code=404)
    await session.delete(wish)
    await session.commit()
    return RedirectResponse("/me/wishes", status_code=302)


@router.post("/vacations")
async def add_vacation(
    start_date: str = Form(...),
    end_date: str = Form(...),
    reason: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    start = Date.fromisoformat(start_date)
    end = Date.fromisoformat(end_date)
    if end < start:
        return RedirectResponse("/me/wishes?error=Enddatum+vor+Startdatum", status_code=302)
    session.add(VacationPeriod(user_id=user.id, start_date=start, end_date=end, reason=reason))
    await session.commit()
    return RedirectResponse("/me/wishes", status_code=302)


@router.post("/vacations/{vac_id}/delete")
async def delete_vacation(
    vac_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    vac = await session.get(VacationPeriod, vac_id)
    if vac and vac.user_id == user.id:
        await session.delete(vac)
        await session.commit()
    return RedirectResponse("/me/wishes", status_code=302)


@router.get("/schedule", response_class=HTMLResponse)
async def my_schedule(request: Request, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    assignments = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.user_id == user.id)
        .order_by(ShiftAssignment.date))).all()
    periods = {p.id: p for p in (await session.exec(select(PlanningPeriod))).all()}
    return templates.TemplateResponse("doctor/schedule.html", {
        "request": request, "user": user,
        "assignments": assignments, "periods": periods,
    })


@router.post("/assignments/{assignment_id}/acknowledge")
async def acknowledge_assignment(assignment_id: int,
                                  user: User = Depends(get_current_user),
                                  session: AsyncSession = Depends(get_session)):
    a = await session.get(ShiftAssignment, assignment_id)
    if not a or a.user_id != user.id:
        raise HTTPException(status_code=403)
    a.acknowledged_at = datetime.utcnow()
    session.add(a)
    await session.commit()
    return RedirectResponse("/me/schedule", status_code=302)


@router.get("/schedule.ics")
async def export_ical(user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    assignments = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.user_id == user.id)
        .order_by(ShiftAssignment.date))).all()
    ical_data = build_ical(user, assignments)
    return Response(content=ical_data, media_type="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=notdienste.ics"})


# ---------------------------------------------------------------------------
# Eigene Statistiken
# ---------------------------------------------------------------------------

@router.get("/stats", response_class=HTMLResponse)
async def my_stats(request: Request, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)):
    profile = (await session.exec(
        select(DoctorProfile).where(DoctorProfile.user_id == user.id)
    )).first()

    # All assignments across all published periods
    all_assignments = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.user_id == user.id)
        .order_by(ShiftAssignment.date)
    )).all()

    periods_map = {p.id: p for p in (await session.exec(select(PlanningPeriod))).all()}

    sdays_raw = (await session.exec(
        select(SpecialDay, SpecialDayCategory).join(
            SpecialDayCategory, SpecialDay.category_id == SpecialDayCategory.id)
    )).all()
    weight_by_date = {sd.date: cat.weight for sd, cat in sdays_raw}

    # Per-period breakdown
    per_period = defaultdict(lambda: {"count": 0, "score": 0.0, "period": None})
    for a in all_assignments:
        pid = a.planning_period_id
        per_period[pid]["count"] += 1
        per_period[pid]["score"] += weight_by_date.get(a.date, 1.0)
        per_period[pid]["period"] = periods_map.get(pid)

    # Peer comparison (anonymized totals)
    all_docs = (await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True)
    )).all()
    holiday_dates_doc = {sd.date for sd, cat in sdays_raw}
    all_scores_map = compute_fairness_score(
        [(a.user_id, a.date) for a in (await session.exec(select(ShiftAssignment))).all()],
        holiday_dates_doc,
    )
    all_score_values = sorted(all_scores_map.values())
    my_score = all_scores_map.get(user.id, 0.0)
    rank = sum(1 for s in all_score_values if s <= my_score)

    return templates.TemplateResponse("doctor/stats.html", {
        "request": request, "user": user,
        "profile": profile,
        "total_count": len(all_assignments),
        "total_score": round(my_score, 1),
        "carryover": round(profile.carried_over_score if profile else 0.0, 2),
        "per_period": sorted(per_period.values(), key=lambda x: x["period"].start_date if x["period"] else date_type.min, reverse=True),
        "peer_count": len(all_docs),
        "peer_rank": rank,
    })


# ---------------------------------------------------------------------------
# Passwort ändern
# ---------------------------------------------------------------------------

@router.get("/password", response_class=HTMLResponse)
async def password_page(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("doctor/password.html",
        {"request": request, "user": user, "error": None, "success": False})


@router.post("/password", response_class=HTMLResponse)
async def change_password(request: Request,
                           user: User = Depends(get_current_user),
                           current_password: str = Form(...),
                           new_password: str = Form(...),
                           confirm_password: str = Form(...),
                           session: AsyncSession = Depends(get_session)):
    ctx = {"request": request, "user": user, "error": None, "success": False}

    if not verify_password(current_password, user.hashed_password):
        ctx["error"] = "Aktuelles Passwort ist falsch."
        return templates.TemplateResponse("doctor/password.html", ctx)

    if len(new_password) < 6:
        ctx["error"] = "Neues Passwort muss mindestens 6 Zeichen lang sein."
        return templates.TemplateResponse("doctor/password.html", ctx)

    if new_password != confirm_password:
        ctx["error"] = "Passwörter stimmen nicht überein."
        return templates.TemplateResponse("doctor/password.html", ctx)

    user.hashed_password = hash_password(new_password)
    session.add(user)
    await session.commit()
    ctx["success"] = True
    return templates.TemplateResponse("doctor/password.html", ctx)

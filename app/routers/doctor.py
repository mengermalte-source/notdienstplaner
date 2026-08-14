from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from datetime import date as date_type, datetime
from app.database import get_session
from app.deps import get_current_user
from app.models.user import User
from app.models.wish import WishEntry, WishType, WishPriority
from app.models.schedule import ShiftAssignment, PlanningPeriod
from app.services.ical import build_ical

router = APIRouter(prefix="/me")
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "templates" if "admin" in str(Path(__file__)) else Path(__file__).parent.parent / "templates")


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
    return templates.TemplateResponse("doctor/wishes.html",
        {"request": request, "user": user, "wishes": wishes,
         "wish_types": WishType, "priorities": WishPriority})


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

from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from typing import Optional
from app.database import get_session
from app.deps import require_admin
from app.models.user import User
from app.models.special_day import SpecialDay, SpecialDayCategory

router = APIRouter(prefix="/admin/calendar", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "templates")


@router.get("", response_class=HTMLResponse)
async def calendar_page(request: Request, year: int = 2027,
                        session: AsyncSession = Depends(get_session),
                        admin: User = Depends(require_admin)):
    cats = (await session.exec(select(SpecialDayCategory))).all()
    days = (await session.exec(
        select(SpecialDay).where(SpecialDay.date.between(
            f"{year}-01-01", f"{year}-12-31")))).all()
    return templates.TemplateResponse("admin/calendar.html",
        {"request": request, "user": admin, "categories": cats, "special_days": days, "year": year})



@router.post("/import-holidays")
async def import_holidays(
    year: int = Form(...),
    session: AsyncSession = Depends(get_session),
):
    import holidays as hol_lib
    from datetime import date as date_type

    # Ensure a "Feiertag" category exists
    cat = (await session.exec(
        select(SpecialDayCategory).where(SpecialDayCategory.name == "Feiertag")
    )).first()
    if not cat:
        cat = SpecialDayCategory(name="Feiertag", weight=2.0, color="#ef4444")
        session.add(cat)
        await session.commit()
        await session.refresh(cat)

    # Collect dates already stored for this year
    existing = {sd.date for sd in (await session.exec(
        select(SpecialDay).where(SpecialDay.date.between(
            f"{year}-01-01", f"{year}-12-31"
        ))
    )).all()}

    # Import Bavarian public holidays (skip duplicates)
    bavarian = hol_lib.Germany(state="BY", years=year)
    for d, name in sorted(bavarian.items()):
        if d not in existing:
            session.add(SpecialDay(
                date=d, category_id=cat.id, label=name, is_auto_imported=True
            ))

    await session.commit()
    return RedirectResponse(f"/admin/calendar?year={year}", status_code=302)


@router.post("/days")
async def add_special_day(date: str = Form(...), category_id: int = Form(...),
                           label: str = Form(""),
                           session: AsyncSession = Depends(get_session)):
    from datetime import date as date_type
    session.add(SpecialDay(date=date_type.fromisoformat(date),
                           category_id=category_id, label=label))
    await session.commit()
    return RedirectResponse("/admin/calendar", status_code=302)


@router.post("/days/{day_id}/delete")
async def delete_special_day(day_id: int, session: AsyncSession = Depends(get_session)):
    day = await session.get(SpecialDay, day_id)
    if not day:
        raise HTTPException(status_code=404)
    await session.delete(day)
    await session.commit()
    return RedirectResponse("/admin/calendar", status_code=302)


@router.post("/days/{day_id}/set-doctors")
async def set_day_doctors(day_id: int,
                           required_doctors: Optional[str] = Form(None),
                           session: AsyncSession = Depends(get_session)):
    day = await session.get(SpecialDay, day_id)
    if not day:
        raise HTTPException(status_code=404)
    doctors = int(required_doctors) if required_doctors and required_doctors.strip() else None
    day.required_doctors = doctors if doctors and doctors > 0 else None
    session.add(day)
    await session.commit()
    return RedirectResponse("/admin/calendar", status_code=302)

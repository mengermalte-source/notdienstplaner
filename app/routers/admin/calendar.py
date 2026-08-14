from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.database import get_session
from app.deps import require_admin
from app.models.user import User
from app.models.special_day import SpecialDay, SpecialDayCategory
from app.services.holidays import get_bavarian_holidays

router = APIRouter(prefix="/admin/calendar", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "templates" if "admin" in str(Path(__file__)) else Path(__file__).parent.parent / "templates")


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
async def import_holidays(year: int = Form(...), session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(SpecialDayCategory).where(
        SpecialDayCategory.name == "Gesetzlicher Feiertag"))
    cat = result.first()
    if not cat:
        cat = SpecialDayCategory(name="Gesetzlicher Feiertag", weight=2.5, color="#dc2626")
        session.add(cat)
        await session.commit()
        await session.refresh(cat)

    for d, name in get_bavarian_holidays(year):
        existing = (await session.exec(
            select(SpecialDay).where(SpecialDay.date == d))).first()
        if not existing:
            session.add(SpecialDay(date=d, category_id=cat.id,
                                   label=name, is_auto_imported=True))
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

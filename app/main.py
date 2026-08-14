from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import init_db, get_session

_BASE = Path(__file__).parent
from app.routers.auth import router as auth_router
from app.routers.doctor import router as doctor_router
from app.routers.swap import router as swap_router
from app.routers.admin.calendar import router as calendar_router
from app.routers.admin.planning import router as planning_router
from app.routers.admin.stats import router as stats_router
from app.routers.admin.users import router as users_router
from app.deps import require_admin
from app.models.user import User


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _ensure_admin()
    await _seed_test_doctors()
    await _seed_holidays()
    yield


async def _ensure_admin():
    from app.database import AsyncSessionLocal
    from app.models.user import User, UserRole
    from app.services.auth import hash_password
    from sqlalchemy import select as sa_select
    async with AsyncSessionLocal() as session:
        result = await session.execute(sa_select(User).where(User.role == UserRole.admin))
        existing = result.scalars().first()
        if not existing:
            session.add(User(
                email="admin",
                hashed_password=hash_password("admin"),
                full_name="Administrator",
                role=UserRole.admin,
            ))
            await session.commit()
            print("Admin-Account angelegt: admin / admin")


async def _seed_test_doctors():
    from app.database import AsyncSessionLocal
    from app.models.user import User, UserRole, DoctorProfile
    from app.services.auth import hash_password
    from sqlalchemy import select as sa_select

    DOCTORS = [
        ("Anna Bauer", "anna.bauer@praxis.de", 1.0),
        ("Thomas Müller", "thomas.mueller@praxis.de", 1.0),
        ("Maria Schmidt", "maria.schmidt@praxis.de", 0.8),
        ("Klaus Weber", "klaus.weber@praxis.de", 1.0),
        ("Sabine Hoffmann", "sabine.hoffmann@praxis.de", 0.6),
        ("Michael Fischer", "michael.fischer@praxis.de", 1.0),
        ("Laura Wagner", "laura.wagner@praxis.de", 1.0),
        ("Stefan Becker", "stefan.becker@praxis.de", 0.8),
        ("Julia Schulz", "julia.schulz@praxis.de", 1.0),
        ("Andreas Koch", "andreas.koch@praxis.de", 1.0),
        ("Sandra Richter", "sandra.richter@praxis.de", 0.6),
        ("Markus Klein", "markus.klein@praxis.de", 1.0),
        ("Christine Wolf", "christine.wolf@praxis.de", 0.8),
        ("Daniel Schröder", "daniel.schroeder@praxis.de", 1.0),
        ("Petra Neumann", "petra.neumann@praxis.de", 1.0),
        ("Jörg Braun", "joerg.braun@praxis.de", 1.0),
        ("Monika Schwarz", "monika.schwarz@praxis.de", 0.8),
        ("Frank Zimmermann", "frank.zimmermann@praxis.de", 1.0),
        ("Ursula Krause", "ursula.krause@praxis.de", 0.6),
        ("Tobias Lange", "tobias.lange@praxis.de", 1.0),
    ]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa_select(User).where(User.role == UserRole.doctor))
        if result.scalars().first():
            return
        for full_name, email, factor in DOCTORS:
            user = User(
                email=email,
                hashed_password=hash_password("arzt123"),
                full_name=full_name,
                role=UserRole.doctor,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            session.add(DoctorProfile(user_id=user.id, part_time_factor=factor))
        await session.commit()
        print(f"20 Test-Arztaccounts angelegt (Passwort: arzt123)")


async def _seed_holidays():
    from datetime import date
    from app.database import AsyncSessionLocal
    from app.models.special_day import SpecialDay, SpecialDayCategory
    from app.services.holidays import get_bavarian_holidays
    from sqlalchemy import select as sa_select

    current_year = date.today().year
    years = [current_year, current_year + 1]

    async with AsyncSessionLocal() as session:
        result = await session.execute(sa_select(SpecialDayCategory).where(
            SpecialDayCategory.name == "Gesetzlicher Feiertag"))
        cat = result.scalars().first()
        if not cat:
            cat = SpecialDayCategory(name="Gesetzlicher Feiertag", weight=2.5, color="#dc2626")
            session.add(cat)
            await session.commit()
            await session.refresh(cat)

        imported = 0
        for year in years:
            for d, name in get_bavarian_holidays(year):
                exists = (await session.execute(
                    sa_select(SpecialDay).where(SpecialDay.date == d))).scalars().first()
                if not exists:
                    session.add(SpecialDay(
                        date=d, category_id=cat.id,
                        label=name, is_auto_imported=True))
                    imported += 1
        if imported:
            await session.commit()
            print(f"Bayerische Feiertage importiert: {imported} Tage ({', '.join(str(y) for y in years)})")


app = FastAPI(title="Notdienstplaner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_BASE / "static"), name="static")

_templates = Jinja2Templates(directory=_BASE / "templates")

app.include_router(auth_router)
app.include_router(doctor_router)
app.include_router(swap_router)
app.include_router(calendar_router)
app.include_router(planning_router)
app.include_router(stats_router)
app.include_router(users_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code in (302, 307) and exc.headers and "Location" in exc.headers:
        return RedirectResponse(url=exc.headers["Location"], status_code=exc.status_code)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/login", status_code=302)


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    admin: User = Depends(require_admin),
    session=Depends(get_session),
):
    from datetime import date as date_type
    from app.models.schedule import PlanningPeriod, PlanStatus, ShiftAssignment
    from app.models.special_day import SpecialDay, SpecialDayCategory
    from app.models.swap import SwapRequest, SwapStatus
    from app.models.user import UserRole, DoctorProfile
    from app.services.fairness import compute_fairness_score
    from sqlmodel import select

    today = date_type.today()

    # Doctors
    doctors = (await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True)
    )).all()
    doctor_count = len(doctors)

    # Latest published period
    all_periods = (await session.exec(
        select(PlanningPeriod).order_by(PlanningPeriod.start_date.desc())
    )).all()
    published = [p for p in all_periods if p.status == PlanStatus.published]
    draft_count = sum(1 for p in all_periods if p.status == PlanStatus.draft)
    latest = published[0] if published else None

    assignments_count = 0
    acknowledged_count = 0
    fairness_rows: list[dict] = []
    next_shift = None

    if latest:
        assignments = (await session.exec(
            select(ShiftAssignment).where(ShiftAssignment.planning_period_id == latest.id)
        )).all()
        assignments_count = len(assignments)
        acknowledged_count = sum(1 for a in assignments if a.acknowledged_at)

        sdays_raw = (await session.exec(
            select(SpecialDay, SpecialDayCategory).join(
                SpecialDayCategory, SpecialDay.category_id == SpecialDayCategory.id)
        )).all()

        class _SD:
            def __init__(self, d, w): self.date = d; self.weight = w

        scores = compute_fairness_score(
            [(a.user_id, a.date) for a in assignments],
            [_SD(sd.date, cat.weight) for sd, cat in sdays_raw],
        )
        profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}
        for doc in doctors:
            fairness_rows.append({
                "name": doc.full_name,
                "score": round(scores.get(doc.id, 0.0), 1),
                "factor": profiles[doc.id].part_time_factor if doc.id in profiles else 1.0,
            })
        fairness_rows.sort(key=lambda r: r["score"])

        future = [a.date for a in assignments if a.date >= today]
        next_shift = min(future) if future else None

    # Pending swaps (accepted by target, waiting for admin)
    pending_swaps = len((await session.exec(
        select(SwapRequest).where(SwapRequest.status == SwapStatus.accepted)
    )).all())
    open_requests = len((await session.exec(
        select(SwapRequest).where(SwapRequest.status == SwapStatus.pending)
    )).all())

    return _templates.TemplateResponse("admin/dashboard.html", {
        "request": request, "user": admin,
        "doctor_count": doctor_count,
        "draft_count": draft_count,
        "latest": latest,
        "assignments_count": assignments_count,
        "acknowledged_count": acknowledged_count,
        "pending_swaps": pending_swaps,
        "open_requests": open_requests,
        "fairness_rows": fairness_rows,
        "next_shift": next_shift,
        "today": today,
    })


@app.get("/admin/substitute", response_class=HTMLResponse)
async def admin_substitute_landing(
    request: Request,
    admin: User = Depends(require_admin),
    session=Depends(get_session),
):
    from app.models.schedule import PlanningPeriod, PlanStatus
    from sqlmodel import select
    periods = (await session.exec(
        select(PlanningPeriod).order_by(PlanningPeriod.start_date.desc())
    )).all()
    published = [p for p in periods if p.status == PlanStatus.published]
    return _templates.TemplateResponse("admin/substitute_landing.html", {
        "request": request, "user": admin, "periods": published,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)

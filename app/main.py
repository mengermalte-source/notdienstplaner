from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import init_db

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
async def admin_dashboard(request: Request, admin: User = Depends(require_admin)):
    return _templates.TemplateResponse("admin/dashboard.html",
        {"request": request, "user": admin})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, Form, Request
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
from app.models.vacation import VacationPeriod  # noqa: F401
from app.models.holiday_carryover import HolidayDutyCarryover  # noqa: F401
from app.models.recurring_block import RecurringBlock  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _ensure_admin()
    await _seed_test_doctors()
    await _ensure_periods()
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


async def _ensure_periods():
    from datetime import date
    from app.database import AsyncSessionLocal
    from app.models.schedule import PlanningPeriod
    from sqlalchemy import select as sa_select

    today = date.today()
    to_ensure = []
    for year in [today.year, today.year + 1]:
        to_ensure.append(dict(
            name=f"Notdienstplan Winter/Frühling {year}",
            start_date=date(year, 1, 10),
            end_date=date(year, 6, 30),
            year=year,
        ))
        to_ensure.append(dict(
            name=f"Notdienstplan Sommer/Herbst {year}/{year + 1}",
            start_date=date(year, 7, 1),
            end_date=date(year + 1, 1, 9),
            year=year,
        ))

    async with AsyncSessionLocal() as session:
        for p in to_ensure:
            exists = (await session.execute(
                sa_select(PlanningPeriod).where(PlanningPeriod.start_date == p["start_date"])
            )).scalars().first()
            if not exists:
                session.add(PlanningPeriod(**p))
        await session.commit()


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
    latest = published[0] if published else None

    # Doctor profiles (used for config checks and fairness)
    profiles_map = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

    # Config issue detection
    config_issues: list[str] = []
    missing_profile = [d for d in doctors if d.id not in profiles_map]
    if missing_profile:
        names = ", ".join(d.full_name for d in missing_profile[:2])
        suffix = f" (+{len(missing_profile) - 2} weitere)" if len(missing_profile) > 2 else ""
        config_issues.append(f"Kein Profil: {names}{suffix}")
    can_do_wednesday = sum(
        1 for d in doctors
        if d.id not in profiles_map or profiles_map[d.id].day_preference in ("alle", "mittwoch")
    )
    can_do_friday = sum(
        1 for d in doctors
        if d.id not in profiles_map or profiles_map[d.id].day_preference in ("alle", "freitag")
    )
    can_do_weekends = sum(
        1 for d in doctors
        if d.id not in profiles_map or profiles_map[d.id].day_preference == "alle"
    )
    if doctor_count > 0 and can_do_wednesday == 0:
        config_issues.append("Kein Arzt für Mittwochsdienste verfügbar")
    if doctor_count > 0 and can_do_friday == 0:
        config_issues.append("Kein Arzt für Freitagsdienste verfügbar")
    if doctor_count > 0 and can_do_weekends < 2:
        config_issues.append(
            f"Zu wenig Ärzte für Wochenenddienste ({can_do_weekends} verfügbar, mind. 2 nötig)"
        )

    assignments_count = 0
    acknowledged_count = 0
    fairness_rows: list[dict] = []
    next_shift = None
    next_shift_doctors: list[str] = []

    if latest:
        assignments = (await session.exec(
            select(ShiftAssignment).where(ShiftAssignment.planning_period_id == latest.id)
        )).all()
        assignments_count = len(assignments)
        acknowledged_count = sum(1 for a in assignments if a.acknowledged_at)

        scores = compute_fairness_score(
            [(a.user_id, a.date) for a in assignments],
            set(),
        )
        for doc in doctors:
            fairness_rows.append({
                "name": doc.full_name,
                "score": round(scores.get(doc.id, 0.0), 1),
            })
        fairness_rows.sort(key=lambda r: r["score"])

        future = [a.date for a in assignments if a.date >= today and not a.is_substitute]
        next_shift = min(future) if future else None

        users_map = {d.id: d for d in doctors}
        if next_shift:
            next_shift_doctors = [
                users_map[a.user_id].full_name
                for a in assignments
                if a.date == next_shift and not a.is_substitute and a.user_id in users_map
            ]

    open_requests = len((await session.exec(
        select(SwapRequest).where(SwapRequest.status == SwapStatus.pending)
    )).all())

    return _templates.TemplateResponse("admin/dashboard.html", {
        "request": request, "user": admin,
        "doctor_count": doctor_count,
        "config_issues": config_issues,
        "latest": latest,
        "assignments_count": assignments_count,
        "acknowledged_count": acknowledged_count,
        "open_requests": open_requests,
        "fairness_rows": fairness_rows,
        "next_shift": next_shift,
        "next_shift_doctors": next_shift_doctors,
        "today": today,
    })



@app.post("/admin/set-period")
async def set_period(
    period_id: int = Form(...),
    admin: User = Depends(require_admin),
):
    response = RedirectResponse(f"/admin/planning/{period_id}", status_code=302)
    response.set_cookie("admin_period_id", str(period_id), max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@app.get("/admin/nav/period-selector", response_class=HTMLResponse)
async def nav_period_selector(
    request: Request,
    admin: User = Depends(require_admin),
    session=Depends(get_session),
):
    from app.models.schedule import PlanningPeriod
    from sqlmodel import select
    from datetime import date as date_type

    periods = (await session.exec(
        select(PlanningPeriod).order_by(PlanningPeriod.start_date.desc())
    )).all()

    current_pid = request.cookies.get("admin_period_id", "")
    if not current_pid and periods:
        today = date_type.today()
        active = next((p for p in reversed(list(periods)) if p.start_date <= today <= p.end_date), None)
        if not active:
            active = next((p for p in reversed(list(periods)) if p.start_date > today), None)
        if not active:
            active = periods[0]
        current_pid = str(active.id)

    return _templates.TemplateResponse("partials/period_selector.html", {
        "request": request,
        "periods": periods,
        "current_pid": current_pid,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)

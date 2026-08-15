from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.database import get_session
from app.deps import require_admin
from app.models.user import User, UserRole, DoctorProfile, DayPreference
from app.services.auth import hash_password

router = APIRouter(prefix="/admin/users", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "templates" if "admin" in str(Path(__file__)) else Path(__file__).parent.parent / "templates")


@router.get("", response_class=HTMLResponse)
async def users_page(request: Request, session: AsyncSession = Depends(get_session),
                     admin: User = Depends(require_admin)):
    users = (await session.exec(select(User).order_by(User.full_name))).all()
    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}
    return templates.TemplateResponse("admin/users.html",
        {"request": request, "user": admin, "users": users, "profiles": profiles})


@router.post("/create")
async def create_user(full_name: str = Form(...), email: str = Form(...),
                       password: str = Form(...), role: str = Form("doctor"),
                       part_time_factor: float = Form(1.0),
                       session: AsyncSession = Depends(get_session)):
    user = User(full_name=full_name, email=email,
                hashed_password=hash_password(password), role=UserRole(role))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    if role == "doctor":
        session.add(DoctorProfile(user_id=user.id, part_time_factor=part_time_factor))
        await session.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/{user_id}/toggle-active")
async def toggle_active(user_id: int, session: AsyncSession = Depends(get_session)):
    user = await session.get(User, user_id)
    if user:
        user.is_active = not user.is_active
        session.add(user)
        await session.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/{user_id}/update-profile")
async def update_profile(
    user_id: int,
    part_time_factor: float = Form(1.0),
    credit_factor: float = Form(1.0),
    desired_shifts_raw: str = Form(""),
    day_preference_raw: str = Form("alle"),
    phone: str = Form(""),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    result = await session.exec(
        select(DoctorProfile).where(DoctorProfile.user_id == user_id)
    )
    profile = result.first()
    if profile is None:
        profile = DoctorProfile(user_id=user_id)
        session.add(profile)

    profile.credit_factor = max(0.0, min(1.0, credit_factor))
    # Keep legacy part_time_factor in sync with credit_factor (R-1 ruling)
    profile.part_time_factor = profile.credit_factor

    profile.desired_shifts = (
        int(desired_shifts_raw) if desired_shifts_raw.strip().isdigit() else None
    )

    try:
        profile.day_preference = DayPreference(day_preference_raw)
    except ValueError:
        profile.day_preference = DayPreference.alle

    profile.phone = phone
    profile.notes = notes

    session.add(profile)
    await session.commit()
    return RedirectResponse("/admin/users", status_code=302)

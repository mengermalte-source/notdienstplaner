from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.database import get_session
from app.deps import require_admin
from app.models.user import User, UserRole, DoctorProfile
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

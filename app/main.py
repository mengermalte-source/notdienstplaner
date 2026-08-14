from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import init_db
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
    yield


async def _ensure_admin():
    from app.database import AsyncSessionLocal
    from app.models.user import User, UserRole
    from app.services.auth import hash_password
    from sqlmodel import select
    async with AsyncSessionLocal() as session:
        existing = (await session.exec(select(User).where(User.role == UserRole.admin))).first()
        if not existing:
            session.add(User(
                email="admin",
                hashed_password=hash_password("admin"),
                full_name="Administrator",
                role=UserRole.admin,
            ))
            await session.commit()
            print("Admin-Account angelegt: admin / admin")


app = FastAPI(title="Notdienstplaner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

_templates = Jinja2Templates(directory="app/templates")

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

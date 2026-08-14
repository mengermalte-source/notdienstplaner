from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import init_db
from app.routers.auth import router as auth_router
from app.deps import get_current_user
from app.models.user import User


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Notdienstplaner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
app.include_router(auth_router)


@app.get("/me", response_class=HTMLResponse)
async def me(current_user: User = Depends(get_current_user)):
    return HTMLResponse(f"<h1>Willkommen, {current_user.full_name}</h1>")

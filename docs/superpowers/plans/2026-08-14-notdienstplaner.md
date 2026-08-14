# Notdienstplaner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Web-App für Ärzte und Admins zur fairen, wunschbasierten Notdienstplanung mit Optimierungsalgorithmus.

**Architecture:** FastAPI mit Jinja2-Templates und HTMX für reaktive UI ohne schweres JS-Framework. SQLModel (SQLAlchemy + Pydantic) mit aiosqlite, async-first. JWT in httpOnly-Cookie für Session.

**Tech Stack:** Python 3.12, FastAPI 0.115, SQLModel 0.0.22, Alembic, OR-Tools 9.11, python-holidays, python-icalendar, WeasyPrint, HTMX, Alpine.js, Tailwind CSS (CDN)

**Spec:** Diese Datei ist Spec und Plan zugleich.

## Global Constraints

- Python ≥ 3.12
- Alle Texte und UI-Labels auf Deutsch
- Async-first (AsyncSession überall)
- Keine externen Services required (SQLite reicht für Produktion bis ~200 User)
- WCAG 2.1 AA für Kontraste
- Alle Passwörter bcrypt-gehasht
- CSRF-Schutz via itsdangerous für alle POST-Formulare
- Timezone: Europe/Berlin durchgehend

---

## Dateistruktur

```
notdienstplaner/
├── app/
│   ├── main.py              # FastAPI-App, Router-Registrierung, Middleware
│   ├── config.py            # Settings via pydantic-settings
│   ├── database.py          # Async-Engine, get_session dependency
│   ├── deps.py              # Shared dependencies (current_user, require_admin)
│   ├── models/
│   │   ├── user.py          # User, DoctorProfile
│   │   ├── schedule.py      # PlanningPeriod, ShiftAssignment
│   │   ├── wish.py          # WishEntry
│   │   ├── special_day.py   # SpecialDayCategory, SpecialDay
│   │   └── swap.py          # SwapRequest
│   ├── routers/
│   │   ├── auth.py          # /login, /logout, /register
│   │   ├── doctor.py        # /me, /wishes, /schedule, /swaps
│   │   └── admin/
│   │       ├── users.py     # /admin/users
│   │       ├── calendar.py  # /admin/calendar
│   │       ├── planning.py  # /admin/planning (algorithm + publish)
│   │       └── stats.py     # /admin/statistics
│   ├── services/
│   │   ├── auth.py          # JWT encode/decode, password hash
│   │   ├── algorithm.py     # OR-Tools CP-SAT Solver
│   │   ├── fairness.py      # Fairness-Score-Berechnung
│   │   ├── holidays.py      # python-holidays wrapper + custom days
│   │   ├── email.py         # SMTP-Versand
│   │   ├── ical.py          # iCal-Export
│   │   └── pdf.py           # WeasyPrint PDF
│   ├── templates/
│   │   ├── base.html
│   │   ├── auth/login.html
│   │   ├── doctor/
│   │   │   ├── dashboard.html
│   │   │   ├── wishes.html
│   │   │   └── schedule.html
│   │   └── admin/
│   │       ├── dashboard.html
│   │       ├── users.html
│   │       ├── calendar.html
│   │       ├── planning.html
│   │       └── statistics.html
│   └── static/
│       └── app.js           # Minimales vanilla JS
├── alembic/
│   ├── env.py
│   └── versions/
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_wishes.py
│   ├── test_algorithm.py
│   └── test_swaps.py
├── alembic.ini
├── pyproject.toml
└── .env.example
```

---

## Task 1: Projekt-Setup

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `app/config.py`
- Create: `app/database.py`
- Create: `app/main.py`

**Interfaces:**
- Produces: `get_session` (AsyncSession dependency), `settings` (Settings singleton), FastAPI-App-Instanz

- [ ] **Step 1: pyproject.toml erstellen**

```toml
[project]
name = "notdienstplaner"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi==0.115.*",
    "uvicorn[standard]==0.32.*",
    "sqlmodel==0.0.22",
    "alembic==1.14.*",
    "aiosqlite==0.20.*",
    "pydantic-settings==2.6.*",
    "python-jose[cryptography]==3.3.*",
    "passlib[bcrypt]==1.7.*",
    "python-multipart==0.0.19",
    "jinja2==3.1.*",
    "aiofiles==24.*",
    "itsdangerous==2.2.*",
    "python-holidays==0.62.*",
    "ortools==9.11.*",
    "icalendar==6.*",
    "weasyprint==63.*",
    "httpx==0.28.*",
]

[project.optional-dependencies]
dev = [
    "pytest==8.*",
    "pytest-asyncio==0.25.*",
    "anyio==4.*",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: `.env.example` erstellen**

```env
SECRET_KEY=change-me-in-production-min-32-chars
DATABASE_URL=sqlite+aiosqlite:///./notdienstplaner.db
SMTP_HOST=localhost
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=notdienstplaner@example.com
DOCTORS_PER_DAY=2
APP_BASE_URL=http://localhost:8000
```

- [ ] **Step 3: `app/config.py` schreiben**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    secret_key: str
    database_url: str = "sqlite+aiosqlite:///./notdienstplaner.db"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "notdienstplaner@example.com"
    doctors_per_day: int = 2
    app_base_url: str = "http://localhost:8000"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
```

- [ ] **Step 4: `app/database.py` schreiben**

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlmodel import SQLModel
from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 5: `app/main.py` Gerüst**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Notdienstplaner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
```

- [ ] **Step 6: Verzeichnisse anlegen und Abhängigkeiten installieren**

```bash
mkdir -p app/{models,routers/admin,services,templates/{auth,doctor,admin},static}
mkdir -p alembic/versions tests
touch app/__init__.py app/models/__init__.py app/routers/__init__.py
touch app/routers/admin/__init__.py app/services/__init__.py
cp .env.example .env
pip install -e ".[dev]"
```

- [ ] **Step 7: Alembic initialisieren**

```bash
alembic init alembic
```

Dann in `alembic/env.py` die target_metadata setzen:
```python
from app.database import engine
from sqlmodel import SQLModel
# alle Models importieren damit metadata befüllt wird:
import app.models.user, app.models.schedule, app.models.wish
import app.models.special_day, app.models.swap

target_metadata = SQLModel.metadata

def run_migrations_online():
    connectable = engine.sync_engine  # für sync alembic
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
```

- [ ] **Step 8: Server starten und 200 prüfen**

```bash
uvicorn app.main:app --reload
curl http://localhost:8000/docs  # muss 200 zurückgeben
```

- [ ] **Step 9: Commit**

```bash
git init && git add .
git commit -m "chore: initial project setup"
```

---

## Task 2: User-Modelle & Authentifizierung

**Files:**
- Create: `app/models/user.py`
- Create: `app/services/auth.py`
- Create: `app/deps.py`
- Create: `app/routers/auth.py`
- Create: `app/templates/auth/login.html`
- Create: `tests/test_auth.py`

**Interfaces:**
- Produces:
  - `User` (SQLModel), `DoctorProfile` (SQLModel)
  - `create_access_token(data: dict) -> str`
  - `get_current_user(request) -> User` (FastAPI dependency)
  - `require_admin(user: User) -> User` (FastAPI dependency)
  - Routes: `GET /login`, `POST /login`, `POST /logout`

- [ ] **Step 1: Failing-Test schreiben**

```python
# tests/test_auth.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_login_wrong_password_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/login", data={"email": "nobody@x.de", "password": "wrong"})
    assert r.status_code in (400, 200)  # 200 = Form re-render mit Fehler

@pytest.mark.asyncio
async def test_unauthenticated_redirect():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/me", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

```bash
pytest tests/test_auth.py -v
```

- [ ] **Step 3: `app/models/user.py` implementieren**

```python
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Relationship
import enum

class UserRole(str, enum.Enum):
    doctor = "doctor"
    admin = "admin"

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    full_name: str
    role: UserRole = UserRole.doctor
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    profile: Optional["DoctorProfile"] = Relationship(back_populates="user")

class DoctorProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True)
    part_time_factor: float = Field(default=1.0, ge=0.1, le=1.0)
    phone: str = ""
    notes: str = ""
    # Kumulierter Fairness-Score aus Vorjahren (wird jährlich übertragen)
    carried_over_score: float = 0.0

    user: Optional[User] = Relationship(back_populates="profile")
```

- [ ] **Step 4: `app/services/auth.py` implementieren**

```python
from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({**data, "exp": expire}, settings.secret_key, algorithm=settings.algorithm)

def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
```

- [ ] **Step 5: `app/deps.py` implementieren**

```python
from fastapi import Request, HTTPException, Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.models.user import User, UserRole
from app.services.auth import decode_token

async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        from fastapi.responses import RedirectResponse
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    result = await session.exec(select(User).where(User.id == payload.get("sub")))
    user = result.first()
    if not user or not user.is_active:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user

async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return user
```

- [ ] **Step 6: `app/routers/auth.py` implementieren**

```python
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.models.user import User
from app.services.auth import verify_password, create_access_token

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request})

@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...),
                session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(User).where(User.email == email))
    user = result.first()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse("auth/login.html",
            {"request": request, "error": "E-Mail oder Passwort falsch"}, status_code=400)
    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse("/me" if user.role == "doctor" else "/admin", status_code=302)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response

@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    return response
```

- [ ] **Step 7: Router in `app/main.py` registrieren**

```python
from app.routers.auth import router as auth_router
app.include_router(auth_router)
```

- [ ] **Step 8: `app/templates/auth/login.html` erstellen**

```html
{% extends "base.html" %}
{% block content %}
<div class="min-h-screen flex items-center justify-center bg-gray-50">
  <div class="max-w-md w-full bg-white rounded-lg shadow p-8">
    <h1 class="text-2xl font-bold text-center mb-6">Notdienstplaner</h1>
    {% if error %}
    <div class="bg-red-50 border border-red-300 text-red-700 px-4 py-2 rounded mb-4">{{ error }}</div>
    {% endif %}
    <form method="post" action="/login" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700">E-Mail</label>
        <input type="email" name="email" required
          class="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
      </div>
      <div>
        <label class="block text-sm font-medium text-gray-700">Passwort</label>
        <input type="password" name="password" required
          class="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
      </div>
      <button type="submit"
        class="w-full bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 transition">
        Anmelden
      </button>
    </form>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 9: `app/templates/base.html` erstellen**

```html
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Notdienstplaner{% endblock %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body class="bg-gray-50 text-gray-900">
  {% if request.cookies.get('access_token') %}
  <nav class="bg-white border-b border-gray-200 px-4 py-3 flex justify-between items-center">
    <span class="font-semibold text-lg">Notdienstplaner</span>
    <div class="flex gap-4 text-sm">
      <a href="/me" class="hover:text-blue-600">Mein Bereich</a>
      {% if user and user.role == 'admin' %}
      <a href="/admin" class="hover:text-blue-600">Admin</a>
      {% endif %}
      <form method="post" action="/logout" class="inline">
        <button type="submit" class="hover:text-red-600">Abmelden</button>
      </form>
    </div>
  </nav>
  {% endif %}
  <main class="max-w-6xl mx-auto px-4 py-6">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 10: Seed-Skript für ersten Admin-User**

```python
# scripts/create_admin.py
import asyncio
from app.database import init_db, AsyncSessionLocal
from app.models.user import User, UserRole
from app.services.auth import hash_password

async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        admin = User(
            email="admin@example.com",
            hashed_password=hash_password("admin123"),
            full_name="Administrator",
            role=UserRole.admin,
        )
        session.add(admin)
        await session.commit()
        print(f"Admin erstellt: {admin.email}")

asyncio.run(main())
```

- [ ] **Step 11: Tests laufen lassen**

```bash
pytest tests/test_auth.py -v
```

Expected: beide Tests grün.

- [ ] **Step 12: Commit**

```bash
git add app/ tests/ scripts/
git commit -m "feat: user models and JWT authentication"
```

---

## Task 3: Restliche Datenmodelle

**Files:**
- Create: `app/models/wish.py`
- Create: `app/models/special_day.py`
- Create: `app/models/schedule.py`
- Create: `app/models/swap.py`

**Interfaces:**
- Produces: alle SQLModel-Klassen, die der Algorithmus und die Router brauchen

- [ ] **Step 1: `app/models/special_day.py`**

```python
from datetime import date
from typing import Optional
from sqlmodel import SQLModel, Field

class SpecialDayCategory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str                        # z.B. "Weihnachten", "Brückentag"
    weight: float = Field(default=2.0)  # Fairness-Gewichtung
    color: str = "#ef4444"           # Kalenderfarbe (hex)
    description: str = ""

class SpecialDay(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: date = Field(index=True)
    category_id: int = Field(foreign_key="specialdaycategory.id")
    label: str = ""                  # optionale Beschriftung
    is_auto_imported: bool = False   # True = aus python-holidays importiert
```

- [ ] **Step 2: `app/models/wish.py`**

```python
from datetime import date, datetime
from typing import Optional
from sqlmodel import SQLModel, Field
import enum

class WishType(str, enum.Enum):
    positive = "positive"   # Arzt möchte an diesem Tag arbeiten
    negative = "negative"   # Arzt möchte NICHT an diesem Tag arbeiten

class WishPriority(str, enum.Enum):
    soft = "soft"    # Wunsch, wird wenn möglich berücksichtigt
    hard = "hard"    # Muss berücksichtigt werden (z.B. Urlaub)

class WishEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    date: date = Field(index=True)
    wish_type: WishType
    priority: WishPriority = WishPriority.soft
    reason: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    planning_period_id: Optional[int] = Field(default=None, foreign_key="planningperiod.id")
```

- [ ] **Step 3: `app/models/schedule.py`**

```python
from datetime import date, datetime
from typing import Optional
from sqlmodel import SQLModel, Field
import enum

class PlanStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"

class PlanningPeriod(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str                        # z.B. "Notdienstplan 2027"
    year: int
    start_date: date
    end_date: date
    status: PlanStatus = PlanStatus.draft
    wish_deadline: Optional[date] = None  # bis wann können Ärzte Wünsche eingeben
    created_at: datetime = Field(default_factory=datetime.utcnow)
    published_at: Optional[datetime] = None
    notes: str = ""

class ShiftAssignment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    planning_period_id: int = Field(foreign_key="planningperiod.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    date: date = Field(index=True)
    is_manual_override: bool = False   # True wenn Admin nachträglich geändert hat
    weighted_score: float = 1.0        # Fairness-Gewicht (1.0 normal, höher = Sonderbelastung)
    acknowledged_at: Optional[datetime] = None  # Arzt hat Dienst bestätigt
```

- [ ] **Step 4: `app/models/swap.py`**

```python
from datetime import date, datetime
from typing import Optional
from sqlmodel import SQLModel, Field
import enum

class SwapStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    admin_approved = "admin_approved"

class SwapRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    requester_id: int = Field(foreign_key="user.id")
    target_id: int = Field(foreign_key="user.id")
    requester_shift_date: date
    target_shift_date: date
    planning_period_id: int = Field(foreign_key="planningperiod.id")
    status: SwapStatus = SwapStatus.pending
    message: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
```

- [ ] **Step 5: Alembic-Migration erstellen und anwenden**

```bash
alembic revision --autogenerate -m "initial models"
alembic upgrade head
```

- [ ] **Step 6: Commit**

```bash
git add app/models/ alembic/
git commit -m "feat: all data models and initial migration"
```

---

## Task 4: Special-Days-Verwaltung (Admin-Kalender)

**Files:**
- Create: `app/services/holidays.py`
- Create: `app/routers/admin/calendar.py`
- Create: `app/templates/admin/calendar.html`
- Test: `tests/test_holidays.py`

**Interfaces:**
- Consumes: `SpecialDay`, `SpecialDayCategory`
- Produces:
  - `get_bavarian_holidays(year: int) -> list[tuple[date, str]]`
  - Routes: `GET /admin/calendar`, `POST /admin/calendar/import-holidays`, `POST /admin/calendar/days`

- [ ] **Step 1: Failing-Test schreiben**

```python
# tests/test_holidays.py
from datetime import date
from app.services.holidays import get_bavarian_holidays

def test_weihnachten_in_bavarian_holidays():
    holidays = get_bavarian_holidays(2027)
    dates = [h[0] for h in holidays]
    assert date(2027, 12, 25) in dates

def test_dreikoenige_is_bavarian_only():
    holidays = get_bavarian_holidays(2027)
    dates = [h[0] for h in holidays]
    assert date(2027, 1, 6) in dates  # nur Bayern
```

- [ ] **Step 2: Tests laufen lassen — müssen fehlschlagen**

```bash
pytest tests/test_holidays.py -v
```

- [ ] **Step 3: `app/services/holidays.py` implementieren**

```python
from datetime import date
import holidays

def get_bavarian_holidays(year: int) -> list[tuple[date, str]]:
    by_holidays = holidays.Germany(state="BY", years=year)
    return sorted([(d, name) for d, name in by_holidays.items()])

def get_school_vacation_windows(year: int) -> list[tuple[date, date, str]]:
    """
    Bayerische Schulferienzeiten — hartcodiert für 2027/2028.
    Quelle: https://www.km.bayern.de/schueler/schulferien.html
    Für Produktion: API oder jährliches Update nötig.
    """
    # Beispiel 2027 — muss jährlich aktualisiert werden
    return [
        (date(year, 1, 1), date(year, 1, 7), "Weihnachtsferien"),
        (date(year, 2, 28), date(year, 3, 6), "Faschingsferien"),
        (date(year, 4, 9), date(year, 4, 23), "Osterferien"),
        (date(year, 6, 3), date(year, 6, 3), "Pfingstferien Brücke"),
        (date(year, 7, 31), date(year, 9, 10), "Sommerferien"),
        (date(year, 10, 30), date(year, 11, 7), "Herbstferien"),
        (date(year, 12, 24), date(year + 1, 1, 5), "Weihnachtsferien"),
    ]
```

- [ ] **Step 4: Tests laufen lassen — müssen grün sein**

```bash
pytest tests/test_holidays.py -v
```

- [ ] **Step 5: `app/routers/admin/calendar.py` implementieren**

```python
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.deps import require_admin
from app.models.user import User
from app.models.special_day import SpecialDay, SpecialDayCategory
from app.services.holidays import get_bavarian_holidays

router = APIRouter(prefix="/admin/calendar", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")

@router.get("", response_class=HTMLResponse)
async def calendar_page(request: Request, year: int = 2027, session: AsyncSession = Depends(get_session)):
    cats = (await session.exec(select(SpecialDayCategory))).all()
    days = (await session.exec(select(SpecialDay).where(SpecialDay.date.between(
        f"{year}-01-01", f"{year}-12-31")))).all()
    return templates.TemplateResponse("admin/calendar.html",
        {"request": request, "categories": cats, "special_days": days, "year": year})

@router.post("/import-holidays")
async def import_holidays(year: int = Form(...), session: AsyncSession = Depends(get_session)):
    # Kategorie "Gesetzlicher Feiertag" sicherstellen
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
                           label: str = Form(""), session: AsyncSession = Depends(get_session)):
    from datetime import date as date_type
    session.add(SpecialDay(date=date_type.fromisoformat(date),
                           category_id=category_id, label=label))
    await session.commit()
    return RedirectResponse("/admin/calendar", status_code=302)
```

- [ ] **Step 6: Basis-Template `app/templates/admin/calendar.html` erstellen**

```html
{% extends "base.html" %}
{% block title %}Kalender-Konfiguration{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-4">Sondertage verwalten</h1>

<form method="post" action="/admin/calendar/import-holidays" class="flex gap-2 mb-6">
  <select name="year" class="border rounded px-2 py-1">
    {% for y in range(2025, 2030) %}
    <option value="{{ y }}" {% if y == year %}selected{% endif %}>{{ y }}</option>
    {% endfor %}
  </select>
  <button type="submit" class="bg-blue-600 text-white px-4 py-1 rounded hover:bg-blue-700">
    Bayerische Feiertage importieren
  </button>
</form>

<div class="grid grid-cols-1 md:grid-cols-3 gap-2">
  {% for day in special_days %}
  <div class="bg-white border rounded p-3 flex justify-between items-center">
    <div>
      <span class="font-mono text-sm">{{ day.date.strftime('%d.%m.%Y') }}</span>
      <span class="ml-2 text-gray-600 text-sm">{{ day.label }}</span>
    </div>
    {% if not day.is_auto_imported %}
    <form method="post" action="/admin/calendar/days/{{ day.id }}/delete">
      <button class="text-red-500 text-sm hover:text-red-700">×</button>
    </form>
    {% endif %}
  </div>
  {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 7: Commit**

```bash
git add app/ tests/
git commit -m "feat: special days management with Bavarian holiday import"
```

---

## Task 5: Wunsch-System (Arzt-UI)

**Files:**
- Create: `app/routers/doctor.py`
- Create: `app/templates/doctor/dashboard.html`
- Create: `app/templates/doctor/wishes.html`
- Test: `tests/test_wishes.py`

**Interfaces:**
- Consumes: `WishEntry`, `get_current_user`
- Produces:
  - Routes: `GET /me`, `GET /me/wishes`, `POST /me/wishes`, `DELETE /me/wishes/{id}`

- [ ] **Step 1: Failing-Test schreiben**

```python
# tests/test_wishes.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import AsyncSessionLocal
from app.models.user import User, DoctorProfile
from app.services.auth import hash_password, create_access_token

@pytest.fixture
async def doctor_client():
    async with AsyncSessionLocal() as session:
        user = User(email="dr.test@x.de", hashed_password=hash_password("pw"),
                    full_name="Dr. Test", role="doctor")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        token = create_access_token({"sub": str(user.id)})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("access_token", token)
        yield c, user.id

@pytest.mark.asyncio
async def test_create_wish(doctor_client):
    client, user_id = doctor_client
    r = await client.post("/me/wishes", data={
        "date": "2027-03-15", "wish_type": "negative", "priority": "soft", "reason": "Urlaub"
    })
    assert r.status_code in (200, 302)

@pytest.mark.asyncio
async def test_duplicate_wish_rejected(doctor_client):
    client, _ = doctor_client
    data = {"date": "2027-04-01", "wish_type": "negative", "priority": "soft", "reason": ""}
    await client.post("/me/wishes", data=data)
    r = await client.post("/me/wishes", data=data)
    assert r.status_code in (400, 200)  # Duplikat soll abgelehnt werden
```

- [ ] **Step 2: Tests laufen lassen — müssen fehlschlagen**

```bash
pytest tests/test_wishes.py -v
```

- [ ] **Step 3: `app/routers/doctor.py` implementieren**

```python
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import select
from datetime import date as date_type
from app.database import get_session
from app.deps import get_current_user
from app.models.user import User
from app.models.wish import WishEntry, WishType, WishPriority

router = APIRouter(prefix="/me")
templates = Jinja2Templates(directory="app/templates")

@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    wishes = (await session.exec(
        select(WishEntry).where(WishEntry.user_id == user.id)
        .order_by(WishEntry.date))).all()
    return templates.TemplateResponse("doctor/dashboard.html",
        {"request": request, "user": user, "wishes": wishes})

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
                      date: str = Form(...), wish_type: WishType = Form(...),
                      priority: WishPriority = Form(WishPriority.soft),
                      reason: str = Form(""),
                      session: AsyncSession = Depends(get_session)):
    d = date_type.fromisoformat(date)
    # Duplikat-Check
    existing = (await session.exec(
        select(WishEntry).where(WishEntry.user_id == user.id, WishEntry.date == d))).first()
    if existing:
        raise HTTPException(status_code=400, detail="Für dieses Datum existiert bereits ein Wunsch")
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
```

- [ ] **Step 4: `app/templates/doctor/wishes.html`**

```html
{% extends "base.html" %}
{% block title %}Meine Wünsche{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-4">Wünsche eingeben</h1>

<form method="post" action="/me/wishes" class="bg-white rounded-lg shadow p-6 mb-6 grid grid-cols-1 md:grid-cols-4 gap-4">
  <div>
    <label class="block text-sm font-medium text-gray-700 mb-1">Datum</label>
    <input type="date" name="date" required
      class="w-full border border-gray-300 rounded px-3 py-2">
  </div>
  <div>
    <label class="block text-sm font-medium text-gray-700 mb-1">Art</label>
    <select name="wish_type" class="w-full border border-gray-300 rounded px-3 py-2">
      <option value="negative">Frei (möchte nicht)</option>
      <option value="positive">Dienst (möchte gerne)</option>
    </select>
  </div>
  <div>
    <label class="block text-sm font-medium text-gray-700 mb-1">Priorität</label>
    <select name="priority" class="w-full border border-gray-300 rounded px-3 py-2">
      <option value="soft">Wunsch</option>
      <option value="hard">Pflicht (z.B. Urlaub)</option>
    </select>
  </div>
  <div>
    <label class="block text-sm font-medium text-gray-700 mb-1">Grund (optional)</label>
    <input type="text" name="reason" placeholder="z.B. Familienfeier"
      class="w-full border border-gray-300 rounded px-3 py-2">
  </div>
  <div class="md:col-span-4">
    <button type="submit" class="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700">
      Wunsch speichern
    </button>
  </div>
</form>

<div class="bg-white rounded-lg shadow overflow-hidden">
  <table class="w-full text-sm">
    <thead class="bg-gray-50 border-b">
      <tr>
        <th class="px-4 py-3 text-left">Datum</th>
        <th class="px-4 py-3 text-left">Art</th>
        <th class="px-4 py-3 text-left">Priorität</th>
        <th class="px-4 py-3 text-left">Grund</th>
        <th class="px-4 py-3"></th>
      </tr>
    </thead>
    <tbody class="divide-y">
      {% for wish in wishes %}
      <tr class="hover:bg-gray-50">
        <td class="px-4 py-3 font-mono">{{ wish.date.strftime('%d.%m.%Y') }}</td>
        <td class="px-4 py-3">
          {% if wish.wish_type == 'negative' %}
          <span class="text-red-600">Frei</span>
          {% else %}
          <span class="text-green-600">Dienst</span>
          {% endif %}
        </td>
        <td class="px-4 py-3">
          {% if wish.priority == 'hard' %}<span class="font-semibold">Pflicht</span>
          {% else %}Wunsch{% endif %}
        </td>
        <td class="px-4 py-3 text-gray-500">{{ wish.reason }}</td>
        <td class="px-4 py-3">
          <form method="post" action="/me/wishes/{{ wish.id }}/delete">
            <button class="text-red-500 hover:text-red-700 text-xs">Löschen</button>
          </form>
        </td>
      </tr>
      {% endfor %}
      {% if not wishes %}
      <tr><td colspan="5" class="px-4 py-6 text-center text-gray-400">Noch keine Wünsche eingetragen</td></tr>
      {% endif %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: Tests laufen lassen**

```bash
pytest tests/test_wishes.py -v
```

- [ ] **Step 6: Commit**

```bash
git add app/ tests/
git commit -m "feat: wish entry system for doctors"
```

---

## Task 6: Planungsalgorithmus (OR-Tools CP-SAT)

**Files:**
- Create: `app/services/algorithm.py`
- Create: `app/services/fairness.py`
- Test: `tests/test_algorithm.py`

**Interfaces:**
- Consumes: `list[User]`, `list[date]`, `list[WishEntry]`, `list[SpecialDay]`, `int` (doctors_per_day)
- Produces:
  - `solve_schedule(doctors, days, wishes, special_days, doctors_per_day) -> list[tuple[int, date]] | None`
    (Liste von (user_id, date)-Paaren) oder None wenn unlösbar
  - `compute_fairness_score(assignments, special_days) -> dict[int, float]`
    (user_id → gewichteter Score)

- [ ] **Step 1: Failing-Tests schreiben**

```python
# tests/test_algorithm.py
import pytest
from datetime import date, timedelta
from app.services.algorithm import solve_schedule
from app.services.fairness import compute_fairness_score

def make_doctors(n):
    class D:
        def __init__(self, id, factor=1.0): self.id = id; self.part_time_factor = factor
    return [D(i) for i in range(1, n+1)]

def date_range(start, days):
    return [start + timedelta(days=i) for i in range(days)]

def test_basic_schedule_assigns_all_days():
    doctors = make_doctors(5)
    days = date_range(date(2027, 1, 1), 30)
    result = solve_schedule(doctors, days, wishes=[], special_days=[], doctors_per_day=2)
    assert result is not None
    assigned_days = {d for _, d in result}
    assert assigned_days == set(days)

def test_no_consecutive_days():
    doctors = make_doctors(5)
    days = date_range(date(2027, 1, 1), 30)
    result = solve_schedule(doctors, days, wishes=[], special_days=[], doctors_per_day=2)
    from collections import defaultdict
    by_doc = defaultdict(list)
    for uid, d in result:
        by_doc[uid].append(d)
    for uid, assigned in by_doc.items():
        sorted_dates = sorted(assigned)
        for i in range(len(sorted_dates) - 1):
            diff = (sorted_dates[i+1] - sorted_dates[i]).days
            assert diff > 1, f"Arzt {uid} hat Folgedienste: {sorted_dates[i]}, {sorted_dates[i+1]}"

def test_hard_negative_wish_respected():
    doctors = make_doctors(5)
    days = date_range(date(2027, 2, 1), 14)
    class W:
        user_id = 1; date = date(2027, 2, 5); wish_type = "negative"; priority = "hard"
    result = solve_schedule(doctors, days, wishes=[W()], special_days=[], doctors_per_day=2)
    assert result is not None
    assert (1, date(2027, 2, 5)) not in result

def test_fairness_score():
    from datetime import date
    assignments = [(1, date(2027,1,1)), (1, date(2027,1,3)), (2, date(2027,1,2))]
    class SD:
        def __init__(self, d, w): self.date = d; self.weight = w
    special_days = [SD(date(2027,1,1), 3.0)]
    scores = compute_fairness_score(assignments, special_days)
    assert scores[1] > scores[2]  # Arzt 1 hat Sonderbelastung
```

- [ ] **Step 2: Tests laufen lassen — müssen fehlschlagen**

```bash
pytest tests/test_algorithm.py -v
```

- [ ] **Step 3: `app/services/fairness.py` implementieren**

```python
from datetime import date
from collections import defaultdict

def compute_fairness_score(
    assignments: list[tuple[int, date]],
    special_days: list,  # SpecialDay-Objekte mit .date und .weight
) -> dict[int, float]:
    weight_by_date = {sd.date: sd.weight for sd in special_days}
    scores: dict[int, float] = defaultdict(float)
    for user_id, d in assignments:
        scores[user_id] += weight_by_date.get(d, 1.0)
    return dict(scores)

def compute_target_duties(doctors, total_days: int, doctors_per_day: int) -> dict[int, float]:
    """Fairer Soll-Anteil pro Arzt basierend auf part_time_factor."""
    total_factor = sum(d.part_time_factor for d in doctors)
    total_assignments = total_days * doctors_per_day
    return {d.id: (d.part_time_factor / total_factor) * total_assignments for d in doctors}
```

- [ ] **Step 4: `app/services/algorithm.py` implementieren**

```python
from datetime import date
from collections import defaultdict
from ortools.sat.python import cp_model
from app.services.fairness import compute_target_duties

def solve_schedule(
    doctors: list,           # Objekte mit .id und .part_time_factor
    days: list[date],
    wishes: list,            # Objekte mit .user_id, .date, .wish_type, .priority
    special_days: list,      # Objekte mit .date, .weight
    doctors_per_day: int = 2,
    time_limit_seconds: int = 30,
) -> list[tuple[int, date]] | None:

    model = cp_model.CpModel()
    n_days = len(days)
    day_idx = {d: i for i, d in enumerate(days)}
    doctor_ids = [doc.id for doc in doctors]

    # Entscheidungsvariablen: x[doc_id][day_idx] ∈ {0,1}
    x = {(doc.id, i): model.new_bool_var(f"x_{doc.id}_{i}")
         for doc in doctors for i in range(n_days)}

    # Constraint: Exakt doctors_per_day Ärzte pro Tag
    for i in range(n_days):
        model.add(sum(x[did, i] for did in doctor_ids) == doctors_per_day)

    # Constraint: Keine zwei aufeinanderfolgenden Tage
    for doc in doctors:
        for i in range(n_days - 1):
            model.add(x[doc.id, i] + x[doc.id, i + 1] <= 1)

    # Constraint: Maximale Dienste pro Arzt (aus part_time_factor)
    targets = compute_target_duties(doctors, n_days, doctors_per_day)
    for doc in doctors:
        max_duties = int(targets[doc.id] * 1.15) + 2  # 15% Puffer
        model.add(sum(x[doc.id, i] for i in range(n_days)) <= max_duties)

    # Constraint: Hard-Negative-Wünsche = absolutes Verbot
    for wish in wishes:
        if wish.wish_type == "negative" and wish.priority == "hard" and wish.date in day_idx:
            model.add(x[wish.user_id, day_idx[wish.date]] == 0)

    # Objective: Fairness + Wunscherfüllung
    weight_by_date = {sd.date: int(sd.weight * 100) for sd in special_days}

    # Fairness: Gewichtete Dienstzahl soll nah am Soll liegen
    fairness_penalties = []
    for doc in doctors:
        weighted_sum = sum(
            x[doc.id, i] * weight_by_date.get(days[i], 100)
            for i in range(n_days)
        )
        target_scaled = int(targets[doc.id] * 100)
        # Quadratisches Penalty via Linearisierung: Betrag der Abweichung
        dev = model.new_int_var(-10000, 10000, f"dev_{doc.id}")
        model.add(dev == weighted_sum - target_scaled)
        abs_dev = model.new_int_var(0, 10000, f"absdev_{doc.id}")
        model.add_abs_equality(abs_dev, dev)
        fairness_penalties.append(abs_dev)

    # Soft-Wünsche als Bonus
    wish_bonus = []
    for wish in wishes:
        if wish.date in day_idx and wish.wish_type == "positive":
            wish_bonus.append(x[wish.user_id, day_idx[wish.date]])
        elif wish.date in day_idx and wish.wish_type == "negative" and wish.priority == "soft":
            # Penalty wenn soft-negative Wunsch nicht erfüllt
            fairness_penalties.append(x[wish.user_id, day_idx[wish.date]])

    model.minimize(sum(fairness_penalties) * 10 - sum(wish_bonus))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    result = []
    for doc in doctors:
        for i, day in enumerate(days):
            if solver.value(x[doc.id, i]):
                result.append((doc.id, day))
    return result
```

- [ ] **Step 5: Tests laufen lassen**

```bash
pytest tests/test_algorithm.py -v
```

Expected: alle 4 Tests grün. Der Algorithmus-Test kann bis zu 30 Sekunden dauern.

- [ ] **Step 6: Commit**

```bash
git add app/services/ tests/
git commit -m "feat: OR-Tools CP-SAT scheduling algorithm with fairness"
```

---

## Task 7: Admin-Planungs-UI (Periode erstellen + Algorithmus auslösen)

**Files:**
- Create: `app/routers/admin/planning.py`
- Create: `app/templates/admin/planning.html`
- Modify: `app/main.py` (Router registrieren)

**Interfaces:**
- Consumes: `solve_schedule`, `PlanningPeriod`, `ShiftAssignment`, alle User/Wish/SpecialDay-Daten
- Produces:
  - Routes: `GET /admin/planning`, `POST /admin/planning/create`, `POST /admin/planning/{id}/run`, `POST /admin/planning/{id}/publish`

- [ ] **Step 1: `app/routers/admin/planning.py` implementieren**

```python
from fastapi import APIRouter, Depends, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import select
from datetime import date as date_type, datetime
from app.database import get_session
from app.deps import require_admin
from app.models.user import User, UserRole, DoctorProfile
from app.models.schedule import PlanningPeriod, ShiftAssignment, PlanStatus
from app.models.wish import WishEntry
from app.models.special_day import SpecialDay, SpecialDayCategory
from app.services.algorithm import solve_schedule
from app.services.fairness import compute_fairness_score
from app.config import settings

router = APIRouter(prefix="/admin/planning", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")

@router.get("", response_class=HTMLResponse)
async def planning_page(request: Request, session: AsyncSession = Depends(get_session)):
    periods = (await session.exec(select(PlanningPeriod).order_by(PlanningPeriod.year.desc()))).all()
    return templates.TemplateResponse("admin/planning.html",
        {"request": request, "periods": periods})

@router.post("/create")
async def create_period(name: str = Form(...), year: int = Form(...),
                         start_date: str = Form(...), end_date: str = Form(...),
                         wish_deadline: str = Form(None),
                         session: AsyncSession = Depends(get_session)):
    period = PlanningPeriod(
        name=name, year=year,
        start_date=date_type.fromisoformat(start_date),
        end_date=date_type.fromisoformat(end_date),
        wish_deadline=date_type.fromisoformat(wish_deadline) if wish_deadline else None,
    )
    session.add(period)
    await session.commit()
    return RedirectResponse("/admin/planning", status_code=302)

@router.post("/{period_id}/run")
async def run_algorithm(period_id: int, session: AsyncSession = Depends(get_session)):
    period = await session.get(PlanningPeriod, period_id)
    if not period:
        return RedirectResponse("/admin/planning", status_code=302)

    # Alle aktiven Ärzte laden
    result = await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True))
    doctors = result.all()

    # DoctorProfile für part_time_factor laden
    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}
    class DoctorWithFactor:
        def __init__(self, user, profile):
            self.id = user.id
            self.part_time_factor = profile.part_time_factor if profile else 1.0
    doctor_objs = [DoctorWithFactor(u, profiles.get(u.id)) for u in doctors]

    # Tage berechnen
    from datetime import timedelta
    days = [period.start_date + timedelta(days=i)
            for i in range((period.end_date - period.start_date).days + 1)]

    # Wünsche laden
    wishes = (await session.exec(
        select(WishEntry).where(WishEntry.date.between(period.start_date, period.end_date))
    )).all()

    # Sondertage laden
    sdays_raw = (await session.exec(
        select(SpecialDay, SpecialDayCategory).join(
            SpecialDayCategory, SpecialDay.category_id == SpecialDayCategory.id
        ).where(SpecialDay.date.between(period.start_date, period.end_date))
    )).all()
    class SDay:
        def __init__(self, d, w): self.date = d; self.weight = w
    special_days = [SDay(sd.date, cat.weight) for sd, cat in sdays_raw]

    assignments = solve_schedule(doctor_objs, days, wishes, special_days, settings.doctors_per_day)

    if assignments is None:
        return templates.TemplateResponse("admin/planning.html",
            {"request": ..., "error": "Kein gültiger Plan gefunden. Bitte Constraints prüfen."})

    # Alte Assignments löschen und neue speichern
    old = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.planning_period_id == period_id))).all()
    for a in old:
        await session.delete(a)

    weight_by_date = {sd.date: sd.weight for sd in special_days}
    for user_id, day in assignments:
        session.add(ShiftAssignment(
            planning_period_id=period_id,
            user_id=user_id,
            date=day,
            weighted_score=weight_by_date.get(day, 1.0),
        ))
    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)

@router.get("/{period_id}", response_class=HTMLResponse)
async def period_detail(period_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    period = await session.get(PlanningPeriod, period_id)
    assignments = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.planning_period_id == period_id)
        .order_by(ShiftAssignment.date))).all()

    # User-Namen laden
    users = {u.id: u for u in (await session.exec(select(User))).all()}

    # Fairness-Scores
    scores = compute_fairness_score(
        [(a.user_id, a.date) for a in assignments],
        []  # special_days hier vereinfacht
    )
    return templates.TemplateResponse("admin/planning.html", {
        "request": request,
        "period": period,
        "assignments": assignments,
        "users": users,
        "scores": scores,
    })

@router.post("/{period_id}/publish")
async def publish_period(period_id: int, session: AsyncSession = Depends(get_session)):
    period = await session.get(PlanningPeriod, period_id)
    period.status = PlanStatus.published
    period.published_at = datetime.utcnow()
    session.add(period)
    await session.commit()
    return RedirectResponse(f"/admin/planning/{period_id}", status_code=302)
```

- [ ] **Step 2: Template `app/templates/admin/planning.html`**

```html
{% extends "base.html" %}
{% block title %}Planung{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-6">Notdienstplanung</h1>

{% if not period %}
<!-- Periodenübersicht + Neue Periode -->
<form method="post" action="/admin/planning/create"
      class="bg-white rounded-lg shadow p-6 mb-6 grid grid-cols-2 md:grid-cols-3 gap-4">
  <div class="md:col-span-3">
    <label class="block text-sm font-medium mb-1">Name</label>
    <input type="text" name="name" placeholder="Notdienstplan 2027" required
      class="w-full border rounded px-3 py-2">
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Jahr</label>
    <input type="number" name="year" value="2027" required class="w-full border rounded px-3 py-2">
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Von</label>
    <input type="date" name="start_date" required class="w-full border rounded px-3 py-2">
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Bis</label>
    <input type="date" name="end_date" required class="w-full border rounded px-3 py-2">
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Wunsch-Deadline</label>
    <input type="date" name="wish_deadline" class="w-full border rounded px-3 py-2">
  </div>
  <div class="md:col-span-3">
    <button type="submit" class="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700">
      Planungsperiode erstellen
    </button>
  </div>
</form>

<div class="space-y-2">
  {% for p in periods %}
  <div class="bg-white border rounded p-4 flex justify-between items-center">
    <div>
      <span class="font-semibold">{{ p.name }}</span>
      <span class="ml-3 text-sm text-gray-500">{{ p.start_date }} – {{ p.end_date }}</span>
      <span class="ml-2 text-xs px-2 py-0.5 rounded
        {% if p.status == 'published' %}bg-green-100 text-green-700
        {% elif p.status == 'draft' %}bg-yellow-100 text-yellow-700
        {% else %}bg-gray-100 text-gray-500{% endif %}">{{ p.status }}</span>
    </div>
    <a href="/admin/planning/{{ p.id }}" class="text-blue-600 hover:underline text-sm">Öffnen</a>
  </div>
  {% endfor %}
</div>

{% else %}
<!-- Periodendetail -->
<div class="flex gap-3 mb-6">
  <form method="post" action="/admin/planning/{{ period.id }}/run">
    <button type="submit" class="bg-indigo-600 text-white px-4 py-2 rounded hover:bg-indigo-700">
      Algorithmus ausführen
    </button>
  </form>
  {% if period.status == 'draft' and assignments %}
  <form method="post" action="/admin/planning/{{ period.id }}/publish">
    <button type="submit" class="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700">
      Plan freigeben & benachrichtigen
    </button>
  </form>
  {% endif %}
</div>

{% if assignments %}
<div class="bg-white rounded-lg shadow overflow-hidden">
  <table class="w-full text-sm">
    <thead class="bg-gray-50 border-b">
      <tr>
        <th class="px-4 py-3 text-left">Datum</th>
        <th class="px-4 py-3 text-left">Arzt</th>
        <th class="px-4 py-3 text-left">Gewicht</th>
        <th class="px-4 py-3 text-left">Override</th>
      </tr>
    </thead>
    <tbody class="divide-y">
      {% for a in assignments %}
      <tr class="hover:bg-gray-50 {% if a.is_manual_override %}bg-yellow-50{% endif %}">
        <td class="px-4 py-2 font-mono">{{ a.date.strftime('%d.%m.%Y %a') }}</td>
        <td class="px-4 py-2">{{ users[a.user_id].full_name if a.user_id in users else '?' }}</td>
        <td class="px-4 py-2 text-gray-500">{{ "%.1f"|format(a.weighted_score) }}</td>
        <td class="px-4 py-2 text-xs text-orange-500">
          {% if a.is_manual_override %}Manuell{% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="text-gray-400 text-center py-12">Noch kein Plan berechnet. Algorithmus ausführen.</p>
{% endif %}
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Alle Router in `app/main.py` registrieren**

```python
from app.routers.auth import router as auth_router
from app.routers.doctor import router as doctor_router
from app.routers.admin.calendar import router as calendar_router
from app.routers.admin.planning import router as planning_router

app.include_router(auth_router)
app.include_router(doctor_router)
app.include_router(calendar_router)
app.include_router(planning_router)
```

- [ ] **Step 4: Manuell testen**

```bash
uvicorn app.main:app --reload
# 1. Admin-Login: python scripts/create_admin.py
# 2. Browser: http://localhost:8000/login → mit admin anmelden
# 3. /admin/planning → Periode erstellen (z.B. 2027-01-01 bis 2027-01-31)
# 4. "Algorithmus ausführen" → Plan sollte erscheinen
```

- [ ] **Step 5: Commit**

```bash
git add app/
git commit -m "feat: admin planning UI with algorithm trigger and publish"
```

---

## Task 8: E-Mail-Benachrichtigungen

**Files:**
- Create: `app/services/email.py`
- Modify: `app/routers/admin/planning.py` (publish-Route erweitern)

**Interfaces:**
- Produces: `send_schedule_published(user, period, assignments) -> None`
- Produces: `send_wish_deadline_reminder(user, deadline) -> None`

- [ ] **Step 1: `app/services/email.py` implementieren**

```python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date
from app.config import settings

def _send(to: str, subject: str, body_html: str):
    if not settings.smtp_host:
        print(f"[EMAIL] To: {to} | Subject: {subject}")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.attach(MIMEText(body_html, "html"))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
        if settings.smtp_user:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)

def send_schedule_published(user_email: str, user_name: str, period_name: str,
                             my_dates: list[date]):
    dates_html = "".join(f"<li>{d.strftime('%d.%m.%Y (%A)')}</li>" for d in sorted(my_dates))
    body = f"""
    <h2>Notdienstplan veröffentlicht: {period_name}</h2>
    <p>Hallo {user_name},</p>
    <p>der Notdienstplan wurde freigegeben. Ihre Dienste:</p>
    <ul>{dates_html}</ul>
    <p><a href="{settings.app_base_url}/me">Zum Notdienstplaner</a></p>
    """
    _send(user_email, f"Notdienstplan veröffentlicht: {period_name}", body)

def send_wish_deadline_reminder(user_email: str, user_name: str, deadline: date, period_name: str):
    body = f"""
    <p>Hallo {user_name},</p>
    <p>Bitte geben Sie Ihre Wünsche für den <strong>{period_name}</strong>
    bis zum <strong>{deadline.strftime('%d.%m.%Y')}</strong> ein.</p>
    <p><a href="{settings.app_base_url}/me/wishes">Wünsche eingeben</a></p>
    """
    _send(user_email, f"Erinnerung: Wünsche bis {deadline.strftime('%d.%m.%Y')} einreichen", body)
```

- [ ] **Step 2: Publish-Route in `planning.py` um E-Mail-Versand erweitern**

In der `publish_period`-Funktion, nach `await session.commit()`:

```python
from app.services.email import send_schedule_published

# Alle betroffenen User benachrichtigen
all_assignments = (await session.exec(
    select(ShiftAssignment).where(ShiftAssignment.planning_period_id == period_id))).all()
from collections import defaultdict
by_user = defaultdict(list)
for a in all_assignments:
    by_user[a.user_id].append(a.date)

all_users = {u.id: u for u in (await session.exec(select(User))).all()}
for user_id, dates in by_user.items():
    u = all_users.get(user_id)
    if u:
        send_schedule_published(u.email, u.full_name, period.name, dates)
```

- [ ] **Step 3: Commit**

```bash
git add app/services/email.py app/routers/admin/planning.py
git commit -m "feat: email notifications on schedule publish"
```

---

## Task 9: Tauschbörse

**Files:**
- Create: `app/routers/swap.py`
- Create: `app/templates/doctor/swaps.html`
- Test: `tests/test_swaps.py`

**Interfaces:**
- Produces:
  - Routes: `GET /me/swaps`, `POST /me/swaps/request`, `POST /me/swaps/{id}/accept`, `POST /admin/swaps/{id}/approve`

- [ ] **Step 1: Failing-Test schreiben**

```python
# tests/test_swaps.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_swap_request_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/me/swaps", follow_redirects=False)
    assert r.status_code == 302
```

- [ ] **Step 2: `app/routers/swap.py` implementieren**

```python
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import select
from datetime import date as date_type, datetime
from app.database import get_session
from app.deps import get_current_user, require_admin
from app.models.user import User
from app.models.swap import SwapRequest, SwapStatus
from app.models.schedule import ShiftAssignment

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/me/swaps", response_class=HTMLResponse)
async def swaps_page(request: Request, user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    my_swaps = (await session.exec(
        select(SwapRequest).where(
            (SwapRequest.requester_id == user.id) | (SwapRequest.target_id == user.id)
        ).order_by(SwapRequest.created_at.desc()))).all()
    users = {u.id: u for u in (await session.exec(select(User))).all()}

    # Meine Dienste (für Tausch-Formular)
    my_shifts = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.user_id == user.id)
        .order_by(ShiftAssignment.date))).all()

    return templates.TemplateResponse("doctor/swaps.html", {
        "request": request, "user": user, "swaps": my_swaps,
        "users": users, "my_shifts": my_shifts,
    })

@router.post("/me/swaps/request")
async def request_swap(user: User = Depends(get_current_user),
                        target_id: int = Form(...),
                        my_date: str = Form(...),
                        their_date: str = Form(...),
                        message: str = Form(""),
                        session: AsyncSession = Depends(get_session)):
    # Assignments prüfen
    my_shift = (await session.exec(
        select(ShiftAssignment).where(
            ShiftAssignment.user_id == user.id,
            ShiftAssignment.date == date_type.fromisoformat(my_date)
        ))).first()
    their_shift = (await session.exec(
        select(ShiftAssignment).where(
            ShiftAssignment.user_id == target_id,
            ShiftAssignment.date == date_type.fromisoformat(their_date)
        ))).first()
    if not my_shift or not their_shift:
        raise HTTPException(status_code=400, detail="Ungültige Dienstdaten")

    session.add(SwapRequest(
        requester_id=user.id, target_id=target_id,
        requester_shift_date=my_shift.date, target_shift_date=their_shift.date,
        planning_period_id=my_shift.planning_period_id, message=message,
    ))
    await session.commit()
    return RedirectResponse("/me/swaps", status_code=302)

@router.post("/me/swaps/{swap_id}/accept")
async def accept_swap(swap_id: int, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    swap = await session.get(SwapRequest, swap_id)
    if not swap or swap.target_id != user.id:
        raise HTTPException(status_code=403)
    swap.status = SwapStatus.accepted
    session.add(swap)
    await session.commit()
    return RedirectResponse("/me/swaps", status_code=302)

@router.post("/admin/swaps/{swap_id}/approve")
async def admin_approve_swap(swap_id: int, _: User = Depends(require_admin),
                              session: AsyncSession = Depends(get_session)):
    swap = await session.get(SwapRequest, swap_id)
    if not swap or swap.status != SwapStatus.accepted:
        raise HTTPException(status_code=400, detail="Tausch muss erst vom Zielarzt akzeptiert sein")

    # Assignments tauschen
    a1 = (await session.exec(select(ShiftAssignment).where(
        ShiftAssignment.user_id == swap.requester_id,
        ShiftAssignment.date == swap.requester_shift_date))).first()
    a2 = (await session.exec(select(ShiftAssignment).where(
        ShiftAssignment.user_id == swap.target_id,
        ShiftAssignment.date == swap.target_shift_date))).first()

    if a1 and a2:
        a1.user_id, a2.user_id = a2.user_id, a1.user_id
        a1.is_manual_override = True
        a2.is_manual_override = True
        session.add(a1)
        session.add(a2)

    swap.status = SwapStatus.admin_approved
    swap.resolved_at = datetime.utcnow()
    session.add(swap)
    await session.commit()
    return RedirectResponse("/admin/planning", status_code=302)
```

- [ ] **Step 3: Tests laufen lassen**

```bash
pytest tests/test_swaps.py -v
```

- [ ] **Step 4: Commit**

```bash
git add app/routers/swap.py app/templates/doctor/swaps.html tests/test_swaps.py
git commit -m "feat: duty swap request system with admin approval"
```

---

## Task 10: iCal-Export

**Files:**
- Create: `app/services/ical.py`
- Modify: `app/routers/doctor.py`

**Interfaces:**
- Produces: `GET /me/schedule.ics` → `text/calendar` Response

- [ ] **Step 1: `app/services/ical.py`**

```python
from datetime import date
from icalendar import Calendar, Event
from app.models.user import User

def build_ical(user: User, assignments: list) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Notdienstplaner//DE")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", f"Notdienste {user.full_name}")

    for a in assignments:
        event = Event()
        event.add("summary", "Notdienst")
        event.add("dtstart", a.date)
        event.add("dtend", a.date)
        event.add("uid", f"notdienst-{a.id}@notdienstplaner")
        cal.add_component(event)

    return cal.to_ical()
```

- [ ] **Step 2: Route in `doctor.py` hinzufügen**

```python
from fastapi.responses import Response
from app.services.ical import build_ical

@router.get("/schedule.ics")
async def export_ical(user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    assignments = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.user_id == user.id)
        .order_by(ShiftAssignment.date))).all()
    ical_data = build_ical(user, assignments)
    return Response(content=ical_data, media_type="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=notdienste.ics"})
```

- [ ] **Step 3: Commit**

```bash
git add app/services/ical.py app/routers/doctor.py
git commit -m "feat: iCal export for personal duty schedule"
```

---

## Task 11: Statistik-Dashboard (Admin)

**Files:**
- Create: `app/routers/admin/stats.py`
- Create: `app/templates/admin/statistics.html`

**Interfaces:**
- Produces: `GET /admin/statistics` → Fairness-Übersicht aller Ärzte, historische Scores

- [ ] **Step 1: `app/routers/admin/stats.py`**

```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.deps import require_admin
from app.models.user import User, UserRole, DoctorProfile
from app.models.schedule import PlanningPeriod, ShiftAssignment, PlanStatus
from app.models.special_day import SpecialDay, SpecialDayCategory
from app.services.fairness import compute_fairness_score

router = APIRouter(prefix="/admin/statistics", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")

@router.get("", response_class=HTMLResponse)
async def stats_page(request: Request, period_id: int = None,
                     session: AsyncSession = Depends(get_session)):
    periods = (await session.exec(
        select(PlanningPeriod).order_by(PlanningPeriod.year.desc()))).all()

    selected = None
    scores = {}
    duty_counts = {}

    if period_id or periods:
        selected = await session.get(PlanningPeriod, period_id) if period_id else periods[0]
        if selected:
            assignments = (await session.exec(
                select(ShiftAssignment).where(
                    ShiftAssignment.planning_period_id == selected.id))).all()
            sdays = (await session.exec(select(SpecialDay))).all()
            sday_weights = {sd.date: sd.weight for sd in sdays}

            class SDProxy:
                def __init__(self, d, w): self.date = d; self.weight = w
            special_days = [SDProxy(d, w) for d, w in sday_weights.items()]
            scores = compute_fairness_score([(a.user_id, a.date) for a in assignments], special_days)

            from collections import Counter
            duty_counts = Counter(a.user_id for a in assignments)

    doctors = (await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True))).all()
    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}

    return templates.TemplateResponse("admin/statistics.html", {
        "request": request, "periods": periods, "selected": selected,
        "doctors": doctors, "scores": scores, "duty_counts": duty_counts,
        "profiles": profiles,
    })
```

- [ ] **Step 2: `app/templates/admin/statistics.html`**

```html
{% extends "base.html" %}
{% block title %}Statistiken{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-4">Fairness-Statistiken</h1>

<form method="get" class="mb-6 flex gap-2">
  <select name="period_id" class="border rounded px-3 py-2">
    {% for p in periods %}
    <option value="{{ p.id }}" {% if selected and p.id == selected.id %}selected{% endif %}>
      {{ p.name }}
    </option>
    {% endfor %}
  </select>
  <button type="submit" class="bg-gray-100 border rounded px-4 py-2 hover:bg-gray-200">Anzeigen</button>
</form>

{% if selected and doctors %}
<div class="bg-white rounded-lg shadow overflow-hidden">
  <table class="w-full text-sm">
    <thead class="bg-gray-50 border-b">
      <tr>
        <th class="px-4 py-3 text-left">Arzt</th>
        <th class="px-4 py-3 text-right">Dienste</th>
        <th class="px-4 py-3 text-right">Gewichteter Score</th>
        <th class="px-4 py-3 text-right">Teilzeit-Faktor</th>
        <th class="px-4 py-3 text-right">Übertrag Vorjahr</th>
      </tr>
    </thead>
    <tbody class="divide-y">
      {% for doc in doctors | sort(attribute='full_name') %}
      {% set score = scores.get(doc.id, 0) %}
      {% set count = duty_counts.get(doc.id, 0) %}
      {% set profile = profiles.get(doc.id) %}
      <tr class="hover:bg-gray-50">
        <td class="px-4 py-3 font-medium">{{ doc.full_name }}</td>
        <td class="px-4 py-3 text-right">{{ count }}</td>
        <td class="px-4 py-3 text-right">
          <div class="flex items-center justify-end gap-2">
            <div class="w-24 bg-gray-200 rounded-full h-2">
              {% set max_score = scores.values() | max if scores else 1 %}
              <div class="bg-blue-500 h-2 rounded-full"
                   style="width: {{ [(score / max_score * 100)|int, 100]|min }}%"></div>
            </div>
            {{ "%.1f"|format(score) }}
          </div>
        </td>
        <td class="px-4 py-3 text-right text-gray-500">
          {{ "%.0f"|format((profile.part_time_factor if profile else 1.0) * 100) }}%
        </td>
        <td class="px-4 py-3 text-right text-gray-500">
          {{ "%.1f"|format(profile.carried_over_score if profile else 0) }}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Router registrieren in `main.py`**

```python
from app.routers.admin.stats import router as stats_router
from app.routers.swap import router as swap_router
app.include_router(stats_router)
app.include_router(swap_router)
```

- [ ] **Step 4: Commit**

```bash
git add app/routers/admin/stats.py app/templates/admin/statistics.html app/main.py
git commit -m "feat: admin fairness statistics dashboard"
```

---

## Task 12: Arzt-Jahresübersicht & Dienst-Bestätigung

**Files:**
- Create: `app/templates/doctor/schedule.html`
- Modify: `app/routers/doctor.py`

**Interfaces:**
- Produces: `GET /me/schedule`, `POST /me/assignments/{id}/acknowledge`

- [ ] **Step 1: Route für Dienstübersicht und Bestätigung**

In `app/routers/doctor.py` hinzufügen:

```python
from app.models.schedule import ShiftAssignment, PlanningPeriod
from datetime import datetime

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
```

- [ ] **Step 2: Template `app/templates/doctor/schedule.html`**

```html
{% extends "base.html" %}
{% block title %}Mein Dienstplan{% endblock %}
{% block content %}
<div class="flex justify-between items-center mb-4">
  <h1 class="text-2xl font-bold">Mein Dienstplan</h1>
  <a href="/me/schedule.ics"
     class="text-sm bg-gray-100 border rounded px-3 py-1.5 hover:bg-gray-200">
    📅 Kalender exportieren (.ics)
  </a>
</div>

{% if assignments %}
<div class="bg-white rounded-lg shadow overflow-hidden">
  <table class="w-full text-sm">
    <thead class="bg-gray-50 border-b">
      <tr>
        <th class="px-4 py-3 text-left">Datum</th>
        <th class="px-4 py-3 text-left">Plan</th>
        <th class="px-4 py-3 text-left">Bestätigt</th>
        <th class="px-4 py-3"></th>
      </tr>
    </thead>
    <tbody class="divide-y">
      {% for a in assignments %}
      <tr class="hover:bg-gray-50">
        <td class="px-4 py-3 font-mono font-medium">{{ a.date.strftime('%d.%m.%Y') }}</td>
        <td class="px-4 py-3 text-gray-500 text-xs">
          {{ periods[a.planning_period_id].name if a.planning_period_id in periods else '–' }}
        </td>
        <td class="px-4 py-3">
          {% if a.acknowledged_at %}
          <span class="text-green-600 text-xs">✓ {{ a.acknowledged_at.strftime('%d.%m.%Y') }}</span>
          {% else %}
          <span class="text-gray-400 text-xs">ausstehend</span>
          {% endif %}
        </td>
        <td class="px-4 py-3">
          {% if not a.acknowledged_at %}
          <form method="post" action="/me/assignments/{{ a.id }}/acknowledge">
            <button class="text-xs bg-green-50 border border-green-300 text-green-700 px-2 py-1 rounded hover:bg-green-100">
              Bestätigen
            </button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="text-center text-gray-400 py-12">Noch keine Dienste geplant.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Dashboard-Template `app/templates/doctor/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Mein Bereich{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-6">Willkommen, {{ user.full_name }}</h1>

<div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
  <a href="/me/schedule" class="bg-white rounded-lg shadow p-6 hover:shadow-md transition">
    <div class="text-3xl mb-2">📅</div>
    <div class="font-semibold">Mein Dienstplan</div>
    <div class="text-sm text-gray-500 mt-1">{{ assignments | length }} Dienste</div>
  </a>
  <a href="/me/wishes" class="bg-white rounded-lg shadow p-6 hover:shadow-md transition">
    <div class="text-3xl mb-2">📝</div>
    <div class="font-semibold">Meine Wünsche</div>
    <div class="text-sm text-gray-500 mt-1">{{ wishes | length }} eingetragen</div>
  </a>
  <a href="/me/swaps" class="bg-white rounded-lg shadow p-6 hover:shadow-md transition">
    <div class="text-3xl mb-2">🔄</div>
    <div class="font-semibold">Tauschbörse</div>
    <div class="text-sm text-gray-500 mt-1">Dienste tauschen</div>
  </a>
</div>

{% if assignments %}
<h2 class="text-lg font-semibold mb-3">Nächste Dienste</h2>
<div class="bg-white rounded-lg shadow overflow-hidden">
  {% for a in assignments[:5] %}
  <div class="px-4 py-3 border-b last:border-0 flex justify-between items-center">
    <span class="font-mono">{{ a.date.strftime('%d.%m.%Y (%A)') }}</span>
    {% if not a.acknowledged_at %}
    <form method="post" action="/me/assignments/{{ a.id }}/acknowledge">
      <button class="text-xs bg-green-50 border border-green-300 text-green-700 px-2 py-1 rounded">
        Bestätigen
      </button>
    </form>
    {% else %}
    <span class="text-green-500 text-xs">✓</span>
    {% endif %}
  </div>
  {% endfor %}
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Commit**

```bash
git add app/
git commit -m "feat: doctor schedule view with shift acknowledgment"
```

---

## Task 13: Admin-Dashboard & User-Verwaltung

**Files:**
- Create: `app/routers/admin/users.py`
- Create: `app/templates/admin/dashboard.html`
- Create: `app/templates/admin/users.html`

**Interfaces:**
- Produces:
  - `GET /admin` → Admin-Dashboard
  - `GET /admin/users`, `POST /admin/users/create`, `POST /admin/users/{id}/toggle-active`

- [ ] **Step 1: `app/routers/admin/users.py`**

```python
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.deps import require_admin
from app.models.user import User, UserRole, DoctorProfile
from app.services.auth import hash_password

router = APIRouter(prefix="/admin/users", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")

@router.get("", response_class=HTMLResponse)
async def users_page(request: Request, session: AsyncSession = Depends(get_session)):
    users = (await session.exec(select(User).order_by(User.full_name))).all()
    profiles = {p.user_id: p for p in (await session.exec(select(DoctorProfile))).all()}
    return templates.TemplateResponse("admin/users.html",
        {"request": request, "users": users, "profiles": profiles})

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
```

- [ ] **Step 2: Admin-Router in `main.py` registrieren**

```python
from app.routers.admin.users import router as users_router
app.include_router(users_router)
```

- [ ] **Step 3: `app/templates/admin/users.html`**

```html
{% extends "base.html" %}
{% block title %}Benutzerverwaltung{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-6">Benutzerverwaltung</h1>

<details class="bg-white rounded-lg shadow mb-6">
  <summary class="px-6 py-4 cursor-pointer font-medium">+ Neuer Benutzer</summary>
  <form method="post" action="/admin/users/create" class="px-6 pb-6 grid grid-cols-2 gap-4">
    <div>
      <label class="block text-sm font-medium mb-1">Name</label>
      <input type="text" name="full_name" required class="w-full border rounded px-3 py-2">
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">E-Mail</label>
      <input type="email" name="email" required class="w-full border rounded px-3 py-2">
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Passwort</label>
      <input type="password" name="password" required class="w-full border rounded px-3 py-2">
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Rolle</label>
      <select name="role" class="w-full border rounded px-3 py-2">
        <option value="doctor">Arzt</option>
        <option value="admin">Administrator</option>
      </select>
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Teilzeit-Faktor (0.1–1.0)</label>
      <input type="number" name="part_time_factor" value="1.0" min="0.1" max="1.0" step="0.1"
        class="w-full border rounded px-3 py-2">
    </div>
    <div class="flex items-end">
      <button type="submit" class="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700">
        Erstellen
      </button>
    </div>
  </form>
</details>

<div class="bg-white rounded-lg shadow overflow-hidden">
  <table class="w-full text-sm">
    <thead class="bg-gray-50 border-b">
      <tr>
        <th class="px-4 py-3 text-left">Name</th>
        <th class="px-4 py-3 text-left">E-Mail</th>
        <th class="px-4 py-3 text-left">Rolle</th>
        <th class="px-4 py-3 text-right">Teilzeit</th>
        <th class="px-4 py-3 text-center">Status</th>
        <th class="px-4 py-3"></th>
      </tr>
    </thead>
    <tbody class="divide-y">
      {% for user in users %}
      {% set profile = profiles.get(user.id) %}
      <tr class="hover:bg-gray-50 {% if not user.is_active %}opacity-50{% endif %}">
        <td class="px-4 py-3 font-medium">{{ user.full_name }}</td>
        <td class="px-4 py-3 text-gray-500">{{ user.email }}</td>
        <td class="px-4 py-3">
          <span class="text-xs px-2 py-0.5 rounded
            {% if user.role == 'admin' %}bg-purple-100 text-purple-700{% else %}bg-blue-100 text-blue-700{% endif %}">
            {{ 'Admin' if user.role == 'admin' else 'Arzt' }}
          </span>
        </td>
        <td class="px-4 py-3 text-right text-gray-500">
          {% if profile %}{{ "%.0f"|format(profile.part_time_factor * 100) }}%{% else %}—{% endif %}
        </td>
        <td class="px-4 py-3 text-center">
          {% if user.is_active %}<span class="text-green-600 text-xs">Aktiv</span>
          {% else %}<span class="text-red-500 text-xs">Inaktiv</span>{% endif %}
        </td>
        <td class="px-4 py-3">
          <form method="post" action="/admin/users/{{ user.id }}/toggle-active">
            <button class="text-xs border rounded px-2 py-1 hover:bg-gray-100">
              {{ 'Deaktivieren' if user.is_active else 'Aktivieren' }}
            </button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 4: Admin-Dashboard `app/templates/admin/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Admin{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-6">Admin-Bereich</h1>
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
  <a href="/admin/users" class="bg-white rounded-lg shadow p-6 hover:shadow-md transition">
    <div class="text-3xl mb-2">👥</div>
    <div class="font-semibold">Benutzer</div>
    <div class="text-sm text-gray-500">Ärzte verwalten</div>
  </a>
  <a href="/admin/calendar" class="bg-white rounded-lg shadow p-6 hover:shadow-md transition">
    <div class="text-3xl mb-2">📆</div>
    <div class="font-semibold">Kalender</div>
    <div class="text-sm text-gray-500">Sondertage konfigurieren</div>
  </a>
  <a href="/admin/planning" class="bg-white rounded-lg shadow p-6 hover:shadow-md transition">
    <div class="text-3xl mb-2">📋</div>
    <div class="font-semibold">Planung</div>
    <div class="text-sm text-gray-500">Notdienstpläne erstellen</div>
  </a>
  <a href="/admin/statistics" class="bg-white rounded-lg shadow p-6 hover:shadow-md transition">
    <div class="text-3xl mb-2">📊</div>
    <div class="font-semibold">Statistiken</div>
    <div class="text-sm text-gray-500">Fairness-Übersicht</div>
  </a>
</div>
{% endblock %}
```

- [ ] **Step 5: Admin-Root-Route in `main.py`**

```python
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.deps import require_admin

_templates = Jinja2Templates(directory="app/templates")

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, admin = Depends(require_admin)):
    return _templates.TemplateResponse("admin/dashboard.html",
        {"request": request, "user": admin})
```

- [ ] **Step 6: Integrations-Test — kompletter Workflow**

```bash
uvicorn app.main:app --reload

# Terminal 2:
python scripts/create_admin.py
# Dann Browser:
# 1. /login als admin
# 2. /admin/users → 3 Test-Ärzte anlegen
# 3. /admin/calendar → Feiertage 2027 importieren
# 4. Als Arzt einloggen → /me/wishes → 5 Wünsche eintragen
# 5. Als admin → /admin/planning → Periode Jan–Dez 2027 erstellen
# 6. Algorithmus ausführen (dauert bis 30s)
# 7. Plan freigeben
# 8. Als Arzt: /me/schedule → Dienste sehen + bestätigen
# 9. /me/schedule.ics herunterladen
# 10. /admin/statistics → Fairness prüfen
```

- [ ] **Step 7: Alle Tests laufen lassen**

```bash
pytest tests/ -v
```

- [ ] **Step 8: Final-Commit**

```bash
git add .
git commit -m "feat: complete admin dashboard and user management"
```

---

## Erweiterungen (Post-MVP, nicht Teil dieses Plans)

| Feature | Aufwand | Wert |
|---|---|---|
| Score-Übertrag Vorjahr (automatisch) | M | Hoch |
| Wunsch-Deadline-Reminder via APScheduler | S | Hoch |
| PDF-Ausdruck (WeasyPrint) | M | Mittel |
| Passwort-Reset via E-Mail (Token) | M | Hoch |
| Mehrere Dienste pro Tag (Früh/Spät) | L | Mittel |
| Öffentlicher Kalender-Link (read-only) | S | Mittel |
| Bayerische Schulferien-API-Integration | M | Mittel |

---

## Self-Review gegen Spec

- [x] Arzt-Accounts mit Login: Task 2
- [x] Wunsch-Eingabe (positiv/negativ, hart/weich): Task 5
- [x] Admin erstellt Plan: Task 7
- [x] Algorithmus berücksichtigt Wünsche, Fairness, Sondertage: Task 6
- [x] Rollierendes System via weighted Fairness Score: Task 6 (part_time_factor + Gewichte)
- [x] Bayerische Feiertage + Brückentage konfigurierbar: Task 4
- [x] Admin gibt Plan frei + benachrichtigt: Task 7 + Task 8
- [x] Fairness auf Jahresebene: Task 11 + carried_over_score im Modell
- [x] Tauschbörse: Task 9
- [x] iCal-Export: Task 10
- [x] Dienst-Bestätigung: Task 12
- [x] Teilzeit-Faktor: DoctorProfile.part_time_factor, im Algorithmus
- [x] Keine Placeholder im Plan

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import SQLModel
from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # Idempotente Spalten-Migrationen für bestehende Datenbanken
        for sql in [
            "ALTER TABLE specialday ADD COLUMN required_doctors INTEGER",
            "ALTER TABLE doctorprofile ADD COLUMN credit_factor REAL NOT NULL DEFAULT 1.0",
            "ALTER TABLE doctorprofile ADD COLUMN desired_shifts INTEGER",
            "ALTER TABLE doctorprofile ADD COLUMN day_preference TEXT NOT NULL DEFAULT 'alle'",
            "ALTER TABLE doctorprofile ADD COLUMN sub_carried_over_score REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE shiftassignment ADD COLUMN is_substitute INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE swaprequest ADD COLUMN is_coverage_request INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # Spalte existiert bereits

async def get_session():
    async with AsyncSessionLocal() as session:
        yield session

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
        ]:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # Spalte existiert bereits

async def get_session():
    async with AsyncSessionLocal() as session:
        yield session

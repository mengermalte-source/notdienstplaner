import os
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from app.main import app as fastapi_app
from app.database import get_session

TEST_DB_PATH = "./test_notdienstplaner.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"

_test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    _test_engine, expire_on_commit=False, class_=AsyncSession
)


async def _get_test_session():
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture(scope="session", autouse=True)
async def setup_test_db():
    import app.models.user  # noqa: F401
    import app.models.schedule  # noqa: F401
    import app.models.wish  # noqa: F401
    import app.models.special_day  # noqa: F401
    import app.models.swap  # noqa: F401
    import app.models.vacation  # noqa: F401

    async with _test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)

    fastapi_app.dependency_overrides[get_session] = _get_test_session

    yield

    fastapi_app.dependency_overrides.clear()
    await _test_engine.dispose()

    try:
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
    except PermissionError:
        pass  # Windows: Datei noch in Nutzung, wird beim nächsten Start überschrieben

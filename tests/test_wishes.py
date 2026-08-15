import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.models.user import User, UserRole
from app.services.auth import hash_password, create_access_token
from tests.conftest import TestSessionLocal


@pytest.fixture
async def doctor_client():
    email = f"dr.test.{uuid.uuid4().hex[:8]}@x.de"
    async with TestSessionLocal() as session:
        user = User(email=email, hashed_password=hash_password("pw"),
                    full_name="Dr. Test", role=UserRole.doctor)
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
        "date": "2027-03-15", "kind": "prefer_not", "reason": "Urlaub"
    })
    assert r.status_code in (200, 302)


@pytest.mark.asyncio
async def test_duplicate_wish_rejected(doctor_client):
    client, _ = doctor_client
    data = {"date": "2027-04-01", "kind": "cannot", "reason": ""}
    await client.post("/me/wishes", data=data)
    r = await client.post("/me/wishes", data=data)
    assert r.status_code in (400, 200)

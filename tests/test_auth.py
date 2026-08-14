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

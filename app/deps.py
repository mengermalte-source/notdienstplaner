from fastapi import Request, HTTPException, Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.models.user import User, UserRole
from app.services.auth import decode_token


async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    token = request.cookies.get("access_token")
    if not token:
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

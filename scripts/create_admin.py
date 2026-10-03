import asyncio
import sys
from app.config import settings
from app.database import init_db, AsyncSessionLocal
from app.models.user import User, UserRole
from app.services.auth import hash_password


async def main():
    if not settings.admin_password:
        sys.exit("ADMIN_PASSWORD ist nicht gesetzt")
    await init_db()
    async with AsyncSessionLocal() as session:
        admin = User(
            email=settings.admin_username,
            hashed_password=hash_password(settings.admin_password),
            full_name="Administrator",
            role=UserRole.admin,
        )
        session.add(admin)
        await session.commit()
        print(f"Admin erstellt: {admin.email}")


asyncio.run(main())

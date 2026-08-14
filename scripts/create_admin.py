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

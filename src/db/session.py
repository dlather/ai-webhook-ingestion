from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory with safe defaults."""
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

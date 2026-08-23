from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

# Same physical Postgres database Zynost's main backend uses (same
# DATABASE_URL value, set independently in this service's own environment)
# — this service reads the existing `users` table (to resolve who's asking,
# via a shared JWT_SECRET) and owns the `merchants`/`merchant_orders`
# tables outright. No Python import ties this to tradeos-backend at all;
# the only coupling is "two services, one database", which is normal and
# safe as long as each service only touches the tables it's responsible for.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"statement_cache_size": 0},
    pool_pre_ping=True,
    pool_recycle=300,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.models.base import Base

logger = logging.getLogger(__name__)

# Database URL from environment
# Defaulting to a placeholder for local dev, will be overridden by docker-compose
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://easyplan:easyplan@localhost:5432/easyplan")

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_size=5,
    max_overflow=10,
)

# Create async session factory
async_session = async_sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

async def init_db():
    """Initialize the database by creating all tables."""
    async with engine.begin() as conn:
        # Import all models here to ensure they are registered with Base.metadata
        from app.models import __all__ 
        logger.info("Initializing database tables...")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully.")

async def get_db():
    """Dependency for getting async database sessions."""
    async with async_session() as session:
        yield session

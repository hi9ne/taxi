# database/db.py - Подключение к PostgreSQL
# Асинхронное подключение через SQLAlchemy + asyncpg

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
import logging

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Создаём асинхронный движок
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # True для отладки SQL запросов
    pool_size=10,
    max_overflow=20
)

# Фабрика сессий
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Базовый класс для моделей
Base = declarative_base()


@asynccontextmanager
async def get_session():
    """Контекстный менеджер для работы с сессией БД"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Ошибка БД: {e}")
            # Если это таймаут, пробуем еще раз
            if "timeout" in str(e).lower() or "Request timeout error" in str(e):
                logger.warning("Обнаружен таймаут БД, пробуем повторно...")
                try:
                    yield session
                    await session.commit()
                    logger.info("Повторная операция с БД успешна")
                except Exception as retry_e:
                    logger.error(f"Повторная ошибка БД: {retry_e}")
                    raise retry_e
            else:
                raise


async def init_db():
    """Инициализация базы данных - создание всех таблиц"""
    from database.models import Base
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("База данных инициализирована")


async def close_db():
    """Закрытие соединения с БД"""
    await engine.dispose()
    logger.info("Соединение с БД закрыто")


#!/usr/bin/env python3
# bot.py - Точка входа, инициализация и запуск бота
# PoputchikBot - Telegram-бот для поиска попутчиков

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, CHANNEL_ID
from database.db import init_db, close_db
from handlers import (
    start_router,
    registration_router,
    post_router,
    subscriptions_router,
    my_posts_router,
    profile_router,
    rating_router,
    callbacks_router
)
from workers.expiration import start_expiration_worker, stop_expiration_worker

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска бота"""
    
    # Проверяем наличие токена
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен! Проверьте файл .env")
        sys.exit(1)
    
    logger.info("Инициализация бота...")
    
    # Инициализация базы данных
    await init_db()
    logger.info("База данных инициализирована")
    
    # Создание бота и диспетчера с увеличенными таймаутами
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            # Увеличиваем таймауты для стабильности
            disable_web_page_preview=True,
        )
    )
    
    # Используем MemoryStorage для FSM (для продакшн рекомендуется Redis)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Регистрация роутеров (порядок важен!)
    dp.include_router(start_router)
    dp.include_router(registration_router)
    dp.include_router(post_router)
    dp.include_router(subscriptions_router)
    dp.include_router(my_posts_router)
    dp.include_router(profile_router)
    dp.include_router(rating_router)
    dp.include_router(callbacks_router)
    
    logger.info("Роутеры зарегистрированы")
    
    # Запуск воркера истечения объявлений
    start_expiration_worker(bot)
    logger.info("Воркер истечения запущен")
    
    try:
        # Удаляем вебхук если был установлен
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Вебхук удален")
        
    except Exception as e:
        logger.warning(f"⚠️ Не удалось удалить webhook (это не критично): {e}")
        # Продолжаем запуск даже если не удалось удалить вебхук
    
    try:
        # Получаем информацию о боте
        bot_info = await bot.get_me()
        logger.info(f"✅ Бот запущен: @{bot_info.username} (ID: {bot_info.id})")
        
    except Exception as e:
        logger.error(f"❌ Не удалось получить информацию о боте: {e}")
        return
    
    try:
        # Отправляем закрепленное сообщение с кнопкой в канал
        if CHANNEL_ID:
            try:
                from services.channel import send_pinned_menu_message
                pinned_msg_id = await send_pinned_menu_message(bot)
                if pinned_msg_id:
                    logger.info(f"✅ Закрепленное сообщение с кнопкой отправлено в канал (msg_id={pinned_msg_id})")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при отправке закрепленного сообщения: {e}")
                logger.info("💡 Убедитесь, что бот является администратором канала")
        
        logger.info("Продолжаем запуск бота...")
        logger.info("Проверка подключения к Telegram API...")
        
        # Запуск polling
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise
    
    finally:
        # Корректное завершение
        stop_expiration_worker()
        await close_db()
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен по запросу пользователя")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)

# utils/retry_utils.py - Утилиты для повторных попыток при ошибках сети
import asyncio
import logging
from typing import Callable, Any
from aiogram.exceptions import TelegramNetworkError

logger = logging.getLogger(__name__)


async def retry_on_network_error(
    func: Callable,
    max_retries: int = 2,
    delay: float = 1.0,
    *args,
    **kwargs
) -> Any:
    """
    Повторная попытка выполнения функции при сетевых ошибках
    
    Args:
        func: Функция для выполнения
        max_retries: Максимальное количество попыток
        delay: Задержка между попытками в секундах
        *args, **kwargs: Аргументы функции
    
    Returns:
        Результат выполнения функции
    
    Raises:
        Последнее исключение, если все попытки неудачны
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except TelegramNetworkError as e:
            last_exception = e
            if attempt < max_retries:
                logger.warning(f"Сетевая ошибка (попытка {attempt + 1}/{max_retries + 1}): {e}")
                await asyncio.sleep(delay * (2 ** attempt))  # Экспоненциальная задержка
            else:
                logger.error(f"Все попытки завершились ошибкой: {e}")
        except Exception as e:
            # Для других ошибок не повторяем, просто пробрасываем дальше
            logger.error(f"Не сетевая ошибка: {e}")
            raise
    
    if last_exception:
        raise last_exception


async def safe_message_answer(message, text: str, **kwargs) -> bool:
    """
    Безопасная отправка сообщения с повторными попытками
    
    Args:
        message: Объект сообщения aiogram
        text: Текст сообщения
        **kwargs: Дополнительные параметры для answer()
    
    Returns:
        True если успешно, False если все попытки неудачны
    """
    try:
        await retry_on_network_error(
            message.answer,
            max_retries=2,
            text=text,
            **kwargs
        )
        return True
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение пользователю {message.from_user.id}: {e}")
        return False


async def safe_callback_message_edit(callback, text: str, **kwargs) -> bool:
    """
    Безопасное редактирование сообщения callback с повторными попытками
    
    Args:
        callback: Объект callback aiogram
        text: Новый текст сообщения
        **kwargs: Дополнительные параметры для edit_text()
    
    Returns:
        True если успешно, False если все попытки неудачны
    """
    try:
        await retry_on_network_error(
            callback.message.edit_text,
            max_retries=2,
            text=text,
            **kwargs
        )
        return True
    except Exception as e:
        logger.error(f"Не удалось отредактировать сообщение для пользователя {callback.from_user.id}: {e}")
        return False

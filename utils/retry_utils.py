# utils/retry_utils.py - Утилиты для повторных попыток при ошибках сети
import asyncio
import logging
from typing import Callable, Any
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest

logger = logging.getLogger(__name__)


async def retry_on_database_error(
    func: Callable,
    max_retries: int = 2,
    delay: float = 1.0,
    *args,
    **kwargs
) -> Any:
    """
    Повторная попытка выполнения функции при ошибках БД
    
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
        except Exception as e:
            last_exception = e
            error_msg = str(e)
            
            # Проверяем, что это ошибка таймаута БД
            if ("timeout" in error_msg.lower() or 
                "Request timeout error" in error_msg or
                "generator didn't stop after athrow" in error_msg):
                
                if attempt < max_retries:
                    logger.warning(f"Ошибка БД (попытка {attempt + 1}/{max_retries + 1}): {e}")
                    await asyncio.sleep(delay * (2 ** attempt))  # Экспоненциальная задержка
                else:
                    logger.error(f"Все попытки БД завершились ошибкой: {e}")
            else:
                # Для других ошибок не повторяем, просто пробрасываем дальше
                logger.error(f"Не ошибка таймаута БД: {e}")
                raise
    
    if last_exception:
        raise last_exception


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
    except (TelegramBadRequest, TelegramNetworkError) as e:
        error_msg = str(e)
        
        # Если сообщение не найдено или нельзя редактировать
        if "message to edit not found" in error_msg or "message can't be edited" in error_msg:
            logger.warning(f"Сообщение нельзя отредактировать для пользователя {callback.from_user.id}: {e}")
            # Показываем ошибку через answer (уведомление)
            try:
                await callback.answer("⚠️ Не удалось обновить сообщение. Попробуйте заново.", show_alert=True)
            except:
                pass  # Игнорируем ошибки answer
            return False
        
        # Другие ошибки - пробуем отправить новое сообщение
        logger.warning(f"Ошибка редактирования сообщения для пользователя {callback.from_user.id}: {e}")
        try:
            await callback.message.answer(text, **kwargs)
            return True
        except Exception as e2:
            logger.error(f"Не удалось отправить новое сообщение: {e2}")
            return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка при редактировании сообщения: {e}")
        return False


async def safe_message_edit(message, text: str, **kwargs) -> bool:
    """
    Безопасное редактирование сообщения с повторными попытками
    
    Args:
        message: Объект сообщения aiogram
        text: Новый текст сообщения
        **kwargs: Дополнительные параметры для edit_text()
    
    Returns:
        True если успешно, False если все попытки неудачны
    """
    try:
        await retry_on_network_error(
            message.edit_text,
            max_retries=2,
            text=text,
            **kwargs
        )
        return True
    except (TelegramBadRequest, TelegramNetworkError) as e:
        error_msg = str(e)
        
        # Если сообщение не найдено или нельзя редактировать
        if "message to edit not found" in error_msg or "message can't be edited" in error_msg:
            logger.warning(f"Сообщение нельзя отредактировать: {e}")
            # Показываем ошибку через answer (уведомление)
            try:
                await message.answer("⚠️ Не удалось обновить сообщение. Попробуйте заново.")
            except:
                pass  # Игнорируем ошибки answer
            return False
        
        # Другие ошибки - пробуем отправить новое сообщение
        logger.warning(f"Ошибка редактирования сообщения: {e}")
        try:
            await message.answer(text, **kwargs)
            return True
        except Exception as e2:
            logger.error(f"Не удалось отправить новое сообщение: {e2}")
            return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка при редактировании сообщения: {e}")
        return False

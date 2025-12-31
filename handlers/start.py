# handlers/start.py - Команда /start и приветствие
# Точка входа в бота

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramNetworkError
from sqlalchemy import select
from typing import Tuple
import logging

from database.db import get_session
from database.models import User, Post
from utils.message_cleaner import clean_chat
from utils.helpers import format_local_time
from utils.retry_utils import safe_message_answer, safe_callback_message_edit, retry_on_database_error
from keyboards import get_role_keyboard, get_main_menu_keyboard, get_remove_keyboard, get_agreement_keyboard
from states import Agreement

router = Router()
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    """
    Обработчик команды /start
    Если пользователь новый - приветствие и выбор роли
    Если зарегистрирован - главное меню
    Если есть параметр post_XXX - показываем информацию об объявлении
    """
    # Проверяем параметр команды /start ДО очистки state
    command_args = message.text.split() if message.text else []
    start_param = command_args[1] if len(command_args) > 1 else None
    
    # Если есть параметр post_XXX - сохраняем его для показа после регистрации
    post_id_to_show = None
    create_post_requested = False
    
    if start_param:
        if start_param.startswith("post_"):
            try:
                post_id_to_show = int(start_param.replace("post_", ""))
            except (ValueError, AttributeError):
                logger.warning(f"Неверный параметр start: {start_param}")
        elif start_param == "create_post":
            create_post_requested = True
    
    # Очищаем все предыдущие сообщения и state
    await clean_chat(bot, message.from_user.id, state)
    await state.clear()
    if post_id_to_show:
        await state.update_data(post_id_after_registration=post_id_to_show)
    if create_post_requested:
        await state.update_data(create_post_after_registration=True)
    
    async with get_session() as session:
        # Проверяем регистрацию
        query = select(User).where(User.telegram_id == message.from_user.id)
        result = await session.execute(query)
        user = result.scalar_one_or_none()
        
        if user:
            # Пользователь зарегистрирован
            if post_id_to_show:
                # Показываем объявление
                await show_post_from_channel(message, post_id_to_show)
            elif create_post_requested:
                # Пользователь нажал кнопку "Создать объявление" из канала
                # Перенаправляем на создание объявления
                from handlers.post import start_create_post
                from aiogram.types import CallbackQuery
                # Создаем виртуальный callback для переиспользования логики
                class FakeCallback:
                    def __init__(self, msg):
                        self.message = msg
                        self.from_user = msg.from_user
                        self.data = "create_post"
                    async def answer(self, *args, **kwargs):
                        pass
                
                fake_callback = FakeCallback(message)
                await start_create_post(fake_callback, state, bot)
            else:
                # Показываем главное меню
                await show_main_menu(message, user, session)
        else:
            # Новый пользователь - показываем предупреждение и запрашиваем согласие
            agreement_text = (
                "⚠️ <b>ВАЖНО ПЕРЕД НАЧАЛОМ</b>\n\n"
                "Это сервис попутчиков, а не такси.\n\n"
                "🚗 Водитель едет по своим делам\n"
                "👥 Пассажир присоединяется по договорённости\n"
                "💬 Все условия обсуждаются напрямую между пользователями\n"
                "💳 Платформа не принимает оплату за поездки\n"
                "🛡 Платформа не является перевозчиком и не несёт ответственности за поездку\n\n"
                "<b>Нажимая «Согласен», вы подтверждаете, что:</b>\n\n"
                "• понимаете формат попутки;\n"
                "• берёте ответственность за своё участие;\n"
                "• соглашаетесь с правилами сервиса.\n\n"
                "❗ Без согласия доступ к сервису невозможен."
            )
            
            try:
                await safe_message_answer(
                    message,
                    agreement_text,
                    parse_mode="HTML",
                    reply_markup=get_agreement_keyboard()
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке соглашения пользователю {message.from_user.id}: {e}", exc_info=True)
                return
            await state.set_state(Agreement.waiting_agreement)


@router.callback_query(F.data == "agreement:accept", Agreement.waiting_agreement)
async def accept_agreement(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Пользователь согласился с правилами"""
    await callback.answer()
    
    # Удаляем сообщение с предупреждением
    try:
        await callback.message.delete()
    except:
        pass
    
    # Показываем выбор роли
    welcome_text = (
        "🚗 <b>Добро пожаловать в PoputchikBot!</b>\n\n"
        "Сервис поиска попутчиков в Бишкеке:\n"
        "• Дешевле такси\n"
        "• Быстрый поиск\n"
        "• Автоматические уведомления\n\n"
        "<b>Выберите кто вы:</b>"
    )
    
    try:
        await safe_callback_message_edit(
            callback,
            welcome_text,
            parse_mode="HTML",
            reply_markup=get_role_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке приветствия пользователю {callback.from_user.id}: {e}", exc_info=True)
        return
    await state.clear()


@router.callback_query(F.data == "agreement:decline", Agreement.waiting_agreement)
async def decline_agreement(callback: CallbackQuery, state: FSMContext):
    """Пользователь отказался от согласия"""
    await callback.answer("❌ Без согласия доступ к сервису невозможен", show_alert=True)
    
    await callback.message.edit_text(
        "❌ <b>Доступ запрещён</b>\n\n"
        "Для использования сервиса необходимо согласиться с правилами.\n\n"
        "Используйте /start для повторной попытки.",
        parse_mode="HTML"
    )
    await state.clear()


async def show_post_from_channel(message: Message, post_id: int):
    """Показать информацию об объявлении из канала"""
    async def _get_post_info(session):
        # Получаем текущего пользователя
        user_query = select(User).where(User.telegram_id == message.from_user.id)
        user_result = await session.execute(user_query)
        user = user_result.scalars().first()
        
        if not user:
            return None, None, None
        
        # Получаем объявление
        post_query = select(Post).where(Post.id == post_id)
        post_result = await session.execute(post_query)
        post = post_result.scalars().first()
        
        if not post:
            return user, None, None
        
        # Получаем автора
        author_query = select(User).where(User.id == post.author_id)
        author_result = await session.execute(author_query)
        author = author_result.scalar_one_or_none()
        
        return user, post, author
    
    try:
        user, post, author = await retry_on_database_error(_get_post_info)
    except Exception as e:
        logger.error(f"Ошибка при получении данных для поста {post_id}: {e}")
        await message.answer("❌ Не удалось загрузить информацию об объявлении. Попробуйте позже.")
        return
    
    if not user:
        await message.answer(
            "❌ <b>Ошибка</b>\n\n"
            "Пользователь не найден. Пожалуйста, перезапустите бота командой /start",
            parse_mode="HTML"
        )
        return
    
    if not post:
        await message.answer(
            "❌ <b>Объявление не найдено</b>\n\n"
            "Возможно, оно было удалено или истекло.",
            parse_mode="HTML"
        )
        return
    
    if not author:
        await message.answer("❌ Автор объявления не найден.")
        return
    
    # Проверяем, является ли текущий пользователь автором
    is_author = user.id == post.author_id
    
    # Формируем текст
    role_emoji = "🚗" if post.role == "driver" else "🚶"
    role_text = "ВОДИТЕЛЬ" if post.role == "driver" else "ПАССАЖИР"
    seats_line = f"🪑 <b>Мест:</b> {post.seats}\n" if post.seats else ""
    rating_display = f"{float(author.rating):.1f}"
    expires_time = format_local_time(post.expires_at)
    
    if is_author:
        # Для автора - показываем информацию с кнопками управления
        text = (
            f"📋 <b>Ваше объявление</b>\n\n"
            f"{role_emoji} <b>{role_text}</b>\n\n"
            f"📍 <b>Откуда:</b> {post.from_place}\n"
            f"📍 <b>Куда:</b> {post.to_place}\n"
            f"⏰ <b>Время:</b> {post.departure_time or 'Не указано'}\n"
            f"{seats_line}"
            f"💰 <b>Цена:</b> {post.price} сом\n\n"
            f"⏰ <b>Активно до:</b> {expires_time}\n"
            f"📊 <b>Статус:</b> {'Активно' if post.status == 'active' else 'Приостановлено'}"
        )
        
        from handlers.my_posts import get_post_actions_keyboard
        from keyboards import get_back_to_menu_keyboard
        
        if post.status in ["active", "paused"]:
            keyboard = get_post_actions_keyboard(post.id, post.status)
        else:
            keyboard = get_back_to_menu_keyboard()
    else:
        # Для других пользователей - показываем кнопку "Связаться"
        text = (
            f"{role_emoji} <b>{role_text}</b>\n\n"
            f"📍 <b>Откуда:</b> {post.from_place}\n"
            f"📍 <b>Куда:</b> {post.to_place}\n"
            f"⏰ <b>Время:</b> {post.departure_time or 'Не указано'}\n"
            f"{seats_line}"
            f"💰 <b>Цена:</b> {post.price} сом\n"
            f"⭐ <b>Рейтинг:</b> {rating_display}\n\n"
            f"⏰ <b>Активно до:</b> {expires_time}"
        )
        
        from keyboards import get_contact_keyboard, get_back_to_menu_keyboard
        
        # Показываем кнопку "Связаться" только если объявление активно
        if post.status == "active":
            keyboard = get_contact_keyboard(author.phone, author.telegram_id)
        else:
            keyboard = get_back_to_menu_keyboard()
    
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


async def get_main_menu_text(user_name: str, user: User, session) -> Tuple[str, bool]:
    """Получить текст главного меню и информацию о наличии активных объявлений"""
    # Получаем активные объявления
    posts_query = select(Post).where(
        Post.author_id == user.id,
        Post.status.in_(["active", "paused"])
    )
    posts_result = await session.execute(posts_query)
    active_posts = list(posts_result.scalars().all())
    has_active_post = len(active_posts) > 0
    
    # Определяем роль для отображения
    role_text = "🚗 Водитель" if user.role == "driver" else "🚶 Пассажир"
    rating_display = f"{float(user.rating):.1f}"
    
    # Формируем информацию об активных объявлениях
    if has_active_post:
        active_count = len([p for p in active_posts if p.status == "active"])
        paused_count = len([p for p in active_posts if p.status == "paused"])
        
        if active_count > 0 and paused_count > 0:
            posts_info = f"📋 Активные объявления: {active_count} активных, {paused_count} приостановлено"
        elif active_count > 0:
            posts_info = f"📋 Активные объявления: {active_count}"
        else:
            posts_info = f"📋 Активные объявления: {paused_count} приостановлено"
    else:
        posts_info = "📋 Активные объявления: У вас нет активных объявлений"
    
    menu_text = (
        f"🏠 <b>Главное меню</b>\n\n"
        f"Привет, {user_name}!\n"
        f"Роль: {role_text}\n"
        f"⭐ Рейтинг: {rating_display}\n"
        f"{posts_info}"
    )
    
    return menu_text, has_active_post


async def show_main_menu(message: Message, user: User, session):
    """Показать главное меню"""
    menu_text, has_active_post = await get_main_menu_text(message.from_user.first_name, user, session)
    
    try:
        await message.answer(
            menu_text,
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(user.role, has_active_post)
        )
    except TelegramNetworkError as e:
        logger.warning(f"Сетевая ошибка при отправке главного меню пользователю {message.from_user.id}: {e}")
        # Не падаем, просто логируем - aiogram сам переподключится
    except Exception as e:
        logger.error(f"Ошибка при отправке главного меню пользователю {message.from_user.id}: {e}", exc_info=True)




@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Возврат в главное меню через callback"""
    await callback.answer()
    # Очищаем все предыдущие сообщения при возврате в главное меню
    await clean_chat(bot, callback.from_user.id, state)
    await state.clear()
    
    async with get_session() as session:
        query = select(User).where(User.telegram_id == callback.from_user.id)
        result = await session.execute(query)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.message.edit_text(
                "❌ Вы не зарегистрированы. Используйте /start"
            )
            return
        
        menu_text, has_active_post = await get_main_menu_text(callback.from_user.first_name, user, session)
        await callback.message.edit_text(
            menu_text,
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(user.role, has_active_post)
        )


@router.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery):
    """Показать помощь"""
    await callback.answer()
    
    help_text = (
        "❓ <b>Как пользоваться ботом</b>\n\n"
        "🚗 <b>Для водителей:</b>\n"
        "1. Нажмите «Создать объявление»\n"
        "2. Укажите маршрут и время\n"
        "3. Установите цену (макс. 220 сом)\n"
        "4. Ждите откликов!\n\n"
        "🚶 <b>Для пассажиров:</b>\n"
        "1. Нажмите «Создать объявление»\n"
        "2. Укажите маршрут и время\n"
        "3. Установите цену\n"
        "4. Ждите откликов!\n\n"
        "🔔 <b>Подписки:</b>\n"
        "Подпишитесь на маршрут — бот уведомит,\n"
        "когда появится подходящее объявление.\n\n"
        "⏰ Объявления активны <b>60 минут</b>.\n"
        "Можно продлить или создать новое."
    )
    
    from keyboards import get_help_keyboard
    await callback.message.edit_text(
        help_text,
        parse_mode="HTML",
        reply_markup=get_help_keyboard()
    )

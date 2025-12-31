# handlers/post.py - Создание объявлений
# Пошаговое создание с валидацией

from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
import logging

from states import CreatePost
from database.db import get_session
from database.models import User, Post, Subscription, NotificationLog
from services.keys_generator import generate_keys, keys_to_display
from services.channel import publish_to_channel
from services.matching import find_matching_subscriptions, get_users_to_notify, log_notification, find_matching_posts
from tasks.notifications import send_match_notification
from config import MAX_PRICE, POST_LIFETIME_MINUTES
from utils.message_cleaner import add_message_to_delete, clean_chat
from utils.retry_utils import safe_callback_message_edit, retry_on_database_error
from keyboards import (
    get_cancel_keyboard,
    get_back_cancel_keyboard,
    get_seats_keyboard,
    get_post_confirm_keyboard,
    get_after_publish_keyboard,
    get_remove_keyboard,
    get_back_to_menu_keyboard,
    get_main_menu_keyboard,
    get_existing_post_keyboard
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "create_post")
async def start_create_post(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Начало создания объявления - проверка активных объявлений"""
    async def _check_active_post(session):
        # Получаем пользователя
        user_query = select(User).where(User.telegram_id == callback.from_user.id)
        user_result = await session.execute(user_query)
        user = user_result.scalars().first()
        
        if not user:
            return None, None
        
        # Проверяем наличие АКТИВНОГО объявления (приостановленные не блокируют)
        active_post_query = select(Post).where(
            Post.author_id == user.id,
            Post.status == "active"
        )
        active_post_result = await session.execute(active_post_query)
        active_post = active_post_result.scalars().first()
        
        return user, active_post
    
    try:
        user, active_post = await retry_on_database_error(_check_active_post)
    except Exception as e:
        logger.error(f"Ошибка при проверке активного объявления: {e}")
        await callback.answer("❌ Ошибка при проверке объявлений. Попробуйте позже.", show_alert=True)
        return
    
    if not user:
        await callback.message.edit_text(
            "❌ Вы не зарегистрированы. Используйте /start"
        )
        return
    
    if active_post:
        # У пользователя уже есть активное объявление
        # Удаляем предыдущее сообщение
        try:
            await callback.message.delete()
        except:
            pass
        
        # Отправляем новое сообщение с информацией о существующем объявлении
        await callback.message.answer(
            f"⚠️ <b>У вас уже есть активное объявление</b>\n\n"
            f"📍 {active_post.from_place} → {active_post.to_place}\n"
            f"🕐 {active_post.departure_time}\n"
            f"Статус: 🟢 активно\n\n"
            f"Чтобы создать новое объявление, сначала удалите или приостановите текущее.",
            parse_mode="HTML",
            reply_markup=get_existing_post_keyboard(active_post.id, active_post.status)
        )
        return
    
    # Сохраняем данные пользователя в state
    await state.update_data(
        user_id=user.id,
        role=user.role,
        user_phone=user.phone,
        user_rating=str(user.rating)
    )
    
    # Очищаем предыдущие сообщения перед началом нового диалога
    await clean_chat(bot, callback.from_user.id, state)
    await state.update_data(messages_to_delete=[])
    
    # Шаг 1: Откуда
    msg = await callback.message.answer(
        "📍 <b>Создание объявления (1/3)</b>\n\n"
        "Откуда едете?\n"
        "<i>(например: Аламедин базар)</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )
    await add_message_to_delete(state, msg.message_id)
    
    await state.set_state(CreatePost.entering_from)


@router.message(CreatePost.entering_from, F.text)
async def process_from(message: Message, state: FSMContext, bot: Bot):
    """Обработка точки отправления"""
    if message.text == "❌ Отмена":
        await cancel_post_creation(message, state, bot)
        return
    
    # Добавляем сообщение пользователя в список для удаления
    await add_message_to_delete(state, message.message_id)
    
    # Сохраняем
    await state.update_data(from_place=message.text.strip())
    
    # Шаг 2: Куда
    msg = await message.answer(
        "📍 <b>Создание объявления (1/3)</b>\n\n"
        "Куда едете?\n"
        "<i>(например: Дордой)</i>",
        parse_mode="HTML",
        reply_markup=get_back_cancel_keyboard()
    )
    await add_message_to_delete(state, msg.message_id)
    
    await state.set_state(CreatePost.entering_to)


@router.message(CreatePost.entering_to, F.text)
async def process_to(message: Message, state: FSMContext, bot: Bot):
    """Обработка точки назначения"""
    if message.text == "❌ Отмена":
        await cancel_post_creation(message, state, bot)
        return
    
    if message.text == "◀️ Назад":
        await add_message_to_delete(state, message.message_id)
        msg = await message.answer(
            "📍 <b>Создание объявления (1/3)</b>\n\n"
            "Откуда едете?",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )
        await add_message_to_delete(state, msg.message_id)
        await state.set_state(CreatePost.entering_from)
        return
    
    # Добавляем сообщение пользователя в список для удаления
    await add_message_to_delete(state, message.message_id)
    
    # Сохраняем
    await state.update_data(to_place=message.text.strip())
    
    # Шаг 3: Время
    msg = await message.answer(
        "⏰ <b>Создание объявления (2/3)</b>\n\n"
        "Когда выезжаете?\n"
        "<i>(например: сейчас, через 30 минут, в 14:00)</i>",
        parse_mode="HTML",
        reply_markup=get_back_cancel_keyboard()
    )
    await add_message_to_delete(state, msg.message_id)
    
    await state.set_state(CreatePost.entering_time)


@router.message(CreatePost.entering_time, F.text)
async def process_time(message: Message, state: FSMContext, bot: Bot):
    """Обработка времени выезда"""
    if message.text == "❌ Отмена":
        await cancel_post_creation(message, state, bot)
        return
    
    if message.text == "◀️ Назад":
        await add_message_to_delete(state, message.message_id)
        msg = await message.answer(
            "📍 <b>Создание объявления (1/3)</b>\n\n"
            "Куда едете?",
            parse_mode="HTML",
            reply_markup=get_back_cancel_keyboard()
        )
        await add_message_to_delete(state, msg.message_id)
        await state.set_state(CreatePost.entering_to)
        return
    
    # Добавляем сообщение пользователя в список для удаления
    await add_message_to_delete(state, message.message_id)
    
    # Сохраняем время
    await state.update_data(departure_time=message.text.strip())
    
    # Проверяем роль - для водителя спрашиваем места
    data = await state.get_data()
    
    if data["role"] == "driver":
        msg1 = await message.answer(
            "🪑 <b>Создание объявления (2/3)</b>\n\n"
            "Сколько мест?",
            parse_mode="HTML",
            reply_markup=get_remove_keyboard()
        )
        msg2 = await message.answer(
            "Выберите:",
            reply_markup=get_seats_keyboard()
        )
        await add_message_to_delete(state, msg1.message_id)
        await add_message_to_delete(state, msg2.message_id)
        await state.set_state(CreatePost.entering_seats)
    else:
        # Для пассажира сразу к цене
        msg = await message.answer(
            f"💰 <b>Создание объявления (3/3)</b>\n\n"
            f"Укажите цену (максимум {MAX_PRICE} сом):",
            parse_mode="HTML",
            reply_markup=get_back_cancel_keyboard()
        )
        await add_message_to_delete(state, msg.message_id)
        await state.set_state(CreatePost.entering_price)


@router.callback_query(CreatePost.entering_seats, F.data.startswith("seats:"))
async def process_seats(callback: CallbackQuery, state: FSMContext):
    """Обработка количества мест"""
    await callback.answer()
    
    action = callback.data.split(":")[1]
    
    if action == "back":
        try:
            await callback.message.delete()
        except:
            pass
        msg = await callback.message.answer(
            "⏰ <b>Создание объявления (2/3)</b>\n\n"
            "Когда выезжаете?",
            parse_mode="HTML",
            reply_markup=get_back_cancel_keyboard()
        )
        await add_message_to_delete(state, msg.message_id)
        await state.set_state(CreatePost.entering_time)
        return
    
    seats = int(action)
    await state.update_data(seats=seats)
    
    try:
        await callback.message.delete()
    except:
        pass
    
    msg = await callback.message.answer(
        f"💰 <b>Создание объявления (3/3)</b>\n\n"
        f"Укажите цену (максимум {MAX_PRICE} сом):",
        parse_mode="HTML",
        reply_markup=get_back_cancel_keyboard()
    )
    await add_message_to_delete(state, msg.message_id)
    
    await state.set_state(CreatePost.entering_price)


@router.message(CreatePost.entering_price, F.text)
async def process_price(message: Message, state: FSMContext, bot: Bot):
    """Обработка цены"""
    if message.text == "❌ Отмена":
        await cancel_post_creation(message, state, bot)
        return
    
    if message.text == "◀️ Назад":
        await add_message_to_delete(state, message.message_id)
        data = await state.get_data()
        if data["role"] == "driver":
            msg1 = await message.answer(
                "🪑 Сколько мест?",
                reply_markup=get_remove_keyboard()
            )
            msg2 = await message.answer(
                "Выберите:",
                reply_markup=get_seats_keyboard()
            )
            await add_message_to_delete(state, msg1.message_id)
            await add_message_to_delete(state, msg2.message_id)
            await state.set_state(CreatePost.entering_seats)
        else:
            msg = await message.answer(
                "⏰ <b>Создание объявления (2/3)</b>\n\n"
                "Когда выезжаете?",
                parse_mode="HTML",
                reply_markup=get_back_cancel_keyboard()
            )
            await add_message_to_delete(state, msg.message_id)
            await state.set_state(CreatePost.entering_time)
        return
    
    # Валидация цены
    try:
        price = int(message.text.replace(" ", ""))
        if price <= 0 or price > MAX_PRICE:
            raise ValueError()
    except ValueError:
        await add_message_to_delete(state, message.message_id)
        msg = await message.answer(
            f"❌ Укажите корректную цену (от 1 до {MAX_PRICE} сом).",
            reply_markup=get_back_cancel_keyboard()
        )
        await add_message_to_delete(state, msg.message_id)
        return
    
    # Добавляем сообщение пользователя в список для удаления
    await add_message_to_delete(state, message.message_id)
    
    # Сохраняем цену
    await state.update_data(price=price)
    
    # Показываем подтверждение
    await show_post_confirmation(message, state, bot)


async def show_post_confirmation(message: Message, state: FSMContext, bot: Bot):
    """Показать превью объявления"""
    data = await state.get_data()
    
    # Генерируем ключи
    keys_from = generate_keys(data["from_place"])
    keys_to = generate_keys(data["to_place"])
    
    await state.update_data(keys_from=keys_from, keys_to=keys_to)
    
    # Формируем текст
    role_emoji = "🚗" if data["role"] == "driver" else "🚶"
    role_text = "Водитель" if data["role"] == "driver" else "Пассажир"
    seats_line = f"🪑 <b>Мест:</b> {data.get('seats', '—')}\n" if data["role"] == "driver" else ""
    
    confirm_text = (
        f"📋 <b>Проверьте объявление:</b>\n\n"
        f"{role_emoji} <b>КТО:</b> {role_text}\n\n"
        f"📍 <b>Откуда:</b> {data['from_place']}\n"
        f"📍 <b>Куда:</b> {data['to_place']}\n"
        f"⏰ <b>Время:</b> {data['departure_time']}\n"
        f"{seats_line}"
        f"💰 <b>Цена:</b> {data['price']} сом\n\n"
        f"🔑 <b>Ключи маршрута:</b>\n"
        f"{keys_to_display(keys_from)} → {keys_to_display(keys_to)}"
    )
    
    msg1 = await message.answer(
        confirm_text,
        parse_mode="HTML",
        reply_markup=get_remove_keyboard()
    )
    
    msg2 = await message.answer(
        "Всё верно?",
        reply_markup=get_post_confirm_keyboard()
    )
    
    await add_message_to_delete(state, msg1.message_id)
    await add_message_to_delete(state, msg2.message_id)
    
    await state.set_state(CreatePost.confirming)


@router.callback_query(CreatePost.confirming, F.data == "post:publish")
async def publish_post(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Публикация объявления"""
    await callback.answer("Публикую...")
    
    data = await state.get_data()
    
    async with get_session() as session:
        # Создаём объявление
        expires_at = datetime.utcnow() + timedelta(minutes=POST_LIFETIME_MINUTES)
        
        post = Post(
            author_id=data["user_id"],
            role=data["role"],
            from_place=data["from_place"],
            to_place=data["to_place"],
            keys_from=data["keys_from"],
            keys_to=data["keys_to"],
            departure_time=data["departure_time"],
            seats=data.get("seats"),
            price=data["price"],
            expires_at=expires_at
        )
        
        session.add(post)
        await session.flush()  # Получаем ID
        
        # Получаем автора для канала
        author_query = select(User).where(User.id == data["user_id"])
        author_result = await session.execute(author_query)
        author = author_result.scalar_one()
        
        # Публикуем в канал
        channel_msg_id = await publish_to_channel(bot, post, author)
        if channel_msg_id:
            post.channel_message_id = channel_msg_id
        
        await session.commit()
        
        # Ищем совпадения и отправляем уведомления
        matching_user_ids = await find_matching_subscriptions(session, post)
        logger.info(f"Найдено {len(matching_user_ids)} совпадений для поста {post.id}: {matching_user_ids}")
        
        if matching_user_ids:
            users_to_notify = await get_users_to_notify(session, post, matching_user_ids)
            logger.info(f"Пользователей для уведомления: {len(users_to_notify)}")
            
            if users_to_notify:
                for user in users_to_notify:
                    logger.info(f"Отправляю уведомление пользователю {user.telegram_id} (user_id={user.id})")
                    # Отправляем через Celery (message_id будет сохранен внутри задачи)
                    send_match_notification.delay(
                        recipient_telegram_id=user.telegram_id,
                        post_data={
                            "id": post.id,
                            "role": post.role,
                            "from_place": post.from_place,
                            "to_place": post.to_place,
                            "departure_time": post.departure_time,
                            "seats": post.seats,
                            "price": post.price
                        },
                        author_data={
                            "user_id": author.id,
                            "name": callback.from_user.first_name,
                            "rating": str(author.rating),
                            "car_photo_file_id": author.car_photo_file_id if author.car_photo_file_id else None
                        },
                        recipient_db_id=user.id
                    )
                
                logger.info(f"✅ Запланировано отправка {len(users_to_notify)} уведомлений о совпадении")
            else:
                logger.info("Нет пользователей для уведомления (все уже получили уведомления ранее)")
        else:
            logger.info(f"Нет совпадений для поста {post.id}")
        
        # Ищем совпадающие объявления противоположной роли
        matching_posts = await find_matching_posts(session, post)
        logger.info(f"Найдено {len(matching_posts)} совпадающих объявлений для поста {post.id}")
        
        if matching_posts:
            # Получаем авторов совпадающих объявлений
            matching_author_ids = [p.author_id for p in matching_posts]
            authors_query = select(User).where(User.id.in_(matching_author_ids))
            authors_result = await session.execute(authors_query)
            matching_authors = {author.id: author for author in authors_result.scalars().all()}
            
            # Отправляем уведомления авторам совпадающих объявлений
            for matching_post in matching_posts:
                matching_author = matching_authors.get(matching_post.author_id)
                if not matching_author:
                    continue
                
                # Проверяем, не отправляли ли уже уведомление этому пользователю
                already_notified_query = select(NotificationLog).where(
                    NotificationLog.post_id == post.id,
                    NotificationLog.recipient_id == matching_author.id
                )
                already_result = await session.execute(already_notified_query)
                if already_result.scalar_one_or_none():
                    logger.info(f"Пропускаем {matching_author.id} - уже получил уведомление")
                    continue
                
                logger.info(f"Отправляю уведомление автору совпадающего объявления {matching_post.id} (user_id={matching_author.id})")
                
                # Определяем текст в зависимости от роли
                if post.role == "driver":
                    notification_text = "🔔 <b>Найден клиент!</b>"
                else:
                    notification_text = "🔔 <b>Найден водитель!</b>"
                
                send_match_notification.delay(
                    recipient_telegram_id=matching_author.telegram_id,
                    post_data={
                        "id": post.id,
                        "role": post.role,
                        "from_place": post.from_place,
                        "to_place": post.to_place,
                        "departure_time": post.departure_time,
                        "seats": post.seats,
                        "price": post.price
                    },
                    author_data={
                        "user_id": author.id,
                        "name": callback.from_user.first_name,
                        "rating": str(author.rating),
                        "car_photo_file_id": author.car_photo_file_id if author.car_photo_file_id else None
                    },
                    recipient_db_id=matching_author.id
                )
                
                # Также отправляем уведомление автору текущего объявления о совпадающем
                logger.info(f"Отправляю уведомление автору текущего объявления о совпадающем {matching_post.id}")
                
                matching_role_text = "клиент" if matching_post.role == "passenger" else "водитель"
                send_match_notification.delay(
                    recipient_telegram_id=author.telegram_id,
                    post_data={
                        "id": matching_post.id,
                        "role": matching_post.role,
                        "from_place": matching_post.from_place,
                        "to_place": matching_post.to_place,
                        "departure_time": matching_post.departure_time,
                        "seats": matching_post.seats,
                        "price": matching_post.price
                    },
                    author_data={
                        "user_id": matching_author.id,
                        "name": matching_author.phone[:4] + "***" if matching_author.phone else "Пользователь",
                        "rating": str(matching_author.rating),
                        "car_photo_file_id": matching_author.car_photo_file_id if matching_author.car_photo_file_id else None
                    },
                    recipient_db_id=author.id
                )
            
            logger.info(f"✅ Запланировано отправка уведомлений о совпадающих объявлениях для поста {post.id}")
        
        logger.info(f"Объявление {post.id} опубликовано пользователем {callback.from_user.id}")
        
        # Получаем пользователя для главного меню
        user_query = select(User).where(User.id == data["user_id"])
        user_result = await session.execute(user_query)
        user = user_result.scalar_one()
    
    # Очищаем все временные сообщения диалога создания
    await clean_chat(bot, callback.from_user.id, state)
    
    # Очищаем состояние
    await state.clear()
    
    # Показываем главное меню вместо сообщения о публикации
    from handlers.start import get_main_menu_text
    async with get_session() as session:
        menu_text, has_active_post = await get_main_menu_text(callback.from_user.first_name, user, session)
        await callback.message.answer(
            menu_text,
        parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(user.role, has_active_post)
    )


@router.callback_query(CreatePost.confirming, F.data == "post:subscribe")
async def subscribe_to_route(callback: CallbackQuery, state: FSMContext):
    """Подписка на маршрут при создании объявления"""
    await callback.answer()
    
    data = await state.get_data()
    
    async with get_session() as session:
        # Создаём подписку
        subscription = Subscription(
            user_id=data["user_id"],
            keys_from=data["keys_from"],
            keys_to=data["keys_to"],
            from_text=data["from_place"],
            to_text=data["to_place"]
        )
        
        try:
            session.add(subscription)
            await session.commit()
            await callback.answer("✅ Подписка создана!", show_alert=True)
        except:
            await callback.answer("Такая подписка уже существует", show_alert=True)


@router.callback_query(CreatePost.confirming, F.data == "post:edit")
async def edit_post(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Редактирование объявления"""
    await callback.answer()
    
    try:
        await callback.message.delete()
    except:
        pass
    
    # Начинаем сначала
    msg = await callback.message.answer(
        "📍 <b>Создание объявления (1/3)</b>\n\n"
        "Откуда едете?",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )
    await add_message_to_delete(state, msg.message_id)
    
    await state.set_state(CreatePost.entering_from)


@router.callback_query(CreatePost.confirming, F.data == "post:cancel")
async def cancel_post_callback(callback: CallbackQuery, state: FSMContext):
    """Отмена создания через callback"""
    await callback.answer("Отменено")
    await state.clear()
    
    await callback.message.edit_text(
        "Создание объявления отменено.",
        reply_markup=get_back_to_menu_keyboard()
    )


async def cancel_post_creation(message: Message, state: FSMContext, bot: Bot):
    """Отмена создания объявления"""
    # Очищаем все временные сообщения
    await clean_chat(bot, message.chat.id, state)
    await state.clear()
    
    await message.answer(
        "Создание объявления отменено.",
        reply_markup=get_remove_keyboard()
    )
    
    await message.answer(
        "Что дальше?",
        reply_markup=get_back_to_menu_keyboard()
    )


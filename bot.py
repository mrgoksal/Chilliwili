import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
import aiosqlite
import sqlite3
from datetime import datetime, date, timedelta
import json
import aiohttp
from calendar import monthrange
from db import DB_PATH, get_available_times as db_get_available_times, get_setting, get_media_setting, calculate_booking_price, get_price_per_hour, get_price_per_extra_guest, get_max_guests_included, get_all_admin_ids, OPEN_HOUR, CLOSE_HOUR, MAX_BOOKING_DURATION

# Загрузка .env (если установлен python-dotenv)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

API_TOKEN = os.getenv("API_TOKEN")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_USER_ID_ENV = os.getenv("ADMIN_USER_ID")
ADMIN_USER_ID = int(ADMIN_USER_ID_ENV) if ADMIN_USER_ID_ENV and ADMIN_USER_ID_ENV.isdigit() else None

# Состояния пользователей
user_states = {}

# URL вашего веб-приложения (замените на реальный URL)
WEBAPP_URL = "https://628164fc148f.ngrok-free.app/"

def get_db():
    """Синхронное подключение к базе данных"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

async def init_db():
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                phone TEXT,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                created_at TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date TEXT,
                time TEXT,
                guests INTEGER,
                duration INTEGER,
                total_price REAL,
                status TEXT CHECK(status IN ('pending', 'confirmed', 'cancelled')) DEFAULT 'pending',
                created_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS time_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time TEXT UNIQUE,
                is_available BOOLEAN DEFAULT 1
            )
        ''')
        await db.commit()
        
        # Миграция: обновляем NULL значения в поле name
        try:
            await db.execute("UPDATE users SET name = 'Пользователь' WHERE name IS NULL")
            await db.commit()
        except Exception as e:
            print(f"Migration error: {e}")
        
        # Добавляем временные слоты если их нет
        async with db.execute("SELECT COUNT(*) FROM time_slots") as cursor:
            count = (await cursor.fetchone())[0]
            if count == 0:
                # Слоты с 00:00 до 23:00 включительно
                time_slots = []
                for hour in range(0, 24):
                    time_slots.append(f"{hour:02d}:00")
                for time_slot in time_slots:
                    await db.execute("INSERT INTO time_slots (time) VALUES (?)", (time_slot,))
                await db.commit()

async def get_or_create_user(telegram_id: int, username: str = None, name: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                # Если name не передан, используем "Пользователь" как значение по умолчанию
                user_name = name if name else "Пользователь"
                await db.execute(
                    "INSERT INTO users (telegram_id, username, name, phone, created_at) VALUES (?, ?, ?, ?, ?)",
                    (telegram_id, username, user_name, None, datetime.now().isoformat())
                )
                await db.commit()
                # Получаем ID созданного пользователя
                async with db.execute("SELECT last_insert_rowid()") as cursor:
                    user_id = (await cursor.fetchone())[0]
                    return user_id
            else:
                # Пользователь существует - обновляем имя и username, если они None или пустые
                user_id = user[0]
                current_name = user[1] if len(user) > 1 else None
                current_username = user[3] if len(user) > 3 else None
                
                # Обновляем имя, если оно None или пустое
                if (not current_name or current_name == "None" or current_name == "Пользователь") and name:
                    await db.execute("UPDATE users SET name = ? WHERE telegram_id = ?", (name, telegram_id))
                
                # Обновляем username, если он None или пустой
                if (not current_username or current_username == "None") and username:
                    await db.execute("UPDATE users SET username = ? WHERE telegram_id = ?", (username, telegram_id))
                
                await db.commit()
            return user[0]

async def get_available_dates():
    """Получить доступные даты (следующие 7 дней)"""
    dates = []
    for i in range(7):
        date_obj = date.today() + timedelta(days=i)
        dates.append(date_obj.strftime("%Y-%m-%d"))
    return dates


async def create_booking(message: types.Message, date: str, time: str, guests: int, duration: int, booking_name: str = None, booking_phone: str = None):
    """Создание бронирования через бота
    
    Args:
        booking_name: Имя для бронирования (если указано, сохраняется в notes, не перезаписывает имя пользователя)
        booking_phone: Телефон для бронирования (если указано, сохраняется в notes, не перезаписывает телефон пользователя)
    """
    try:
        # Получаем информацию о пользователе
        telegram_id = message.from_user.id
        user_name = message.from_user.full_name
        
        # Создаем или получаем пользователя (без обновления имени и телефона)
        conn = get_db()
        cur = conn.cursor()
        
        # Ищем пользователя по Telegram ID
        cur.execute("SELECT id, name, phone FROM users WHERE telegram_id = ?", (telegram_id,))
        user = cur.fetchone()
        
        if user:
            db_user_id, db_name, db_phone = user
            user_id = db_user_id
        else:
            # Создаем нового пользователя с Telegram именем
            cur.execute(
                "INSERT INTO users (name, phone, telegram_id, created_at) VALUES (?, ?, ?, ?)",
                (user_name, "Не указан", telegram_id, datetime.now().isoformat())
            )
            user_id = cur.lastrowid
        
        # Для notes ВСЕГДА используем данные, которые ввел пользователь (booking_name, booking_phone)
        # Эти данные передаются из handle_phone_input и содержат имя и телефон, которые пользователь ВВЕЛ
        # НЕ используем данные из таблицы users, чтобы каждая бронь была уникальной
        # Если booking_name/booking_phone не указаны (что не должно происходить), используем Telegram профиль как fallback
        display_name = booking_name if booking_name else user_name
        display_phone = booking_phone if booking_phone else "Не указан"
        
        # Расчет стоимости с использованием настроек цен
        total_price = await calculate_booking_price(guests, duration)
        
        # Проверяем доступность времени
        cur.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (date,))
        existing_bookings = cur.fetchall()
        
        # Проверяем бронирования с предыдущего дня, которые могут продолжаться на текущий день
        prev_date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        cur.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (prev_date,))
        prev_day_bookings = cur.fetchall()
        
        # Проверяем пересечения с учетом буфера ДО и ПОСЛЕ (1 час)
        booking_start = datetime.strptime(time, '%H:%M')
        booking_end = booking_start + timedelta(hours=duration)

        if booking_start.hour < OPEN_HOUR or booking_start.hour >= CLOSE_HOUR:
            await message.answer("❌ Забронировать можно только с 10:00 до 22:00. Выберите другое время.")
            conn.close()
            return

        closing_datetime = booking_start.replace(hour=CLOSE_HOUR, minute=0)
        if booking_end > closing_datetime:
            max_hours = CLOSE_HOUR - booking_start.hour
            await message.answer(
                f"❌ Бронирование должно завершаться до {CLOSE_HOUR:02d}:00. "
                f"Для этого времени доступно максимум {max_hours} ч."
            )
            conn.close()
            return
        
        # Проверяем пересечения с бронированиями текущего дня
        for existing_time, existing_duration in existing_bookings:
            exist_start = datetime.strptime(existing_time, '%H:%M')
            exist_end = exist_start + timedelta(hours=existing_duration)
            
            # Проверяем пересечение с учетом зазора ДО (1 час) и ПОСЛЕ (1 час)
            buffer_start = exist_start - timedelta(hours=1)  # Зазор ДО начала брони
            buffer_end = exist_end + timedelta(hours=1)      # Зазор ПОСЛЕ окончания брони
            
            # Проверяем пересечение: новая бронь не должна начинаться в зоне зазора или самой брони
            if booking_start < buffer_end and booking_end > buffer_start:
                reason = "В это время уже есть другое бронирование!"
                await message.answer(f"❌ {reason} Пожалуйста, выберите другое время.")
                conn.close()
                return
        
        # Проверяем пересечения с бронированиями предыдущего дня
        for existing_time, existing_duration in prev_day_bookings:
            exist_start = datetime.strptime(existing_time, '%H:%M')
            exist_start_hour = exist_start.hour
            
            # Проверяем, переходит ли бронирование через полночь
            # Если start_hour + duration >= 24, то бронирование продолжается на следующий день
            if exist_start_hour + existing_duration >= 24:
                # Рассчитываем, до какого часа на следующий день продолжается бронирование
                hours_into_next_day = (exist_start_hour + existing_duration) % 24
                
                # Время окончания бронирования на следующий день
                next_day_end = datetime.strptime(f"{hours_into_next_day:02d}:00", '%H:%M')
                
                # Зазор ПОСЛЕ окончания бронирования (1 час)
                buffer_end = next_day_end + timedelta(hours=1)
                
                # Проверяем пересечение: новая бронь не должна начинаться в зоне брони или зазора
                if booking_start < buffer_end and booking_end > datetime.strptime("00:00", '%H:%M'):
                    reason = "В это время помещение занято бронированием с предыдущего дня!"
                    await message.answer(f"❌ {reason} Пожалуйста, выберите другое время.")
                    conn.close()
                    return
        

        
        # ВСЕГДА формируем notes для бронирования с именем и телефоном, которые ввел пользователь
        # booking_name и booking_phone - это данные, которые пользователь ВВЕЛ для ЭТОГО бронирования
        # Каждое бронирование имеет свои уникальные данные в notes
        notes_parts = []
        # ВАЖНО: Используем booking_name и booking_phone (введенные пользователем), а не display_name/display_phone
        # Это гарантирует, что каждое бронирование сохраняет свои уникальные данные
        final_booking_name = booking_name if booking_name else display_name
        final_booking_phone = booking_phone if booking_phone else display_phone
        notes_parts.append(f"Имя для брони: {final_booking_name}")
        notes_parts.append(f"Телефон для брони: {final_booking_phone}")
        notes = " | ".join(notes_parts)
        print(f"[DEBUG create_booking] Сохраняем notes: '{notes}' (booking_name={booking_name}, booking_phone={booking_phone})")
        
        # Создаем бронирование (ВСЕГДА сохраняем имя и телефон в notes для уникальности каждого бронирования)
        cur.execute(
            "INSERT INTO bookings (user_id, date, time, guests, duration, total_price, status, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (user_id, date, time, guests, duration, total_price, notes, datetime.now().isoformat())
        )
        conn.commit()
        booking_id = cur.lastrowid
        conn.close()
        
        # Формируем информацию о стоимости
        price_per_hour = await get_price_per_hour()
        price_per_extra = await get_price_per_extra_guest()
        max_included = await get_max_guests_included()
        
        price_info = f"💰 Стоимость: {total_price}₽"
        if guests > max_included:
            extra_guests = guests - max_included
            price_info += f"\n   ({price_per_hour}₽/час + {extra_guests}×{price_per_extra}₽ за {extra_guests} гостей сверх {max_included})"
        else:
            price_info += f"\n   ({price_per_hour}₽/час)"
        
        # Уведомляем пользователя о создании бронирования (ожидает подтверждения)
        await message.answer(
            f"⏳ Бронирование создано и ожидает подтверждения!\n\n"
            f"📅 Дата: {date}\n"
            f"🕐 Время: {time}\n"
            f"👥 Гости: {guests}\n"
            f"⏱ Длительность: {duration} ч.\n"
            f"{price_info}\n\n"
            f"🆔 ID брони: {booking_id}\n\n"
            f"📞 Мы свяжемся с вами для подтверждения бронирования!"
        )
        
        # Получаем username пользователя
        username = message.from_user.username
        # Для уведомления админу ВСЕГДА используем имя и телефон, которые ввел пользователь
        # booking_name и booking_phone - это данные, которые пользователь ВВЕЛ для ЭТОГО бронирования
        admin_display_name = booking_name if booking_name else display_name
        admin_display_phone = booking_phone if booking_phone else display_phone
        
        # Получаем username из БД
        cur = get_db().cursor()
        cur.execute("SELECT username FROM users WHERE telegram_id = ?", (telegram_id,))
        user_row = cur.fetchone()
        booking_username = user_row[0] if user_row and user_row[0] else None
        
        # Формируем тег
        if booking_username and booking_username != "None":
            tg_tag = f"@{booking_username}"
        elif username:
            tg_tag = f"@{username}"
        else:
            tg_tag = f"tg://user?id={telegram_id}"
        # Вычисляем время окончания
        start_time = datetime.strptime(time, '%H:%M')
        end_time_obj = start_time + timedelta(hours=duration)
        # Если время переходит через полночь, показываем следующий день
        if end_time_obj.day > start_time.day:
            end_time = f"{end_time_obj.strftime('%H:%M')} (+1 день)"
        else:
            end_time = end_time_obj.strftime('%H:%M')
        # Формируем информацию о стоимости для админа
        admin_price_info = f"💰 Стоимость: {total_price}₽"
        if guests > max_included:
            extra_guests = guests - max_included
            admin_price_info += f" ({price_per_hour}₽/час + {extra_guests}×{price_per_extra}₽ за {extra_guests} гостей сверх {max_included})"
        else:
            admin_price_info += f" ({price_per_hour}₽/час)"
        
        # Уведомляем админа (используем имя и телефон, которые ввел пользователь для ЭТОГО бронирования)
        notification_text = f"🆕 Новая заявка из бота!\n"
        notification_text += f"👤 Имя: {admin_display_name}\n"
        notification_text += f"📞 Телефон: {admin_display_phone}\n"
        notification_text += f"Тег: {tg_tag}\n"
        notification_text += f"🧩 TG ID: {telegram_id}\n"
        notification_text += f"📅 Дата: {date}\n"
        notification_text += f"🕐 Время: {time}\n"
        notification_text += f"⏰ Окончание: {end_time}\n"
        notification_text += f"👥 Гости: {guests}\n"
        notification_text += f"⏱ Длительность: {duration} ч.\n"
        notification_text += f"{admin_price_info}\n"
        notification_text += f"🆔 ID брони: {booking_id}"
        await notify_admin(notification_text)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при создании бронирования: {str(e)}")
        print(f"Error creating booking: {e}")

async def get_user_bookings(user_id: int):
    """Получить бронирования пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT * FROM bookings 
            WHERE user_id = ? AND status != 'cancelled'
            ORDER BY date DESC, time DESC
        """, (user_id,)) as cursor:
            return await cursor.fetchall()

def create_main_menu():
    """Создать главное меню с кнопками"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Забронировать ЧиллиВили!"), KeyboardButton(text="📝 Мои бронирования")],
            [KeyboardButton(text="❌ Отменить бронирование"), KeyboardButton(text="ℹ️ Информация")],
            [KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )
    return keyboard

def create_webapp_keyboard():
    """Создать клавиатуру с кнопкой веб-приложения"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📱 Открыть приложение ЧиллиВили", 
            web_app=WebAppInfo(url=WEBAPP_URL)
        )]
    ])
    return keyboard

# === Дополнительные клавиатуры для выбора месяца и даты ===

def month_name_ru(year: int, month: int) -> str:
    months = [
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]
    return f"{months[month-1]} {year}"

async def create_months_keyboard():
    """Клавиатура выбора месяца (следующие 12 месяцев)."""
    keyboard_rows = []
    today = date.today()
    cur_year, cur_month = today.year, today.month
    buttons = []
    for i in range(12):
        y = cur_year + (cur_month + i - 1) // 12
        m = (cur_month + i - 1) % 12 + 1
        buttons.append(InlineKeyboardButton(text=month_name_ru(y, m), callback_data=f"month_{y:04d}-{m:02d}"))
    for i in range(0, len(buttons), 2):
        keyboard_rows.append(buttons[i:i+2])
    keyboard_rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

async def create_calendar_keyboard(year: int, month: int):
    """Календарь-раскладка дней выбранного месяца."""
    first_weekday, days_in_month = monthrange(year, month)  # Пн=0
    rows = [[
        InlineKeyboardButton(text="Пн", callback_data="noop"),
        InlineKeyboardButton(text="Вт", callback_data="noop"),
        InlineKeyboardButton(text="Ср", callback_data="noop"),
        InlineKeyboardButton(text="Чт", callback_data="noop"),
        InlineKeyboardButton(text="Пт", callback_data="noop"),
        InlineKeyboardButton(text="Сб", callback_data="noop"),
        InlineKeyboardButton(text="Вс", callback_data="noop"),
    ]]
    week = []
    for _ in range(first_weekday):
        week.append(InlineKeyboardButton(text=" ", callback_data="noop"))
    today_d = date.today()
    for d in range(1, days_in_month + 1):
        cur = date(year, month, d)
        label = f"{d:02d}"
        cb = f"date_{cur.strftime('%Y-%m-%d')}" if cur >= today_d else "noop"
        week.append(InlineKeyboardButton(text=label, callback_data=cb))
        if len(week) == 7:
            rows.append(week)
            week = []
    if week:
        while len(week) < 7:
            week.append(InlineKeyboardButton(text=" ", callback_data="noop"))
        rows.append(week)
    rows.append([InlineKeyboardButton(text="⬅️ К месяцам", callback_data="choose_other_date"), InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def create_date_keyboard():
    """Создать клавиатуру с датами"""
    keyboard = []
    dates = await get_available_dates()
    
    for i, date_str in enumerate(dates):
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        display_date = date_obj.strftime("%d.%m")
        day_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][date_obj.weekday()]
        
        keyboard.append([InlineKeyboardButton(
            text=f"{display_date} ({day_name})", 
            callback_data=f"date_{date_str}"
        )])
    # Календарь для выбора других дат
    keyboard.append([InlineKeyboardButton(text="📅 Другая дата", callback_data="choose_other_date")])
    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_time_keyboard(times):
    """Создать клавиатуру со временем"""
    keyboard = []
    for time in times:
        keyboard.append([InlineKeyboardButton(text=time, callback_data=f"time_{time}")])
    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_guests_keyboard():
    """Создать клавиатуру с количеством гостей"""
    keyboard = []
    for i in range(1, 16):  # От 1 до 15 гостей
        keyboard.append([InlineKeyboardButton(text=str(i), callback_data=f"guests_{i}")])
    keyboard.append([InlineKeyboardButton(text="И более", callback_data="guests_more")])
    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_duration_keyboard():
    """Создать клавиатуру с длительностью"""
    max_duration = max(1, MAX_BOOKING_DURATION)
    durations = list(range(1, max_duration + 1))
    keyboard = []
    for duration in durations:
        text = f"{duration} час{'а' if duration in [2,3,4] else 'ов' if duration > 4 else ''}"
        keyboard.append([InlineKeyboardButton(text=text, callback_data=f"duration_{duration}")])
    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_cancel_booking_keyboard(bookings):
    keyboard = []
    for booking in bookings:
        date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
        display_date = date_obj.strftime("%d.%m.%Y")
        time = booking[3]
        booking_id = booking[0]
        btn_text = f"{display_date} {time} (ID: {booking_id})"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"cancel_booking_{booking_id}")])
    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def notify_admin(text):
    """Отправить уведомление всем администраторам"""
    if not ADMIN_BOT_TOKEN:
        print("⚠️ ADMIN_BOT_TOKEN не задан. Отправка уведомления администраторам отключена.")
        return
    
    # Получаем список всех администраторов
    admin_ids = await get_all_admin_ids()
    
    if not admin_ids:
        # Если нет админов в БД, используем ADMIN_USER_ID из переменной окружения (для обратной совместимости)
        if ADMIN_USER_ID:
            admin_ids = [ADMIN_USER_ID]
        else:
            print("⚠️ Нет администраторов для отправки уведомлений.")
            return
    
    url = f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage"
    
    # Отправляем уведомление всем администраторам
    for admin_id in admin_ids:
        payload = {"chat_id": admin_id, "text": text}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=5) as resp:
                    if resp.status != 200:
                        print(f"[admin notify error] Status: {resp.status} for admin {admin_id}, Response: {await resp.text()}")
        except Exception as e:
            print(f"[admin notify error] for admin {admin_id}: {e}")

async def main():
    # Проверка переменных окружения
    if not API_TOKEN:
        raise RuntimeError("Переменная окружения API_TOKEN не задана. Установите токен основного бота.")
    if not ADMIN_BOT_TOKEN:
        print("[warn] ADMIN_BOT_TOKEN не задан. Уведомления администратору через админ-бот могут не работать.")
    if ADMIN_USER_ID is None:
        print("[warn] ADMIN_USER_ID не задан. Сообщения админу отправляться не будут.")

    if not API_TOKEN:
        print("❌ API_TOKEN не установлен. Задайте его в переменных окружения.")
        return
    if not ADMIN_BOT_TOKEN or ADMIN_USER_ID is None:
        print("❌ Административные настройки (ADMIN_BOT_TOKEN, ADMIN_USER_ID) не заданы. Задайте их в переменных окружения.")
        return

    await init_db()
    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(message: types.Message):
        await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name or "Пользователь")
        
        # Получаем приветственный текст из настроек
        welcome_text = await get_setting("welcome_text", f"""
🏠 Добро пожаловать в антикафе «ЧиллиВили»!

Привет, {message.from_user.first_name}! 👋

💸 Наши актуальные цены
— Платишь за время. Всё остальное — уже включено.

В ЧиллиВили не нужно выбирать между капучино и уютом. У нас всё просто: ты платишь только за время, а внутри тебя уже ждут:

✔️ Настольные игры, приставки, уютные зоны
✔️ Wi-Fi и зарядки
✔️ Атмосфера — как дома, только лучше
✔️ Микрофоны что бы покричать караоке

💰 Цены:
🕒 800 ₽ / час до 8 человек
👥 +500 ₽ за каждого человека сверх 8 человек (на всё время пребывания)

❗Минимальное посещение — 1 час
❗Оплата почасовая (всё честно — ты платишь только за то, сколько был)

📍 Часы работы: по договоренности
📍 По всем вопросам поддержка 24/7: @ChilliWiliKirov

Выберите действие из меню ниже:
        """)
        
        # Заменяем плейсхолдер {first_name} на реальное имя
        welcome_text = welcome_text.replace("{first_name}", message.from_user.first_name or "Пользователь")
        
        # Проверяем, есть ли медиа для приветствия
        photo_id = await get_media_setting("welcome", "photo")
        video_id = await get_media_setting("welcome", "video")
        
        # Обрезаем caption если слишком длинный (максимум 1024 символа для Telegram)
        caption = welcome_text[:1024] if len(welcome_text) > 1024 else welcome_text
        
        # Приоритет: видео > фото
        if video_id and video_id.strip():
            try:
                await message.answer_video(video=video_id, caption=caption, reply_markup=create_main_menu())
            except Exception as e:
                print(f"Ошибка при отправке видео: {e}")
                await message.answer(welcome_text, reply_markup=create_main_menu())
        elif photo_id and photo_id.strip():
            try:
                await message.answer_photo(photo=photo_id, caption=caption, reply_markup=create_main_menu())
            except Exception as e:
                print(f"Ошибка при отправке фото: {e}")
                await message.answer(welcome_text, reply_markup=create_main_menu())
        else:
            await message.answer(welcome_text, reply_markup=create_main_menu())

    # @dp.message(F.text == "📱 Открыть приложение")
    # async def handle_webapp_button(message: types.Message):
    #     webapp_text = """
    # 📱 **Приложение ЧиллиВили**
    # 
    # Откройте наше удобное приложение для бронирования столиков!
    # 
    # ✨ **Возможности приложения:**
    # • Быстрое бронирование
    # • Просмотр доступного времени
    # • Управление бронированиями
    # • Красивый интерфейс
    # • Автоматический расчет стоимости
    # 
    # Нажмите кнопку ниже, чтобы открыть приложение:
    #         """
    #         
    #         keyboard = create_webapp_keyboard()
    #         await message.answer(webapp_text, reply_markup=keyboard)

    @dp.message(F.text == "🏠 Забронировать ЧиллиВили!")
    async def handle_book_button(message: types.Message):
        user_id = await get_or_create_user(
            message.from_user.id, 
            message.from_user.username, 
            message.from_user.full_name or "Пользователь"
        )
        # ВСЕГДА создаем новый чистый state для каждого нового бронирования
        # Это гарантирует, что имя и телефон будут запрошены заново
        user_states[message.from_user.id] = {"state": "selecting_date"}
        
        keyboard = await create_date_keyboard()
        await message.answer("📅 Выберите дату для бронирования ЧиллиВили:", reply_markup=keyboard)

    @dp.message(F.text == "📝 Мои бронирования")
    async def handle_my_bookings_button(message: types.Message):
        user_id = await get_or_create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name or "Пользователь"
        )
        bookings = await get_user_bookings(user_id)
        
        if not bookings:
            await message.answer("📝 У вас пока нет активных бронирований")
            return
        
        text = "📝 Ваши бронирования:\n\n"
        for booking in bookings:
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m.%Y")
            guests = booking[4]
            total_price = booking[6]
            
            # Формируем информацию о стоимости
            price_info = f"💰 {total_price} ₽"
            if guests > 8:
                extra_guests = guests - 8
                price_info += f" (800₽/час + {extra_guests}×500₽ за {extra_guests} гостей сверх 8)"
            else:
                price_info += f" (800₽/час)"
            
            text += f"📅 {display_date} в {booking[3]}\n"
            text += f"👥 {guests} гостей\n"
            text += f"⏱ {booking[5]} час{'а' if booking[5] in [2,3,4] else 'ов' if booking[5] > 4 else ''}\n"
            text += f"{price_info}\n"
            text += f"📋 ID: {booking[0]}\n\n"
        
        await message.answer(text)

    @dp.message(F.text == "❌ Отменить бронирование")
    async def handle_cancel_booking_button(message: types.Message):
        user_id = await get_or_create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name or "Пользователь"
        )
        bookings = await get_user_bookings(user_id)
        active_bookings = [b for b in bookings if b[7] != 'cancelled']
        if not active_bookings:
            await message.answer("📝 У вас нет активных бронирований")
            return
        keyboard = create_cancel_booking_keyboard(active_bookings)
        await message.answer("Выберите бронирование для отмены:", reply_markup=keyboard)

    @dp.message(F.text == "ℹ️ Информация")
    async def handle_info_button(message: types.Message):
        # Получаем текст информации из настроек
        info_text = await get_setting("info_text", """
🏠 Антикафе «ЧиллиВили»

📍 По всем вопросам поддержка 24/7: @ChilliWiliKirov
📍 Адрес: ул. Современная, 5
📞 Телефон: +7 (951) 353-44-35
🌐 Сайт: https://vk.com/chilivilivili?from=groups

🕐 Часы работы: по договоренности

💸 Наши актуальные цены
— Платишь за время. Всё остальное — уже включено.

💰 Цены:
🕒 800 ₽ / час до 8 человек
👥 +500 ₽ за каждого человека сверх 8 человек (на всё время пребывания)

❗Минимальное посещение — 1 час
❗Оплата почасовая (всё честно — ты платишь только за то, сколько был)

🛋 А что включено?
✅ Пространство для работы и отдыха
✅ Кино, приставки, настолки
✅ Идеальные условия для душевного вечера, уютного дня или спонтанной встречи
✅ Wi-Fi и зарядки
✅ Атмосфера — как дома, только лучше
✅ Микрофоны что бы покричать караоке

📋 Правила:
• Бронирование за 2 часа
• Отмена за 1 час
• Оплата при входе

Загляни в ЧиллиВили — тут время действительно твоё.
Только бронируй заранее, особенно в выходные 😉
        """)
        
        # Проверяем, есть ли медиа для этого раздела
        photo_id = await get_media_setting("info", "photo")
        video_id = await get_media_setting("info", "video")
        
        # Отладочная информация
        print(f"DEBUG info: photo_id={photo_id[:50] if photo_id else 'None'}..., video_id={video_id[:50] if video_id else 'None'}...")
        
        # Обрезаем caption если слишком длинный (максимум 1024 символа для Telegram)
        caption = info_text[:1024] if len(info_text) > 1024 else info_text
        
        # Приоритет: видео > фото
        if video_id and video_id.strip():
            try:
                print(f"Отправка видео для info: file_id={video_id[:50]}...")
                await message.answer_video(video=video_id, caption=caption)
            except Exception as e:
                print(f"Ошибка при отправке видео: {e}")
                await message.answer(info_text)
        elif photo_id and photo_id.strip():
            try:
                print(f"Отправка фото для info: file_id={photo_id[:50]}...")
                await message.answer_photo(photo=photo_id, caption=caption)
            except Exception as e:
                print(f"Ошибка при отправке фото: {e}, file_id={photo_id[:50] if photo_id else 'None'}")
                await message.answer(info_text)
        else:
            print(f"DEBUG: Медиа не найдено для info (photo_id={photo_id}, video_id={video_id})")
        await message.answer(info_text)

    @dp.message(F.text == "❓ Помощь")
    async def handle_help_button(message: types.Message):
        # Получаем текст помощи из настроек
        help_text = await get_setting("help_text", """
🏠 Антикафе «ЧиллиВили» - справка

💡 Как забронировать:
1. Нажмите "🏠 Забронировать ЧиллиВили!" для быстрого бронирования
2. Выберите дату, время, количество гостей и длительность
3. Получите подтверждение

💸 Наша ценовая политика:
— Платишь за время. Всё остальное — уже включено.

💰 Цены:
🕒 800 ₽ / час до 8 человек
👥 +500 ₽ за каждого человека сверх 8 человек (на всё время пребывания)

🛋 Что включено в стоимость:
✅ Пространство для работы и отдыха
✅ Настольные игры, приставки
✅ Wi-Fi и зарядки
✅ Атмосфера — как дома, только лучше
✅ Микрофоны что бы покричать караоке

📋 Важно:
• Бронирование за 2 часа
• Отмена за 1 час до времени
• Оплата при входе
• Минимум 1 час
• Оплата почасовая 

📍 По всем вопросам поддержка 24/7: @ChilliWiliKirov
        """)
        
        # Проверяем, есть ли медиа для этого раздела
        photo_id = await get_media_setting("help", "photo")
        video_id = await get_media_setting("help", "video")
        
        # Обрезаем caption если слишком длинный (максимум 1024 символа для Telegram)
        caption = help_text[:1024] if len(help_text) > 1024 else help_text
        
        # Приоритет: видео > фото
        if video_id and video_id.strip():
            try:
                await message.answer_video(video=video_id, caption=caption)
            except Exception as e:
                print(f"Ошибка при отправке видео: {e}")
                await message.answer(help_text)
        elif photo_id and photo_id.strip():
            try:
                await message.answer_photo(photo=photo_id, caption=caption)
            except Exception as e:
                print(f"Ошибка при отправке фото: {e}")
                await message.answer(help_text)
        else:
            await message.answer(help_text)

    @dp.callback_query(F.data.regexp(r"^date_"))
    async def handle_date_selection(callback: types.CallbackQuery):
        selected_date = callback.data.split("_")[1]
        user_id = await get_or_create_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name or "Пользователь"
        )
        
        user_states[callback.from_user.id] = {
            "state": "selecting_time",
            "date": selected_date
        }
        
        available_times = await db_get_available_times(selected_date)
        if not available_times:
            await callback.message.edit_text("❌ На выбранную дату нет свободных мест")
            return
        
        keyboard = create_time_keyboard(available_times)
        date_obj = datetime.strptime(selected_date, "%Y-%m-%d")
        display_date = date_obj.strftime("%d.%m.%Y")
        await callback.message.edit_text(f"🕐 Выберите время для {display_date}:", reply_markup=keyboard)

    @dp.callback_query(F.data.regexp(r"^time_"))
    async def handle_time_selection(callback: types.CallbackQuery):
        selected_time = callback.data.split("_")[1]
        user_id = await get_or_create_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name or "Пользователь"
        )
        state = user_states.get(callback.from_user.id)
        
        if not state or state["state"] != "selecting_time":
            await callback.answer("❌ Ошибка")
            return
        
        user_states[callback.from_user.id] = {
            "state": "selecting_guests",
            "date": state["date"],
            "time": selected_time
        }
        
        keyboard = create_guests_keyboard()
        await callback.message.edit_text("👥 Выберите количество гостей:", reply_markup=keyboard)

    @dp.callback_query(F.data.regexp(r"^guests_"))
    async def handle_guests_selection(callback: types.CallbackQuery):
        if callback.data == "guests_more":
            # Обработка кнопки "И более"
            user_id = await get_or_create_user(
                callback.from_user.id,
                callback.from_user.username,
                callback.from_user.full_name or "Пользователь"
            )
            state = user_states.get(callback.from_user.id)
            
            if not state or state["state"] != "selecting_guests":
                await callback.answer("❌ Ошибка")
                return
            
            user_states[callback.from_user.id] = {
                "state": "waiting_for_guests_count",
                "date": state["date"],
                "time": state["time"]
            }
            
            await callback.message.edit_text("👥 Введите количество гостей (число):")
            return
        
        guests = int(callback.data.split("_")[1])
        user_id = await get_or_create_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name or "Пользователь"
        )
        state = user_states.get(callback.from_user.id)
        
        if not state or state["state"] != "selecting_guests":
            await callback.answer("❌ Ошибка")
            return
        
        user_states[callback.from_user.id] = {
            "state": "selecting_duration",
            "date": state["date"],
            "time": state["time"],
            "guests": guests
        }
        
        keyboard = create_duration_keyboard()
        await callback.message.edit_text("⏱ Выберите длительность посещения:", reply_markup=keyboard)

    @dp.callback_query(F.data.regexp(r"^duration_"))
    async def handle_duration_selection(callback: types.CallbackQuery):
        duration = int(callback.data.split("_")[1])
        user_id = await get_or_create_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name or "Пользователь"
        )
        state = user_states.get(callback.from_user.id)
        if not state or state["state"] != "selecting_duration":
            await callback.answer("❌ Ошибка")
            return

        start_time = datetime.strptime(state["time"], "%H:%M")
        max_duration = CLOSE_HOUR - start_time.hour
        if max_duration <= 0:
            await callback.answer("❌ На это время нельзя забронировать.", show_alert=True)
            return
        if duration > max_duration:
            await callback.answer(
                f"❌ Максимальная длительность для {state['time']} — {max_duration} ч.",
                show_alert=True
            )
            return
        # Сохраняем выбранную длительность
        # ВАЖНО: При переходе к вводу имени создаем новый state БЕЗ старых name и phone
        # Это гарантирует, что для каждого нового бронирования имя и телефон будут запрошены заново
        user_states[callback.from_user.id] = {
            "state": "waiting_for_name",
            "date": state["date"],
            "time": state["time"],
            "guests": state["guests"],
            "duration": duration
            # НЕ включаем старые "name" и "phone" - они будут введены заново
        }
        await callback.message.edit_text("✏️ Введите ваше имя для бронирования:")

    @dp.message(lambda message: user_states.get(message.from_user.id, {}).get("state") == "waiting_for_guests_count")
    async def handle_guests_count_input(message: types.Message):
        try:
            guests = int(message.text.strip())
            if guests < 1:
                await message.answer("❌ Количество гостей должно быть больше 0!")
                return
            state = user_states.get(message.from_user.id)
            user_states[message.from_user.id]["guests"] = guests
            user_states[message.from_user.id]["state"] = "selecting_duration"
            
            keyboard = create_duration_keyboard()
            await message.answer("⏱ Выберите длительность посещения:", reply_markup=keyboard)
        except ValueError:
            await message.answer("❌ Пожалуйста, введите корректное число!")

    @dp.message(lambda message: user_states.get(message.from_user.id, {}).get("state") == "waiting_for_name")
    async def handle_name_input(message: types.Message):
        name = message.text.strip()
        if not name:
            await message.answer("❌ Пожалуйста, введите имя!")
            return
        
        # ВАЖНО: ВСЕГДА сохраняем имя, которое пользователь ВВЕЛ для ЭТОГО бронирования
        # Это гарантирует, что каждое бронирование будет иметь свои уникальные данные
        state = user_states.get(message.from_user.id)
        if not state:
            await message.answer("❌ Ошибка: сессия бронирования не найдена. Пожалуйста, начните бронирование заново.")
            return
        
        # Сохраняем имя для ЭТОГО бронирования (перезаписываем старое значение, если оно есть)
        state["name"] = name
        state["state"] = "waiting_for_phone"
        await message.answer("📱 Введите ваш номер телефона:")

    @dp.message(lambda message: user_states.get(message.from_user.id, {}).get("state") == "waiting_for_phone")
    async def handle_phone_input(message: types.Message):
        phone = message.text.strip()
        # Простейшая валидация номера (можно доработать)
        if not phone or len(phone) < 6:
            await message.answer("❌ Пожалуйста, введите корректный номер телефона!")
            return
        
        state = user_states.get(message.from_user.id)
        if not state:
            await message.answer("❌ Ошибка: сессия бронирования не найдена. Пожалуйста, начните бронирование заново.")
            return
        
        # ВАЖНО: ВСЕГДА сохраняем телефон, который пользователь ВВЕЛ для ЭТОГО бронирования
        # Это гарантирует, что каждое бронирование будет иметь свои уникальные данные
        state["phone"] = phone
        # Получаем или создаем пользователя (без обновления имени и телефона)
        user_id = await get_or_create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name or "Пользователь"
        )
        # Сохраняем имя и телефон только для этого бронирования
        # (они будут использованы в create_booking, но не изменят данные пользователя)
        async with aiosqlite.connect(DB_PATH) as db:
            # Обновляем имя и телефон только если они пустые (None или "Пользователь")
            async with db.execute("SELECT name, phone FROM users WHERE telegram_id = ?", (message.from_user.id,)) as cursor:
                user_data = await cursor.fetchone()
                if user_data:
                    current_name, current_phone = user_data
                    # Обновляем только если текущие данные пустые или дефолтные
                    if (not current_name or current_name == "None" or current_name == "Пользователь"):
                        await db.execute("UPDATE users SET name = ? WHERE telegram_id = ?", (state["name"], message.from_user.id))
                    if (not current_phone or current_phone == "None" or current_phone == "Не указан"):
                        await db.execute("UPDATE users SET phone = ? WHERE telegram_id = ?", (phone, message.from_user.id))
            await db.commit()
        # ВАЖНО: ВСЕГДА используем имя и телефон, которые только что ввел пользователь
        # Эти данные будут сохранены в notes для этого конкретного бронирования
        booking_name = state.get("name")  # Имя, которое ввел пользователь
        booking_phone = phone  # Телефон, который ввел пользователь
        
        # Проверяем, что данные есть
        if not booking_name:
            await message.answer("❌ Ошибка: имя не найдено. Пожалуйста, начните бронирование заново.")
            del user_states[message.from_user.id]
            return
        
        if not booking_phone:
            await message.answer("❌ Ошибка: телефон не найден. Пожалуйста, начните бронирование заново.")
            del user_states[message.from_user.id]
            return
        
        # Создаём бронирование с именем и телефоном, которые ввел пользователь
        await create_booking(message, state["date"], state["time"], state["guests"], state["duration"], booking_name, booking_phone)
        
        # Очищаем состояние пользователя после создания бронирования
        del user_states[message.from_user.id]

    @dp.callback_query(F.data == "cancel")
    async def handle_cancel(callback: types.CallbackQuery):
        if callback.from_user.id in user_states:
            del user_states[callback.from_user.id]
        await callback.message.edit_text("❌ Бронирование отменено")

    @dp.callback_query(F.data.regexp(r"^cancel_booking_"))
    async def handle_cancel_booking_callback(callback: types.CallbackQuery):
        booking_id = int(callback.data.split("_")[-1])
        user_id = await get_or_create_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name or "Пользователь"
        )
        
        # Получаем информацию о бронировании перед отменой
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT b.*, u.name, u.phone, u.username, u.telegram_id
                FROM bookings b 
                JOIN users u ON b.user_id = u.id 
                WHERE b.id = ? AND b.user_id = ? AND b.status != 'cancelled'
            """, (booking_id, user_id)) as cursor:
                booking_info = await cursor.fetchone()
                
                if not booking_info:
                    await callback.message.edit_text("❌ Бронирование не найдено или уже отменено")
                    return
                
                # Отменяем бронирование
                async with db.execute("""
                    UPDATE bookings 
                    SET status = 'cancelled' 
                    WHERE id = ? AND user_id = ? AND status != 'cancelled'
                """, (booking_id, user_id)) as cursor:
                    await db.commit()
                    
                    if cursor.rowcount > 0:
                        await callback.message.edit_text("✅ Бронирование отменено!")
                        
                        # Отправляем уведомление админу
                        booking_date = booking_info[2]
                        booking_time = booking_info[3]
                        guests = booking_info[4]
                        duration = booking_info[5]
                        total_price = booking_info[6]
                        user_name = booking_info[7]
                        user_phone = booking_info[8]
                        user_username = booking_info[9]
                        user_telegram_id = booking_info[10]
                        
                        # Формируем тег пользователя
                        if user_username:
                            tg_tag = f"@{user_username}"
                        else:
                            tg_tag = f"tg://user?id={user_telegram_id}"
                        
                        # Формируем информацию о стоимости
                        admin_price_info = f"💰 Стоимость: {total_price}₽"
                        if guests > 8:
                            extra_guests = guests - 8
                            admin_price_info += f" (800₽/час + {extra_guests}×500₽ за {extra_guests} гостей сверх 8)"
                        else:
                            admin_price_info += f" (800₽/час)"
                        
                        # Вычисляем время окончания
                        start_time = datetime.strptime(booking_time, '%H:%M')
                        end_time_obj = start_time + timedelta(hours=duration)
                        if end_time_obj.day > start_time.day:
                            end_time = f"{end_time_obj.strftime('%H:%M')} (+1 день)"
                        else:
                            end_time = end_time_obj.strftime('%H:%M')
                        
                        await notify_admin(
                            f"❌ **Бронирование отменено пользователем!**\n\n"
                            f"👤 Имя: {user_name}\n"
                            f"📞 Телефон: {user_phone}\n"
                            f"Тег: {tg_tag}\n"
                            f"🧩 TG ID: {user_telegram_id}\n"
                            f"📅 Дата: {booking_date}\n"
                            f"🕐 Время: {booking_time}\n"
                            f"⏰ Окончание: {end_time}\n"
                            f"👥 Гости: {guests}\n"
                            f"⏱ Длительность: {duration} ч.\n"
                            f"{admin_price_info}\n"
                            f"🆔 ID брони: {booking_id}"
                        )
                    else:
                        await callback.message.edit_text("❌ Бронирование не найдено или уже отменено")

    @dp.callback_query(F.data == "choose_other_date")
    async def handle_choose_other_date(callback: types.CallbackQuery):
        months_kb = await create_months_keyboard()
        await callback.message.edit_text("🗓 Выберите месяц:", reply_markup=months_kb)

    @dp.callback_query(F.data.regexp(r"^month_\d{4}-\d{2}$"))
    async def handle_month_select(callback: types.CallbackQuery):
        _, ym = callback.data.split("_")
        year, month = map(int, ym.split("-"))
        cal_kb = await create_calendar_keyboard(year, month)
        title = month_name_ru(year, month)
        await callback.message.edit_text(f"🗓 {title}\nВыберите дату:", reply_markup=cal_kb)

    @dp.callback_query(F.data == "noop")
    async def handle_noop(callback: types.CallbackQuery):
        await callback.answer()

    # Обработка команд для совместимости
    @dp.message(Command("book"))
    async def cmd_book(message: types.Message):
        await handle_book_button(message)

    @dp.message(Command("my_bookings"))
    async def cmd_my_bookings(message: types.Message):
        await handle_my_bookings_button(message)

    @dp.message(Command("cancel_booking"))
    async def cmd_cancel_booking(message: types.Message):
        await handle_cancel_booking_button(message)

    @dp.message(Command("info"))
    async def cmd_info(message: types.Message):
        await handle_info_button(message)

    @dp.message(Command("help"))
    async def cmd_help(message: types.Message):
        await handle_help_button(message)

    # @dp.message(Command("webapp"))
    # async def cmd_webapp(message: types.Message):
    #     await handle_webapp_button(message)

    print("🏠 Антикафе «ЧиллиВили» - бот запущен!")
    print("✅ Система бронирования готова к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
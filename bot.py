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
    conn = sqlite3.connect('chillivili.db')
    conn.row_factory = sqlite3.Row
    return conn

async def init_db():
    async with aiosqlite.connect("chillivili.db") as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                name TEXT,
                phone TEXT,
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
        
        # Добавляем временные слоты если их нет
        async with db.execute("SELECT COUNT(*) FROM time_slots") as cursor:
            count = (await cursor.fetchone())[0]
            if count == 0:
                # Слоты с 11:00 до 23:00 включительно
                time_slots = []
                for hour in range(11, 24):
                    time_slots.append(f"{hour:02d}:00")
                for time_slot in time_slots:
                    await db.execute("INSERT INTO time_slots (time) VALUES (?)", (time_slot,))
                await db.commit()

async def get_or_create_user(telegram_id: int, username: str = None, name: str = None):
    async with aiosqlite.connect("chillivili.db") as db:
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute(
                    "INSERT INTO users (telegram_id, username, name, created_at) VALUES (?, ?, ?, ?)",
                    (telegram_id, username, name, datetime.now().isoformat())
                )
                await db.commit()
                return await db.execute("SELECT last_insert_rowid()")
            return user[0]

async def get_available_dates():
    """Получить доступные даты (следующие 7 дней)"""
    dates = []
    for i in range(7):
        date_obj = date.today() + timedelta(days=i)
        dates.append(date_obj.strftime("%Y-%m-%d"))
    return dates

async def get_available_times(selected_date: str):
    """Получить доступные временные слоты для выбранной даты"""
    async with aiosqlite.connect("chillivili.db") as db:
        # Получаем все временные слоты
        async with db.execute("SELECT time FROM time_slots ORDER BY time") as cursor:
            all_times = [row[0] for row in await cursor.fetchall()]
        
        # Получаем забронированные времена с длительностью для выбранной даты
        async with db.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (selected_date,)) as cursor:
            existing_bookings = await cursor.fetchall()
        
        # Создаем множество заблокированных временных слотов (час ДО, сама бронь, час ПОСЛЕ)
        blocked_times = set()
        for booking_time, booking_duration in existing_bookings:
            start_time = datetime.strptime(booking_time, '%H:%M')
            # Блокируем с (start - 1ч) до (end + 1ч) невключительно
            for i in range(-1, booking_duration + 1):
                blocked_time = start_time + timedelta(hours=i)
                blocked_times.add(blocked_time.strftime('%H:%M'))
        
        # Фильтруем слоты по правилу "бронь не раньше чем за 1 час" для сегодняшней даты
        available = [time for time in all_times if time not in blocked_times]
        today_str = date.today().strftime("%Y-%m-%d")
        if selected_date == today_str:
            now = datetime.now()
            base = now.replace(minute=0, second=0, microsecond=0)
            # Если ровно на час (минуты == 0), ближайший слот допускается только через 1 час
            # Если уже идут минуты, ближайший допустимый полный час + ещё 1 час, чтобы соблюсти правило "за час"
            earliest_dt = base + timedelta(hours=1 if now.minute == 0 else 2)
            cutoff_str = earliest_dt.strftime('%H:%M')
            available = [t for t in available if t >= cutoff_str]
        
        return available

async def create_booking(message: types.Message, date: str, time: str, guests: int, duration: int):
    """Создание бронирования через бота"""
    try:
        # Получаем информацию о пользователе
        user_id = message.from_user.id
        user_name = message.from_user.full_name
        user_phone = "Не указан"  # В боте нет поля для телефона
        
        # Создаем или получаем пользователя
        conn = get_db()
        cur = conn.cursor()
        
        # Ищем пользователя по Telegram ID
        cur.execute("SELECT id, name, phone FROM users WHERE telegram_id = ?", (user_id,))
        user = cur.fetchone()
        
        if user:
            db_user_id, db_name, db_phone = user
            # Не обновляем имя из Telegram, используем то, что сохранено в боте
            user_id = db_user_id
        else:
            # Создаем нового пользователя (временно ставим Telegram имя, позже будет обновлено введенным)
            cur.execute(
                "INSERT INTO users (name, phone, telegram_id, created_at) VALUES (?, ?, ?, ?)",
                (user_name, user_phone, message.from_user.id, datetime.now().isoformat())
            )
            user_id = cur.lastrowid
        
        # Расчет стоимости по новой системе (цена за время, а не за гостей)
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        is_weekend = date_obj.weekday() >= 5  # Суббота и воскресенье
        
        # Определяем тариф в зависимости от времени и дня недели
        time_obj = datetime.strptime(time, "%H:%M")
        hour = time_obj.hour
        
        # Рассчитываем стоимость по часам с учетом разных тарифов
        total_price = 0
        current_time = time_obj
        
        for i in range(duration):
            current_hour = current_time.hour
            
            # Определяем тариф для текущего часа
            if current_hour >= 23:
                # После 23:00 фиксированная цена
                price_per_hour = 1500
            elif is_weekend:
                # Выходные и праздники
                if 11 <= current_hour < 18:
                    price_per_hour = 1000
                else:  # 18:00 - 22:59
                    price_per_hour = 1300
            else:
                # Будни
                if 11 <= current_hour < 18:
                    price_per_hour = 800
                else:  # 18:00 - 22:59
                    price_per_hour = 1000
            
            total_price += price_per_hour
            current_time += timedelta(hours=1)
        
        # Проверяем доступность времени
        cur.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (date,))
        existing_bookings = cur.fetchall()
        
        # Проверяем пересечения с учетом буфера ДО и ПОСЛЕ (1 час)
        booking_start = datetime.strptime(time, '%H:%M')
        booking_end = booking_start + timedelta(hours=duration)
        for existing_time, existing_duration in existing_bookings:
            exist_start = datetime.strptime(existing_time, '%H:%M')
            exist_end = exist_start + timedelta(hours=existing_duration)
            # Диапазон с буфером: [exist_start - 1ч, exist_end + 1ч)
            buffer_start = exist_start - timedelta(hours=1)
            buffer_end = exist_end + timedelta(hours=1)
            # Пересечение?
            if booking_start < buffer_end and booking_end > buffer_start:
                # Определяем причину
                if booking_start < exist_end and booking_end > exist_start:
                    reason = "В это время уже есть другое бронирование!"
                elif booking_start < exist_start and booking_end > buffer_start and booking_end <= exist_start:
                    reason = "Требуется 1 час на уборку перед следующим бронированием!"
                elif booking_start >= exist_end and booking_start < buffer_end:
                    reason = "Требуется 1 час на уборку после предыдущего бронирования!"
                else:
                    reason = "Время занято или требуется уборка между бронированиями!"
                await message.answer(f"❌ {reason} Пожалуйста, выберите другое время.")
                conn.close()
                return
        

        
        # Создаем бронирование
        cur.execute(
            "INSERT INTO bookings (user_id, date, time, guests, duration, total_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (user_id, date, time, guests, duration, total_price, datetime.now().isoformat())
        )
        conn.commit()
        booking_id = cur.lastrowid
        conn.close()
        
        # Уведомляем пользователя о создании бронирования (ожидает подтверждения)
        await message.answer(
            f"⏳ **Бронирование создано и ожидает подтверждения!**\n\n"
            f"📅 Дата: {date}\n"
            f"🕐 Время: {time}\n"
            f"👥 Гости: {guests}\n"
            f"⏱ Длительность: {duration} ч.\n"
            f"💰 Стоимость: {total_price}₽ (за время)\n\n"
            f"🆔 ID брони: {booking_id}\n\n"
            f"📞 Мы свяжемся с вами для подтверждения бронирования!"
        )
        
        # Получаем username пользователя
        username = message.from_user.username
        # Получаем имя и телефон из БД
        cur = get_db().cursor()
        cur.execute("SELECT name, phone, username FROM users WHERE telegram_id = ?", (message.from_user.id,))
        user_row = cur.fetchone()
        if user_row:
            booking_name, booking_phone, booking_username = user_row
        else:
            booking_name, booking_phone, booking_username = user_name, 'Не указан', None
        # Формируем тег
        if booking_username:
            tg_tag = f"@{booking_username}"
        elif username:
            tg_tag = f"@{username}"
        else:
            tg_tag = f"tg://user?id={message.from_user.id}"
        # Вычисляем время окончания
        end_time = (datetime.strptime(time, '%H:%M') + timedelta(hours=duration)).strftime('%H:%M')
        # Уведомляем админа
        await notify_admin(
            f"🆕 Новая заявка из бота!\n"
            f"👤 Имя: {booking_name}\n"
            f"📞 Телефон: {booking_phone}\n"
            f"Тег: {tg_tag}\n"
            f"🧩 TG ID: {message.from_user.id}\n"
            f"📅 Дата: {date}\n"
            f"🕐 Время: {time}\n"
            f"⏰ Окончание: {end_time}\n"
            f"👥 Гости: {guests}\n"
            f"⏱ Длительность: {duration} ч.\n"
            f"💰 Стоимость: {total_price}₽ (за время)\n"
            f"🆔 ID брони: {booking_id}")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при создании бронирования: {str(e)}")
        print(f"Error creating booking: {e}")

async def get_user_bookings(user_id: int):
    """Получить бронирования пользователя"""
    async with aiosqlite.connect("chillivili.db") as db:
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
    for i in range(1, 11):  # От 1 до 10 гостей
        keyboard.append([InlineKeyboardButton(text=str(i), callback_data=f"guests_{i}")])
    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_duration_keyboard():
    """Создать клавиатуру с длительностью"""
    durations = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]  # От 1 до 12 часов
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
    if not ADMIN_BOT_TOKEN or not ADMIN_USER_ID:
        print("⚠️ Административные настройки не заданы. Отправка уведомления администратору отключена.")
        return
    url = f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_USER_ID, "text": text}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=5) as resp:
                if resp.status != 200:
                    print(f"[admin notify error] Status: {resp.status}, Response: {await resp.text()}")
    except Exception as e:
        print(f"[admin notify error] {e}")

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
        await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
        
        welcome_text = f"""
🏠 Добро пожаловать в антикафе «ЧиллиВили»!

Привет, {message.from_user.first_name}! 👋

💸 Наши актуальные цены
— Платишь за время. Всё остальное — уже включено.

В ЧиллиВили не нужно выбирать между капучино и уютом. У нас всё просто: ты платишь только за время, а внутри тебя уже ждут:

✔️ Чай, кофе, вода
✔️ Печеньки и лёгкие снеки  
✔️ Настольные игры, приставки, уютные зоны
✔️ Wi-Fi и зарядки
✔️ Атмосфера — как дома, только лучше

🗓 Будни (Пн–Пт)
🕒 С 11:00 до 18:00 — 800 ₽ / час
🕔 С 18:00 до 23:00 — 1000 ₽ / час
🌙 После 23:00 — 1500 ₽ / час

🏖 Выходные и праздники
🕒 С 11:00 до 18:00 — 1000 ₽ / час
🕔 С 18:00 до 23:00 — 1300 ₽ / час
🌙 После 23:00 — 1500 ₽ / час

❗Минимальное посещение — 1 час
❗Оплата почасовая (всё честно — ты платишь только за то, сколько был)

📍 Мы работаем каждый день с 11:00 до 23:00 (бронирования могут заканчиваться позже)

Выберите действие из меню ниже:
        """
        
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
        user_id = await get_or_create_user(message.from_user.id)
        user_states[message.from_user.id] = {"state": "selecting_date"}
        
        keyboard = await create_date_keyboard()
        await message.answer("📅 Выберите дату для бронирования ЧиллиВили:", reply_markup=keyboard)

    @dp.message(F.text == "📝 Мои бронирования")
    async def handle_my_bookings_button(message: types.Message):
        user_id = await get_or_create_user(message.from_user.id)
        bookings = await get_user_bookings(user_id)
        
        if not bookings:
            await message.answer("📝 У вас пока нет активных бронирований")
            return
        
        text = "📝 Ваши бронирования:\n\n"
        for booking in bookings:
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m.%Y")
            text += f"📅 {display_date} в {booking[3]}\n"
            text += f"👥 {booking[4]} гостей\n"
            text += f"⏱ {booking[5]} час{'а' if booking[5] in [2,3,4] else 'ов' if booking[5] > 4 else ''}\n"
            text += f"💰 {booking[6]} ₽ (за время)\n"
            text += f"📋 ID: {booking[0]}\n\n"
        
        await message.answer(text)

    @dp.message(F.text == "❌ Отменить бронирование")
    async def handle_cancel_booking_button(message: types.Message):
        user_id = await get_or_create_user(message.from_user.id)
        bookings = await get_user_bookings(user_id)
        active_bookings = [b for b in bookings if b[7] != 'cancelled']
        if not active_bookings:
            await message.answer("📝 У вас нет активных бронирований")
            return
        keyboard = create_cancel_booking_keyboard(active_bookings)
        await message.answer("Выберите бронирование для отмены:", reply_markup=keyboard)

    @dp.message(F.text == "ℹ️ Информация")
    async def handle_info_button(message: types.Message):
        info_text = """
🏠 Антикафе «ЧиллиВили»

📍 Адрес: ул. Современная, 5
📞 Телефон: +7 (951) 353-44-35
🌐 Сайт: https://vk.com/chilivilivili?from=groups

🕐 Часы работы:
Каждый день с 11:00 до 23:00

💸 Наши актуальные цены
— Платишь за время. Всё остальное — уже включено.

🗓 Будни (Пн–Пт)
🕒 С 11:00 до 18:00 — 800 ₽ / час
🕔 С 18:00 до 23:00 — 1000 ₽ / час
🌙 После 23:00 — 1500 ₽ / час

🏖 Выходные и праздники
🕒 С 11:00 до 18:00 — 1000 ₽ / час
🕔 С 18:00 до 23:00 — 1300 ₽ / час
🌙 После 23:00 — 1500 ₽ / час

❗Минимальное посещение — 1 час
❗Оплата почасовая (всё честно — ты платишь только за то, сколько был)

🛋 А что включено?
✅ Пространство для работы и отдыха
✅ Кино, приставки, настолки
✅ Идеальные условия для душевного вечера, уютного дня или спонтанной встречи
✅ Чай, кофе, вода
✅ Печеньки и лёгкие снеки
✅ Wi-Fi и зарядки
✅ Атмосфера — как дома, только лучше

📋 Правила:
• Бронирование за 2 часа
• Отмена за 1 час
• Оплата при входе
• Максимум 12 часов

📍 Мы работаем каждый день с 11:00 до 23:00 (бронирования могут заканчиваться позже)

Загляни в ЧиллиВили — тут время действительно твоё.
Только бронируй заранее, особенно в выходные 😉
        """
        await message.answer(info_text)

    @dp.message(F.text == "❓ Помощь")
    async def handle_help_button(message: types.Message):
        help_text = """
🏠 Антикафе «ЧиллиВили» - справка

💡 Как забронировать:
1. Нажмите "🏠 Забронировать ЧиллиВили!" для быстрого бронирования
2. Выберите дату, время, количество гостей и длительность
3. Получите подтверждение

💸 Наша ценовая политика:
— Платишь за время. Всё остальное — уже включено.

🗓 Будни (Пн–Пт)
🕒 С 11:00 до 18:00 — 800 ₽ / час
🕔 С 18:00 до 23:00 — 1000 ₽ / час
🌙 После 23:00 — 1500 ₽ / час

🏖 Выходные и праздники
🕒 С 11:00 до 18:00 — 1000 ₽ / час
🕔 С 18:00 до 23:00 — 1300 ₽ / час
🌙 После 23:00 — 1500 ₽ / час

🛋 Что включено в стоимость:
✅ Пространство для работы и отдыха
✅ Чай, кофе, вода
✅ Печеньки и лёгкие снеки
✅ Настольные игры, приставки
✅ Wi-Fi и зарядки
✅ Атмосфера — как дома, только лучше

📋 Важно:
• Бронирование за 2 часа
• Отмена за 1 час до времени
• Оплата при входе
• Минимум 1 час, максимум 12 часов
• Оплата почасовая (за время, а не за гостей)

❓ Остались вопросы? Звоните: +7 (951) 353-44-35
        """
        await message.answer(help_text)

    @dp.callback_query(F.data.regexp(r"^date_"))
    async def handle_date_selection(callback: types.CallbackQuery):
        selected_date = callback.data.split("_")[1]
        user_id = await get_or_create_user(callback.from_user.id)
        
        user_states[callback.from_user.id] = {
            "state": "selecting_time",
            "date": selected_date
        }
        
        available_times = await get_available_times(selected_date)
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
        user_id = await get_or_create_user(callback.from_user.id)
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
        guests = int(callback.data.split("_")[1])
        user_id = await get_or_create_user(callback.from_user.id)
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
        user_id = await get_or_create_user(callback.from_user.id)
        state = user_states.get(callback.from_user.id)
        if not state or state["state"] != "selecting_duration":
            await callback.answer("❌ Ошибка")
            return
        # Сохраняем выбранную длительность
        user_states[callback.from_user.id] = {
            "state": "waiting_for_name",
            "date": state["date"],
            "time": state["time"],
            "guests": state["guests"],
            "duration": duration
        }
        await callback.message.edit_text("✏️ Введите ваше имя для бронирования:")

    @dp.message(lambda message: user_states.get(message.from_user.id, {}).get("state") == "waiting_for_name")
    async def handle_name_input(message: types.Message):
        name = message.text.strip()
        if not name:
            await message.answer("❌ Пожалуйста, введите имя!")
            return
        state = user_states.get(message.from_user.id)
        user_states[message.from_user.id]["name"] = name
        user_states[message.from_user.id]["state"] = "waiting_for_phone"
        await message.answer("📱 Введите ваш номер телефона:")

    @dp.message(lambda message: user_states.get(message.from_user.id, {}).get("state") == "waiting_for_phone")
    async def handle_phone_input(message: types.Message):
        phone = message.text.strip()
        # Простейшая валидация номера (можно доработать)
        if not phone or len(phone) < 6:
            await message.answer("❌ Пожалуйста, введите корректный номер телефона!")
            return
        state = user_states.get(message.from_user.id)
        # Сохраняем имя и телефон в БД пользователя
        user_id = await get_or_create_user(message.from_user.id, name=state["name"])
        async with aiosqlite.connect("chillivili.db") as db:
            await db.execute("UPDATE users SET name = ?, phone = ? WHERE telegram_id = ?", (state["name"], phone, message.from_user.id))
            await db.commit()
        # Создаём бронирование
        await create_booking(message, state["date"], state["time"], state["guests"], state["duration"])
        
        # Очищаем состояние пользователя
        del user_states[message.from_user.id]

    @dp.callback_query(F.data == "cancel")
    async def handle_cancel(callback: types.CallbackQuery):
        if callback.from_user.id in user_states:
            del user_states[callback.from_user.id]
        await callback.message.edit_text("❌ Бронирование отменено")

    @dp.callback_query(F.data.regexp(r"^cancel_booking_"))
    async def handle_cancel_booking_callback(callback: types.CallbackQuery):
        booking_id = int(callback.data.split("_")[-1])
        user_id = await get_or_create_user(callback.from_user.id)
        async with aiosqlite.connect("chillivili.db") as db:
            async with db.execute("""
                UPDATE bookings 
                SET status = 'cancelled' 
                WHERE id = ? AND user_id = ? AND status != 'cancelled'
            """, (booking_id, user_id)) as cursor:
                await db.commit()
                if cursor.rowcount > 0:
                    await callback.message.edit_text("✅ Бронирование отменено!")
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
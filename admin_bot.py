import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import aiosqlite
import sqlite3
from datetime import datetime, date, timedelta
import json
import aiohttp

# Загрузка .env (если установлен python-dotenv)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# Токены и настройки
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
MAIN_BOT_TOKEN = os.getenv("API_TOKEN")
ADMIN_USER_ID_ENV = os.getenv("ADMIN_USER_ID")
ADMIN_USER_ID = int(ADMIN_USER_ID_ENV) if ADMIN_USER_ID_ENV and ADMIN_USER_ID_ENV.isdigit() else None

# Проверка переменных окружения
if not ADMIN_BOT_TOKEN:
    raise RuntimeError("Переменная окружения ADMIN_BOT_TOKEN не задана. Установите токен админ-бота.")
if not MAIN_BOT_TOKEN:
    print("[warn] API_TOKEN (MAIN_BOT_TOKEN) не задан. Отправка сообщений пользователям через основной бот не будет работать.")
if ADMIN_USER_ID is None:
    print("[warn] ADMIN_USER_ID не задан. Проверка доступа супер-админа может не работать.")

# Состояния админа
admin_states = {}

def get_db():
    """Синхронное подключение к базе данных"""
    conn = sqlite3.connect('chillivili.db')
    conn.row_factory = sqlite3.Row
    return conn

async def notify_user(user_id, text):
    """Уведомить пользователя через основной бот"""
    url = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": user_id, "text": text}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=5) as resp:
                if resp.status != 200:
                    print(f"[user notify error] Status: {resp.status}, Response: {await resp.text()}")
    except Exception as e:
        print(f"[user notify error] {e}")

def create_admin_menu():
    """Создать главное меню админа"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📅 Бронирования сегодня")],
            [KeyboardButton(text="📋 Все бронирования"), KeyboardButton(text="🔍 Найти бронирование")],
            [KeyboardButton(text="✅ Подтвердить бронирование"), KeyboardButton(text="❌ Отменить бронирование")],
            [KeyboardButton(text="✏️ Редактировать бронирование"), KeyboardButton(text="🗑 Удалить бронирование")],
            [KeyboardButton(text="📱 Уведомить пользователя"), KeyboardButton(text="⚙️ Настройки")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )
    return keyboard

async def get_today_bookings():
    """Получить бронирования на сегодня"""
    today = date.today().strftime("%Y-%m-%d")
    async with aiosqlite.connect("chillivili.db") as db:
        async with db.execute("""
            SELECT b.*, u.name, u.phone, u.telegram_id 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            WHERE b.date = ? AND b.status != 'cancelled'
            ORDER BY b.time
        """, (today,)) as cursor:
            return await cursor.fetchall()

async def get_all_bookings(limit=50):
    """Получить все бронирования"""
    async with aiosqlite.connect("chillivili.db") as db:
        async with db.execute("""
            SELECT b.*, u.name, u.phone, u.telegram_id, u.username 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            WHERE b.status != 'cancelled'
            ORDER BY b.date DESC, b.time DESC
            LIMIT ?
        """, (limit,)) as cursor:
            return await cursor.fetchall()

async def get_booking_by_id(booking_id):
    """Получить бронирование по ID"""
    async with aiosqlite.connect("chillivili.db") as db:
        async with db.execute("""
            SELECT b.*, u.name, u.phone, u.telegram_id, u.username 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            WHERE b.id = ?
        """, (booking_id,)) as cursor:
            return await cursor.fetchone()

async def get_statistics():
    """Получить статистику"""
    async with aiosqlite.connect("chillivili.db") as db:
        # Общая статистика
        async with db.execute("SELECT COUNT(*) FROM bookings WHERE status != 'cancelled'") as cursor:
            total_bookings = (await cursor.fetchone())[0]
        
        # Сегодняшние бронирования
        today = date.today().strftime("%Y-%m-%d")
        async with db.execute("SELECT COUNT(*) FROM bookings WHERE date = ? AND status != 'cancelled'", (today,)) as cursor:
            today_bookings = (await cursor.fetchone())[0]
        
        # Завтрашние бронирования
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        async with db.execute("SELECT COUNT(*) FROM bookings WHERE date = ? AND status != 'cancelled'", (tomorrow,)) as cursor:
            tomorrow_bookings = (await cursor.fetchone())[0]
        
        # Общая выручка
        async with db.execute("SELECT SUM(total_price) FROM bookings WHERE status != 'cancelled'") as cursor:
            total_revenue = (await cursor.fetchone())[0] or 0
        
        # Выручка за сегодня
        async with db.execute("SELECT SUM(total_price) FROM bookings WHERE date = ? AND status != 'cancelled'", (today,)) as cursor:
            today_revenue = (await cursor.fetchone())[0] or 0
        
        return {
            'total_bookings': total_bookings,
            'today_bookings': today_bookings,
            'tomorrow_bookings': tomorrow_bookings,
            'total_revenue': total_revenue,
            'today_revenue': today_revenue
        }

def format_booking_info(booking):
    date_str = datetime.strptime(booking[2], "%Y-%m-%d").strftime("%d.%m.%Y")
    end_time = (datetime.strptime(booking[3], "%H:%M") + timedelta(hours=booking[5])).strftime("%H:%M")
    username = booking[12]
    tg_link = f"@{username}" if username else f"tg://user?id={booking[11]}"
    return (
        f"📅 Дата: {date_str}\n"
        f"🕐 Время: {booking[3]}\n"
        f"⏰ Окончание: {end_time}\n"
        f"👥 Гости: {booking[4]}\n"
        f"⏱ Длительность: {booking[5]} ч.\n"
        f"💰 Стоимость: {booking[6]} ₽\n"
        f"👤 Имя: {booking[9]}\n"
        f"📞 Телефон: {booking[10]}\n"
        f"🔗 Аккаунт: {tg_link}\n"
        f"🆔 ID: {booking[0]}\n"
        f"🧩 TG ID: {booking[11]}\n"
        f"Статус: {booking[7]}"
    )

def create_booking_keyboard(booking_id, actions=['confirm', 'cancel', 'edit', 'delete']):
    """Создать клавиатуру для управления бронированием"""
    keyboard = []
    row = []
    
    if 'confirm' in actions:
        row.append(InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{booking_id}"))
    if 'cancel' in actions:
        row.append(InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{booking_id}"))
    if row:
        keyboard.append(row)
    
    row = []
    if 'edit' in actions:
        row.append(InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{booking_id}"))
    if 'delete' in actions:
        row.append(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{booking_id}"))
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def init_admin_db():
    """Инициализация таблицы администраторов"""
    async with aiosqlite.connect("chillivili.db") as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                name TEXT,
                role TEXT DEFAULT 'admin',
                created_at TEXT,
                created_by INTEGER
            )
        ''')
        await db.commit()
        
        # Добавляем главного админа если его нет
        async with db.execute("SELECT COUNT(*) FROM admins WHERE telegram_id = ?", (ADMIN_USER_ID,)) as cursor:
            count = (await cursor.fetchone())[0]
            if count == 0:
                await db.execute(
                    "INSERT INTO admins (telegram_id, username, name, role, created_at) VALUES (?, ?, ?, 'super_admin', ?)",
                    (ADMIN_USER_ID, "main_admin", "Главный администратор", datetime.now().isoformat())
                )
                await db.commit()

async def is_admin(telegram_id: int) -> bool:
    """Проверить, является ли пользователь администратором"""
    async with aiosqlite.connect("chillivili.db") as db:
        async with db.execute("SELECT COUNT(*) FROM admins WHERE telegram_id = ?", (telegram_id,)) as cursor:
            count = (await cursor.fetchone())[0]
            return count > 0

async def is_super_admin(telegram_id: int) -> bool:
    """Проверить, является ли пользователь супер-администратором"""
    async with aiosqlite.connect("chillivili.db") as db:
        async with db.execute("SELECT role FROM admins WHERE telegram_id = ?", (telegram_id,)) as cursor:
            result = await cursor.fetchone()
            return result and result[0] == 'super_admin'

async def get_all_admins():
    """Получить список всех администраторов"""
    async with aiosqlite.connect("chillivili.db") as db:
        async with db.execute("""
            SELECT a.*, creator.name as created_by_name 
            FROM admins a 
            LEFT JOIN admins creator ON a.created_by = creator.telegram_id
            ORDER BY a.created_at DESC
        """) as cursor:
            return await cursor.fetchall()

async def main():
    await init_admin_db()
    bot = Bot(token=ADMIN_BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(message: types.Message):
        if not await is_admin(message.from_user.id):
            await message.answer("❌ У вас нет доступа к админ-панели")
            return
        
        welcome_text = """
🔐 **Админ-панель ЧиллиВили**

Добро пожаловать в систему управления бронированиями!

Выберите действие из меню ниже:
        """
        await message.answer(welcome_text, reply_markup=create_admin_menu())

    @dp.message(F.text == "📊 Статистика")
    async def handle_statistics(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        stats = await get_statistics()
        stats_text = f"""
📊 **Статистика ЧиллиВили**

📈 **Общая статистика:**
• Всего бронирований: {stats['total_bookings']}
• Общая выручка: {stats['total_revenue']} ₽

📅 **Сегодня ({date.today().strftime('%d.%m.%Y')}):**
• Бронирований: {stats['today_bookings']}
• Выручка: {stats['today_revenue']} ₽

📅 **Завтра ({(date.today() + timedelta(days=1)).strftime('%d.%m.%Y')}):**
• Бронирований: {stats['tomorrow_bookings']}
        """
        await message.answer(stats_text)

    @dp.message(F.text == "📅 Бронирования сегодня")
    async def handle_today_bookings(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        bookings = await get_today_bookings()
        if not bookings:
            await message.answer("📅 На сегодня нет активных бронирований")
            return
        
        text = f"📅 **Бронирования на сегодня ({date.today().strftime('%d.%m.%Y')}):**\n\n"
        for booking in bookings:
            text += f"🕐 **{booking[3]}** - {booking[9]} ({booking[4]} чел., {booking[5]} ч.) - {booking[6]} ₽\n"
            text += f"📞 {booking[10]} | ID: {booking[0]}\n\n"
        
        await message.answer(text)

    @dp.message(F.text == "📋 Все бронирования")
    async def handle_all_bookings(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        bookings = await get_all_bookings(20)  # Показываем последние 20
        if not bookings:
            await message.answer("📋 Нет активных бронирований")
            return
        
        text = "📋 **Последние бронирования:**\n\n"
        for booking in bookings:
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            text += f"📅 **{display_date} {booking[3]}** - {booking[9]} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]} | Статус: {booking[7]}\n\n"
        
        await message.answer(text)

    @dp.message(F.text == "🔍 Найти бронирование")
    async def handle_find_booking(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        # Показываем последние 10 бронирований для выбора
        bookings = await get_all_bookings(10)
        if not bookings:
            await message.answer("📋 Нет активных бронирований для поиска")
            return
        
        text = "🔍 **Выберите бронирование для управления:**\n\n"
        keyboard = []
        
        for booking in bookings:
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            status_emoji = "✅" if booking[7] == "confirmed" else "⏳" if booking[7] == "pending" else "❌"
            tg_link = f"@{booking[12]}" if booking[12] else f"tg://user?id={booking[11]}"
            
            text += f"{status_emoji} **{display_date} {booking[3]}** - {booking[9]} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"🔗 Аккаунт: {tg_link}\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"
            
            # Создаем кнопку для каждого бронирования
            btn_text = f"{display_date} {booking[3]} - {booking[9]}"
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"select_booking_{booking[0]}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(text, reply_markup=markup)

    @dp.message(F.text == "✅ Подтвердить бронирование")
    async def handle_confirm_booking_button(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        # Показываем бронирования со статусом "pending" для подтверждения
        async with aiosqlite.connect("chillivili.db") as db:
            async with db.execute("""
                SELECT b.*, u.name, u.phone, u.telegram_id 
                FROM bookings b 
                JOIN users u ON b.user_id = u.id 
                WHERE b.status = 'pending'
                ORDER BY b.date ASC, b.time ASC
                LIMIT 10
            """) as cursor:
                bookings = await cursor.fetchall()
        
        if not bookings:
            await message.answer("✅ Нет бронирований, ожидающих подтверждения")
            return
        
        text = "✅ **Бронирования для подтверждения:**\n\n"
        keyboard = []
        
        for booking in bookings:
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            
            text += f"⏳ **{display_date} {booking[3]}** - {booking[9]} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"
            
            # Создаем кнопку для каждого бронирования
            btn_text = f"✅ {display_date} {booking[3]} - {booking[9]}"
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"confirm_{booking[0]}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(text, reply_markup=markup)

    @dp.message(F.text == "❌ Отменить бронирование")
    async def handle_cancel_booking_button(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        # Показываем активные бронирования для отмены
        async with aiosqlite.connect("chillivili.db") as db:
            async with db.execute("""
                SELECT b.*, u.name, u.phone, u.telegram_id 
                FROM bookings b 
                JOIN users u ON b.user_id = u.id 
                WHERE b.status IN ('pending', 'confirmed')
                ORDER BY b.date ASC, b.time ASC
                LIMIT 10
            """) as cursor:
                bookings = await cursor.fetchall()
        
        if not bookings:
            await message.answer("❌ Нет активных бронирований для отмены")
            return
        
        text = "❌ **Бронирования для отмены:**\n\n"
        keyboard = []
        
        for booking in bookings:
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            status_emoji = "✅" if booking[7] == "confirmed" else "⏳"
            
            text += f"{status_emoji} **{display_date} {booking[3]}** - {booking[9]} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"
            
            # Создаем кнопка для каждого бронирования
            btn_text = f"❌ {display_date} {booking[3]} - {booking[9]}"
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"cancel_{booking[0]}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(text, reply_markup=markup)

    @dp.message(F.text == "✏️ Редактировать бронирование")
    async def handle_edit_booking_button(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        # Показываем все бронирования для редактирования
        bookings = await get_all_bookings(10)
        if not bookings:
            await message.answer("✏️ Нет бронирований для редактирования")
            return
        
        text = "✏️ **Бронирования для редактирования:**\n\n"
        keyboard = []
        
        for booking in bookings:
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            status_emoji = "✅" if booking[7] == "confirmed" else "⏳" if booking[7] == "pending" else "❌"
            
            text += f"{status_emoji} **{display_date} {booking[3]}** - {booking[9]} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"
            
            # Создаем кнопку для каждого бронирования
            btn_text = f"✏️ {display_date} {booking[3]} - {booking[9]}"
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"edit_{booking[0]}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(text, reply_markup=markup)

    @dp.message(F.text == "🗑 Удалить бронирование")
    async def handle_delete_booking_button(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        # Показываем все бронирования для удаления
        bookings = await get_all_bookings(10)
        if not bookings:
            await message.answer("🗑 Нет бронирований для удаления")
            return
        
        text = "🗑 **Бронирования для удаления:**\n\n"
        keyboard = []
        
        for booking in bookings:
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            status_emoji = "✅" if booking[7] == "confirmed" else "⏳" if booking[7] == "pending" else "❌"
            
            text += f"{status_emoji} **{display_date} {booking[3]}** - {booking[9]} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"
            
            # Создаем кнопку для каждого бронирования
            btn_text = f"🗑 {display_date} {booking[3]} - {booking[9]}"
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"delete_{booking[0]}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(text, reply_markup=markup)

    @dp.message(F.text == "📱 Уведомить пользователя")
    async def handle_notify_user(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        admin_states[message.from_user.id] = {"state": "waiting_for_user_id"}
        await message.answer("📱 Введите Telegram ID пользователя:")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "waiting_for_user_id")
    async def handle_user_id_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        user_input = message.text.strip()
        user_id = None
        username = None
        
        if user_input.startswith("@"):  # Поиск по username
            username = user_input[1:].lower()
            async with aiosqlite.connect("chillivili.db") as db:
                async with db.execute("SELECT telegram_id FROM users WHERE LOWER(username) = ?", (username,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        user_id = row[0]
        else:
            try:
                user_id = int(user_input)
            except ValueError:
                await message.answer("❌ Введите корректный Telegram ID или username (например, @username)")
                return
        
        if not user_id:
            await message.answer("❌ Пользователь не найден по этому ID или username")
            return
        
        admin_states[message.from_user.id] = {"state": "waiting_for_notification_text", "user_id": user_id}
        await message.answer(f"✏️ Введите текст уведомления для пользователя {user_id}:")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "waiting_for_notification_text")
    async def handle_notification_text_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states[message.from_user.id]
        user_id = state["user_id"]
        notification_text = message.text.strip()
        
        try:
            await notify_user(user_id, notification_text)
            await message.answer(f"✅ Уведомление отправлено пользователю {user_id}")
        except Exception as e:
            await message.answer(f"❌ Ошибка при отправке уведомления: {str(e)}")
        
        del admin_states[message.from_user.id]

    # Обработчики редактирования бронирований
    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "editing_date")
    async def handle_edit_date_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states[message.from_user.id]
        booking_id = state["booking_id"]
        new_date = message.text.strip()
        
        try:
            # Парсим дату из формата ДД.ММ.ГГГГ
            date_obj = datetime.strptime(new_date, "%d.%m.%Y")
            formatted_date = date_obj.strftime("%Y-%m-%d")
            
            # Обновляем дату в базе данных
            async with aiosqlite.connect("chillivili.db") as db:
                await db.execute("UPDATE bookings SET date = ? WHERE id = ?", (formatted_date, booking_id))
                await db.commit()
            
            await message.answer(f"✅ Дата бронирования обновлена на {new_date}")
            
            # Показываем обновленную информацию о бронировании
            booking = await get_booking_by_id(booking_id)
            if booking:
                booking_info = format_booking_info(booking)
                keyboard = create_booking_keyboard(booking_id)
                await message.answer(booking_info, reply_markup=keyboard)
            
        except ValueError:
            await message.answer("❌ Неверный формат даты. Используйте формат ДД.ММ.ГГГГ (например, 15.08.2025)")
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка при обновлении даты: {str(e)}")
            return
        
        del admin_states[message.from_user.id]

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "editing_time")
    async def handle_edit_time_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states[message.from_user.id]
        booking_id = state["booking_id"]
        new_time = message.text.strip()
        
        try:
            # Проверяем формат времени
            time_obj = datetime.strptime(new_time, "%H:%M")
            formatted_time = time_obj.strftime("%H:%M")
            
            # Обновляем время в базе данных
            async with aiosqlite.connect("chillivili.db") as db:
                await db.execute("UPDATE bookings SET time = ? WHERE id = ?", (formatted_time, booking_id))
                await db.commit()
            
            await message.answer(f"✅ Время бронирования обновлено на {new_time}")
            
            # Показываем обновленную информацию о бронировании
            booking = await get_booking_by_id(booking_id)
            if booking:
                booking_info = format_booking_info(booking)
                keyboard = create_booking_keyboard(booking_id)
                await message.answer(booking_info, reply_markup=keyboard)
            
        except ValueError:
            await message.answer("❌ Неверный формат времени. Используйте формат ЧЧ:ММ (например, 14:30)")
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка при обновлении времени: {str(e)}")
            return
        
        del admin_states[message.from_user.id]

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "editing_guests")
    async def handle_edit_guests_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states[message.from_user.id]
        booking_id = state["booking_id"]
        new_guests = message.text.strip()
        
        try:
            guests = int(new_guests)
            if guests < 1 or guests > 50:
                await message.answer("❌ Количество гостей должно быть от 1 до 50")
                return
            
            # Обновляем количество гостей в базе данных
            async with aiosqlite.connect("chillivili.db") as db:
                await db.execute("UPDATE bookings SET guests = ? WHERE id = ?", (guests, booking_id))
                await db.commit()
            
            await message.answer(f"✅ Количество гостей обновлено на {guests}")
            
            # Показываем обновленную информацию о бронировании
            booking = await get_booking_by_id(booking_id)
            if booking:
                booking_info = format_booking_info(booking)
                keyboard = create_booking_keyboard(booking_id)
                await message.answer(booking_info, reply_markup=keyboard)
            
        except ValueError:
            await message.answer("❌ Введите корректное число гостей")
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка при обновлении количества гостей: {str(e)}")
            return
        
        del admin_states[message.from_user.id]

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "editing_duration")
    async def handle_edit_duration_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states[message.from_user.id]
        booking_id = state["booking_id"]
        new_duration = message.text.strip()
        
        try:
            duration = int(new_duration)
            if duration < 1 or duration > 12:
                await message.answer("❌ Длительность должна быть от 1 до 12 часов")
                return
            
            # Обновляем длительность в базе данных
            async with aiosqlite.connect("chillivili.db") as db:
                await db.execute("UPDATE bookings SET duration = ? WHERE id = ?", (duration, booking_id))
                await db.commit()
            
            await message.answer(f"✅ Длительность обновлена на {duration} часов")
            
            # Показываем обновленную информацию о бронировании
            booking = await get_booking_by_id(booking_id)
            if booking:
                booking_info = format_booking_info(booking)
                keyboard = create_booking_keyboard(booking_id)
                await message.answer(booking_info, reply_markup=keyboard)
            
        except ValueError:
            await message.answer("❌ Введите корректное число часов")
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка при обновлении длительности: {str(e)}")
            return
        
        del admin_states[message.from_user.id]

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "editing_price")
    async def handle_edit_price_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states[message.from_user.id]
        booking_id = state["booking_id"]
        new_price = message.text.strip()
        
        try:
            price = float(new_price)
            if price < 0:
                await message.answer("❌ Стоимость не может быть отрицательной")
                return
            
            # Обновляем стоимость в базе данных
            async with aiosqlite.connect("chillivili.db") as db:
                await db.execute("UPDATE bookings SET total_price = ? WHERE id = ?", (price, booking_id))
                await db.commit()
            
            await message.answer(f"✅ Стоимость обновлена на {price} ₽")
            
            # Показываем обновленную информацию о бронировании
            booking = await get_booking_by_id(booking_id)
            if booking:
                booking_info = format_booking_info(booking)
                keyboard = create_booking_keyboard(booking_id)
                await message.answer(booking_info, reply_markup=keyboard)
            
        except ValueError:
            await message.answer("❌ Введите корректную стоимость (число)")
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка при обновлении стоимости: {str(e)}")
            return
        
        del admin_states[message.from_user.id]

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "waiting_for_admin_id")
    async def handle_admin_id_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        if not await is_super_admin(message.from_user.id):
            await message.answer("❌ Только супер-администратор может добавлять администраторов")
            del admin_states[message.from_user.id]
            return
        
        try:
            new_admin_id = int(message.text.strip())
            
            # Проверяем, не является ли этот ID уже администратором
            if await is_admin(new_admin_id):
                await message.answer("❌ Этот пользователь уже является администратором")
                del admin_states[message.from_user.id]
                return
            
            # Добавляем нового администратора
            async with aiosqlite.connect("chillivili.db") as db:
                await db.execute(
                    "INSERT INTO admins (telegram_id, username, name, role, created_at, created_by) VALUES (?, ?, ?, 'admin', ?, ?)",
                    (new_admin_id, "new_admin", f"Администратор {new_admin_id}", datetime.now().isoformat(), message.from_user.id)
                )
                await db.commit()
            
            await message.answer(f"✅ Администратор с ID {new_admin_id} успешно добавлен!")
            
        except ValueError:
            await message.answer("❌ Пожалуйста, введите корректный Telegram ID (число)")
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка при добавлении администратора: {str(e)}")
            return
        
        del admin_states[message.from_user.id]

    @dp.message(F.text == "⚙️ Настройки")
    async def handle_settings(message: types.Message):
        if not await is_admin(message.from_user.id):
            await message.answer("❌ У вас нет доступа к настройкам")
            return
        
        # Проверяем, является ли пользователь супер-администратором
        if not await is_super_admin(message.from_user.id):
            await message.answer("❌ Только супер-администратор может управлять настройками")
            return
        
        text = "⚙️ **Настройки администраторов**\n\nВыберите действие:" 
        keyboard = [
            [InlineKeyboardButton(text="👥 Список администраторов", callback_data="list_admins")],
            [InlineKeyboardButton(text="➕ Добавить администратора", callback_data="add_admin")],
            [InlineKeyboardButton(text="❌ Удалить администратора", callback_data="remove_admin")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.answer(text, reply_markup=markup)

    # Обработчики callback-запросов
    @dp.callback_query(F.data.regexp(r"^select_booking_"))
    async def handle_select_booking(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        booking_id = int(callback.data.split("_")[2])
        booking = await get_booking_by_id(booking_id)
        
        if not booking:
            await callback.message.edit_text("❌ Бронирование не найдено")
            return
        
        booking_info = format_booking_info(booking)
        keyboard = create_booking_keyboard(booking_id)
        
        await callback.message.edit_text(booking_info, reply_markup=keyboard)

    @dp.callback_query(F.data.regexp(r"^confirm_"))
    async def handle_confirm_booking(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        booking_id = int(callback.data.split("_")[1])
        
        async with aiosqlite.connect("chillivili.db") as db:
            async with db.execute("""
                UPDATE bookings SET status = 'confirmed' WHERE id = ?
            """, (booking_id,)) as cursor:
                await db.commit()
                
                if cursor.rowcount > 0:
                    # Получаем информацию о бронировании для уведомления пользователя
                    booking = await get_booking_by_id(booking_id)
                    if booking:
                        notification_text = f"""
✅ **Ваше бронирование подтверждено!**

📅 Дата: {datetime.strptime(booking[2], '%Y-%m-%d').strftime('%d.%m.%Y')}
🕐 Время: {booking[3]}
👥 Гости: {booking[4]}
⏱ Длительность: {booking[5]} ч.
💰 Стоимость: {booking[6]} ₽

Ждем вас в гости! 🏠
                        """
                        await notify_user(booking[11], notification_text)
                    
                    await callback.message.edit_text("✅ Бронирование подтверждено!")
                else:
                    await callback.message.edit_text("❌ Ошибка при подтверждении бронирования")

    @dp.callback_query(F.data.regexp(r"^cancel_"))
    async def handle_cancel_booking(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        booking_id = int(callback.data.split("_")[1])
        
        async with aiosqlite.connect("chillivili.db") as db:
            async with db.execute("""
                UPDATE bookings SET status = 'cancelled' WHERE id = ?
            """, (booking_id,)) as cursor:
                await db.commit()
                
                if cursor.rowcount > 0:
                    # Получаем информацию о бронировании для уведомления пользователя
                    booking = await get_booking_by_id(booking_id)
                    if booking:
                        notification_text = f"""
❌ **Ваше бронирование отменено администратором**

📅 Дата: {datetime.strptime(booking[2], '%Y-%m-%d').strftime('%d.%m.%Y')}
🕐 Время: {booking[3]}

По всем вопросам обращайтесь к администрации.
                        """
                        await notify_user(booking[11], notification_text)
                    
                    await callback.message.edit_text("❌ Бронирование отменено!")
                else:
                    await callback.message.edit_text("❌ Ошибка при отмене бронирования")

    @dp.callback_query(F.data.regexp(r"^edit_"))
    async def handle_edit_booking(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        # Проверяем, это основная кнопка редактирования или конкретное поле
        parts = callback.data.split("_")
        if len(parts) == 2:
            # Основная кнопка редактирования (edit_123)
            booking_id = int(parts[1])
            booking = await get_booking_by_id(booking_id)
            
            if not booking:
                await callback.message.edit_text("❌ Бронирование не найдено")
                return
            
            # Показываем информацию о бронировании с кнопками редактирования
            booking_info = format_booking_info(booking)
            text = f"{booking_info}\n\n✏️ **Выберите, что хотите изменить:**"
            
            keyboard = [
                [InlineKeyboardButton(text="📅 Дата", callback_data=f"edit_date_{booking_id}")],
                [InlineKeyboardButton(text="🕐 Время", callback_data=f"edit_time_{booking_id}")],
                [InlineKeyboardButton(text="👥 Количество гостей", callback_data=f"edit_guests_{booking_id}")],
                [InlineKeyboardButton(text="⏱ Длительность", callback_data=f"edit_duration_{booking_id}")],
                [InlineKeyboardButton(text="💰 Стоимость", callback_data=f"edit_price_{booking_id}")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
            ]
            markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            
            await callback.message.edit_text(text, reply_markup=markup)
        else:
            # Конкретное поле для редактирования (edit_date_123, edit_time_123, etc.)
            field = parts[1]
            booking_id = int(parts[2])
            
            # Сохраняем состояние редактирования
            admin_states[callback.from_user.id] = {
                "state": f"editing_{field}",
                "booking_id": booking_id
            }
            
            # Показываем соответствующий интерфейс для редактирования
            if field == "date":
                await callback.message.edit_text("📅 Введите новую дату в формате ДД.ММ.ГГГГ:")
            elif field == "time":
                await callback.message.edit_text("🕐 Введите новое время в формате ЧЧ:ММ:")
            elif field == "guests":
                await callback.message.edit_text("👥 Введите новое количество гостей (число):")
            elif field == "duration":
                await callback.message.edit_text("⏱ Введите новую длительность в часах (число от 1 до 12):")
            elif field == "price":
                await callback.message.edit_text("💰 Введите новую стоимость (число):")

    @dp.callback_query(F.data.regexp(r"^delete_"))
    async def handle_delete_booking(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        booking_id = int(callback.data.split("_")[1])
        
        async with aiosqlite.connect("chillivili.db") as db:
            async with db.execute("DELETE FROM bookings WHERE id = ?", (booking_id,)) as cursor:
                await db.commit()
                
                if cursor.rowcount > 0:
                    await callback.message.edit_text("🗑 Бронирование удалено!")
                else:
                    await callback.message.edit_text("❌ Ошибка при удалении бронирования")

    # Обработчики управления администраторами
    @dp.callback_query(F.data == "list_admins")
    async def handle_list_admins(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может просматривать список администраторов")
            return
        
        admins = await get_all_admins()
        text = "👥 **Список администраторов:**\n\n"
        
        for admin in admins:
            created_at = datetime.strptime(admin[5], "%Y-%m-%dT%H:%M:%S.%f").strftime("%d.%m.%Y %H:%M")
            role_emoji = "👑" if admin[4] == "super_admin" else "👤"
            text += f"{role_emoji} **{admin[3]}** (@{admin[2]})\n"
            text += f"🆔 ID: {admin[1]} | Роль: {admin[4]}\n"
            text += f"📅 Добавлен: {created_at}\n\n"
        
        keyboard = [[InlineKeyboardButton(text="🔙 Назад", callback_data="settings_back")]]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=markup)

    @dp.callback_query(F.data == "add_admin")
    async def handle_add_admin(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может добавлять администраторов")
            return
        
        admin_states[callback.from_user.id] = {"state": "waiting_for_admin_id"}
        await callback.message.edit_text("➕ Введите Telegram ID нового администратора:")

    @dp.callback_query(F.data == "remove_admin")
    async def handle_remove_admin(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может удалять администраторов")
            return
        
        admins = await get_all_admins()
        text = "❌ **Выберите администратора для удаления:**\n\n"
        keyboard = []
        
        for admin in admins:
            if admin[1] != callback.from_user.id:  # Нельзя удалить самого себя
                role_emoji = "👑" if admin[4] == "super_admin" else "👤"
                btn_text = f"{role_emoji} {admin[3]} (@{admin[2]})"
                keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"remove_admin_{admin[1]}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="settings_back")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=markup)

    @dp.callback_query(F.data == "settings_back")
    async def handle_settings_back(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может управлять настройками")
            return
        
        text = "⚙️ **Настройки администраторов**\n\n"
        text += "Выберите действие:"
        
        keyboard = [
            [InlineKeyboardButton(text="👥 Список администраторов", callback_data="list_admins")],
            [InlineKeyboardButton(text="➕ Добавить администратора", callback_data="add_admin")],
            [InlineKeyboardButton(text="❌ Удалить администратора", callback_data="remove_admin")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=markup)

    @dp.callback_query(F.data.regexp(r"^remove_admin_"))
    async def handle_remove_admin_confirm(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может удалять администраторов")
            return
        
        admin_id = int(callback.data.split("_")[2])
        
        # Удаляем администратора из базы данных
        async with aiosqlite.connect("chillivili.db") as db:
            await db.execute("DELETE FROM admins WHERE telegram_id = ?", (admin_id,))
            await db.commit()
        
        await callback.message.edit_text("✅ Администратор успешно удален!")

    @dp.callback_query(F.data == "back_to_menu")
    async def handle_back_to_menu(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        welcome_text = """
🔐 **Админ-панель ЧиллиВили**

Добро пожаловать в систему управления бронированиями!

Выберите действие из меню ниже:
        """
        await callback.message.edit_text(welcome_text)

    print("🔐 Админ-бот ЧиллиВили запущен!")
    print("✅ Система управления готова к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
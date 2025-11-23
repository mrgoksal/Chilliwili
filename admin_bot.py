import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
import aiosqlite
import sqlite3
from datetime import datetime, date, timedelta
import json
import re
import aiohttp
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import platform
from db import (
    init_db, DB_PATH, get_setting, set_setting, get_all_settings, 
    set_media_setting, get_media_setting, delete_media_setting, create_booking_by_admin,
    get_price_per_hour, set_price_per_hour, get_price_per_extra_guest, set_price_per_extra_guest,
    get_max_guests_included, set_max_guests_included,
    add_expense, get_expenses, get_expenses_by_month, delete_expense, update_expense, get_expense_by_id,
    get_revenue_by_month, get_bookings_for_export, OPEN_HOUR, CLOSE_HOUR, MAX_BOOKING_DURATION
)

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

# Состояния для редактирования текстов
TEXT_EDITING_STATES = {
    "waiting_for_info_text": "info_text",
    "waiting_for_help_text": "help_text", 
    "waiting_for_welcome_text": "welcome_text"
}

def get_db():
    """Синхронное подключение к базе данных"""
    conn = sqlite3.connect(DB_PATH)
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
            [KeyboardButton(text="📜 Прошедшие брони")],
            [KeyboardButton(text="✅ Подтвердить бронирование"), KeyboardButton(text="❌ Отменить бронирование")],
            [KeyboardButton(text="✏️ Редактировать бронирование"), KeyboardButton(text="🗑 Удалить бронирование")],
            [KeyboardButton(text="➕ Создать бронирование"), KeyboardButton(text="📱 Уведомить пользователя")],
            [KeyboardButton(text="💰 Управление ценами"), KeyboardButton(text="📉 Расходы")],
            [KeyboardButton(text="📄 Выгрузить таблицу"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="🔧 Расширенные настройки")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )
    return keyboard

async def get_today_bookings():
    """Получить бронирования на сегодня"""
    today = date.today().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT b.*, u.name, u.phone, u.telegram_id, u.username 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            WHERE b.date = ? AND b.status != 'cancelled'
            ORDER BY b.time
        """, (today,)) as cursor:
            return await cursor.fetchall()

async def get_all_bookings(limit=50):
    """Получить все бронирования"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT b.*, u.name, u.phone, u.telegram_id, u.username 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            WHERE b.status != 'cancelled'
            ORDER BY b.date DESC, b.time DESC
            LIMIT ?
        """, (limit,)) as cursor:
            return await cursor.fetchall()

async def get_past_bookings(limit=50):
    """Получить прошедшие бронирования (закончились более дня назад)
    
    Бронь считается прошедшей, если она закончилась на следующий день после окончания.
    Например, если бронь закончилась 28.04.2026 в 22:00, то она станет прошедшей 29.04.2026.
    """
    today = date.today()
    # Бронь считается прошедшей, если она закончилась хотя бы вчера
    # То есть дата окончания + 1 день < сегодня
    async with aiosqlite.connect(DB_PATH) as db:
        bookings = []
        async with db.execute("""
            SELECT b.*, u.name, u.phone, u.telegram_id, u.username 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            WHERE b.status != 'cancelled'
            ORDER BY b.date DESC, b.time DESC
        """) as cursor:
            all_bookings = await cursor.fetchall()
        
        for booking in all_bookings:
            booking_date_str = booking[2]  # date
            booking_time_str = booking[3]  # time
            duration = booking[5]  # duration
            
            # Вычисляем дату и время окончания брони
            booking_date = datetime.strptime(booking_date_str, "%Y-%m-%d").date()
            booking_time = datetime.strptime(booking_time_str, "%H:%M").time()
            booking_datetime = datetime.combine(booking_date, booking_time)
            end_datetime = booking_datetime + timedelta(hours=duration)
            end_date = end_datetime.date()
            
            # Бронь прошедшая, если дата окончания + 1 день <= сегодня
            # (то есть на следующий день после окончания бронь становится прошедшей)
            if end_date + timedelta(days=1) <= today:
                bookings.append(booking)
                if len(bookings) >= limit:
                    break
        
        return bookings

async def get_booking_by_id(booking_id):
    """Получить бронирование по ID"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT b.*, u.name, u.phone, u.telegram_id, u.username 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            WHERE b.id = ?
        """, (booking_id,)) as cursor:
            return await cursor.fetchone()

async def get_statistics():
    """Получить статистику"""
    async with aiosqlite.connect(DB_PATH) as db:
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
        
        # Общие расходы
        async with db.execute("SELECT SUM(amount) FROM expenses") as cursor:
            total_expenses = (await cursor.fetchone())[0] or 0
        
        # Выручка по месяцам (последние 6 месяцев)
        revenue_by_month = await get_revenue_by_month()
        
        # Расходы по месяцам (последние 6 месяцев)
        expenses_by_month = await get_expenses_by_month()
        
        return {
            'total_bookings': total_bookings,
            'today_bookings': today_bookings,
            'tomorrow_bookings': tomorrow_bookings,
            'total_revenue': total_revenue,
            'today_revenue': today_revenue,
            'total_expenses': total_expenses,
            'revenue_by_month': revenue_by_month[:6],  # Последние 6 месяцев
            'expenses_by_month': expenses_by_month[:6]  # Последние 6 месяцев
        }

def extract_booking_name_phone(booking):
    """Извлекает имя и телефон из notes бронирования.
    ВСЕГДА использует данные из notes, если они есть, т.к. каждая бронь имеет свои уникальные данные.
    """
    # ВАЖНО: При запросе SELECT b.*, u.name, u.phone... структура такая:
    # Индексы 0-9: поля из bookings (id, user_id, date, time, guests, duration, total_price, status, created_at, notes)
    # Индексы 10-13: поля из users (name, phone, telegram_id, username)
    # notes находится на индексе 9
    notes = booking[9] if len(booking) > 9 and booking[9] else None
    booking_name = None
    booking_phone = None
    
    # ОТЛАДКА: выводим информацию о notes
    if notes:
        print(f"[DEBUG extract_booking_name_phone] notes: {notes}")
    
    # Проверяем, содержит ли notes данные для бронирования
    has_booking_data = notes and ("Имя для брони:" in notes or "Телефон для брони:" in notes)
    
    if has_booking_data:
        print(f"[DEBUG extract_booking_name_phone] has_booking_data=True, notes содержит данные для бронирования")
    
    if has_booking_data:
        # Формат: "Имя для брони: Имя | Телефон для брони: Телефон"
        if "Имя для брони:" in notes:
            try:
                # Извлекаем часть после "Имя для брони:"
                name_part = notes.split("Имя для брони:")[1]
                # Если есть "|", берем до него, иначе берем все
                if "|" in name_part:
                    booking_name = name_part.split("|")[0].strip()
                else:
                    booking_name = name_part.strip()
                print(f"[DEBUG] Извлечено имя из notes: '{booking_name}'")
            except Exception as e:
                print(f"Ошибка парсинга имени из notes: {e}, notes: {notes}")
                booking_name = None
        
        if "Телефон для брони:" in notes:
            try:
                # Извлекаем часть после "Телефон для брони:"
                phone_part = notes.split("Телефон для брони:")[1]
                # Берем все после "Телефон для брони:", даже если есть "|" в начале
                booking_phone = phone_part.strip()
                print(f"[DEBUG] Извлечен телефон из notes: '{booking_phone}'")
            except Exception as e:
                print(f"Ошибка парсинга телефона из notes: {e}, notes: {notes}")
                booking_phone = None
        
        # ВАЖНО: Если notes содержит данные для бронирования, ВСЕГДА используем извлеченные значения
        # Даже если значение пустое (пустая строка), используем его, а не данные из users
        # Это гарантирует, что каждое бронирование показывает свои уникальные данные
        name = booking_name if booking_name is not None else (booking[10] if len(booking) > 10 else "Не указано")
        phone = booking_phone if booking_phone is not None else (booking[11] if len(booking) > 11 else "Не указан")
        
        print(f"[DEBUG extract_booking_name_phone] ФИНАЛЬНЫЙ результат: name='{name}', phone='{phone}' (извлечено: name={booking_name}, phone={booking_phone})")
    else:
        # Если notes нет или не содержит данных для бронирования, используем из таблицы users
        name = booking[10] if len(booking) > 10 else "Не указано"
        phone = booking[11] if len(booking) > 11 else "Не указан"
        print(f"[DEBUG extract_booking_name_phone] Используем данные из users (notes нет): name='{name}', phone='{phone}'")
    
    return name, phone

def parse_expenses_from_text(text: str) -> list:
    """Парсит текст и извлекает расходы в формате (amount, description)
    
    Обрабатывает форматы:
    - "1600 посуда" - число в начале, затем текст
    - "2000dns" - число без пробела, затем текст
    - "3000 dns" - число с пробелом, затем текст
    - "6458лемано" - число без пробела, затем текст
    """
    expenses = []
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:  # Пропускаем пустые строки
            continue
        
        # Пытаемся найти число в строке
        # Вариант 1: Число в начале строки (с пробелом или без)
        match = re.match(r'^(\d+)\s*(.+)?$', line)
        if match:
            amount = int(match.group(1))
            description = match.group(2).strip() if match.group(2) else None
            expenses.append((amount, description or "Расход"))
            continue
        
        # Вариант 2: Число в конце строки (с пробелом или без)
        match = re.match(r'^(.+?)\s*(\d+)$', line)
        if match:
            description = match.group(1).strip()
            amount = int(match.group(2))
            expenses.append((amount, description or "Расход"))
            continue
        
        # Вариант 3: Ищем первое число в строке (любое место)
        match = re.search(r'(\d+)', line)
        if match:
            amount = int(match.group(1))
            # Описание - всё кроме числа
            description = re.sub(r'\d+', '', line).strip()
            expenses.append((amount, description or "Расход"))
        else:
            # Если числа нет, пропускаем строку
            continue
    
    return expenses

def format_booking_info(booking):
    # Структура результата запроса: b.* (id, user_id, date, time, guests, duration, total_price, status, created_at, notes), 
    # затем u.name, u.phone, u.telegram_id, u.username
    # Индексы: 0-9 из bookings (где notes на индексе 9), 10-13 из users
    date_str = datetime.strptime(booking[2], "%Y-%m-%d").strftime("%d.%m.%Y")
    end_time = (datetime.strptime(booking[3], "%H:%M") + timedelta(hours=booking[5])).strftime("%H:%M")
    username = booking[13] if len(booking) > 13 else None  # u.username
    telegram_id = booking[12] if len(booking) > 12 else None  # u.telegram_id
    notes = booking[9] if len(booking) > 9 and booking[9] else None  # b.notes (индекс 9 в результате JOIN)
    
    # Используем вспомогательную функцию для извлечения имени и телефона
    name, phone = extract_booking_name_phone(booking)
    
    tg_link = f"@{username}" if username and username != "None" else (f"tg://user?id={telegram_id}" if telegram_id else "—")
    
    text = (
        f"📅 Дата: {date_str}\n"
        f"🕐 Время: {booking[3]}\n"
        f"⏰ Окончание: {end_time}\n"
        f"👥 Гости: {booking[4]}\n"
        f"⏱ Длительность: {booking[5]} ч.\n"
        f"💰 Стоимость: {booking[6]} ₽\n"
        f"👤 Имя: {name}\n"
        f"📞 Телефон: {phone}\n"
    )
    
    text += (
        f"🔗 Аккаунт: {tg_link}\n"
        f"🆔 ID: {booking[0]}\n"
        f"🧩 TG ID: {telegram_id if telegram_id else '—'}\n"
        f"Статус: {booking[7]}"
    )
    
    return text

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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM admins WHERE telegram_id = ?", (telegram_id,)) as cursor:
            count = (await cursor.fetchone())[0]
            return count > 0

async def is_super_admin(telegram_id: int) -> bool:
    """Проверить, является ли пользователь супер-администратором"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT role FROM admins WHERE telegram_id = ?", (telegram_id,)) as cursor:
            result = await cursor.fetchone()
            return result and result[0] == 'super_admin'

async def get_all_admins():
    """Получить список всех администраторов"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT a.*, creator.name as created_by_name 
            FROM admins a 
            LEFT JOIN admins creator ON a.created_by = creator.telegram_id
            ORDER BY a.created_at DESC
        """) as cursor:
            return await cursor.fetchall()

def register_cyrillic_font():
    """Регистрирует кириллический шрифт для PDF"""
    # Пробуем найти системные шрифты с поддержкой кириллицы
    system = platform.system()
    font_paths = []
    
    if system == 'Windows':
        # Пути к шрифтам Windows
        windir = os.environ.get('WINDIR', 'C:\\Windows')
        font_paths = [
            os.path.join(windir, 'Fonts', 'arial.ttf'),
            os.path.join(windir, 'Fonts', 'arialbd.ttf'),
            os.path.join(windir, 'Fonts', 'Arial.ttf'),
            os.path.join(windir, 'Fonts', 'Arialbd.ttf'),
            os.path.join(windir, 'Fonts', 'tahoma.ttf'),
            os.path.join(windir, 'Fonts', 'tahomabd.ttf'),
            os.path.join(windir, 'Fonts', 'Tahoma.ttf'),
            os.path.join(windir, 'Fonts', 'Tahomabd.ttf'),
        ]
    elif system == 'Linux':
        # Пути к шрифтам Linux
        font_paths = [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        ]
    elif system == 'Darwin':  # macOS
        font_paths = [
            '/Library/Fonts/Arial.ttf',
            '/Library/Fonts/Arial Bold.ttf',
        ]
    
    # Регистрируем шрифты
    regular_font = None
    bold_font = None
    
    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                if 'bold' in font_path.lower() or 'bd' in font_path.lower():
                    if not bold_font:
                        pdfmetrics.registerFont(TTFont('CyrillicBold', font_path))
                        bold_font = 'CyrillicBold'
                else:
                    if not regular_font:
                        pdfmetrics.registerFont(TTFont('Cyrillic', font_path))
                        regular_font = 'Cyrillic'
                if regular_font and bold_font:
                    break
            except Exception as e:
                print(f"Ошибка регистрации шрифта {font_path}: {e}")
                continue
    
    # Если не нашли системные шрифты, используем встроенные (но они могут не поддерживать кириллицу)
    if not regular_font:
        regular_font = 'Helvetica'
        bold_font = 'Helvetica-Bold'
        print("⚠️ Кириллические шрифты не найдены, используется Helvetica (может отображаться некорректно)")
    
    return regular_font, bold_font

async def generate_bookings_pdf(start_date: str = None, end_date: str = None, period_name: str = "Все время") -> str:
    """Генерировать PDF файл с таблицей бронирований"""
    bookings = await get_bookings_for_export(start_date, end_date)
    
    if not bookings:
        return None
    
    # Регистрируем кириллические шрифты
    cyrillic_font, cyrillic_bold = register_cyrillic_font()
    
    # Создаем временный файл
    filename = f"bookings_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    filepath = os.path.join(os.path.dirname(__file__), filename)
    
    # Создаем PDF документ (альбомная ориентация для широкой таблицы)
    doc = SimpleDocTemplate(filepath, pagesize=landscape(A4))
    story = []
    
    # Стили
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        textColor=colors.HexColor('#1a1a1a'),
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName=cyrillic_bold
    )
    
    # Создаем стиль для обычного текста с кириллицей
    normal_style = ParagraphStyle(
        'CyrillicNormal',
        parent=styles['Normal'],
        fontName=cyrillic_font
    )
    
    # Заголовок
    title = Paragraph(f"Сводная таблица бронирований - {period_name}", title_style)
    story.append(title)
    story.append(Spacer(1, 0.5*cm))
    
    # Информация о периоде и общая статистика
    total_revenue = sum(b['total_price'] for b in bookings)
    info_text = f"<b>Период:</b> {period_name}<br/>"
    info_text += f"<b>Всего бронирований:</b> {len(bookings)}<br/>"
    info_text += f"<b>Общая выручка:</b> {total_revenue:,} ₽"
    info_para = Paragraph(info_text, normal_style)
    story.append(info_para)
    story.append(Spacer(1, 0.5*cm))
    
    # Подготовка данных для таблицы
    table_data = []
    
    # Заголовки таблицы
    headers = [
        'ID', 'Дата', 'Время', 'Гости', 'Длит.', 
        'Стоимость', 'Имя', 'Телефон', 'TG ID', 'Статус'
    ]
    table_data.append(headers)
    
    # Данные бронирований
    for booking in bookings:
        date_str = datetime.strptime(booking['date'], '%Y-%m-%d').strftime('%d.%m.%Y')
        time_str = booking['time']
        end_time = (datetime.strptime(booking['time'], '%H:%M') + timedelta(hours=booking['duration'])).strftime('%H:%M')
        time_range = f"{time_str}-{end_time}"
        
        tg_info = f"@{booking['username']}" if booking['username'] else f"ID:{booking['telegram_id']}" if booking['telegram_id'] else "—"
        
        status_ru = {
            'pending': 'Ожидает',
            'confirmed': 'Подтверждено',
            'cancelled': 'Отменено'
        }.get(booking['status'], booking['status'])
        
        row = [
            str(booking['id']),
            date_str,
            time_range,
            str(booking['guests']),
            f"{booking['duration']}ч",
            f"{booking['total_price']:,} ₽",
            booking['name'] or '—',
            booking['phone'] or '—',
            tg_info,
            status_ru
        ]
        table_data.append(row)
    
    # Создаем таблицу
    table = Table(table_data, colWidths=[1*cm, 2*cm, 2*cm, 1*cm, 1*cm, 2*cm, 2.5*cm, 2.5*cm, 2*cm, 1.5*cm])
    
    # Стиль таблицы
    table.setStyle(TableStyle([
        # Заголовок
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a90e2')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), cyrillic_bold),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING', (0, 0), (-1, 0), 12),
        
        # Данные
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 1), (-1, -1), cyrillic_font),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        
        # Чередование цветов строк
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
    ]))
    
    story.append(table)
    
    # Создаем PDF
    doc.build(story)
    
    return filepath

async def main():
    await init_db()  # Инициализируем основные таблицы БД
    await init_admin_db()  # Инициализируем таблицу администраторов
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
        
        # Формируем текст статистики
        stats_text = f"""
📊 **Статистика ЧиллиВили**

📈 **Общая статистика:**
• Всего бронирований: {stats['total_bookings']}
• Общая выручка: {stats['total_revenue']:,} ₽
• Общие расходы: {stats['total_expenses']:,} ₽
• Чистая прибыль: {stats['total_revenue'] - stats['total_expenses']:,} ₽

📅 **Сегодня ({date.today().strftime('%d.%m.%Y')}):**
• Бронирований: {stats['today_bookings']}
• Выручка: {stats['today_revenue']:,} ₽

📅 **Завтра ({(date.today() + timedelta(days=1)).strftime('%d.%m.%Y')}):**
• Бронирований: {stats['tomorrow_bookings']}

📊 **Выручка по месяцам (последние 6 месяцев):**
"""
        
        # Добавляем выручку по месяцам
        for month_data in stats['revenue_by_month']:
            month_name = datetime.strptime(month_data['month'], '%Y-%m').strftime('%B %Y')
            stats_text += f"• {month_name}: {month_data['revenue']:,} ₽ ({month_data['bookings']} бронирований)\n"
        
        stats_text += "\n📉 **Расходы по месяцам (последние 6 месяцев):**\n"
        
        # Добавляем расходы по месяцам
        expenses_dict = {exp['month']: exp for exp in stats['expenses_by_month']}
        for month_data in stats['revenue_by_month']:
            month_name = datetime.strptime(month_data['month'], '%Y-%m').strftime('%B %Y')
            expense = expenses_dict.get(month_data['month'], {'total': 0, 'count': 0})
            profit = month_data['revenue'] - expense['total']
            stats_text += f"• {month_name}: {expense['total']:,} ₽ ({expense['count']} расходов) | Прибыль: {profit:,} ₽\n"
        
        await message.answer(stats_text)

    @dp.message(F.text == "📅 Бронирования сегодня")
    async def handle_today_bookings(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        bookings = await get_today_bookings()
        if not bookings:
            await message.answer("📅 На сегодня нет активных бронирований")
            return
        
        # Отправляем заголовок
        await message.answer(f"📅 **Бронирования на сегодня ({date.today().strftime('%d.%m.%Y')}):**\n\nВсего бронирований: {len(bookings)}")
        
        # Отправляем каждое бронирование отдельным сообщением с полной информацией и кнопками
        for booking in bookings:
            booking_info = format_booking_info(booking)
            booking_id = booking[0]
            keyboard = create_booking_keyboard(booking_id, actions=['confirm', 'cancel', 'edit', 'delete'])
        
            await message.answer(booking_info, reply_markup=keyboard)

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
            # get_all_bookings возвращает: b.*, u.name, u.phone, u.telegram_id, u.username
            # Индексы: 0-9 из bookings, 10=name, 11=phone, 12=telegram_id, 13=username
            name, _ = extract_booking_name_phone(booking)
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            text += f"📅 **{display_date} {booking[3]}** - {name} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]} | Статус: {booking[7]}\n\n"
        
        await message.answer(text)
    
    @dp.message(F.text == "📜 Прошедшие брони")
    async def handle_past_bookings(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        bookings = await get_past_bookings(limit=50)
        
        if not bookings:
            await message.answer("📜 Нет прошедших бронирований")
            return
        
        text = "📜 **Прошедшие бронирования:**\n\n"
        for booking in bookings:
            name, _ = extract_booking_name_phone(booking)
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m.%Y")
            end_time = (datetime.strptime(booking[3], "%H:%M") + timedelta(hours=booking[5])).strftime("%H:%M")
            text += f"📅 **{display_date} {booking[3]}-{end_time}** - {name} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]} | Статус: {booking[7]}\n\n"
        
        await message.answer(text)

    @dp.message(F.text == "🔍 Найти бронирование")
    async def handle_find_booking(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        # Получаем все бронирования (включая старые), сгруппированные по датам
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT b.*, u.name, u.phone, u.telegram_id, u.username 
                FROM bookings b 
                JOIN users u ON b.user_id = u.id 
                WHERE b.status != 'cancelled'
                ORDER BY b.date ASC, b.time ASC
                LIMIT 50
            """) as cursor:
                bookings = await cursor.fetchall()
        
        if not bookings:
            await message.answer("📋 Нет активных бронирований для поиска")
            return
        
        # Группируем по датам
        bookings_by_date = {}
        for booking in bookings:
            booking_date = booking[2]
            if booking_date not in bookings_by_date:
                bookings_by_date[booking_date] = []
            bookings_by_date[booking_date].append(booking)
        
        text = "🔍 **Выберите бронирование для управления:**\n\n"
        keyboard = []
        
        # Показываем все бронирования, сгруппированные по датам
        for booking_date in sorted(bookings_by_date.keys()):
            date_obj = datetime.strptime(booking_date, "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m.%Y")
            
            # Если на один день несколько бронирований, показываем все
            day_bookings = bookings_by_date[booking_date]
            
            if len(day_bookings) > 1:
                text += f"📅 **{display_date}** ({len(day_bookings)} бронирований):\n\n"
            
            for booking in day_bookings:
                # get_all_bookings возвращает: b.*, u.name, u.phone, u.telegram_id, u.username
                # Индексы: 0-9 из bookings, 10=name, 11=phone, 12=telegram_id, 13=username
                status_emoji = "✅" if booking[7] == "confirmed" else "⏳" if booking[7] == "pending" else "❌"
                name, _ = extract_booking_name_phone(booking)
                username = booking[13] if len(booking) > 13 and booking[13] and booking[13] != "None" else None
                telegram_id = booking[12] if len(booking) > 12 else None
                tg_link = f"@{username}" if username else (f"tg://user?id={telegram_id}" if telegram_id else "—")

                text += f"{status_emoji} **{booking[3]}** - {name} ({booking[4]} чел., {booking[5]} ч.)\n"
                text += f"🔗 {tg_link} | 💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"

                btn_text = f"{display_date} {booking[3]} - {name}"
                if len(btn_text) > 60:  # Ограничение длины текста кнопки
                    btn_text = f"{display_date} {booking[3]} - {name[:20]}"
                keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"select_booking_{booking[0]}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(text, reply_markup=markup)

    @dp.message(F.text == "✅ Подтвердить бронирование")
    async def handle_confirm_booking_button(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        # Показываем бронирования со статусом "pending" для подтверждения
        async with aiosqlite.connect(DB_PATH) as db:
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
            # get_pending_bookings возвращает: b.*, u.name, u.phone, u.telegram_id
            # Индексы: 0-9 из bookings, 10=name, 11=phone, 12=telegram_id
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            name, _ = extract_booking_name_phone(booking)
            
            text += f"⏳ **{display_date} {booking[3]}** - {name} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"
            
            # Создаем кнопку для каждого бронирования
            btn_text = f"✅ {display_date} {booking[3]} - {name}"
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"confirm_{booking[0]}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(text, reply_markup=markup)

    @dp.message(F.text == "❌ Отменить бронирование")
    async def handle_cancel_booking_button(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        # Показываем активные бронирования для отмены
        async with aiosqlite.connect(DB_PATH) as db:
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
            # get_active_bookings возвращает: b.*, u.name, u.phone, u.telegram_id
            # Индексы: 0-9 из bookings, 10=name, 11=phone, 12=telegram_id
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            status_emoji = "✅" if booking[7] == "confirmed" else "⏳"
            name, _ = extract_booking_name_phone(booking)
            
            text += f"{status_emoji} **{display_date} {booking[3]}** - {name} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"
            
            # Создаем кнопка для каждого бронирования
            btn_text = f"❌ {display_date} {booking[3]} - {name}"
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
            # get_active_bookings возвращает: b.*, u.name, u.phone, u.telegram_id
            # Индексы: 0-9 из bookings, 10=name, 11=phone, 12=telegram_id
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            status_emoji = "✅" if booking[7] == "confirmed" else "⏳" if booking[7] == "pending" else "❌"
            name, _ = extract_booking_name_phone(booking)
            
            text += f"{status_emoji} **{display_date} {booking[3]}** - {name} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"
            
            # Создаем кнопку для каждого бронирования
            btn_text = f"✏️ {display_date} {booking[3]} - {name}"
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
            # get_active_bookings возвращает: b.*, u.name, u.phone, u.telegram_id
            # Индексы: 0-9 из bookings, 10=name, 11=phone, 12=telegram_id
            date_obj = datetime.strptime(booking[2], "%Y-%m-%d")
            display_date = date_obj.strftime("%d.%m")
            status_emoji = "✅" if booking[7] == "confirmed" else "⏳" if booking[7] == "pending" else "❌"
            name, _ = extract_booking_name_phone(booking)
            
            text += f"{status_emoji} **{display_date} {booking[3]}** - {name} ({booking[4]} чел., {booking[5]} ч.)\n"
            text += f"💰 {booking[6]} ₽ | ID: {booking[0]}\n\n"
            
            # Создаем кнопку для каждого бронирования
            btn_text = f"🗑 {display_date} {booking[3]} - {name}"
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
            async with aiosqlite.connect(DB_PATH) as db:
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

    @dp.message(F.text == "➕ Создать бронирование")
    async def handle_create_booking_button(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        admin_states[message.from_user.id] = {"state": "creating_booking_date"}
        await message.answer("📅 Введите дату бронирования в формате ДД.ММ.ГГГГ (например, 15.11.2024):")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "creating_booking_date")
    async def handle_create_booking_date(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        try:
            date_obj = datetime.strptime(message.text.strip(), "%d.%m.%Y")
            formatted_date = date_obj.strftime("%Y-%m-%d")
            admin_states[message.from_user.id] = {
                "state": "creating_booking_time",
                "date": formatted_date
            }
            await message.answer("🕐 Введите время начала бронирования в формате ЧЧ:ММ (например, 16:00):")
        except ValueError:
            await message.answer("❌ Неверный формат даты. Используйте формат ДД.ММ.ГГГГ (например, 15.11.2024)")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "creating_booking_time")
    async def handle_create_booking_time(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        try:
            time_obj = datetime.strptime(message.text.strip(), "%H:%M")
            if time_obj.minute != 0:
                await message.answer("❌ Время должно быть указано целыми часами (например, 16:00).")
                return
            if time_obj.hour < OPEN_HOUR or time_obj.hour >= CLOSE_HOUR:
                await message.answer(
                    f"❌ Можно бронировать только с {OPEN_HOUR:02d}:00 до {CLOSE_HOUR:02d}:00."
                )
                return
            formatted_time = time_obj.strftime("%H:%M")
            state = admin_states[message.from_user.id]
            admin_states[message.from_user.id] = {
                "state": "creating_booking_guests",
                "date": state["date"],
                "time": formatted_time
            }
            await message.answer("👥 Введите количество гостей (число):")
        except ValueError:
            await message.answer("❌ Неверный формат времени. Используйте формат ЧЧ:ММ (например, 16:00)")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "creating_booking_guests")
    async def handle_create_booking_guests(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        try:
            guests = int(message.text.strip())
            if guests < 1 or guests > 50:
                await message.answer("❌ Количество гостей должно быть от 1 до 50")
                return
            state = admin_states[message.from_user.id]
            admin_states[message.from_user.id] = {
                "state": "creating_booking_duration",
                "date": state["date"],
                "time": state["time"],
                "guests": guests
            }
            await message.answer(
                f"⏱ Введите длительность бронирования в часах (число от 1 до {MAX_BOOKING_DURATION}):"
            )
        except ValueError:
            await message.answer("❌ Введите корректное число гостей")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "creating_booking_duration")
    async def handle_create_booking_duration(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        try:
            duration = int(message.text.strip())
            if duration < 1 or duration > MAX_BOOKING_DURATION:
                await message.answer(
                    f"❌ Длительность должна быть от 1 до {MAX_BOOKING_DURATION} часов"
                )
                return
            state = admin_states[message.from_user.id]
            start_time = datetime.strptime(state["time"], "%H:%M")
            max_duration_for_time = CLOSE_HOUR - start_time.hour
            if max_duration_for_time <= 0:
                await message.answer("❌ На это время нельзя забронировать.")
                return
            if duration > max_duration_for_time:
                await message.answer(
                    f"❌ Для времени {state['time']} доступно максимум {max_duration_for_time} ч."
                )
                return
            admin_states[message.from_user.id] = {
                "state": "creating_booking_name",
                "date": state["date"],
                "time": state["time"],
                "guests": state["guests"],
                "duration": duration
            }
            await message.answer("👤 Введите имя клиента:")
        except ValueError:
            await message.answer("❌ Введите корректное число часов")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "creating_booking_name")
    async def handle_create_booking_name(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        name = message.text.strip()
        if not name:
            await message.answer("❌ Имя не может быть пустым!")
            return
        
        state = admin_states[message.from_user.id]
        admin_states[message.from_user.id] = {
            "state": "creating_booking_phone",
            "date": state["date"],
            "time": state["time"],
            "guests": state["guests"],
            "duration": state["duration"],
            "name": name
        }
        await message.answer("📱 Введите телефон клиента (или отправьте '-' для пропуска):")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "creating_booking_phone")
    async def handle_create_booking_phone(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        phone = message.text.strip()
        if phone == "-":
            phone = None
        
        state = admin_states[message.from_user.id]
        
        try:
            # Создаем бронирование
            booking_id = await create_booking_by_admin(
                date=state["date"],
                time=state["time"],
                guests=state["guests"],
                duration=state["duration"],
                name=state["name"],
                phone=phone,
                telegram_id=None,  # Для внешних бронирований
                status="confirmed"
            )
            
            await message.answer(
                f"✅ Бронирование успешно создано!\n\n"
                f"🆔 ID: {booking_id}\n"
                f"📅 Дата: {datetime.strptime(state['date'], '%Y-%m-%d').strftime('%d.%m.%Y')}\n"
                f"🕐 Время: {state['time']}\n"
                f"👥 Гости: {state['guests']}\n"
                f"⏱ Длительность: {state['duration']} ч.\n"
                f"👤 Имя: {state['name']}\n"
                f"📞 Телефон: {phone or 'Не указан'}\n"
                f"✅ Статус: подтверждено"
            )
            
            del admin_states[message.from_user.id]
        except Exception as e:
            await message.answer(f"❌ Ошибка при создании бронирования: {str(e)}")
            print(f"Ошибка создания бронирования: {e}")
            del admin_states[message.from_user.id]

    # Обработчики управления ценами
    @dp.message(F.text == "💰 Управление ценами")
    async def handle_price_management(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        if not await is_super_admin(message.from_user.id):
            await message.answer("❌ Только супер-администратор может управлять ценами")
            return
        
        price_per_hour = await get_price_per_hour()
        price_per_extra = await get_price_per_extra_guest()
        max_guests = await get_max_guests_included()
        
        text = f"""💰 **Управление ценами**

📊 **Текущие цены:**
• Цена за час (до {max_guests} человек): {price_per_hour} ₽
• Цена за дополнительного гостя (сверх {max_guests}): {price_per_extra} ₽
• Максимум гостей в базовой цене: {max_guests}

Выберите, что хотите изменить:"""
        
        keyboard = [
            [InlineKeyboardButton(text="🕐 Изменить цену за час", callback_data="edit_price_per_hour")],
            [InlineKeyboardButton(text="👥 Изменить цену за доп. гостя", callback_data="edit_price_per_extra")],
            [InlineKeyboardButton(text="🔢 Изменить макс. гостей в базе", callback_data="edit_max_guests")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.answer(text, reply_markup=markup)

    @dp.callback_query(F.data == "edit_price_per_hour")
    async def handle_edit_price_per_hour_button(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        admin_states[callback.from_user.id] = {"state": "editing_price_per_hour"}
        await callback.message.edit_text("💰 Введите новую цену за час (в рублях, число):")
        await callback.answer()

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "editing_price_per_hour")
    async def handle_edit_price_per_hour_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        try:
            price = int(message.text.strip())
            if price < 0:
                await message.answer("❌ Цена не может быть отрицательной!")
                return
            
            await set_price_per_hour(price)
            await message.answer(f"✅ Цена за час обновлена: {price} ₽")
        except ValueError:
            await message.answer("❌ Введите корректное число!")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {str(e)}")
        
        del admin_states[message.from_user.id]

    @dp.callback_query(F.data == "edit_price_per_extra")
    async def handle_edit_price_per_extra_button(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        admin_states[callback.from_user.id] = {"state": "editing_price_per_extra"}
        await callback.message.edit_text("💰 Введите новую цену за дополнительного гостя (в рублях, число):")
        await callback.answer()

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "editing_price_per_extra")
    async def handle_edit_price_per_extra_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        try:
            price = int(message.text.strip())
            if price < 0:
                await message.answer("❌ Цена не может быть отрицательной!")
                return
            
            await set_price_per_extra_guest(price)
            await message.answer(f"✅ Цена за дополнительного гостя обновлена: {price} ₽")
        except ValueError:
            await message.answer("❌ Введите корректное число!")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {str(e)}")
        
        del admin_states[message.from_user.id]

    @dp.callback_query(F.data == "edit_max_guests")
    async def handle_edit_max_guests_button(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        admin_states[callback.from_user.id] = {"state": "editing_max_guests"}
        await callback.message.edit_text("🔢 Введите новое максимальное количество гостей, включенных в базовую цену (число):")
        await callback.answer()

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "editing_max_guests")
    async def handle_edit_max_guests_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        try:
            count = int(message.text.strip())
            if count < 1:
                await message.answer("❌ Количество должно быть больше 0!")
                return
            
            await set_max_guests_included(count)
            await message.answer(f"✅ Максимальное количество гостей обновлено: {count}")
        except ValueError:
            await message.answer("❌ Введите корректное число!")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {str(e)}")
        
        del admin_states[message.from_user.id]

    # Обработчики управления расходами
    @dp.message(F.text == "📉 Расходы")
    async def handle_expenses_menu(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        text = "📉 **Управление расходами**\n\nВыберите действие:"
        keyboard = [
            [InlineKeyboardButton(text="➕ Добавить расход", callback_data="add_expense")],
            [InlineKeyboardButton(text="📝 Массовое добавление", callback_data="add_expenses_bulk")],
            [InlineKeyboardButton(text="📋 Список расходов", callback_data="list_expenses")],
            [InlineKeyboardButton(text="📊 Расходы по месяцам", callback_data="expenses_by_month")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.answer(text, reply_markup=markup)

    @dp.callback_query(F.data == "add_expense")
    async def handle_add_expense_button(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        admin_states[callback.from_user.id] = {"state": "adding_expense_date"}
        await callback.message.edit_text(
            "📅 Введите дату расхода в формате ДД.ММ.ГГГГ (например, 15.11.2024)\n"
            "Или отправьте '-' для использования сегодняшней даты:"
        )
        await callback.answer()
    
    @dp.callback_query(F.data == "add_expenses_bulk")
    async def handle_add_expenses_bulk_button(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        admin_states[callback.from_user.id] = {"state": "adding_expenses_bulk_date"}
        await callback.message.edit_text(
            "📝 **Массовое добавление расходов**\n\n"
            "📅 Сначала введите дату в формате ДД.ММ.ГГГГ (например, 15.11.2024)\n"
            "Или отправьте '-' для использования сегодняшней даты:\n\n"
            "После этого вы сможете отправить список расходов в формате:\n"
            "1600 посуда\n"
            "4600 колонка\n"
            "3600 подписки"
        )
        await callback.answer()

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "adding_expense_date")
    async def handle_add_expense_date(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        text = message.text.strip()
        if text == "-":
            # Используем сегодняшнюю дату
            formatted_date = date.today().strftime("%Y-%m-%d")
        else:
            try:
                date_obj = datetime.strptime(text, "%d.%m.%Y")
                formatted_date = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                await message.answer("❌ Неверный формат даты. Используйте формат ДД.ММ.ГГГГ (например, 15.11.2024) или '-' для сегодняшней даты")
                return
        
        admin_states[message.from_user.id] = {
            "state": "adding_expense_amount",
            "expense_date": formatted_date
        }
        await message.answer("💰 Введите сумму расхода (в рублях, число):")
    
    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "adding_expenses_bulk_date")
    async def handle_add_expenses_bulk_date(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        text = message.text.strip()
        if text == "-":
            # Используем сегодняшнюю дату
            formatted_date = date.today().strftime("%Y-%m-%d")
        else:
            try:
                date_obj = datetime.strptime(text, "%d.%m.%Y")
                formatted_date = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                await message.answer("❌ Неверный формат даты. Используйте формат ДД.ММ.ГГГГ (например, 15.11.2024) или '-' для сегодняшней даты")
                return
        
        admin_states[message.from_user.id] = {
            "state": "adding_expenses_bulk",
            "expense_date": formatted_date
        }
        
        date_display = datetime.strptime(formatted_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        await message.answer(
            f"✅ Дата установлена: {date_display}\n\n"
            "📝 Теперь отправьте список расходов в формате:\n"
            "1600 посуда\n"
            "4600 колонка\n"
            "3600 подписки\n"
            "490 интернет\n\n"
            "Каждая строка должна содержать число (сумма) и описание.\n"
            "Числа могут быть в начале строки или после текста."
        )

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "adding_expense_amount")
    async def handle_add_expense_amount(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        try:
            amount = int(message.text.strip())
            if amount < 0:
                await message.answer("❌ Сумма не может быть отрицательной!")
                return
            state = admin_states[message.from_user.id]
            admin_states[message.from_user.id] = {
                "state": "adding_expense_category",
                "expense_date": state["expense_date"],
                "expense_amount": amount
            }
            await message.answer("📂 Введите категорию расхода (например, Аренда, Зарплата, Продукты) или отправьте '-' для пропуска:")
        except ValueError:
            await message.answer("❌ Введите корректное число!")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "adding_expense_category")
    async def handle_add_expense_category(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        category = message.text.strip()
        if category == "-":
            category = None
        
        state = admin_states[message.from_user.id]
        admin_states[message.from_user.id] = {
            "state": "adding_expense_description",
            "expense_date": state["expense_date"],
            "expense_amount": state["expense_amount"],
            "expense_category": category
        }
        await message.answer("📝 Введите описание расхода (или отправьте '-' для пропуска):")

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "adding_expense_description")
    async def handle_add_expense_description(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        description = message.text.strip()
        if description == "-":
            description = None
        
        state = admin_states[message.from_user.id]
        
        try:
            expense_id = await add_expense(
                expense_date=state["expense_date"],
                amount=state["expense_amount"],
                category=state.get("expense_category"),
                description=description
            )
            
            date_display = datetime.strptime(state["expense_date"], "%Y-%m-%d").strftime("%d.%m.%Y")
            await message.answer(
                f"✅ Расход успешно добавлен!\n\n"
                f"🆔 ID: {expense_id}\n"
                f"📅 Дата: {date_display}\n"
                f"💰 Сумма: {state['expense_amount']:,} ₽\n"
                f"📂 Категория: {state.get('expense_category', 'Не указана')}\n"
                f"📝 Описание: {description or 'Не указано'}"
            )
            
            del admin_states[message.from_user.id]
        except Exception as e:
            await message.answer(f"❌ Ошибка при добавлении расхода: {str(e)}")
            print(f"Ошибка добавления расхода: {e}")
    
    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "adding_expenses_bulk")
    async def handle_add_expenses_bulk(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states[message.from_user.id]
        expense_date = state["expense_date"]
        
        # Парсим текст и извлекаем расходы
        expenses = parse_expenses_from_text(message.text)
        
        if not expenses:
            await message.answer(
                "❌ Не удалось найти расходы в тексте.\n\n"
                "Убедитесь, что каждая строка содержит число (сумму) и описание.\n"
                "Примеры:\n"
                "1600 посуда\n"
                "4600 колонка\n"
                "2000dns"
            )
            return
        
        # Добавляем расходы в базу данных
        added_count = 0
        total_amount = 0
        failed_lines = []
        
        for i, (amount, description) in enumerate(expenses, 1):
            try:
                expense_id = await add_expense(
                    expense_date=expense_date,
                    amount=amount,
                    category=None,
                    description=description
                )
                added_count += 1
                total_amount += amount
            except Exception as e:
                failed_lines.append(f"Строка {i}: {amount} {description} - ошибка: {str(e)}")
        
        # Формируем отчет
        date_display = datetime.strptime(expense_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        report = (
            f"✅ **Массовое добавление расходов завершено!**\n\n"
            f"📅 Дата: {date_display}\n"
            f"📊 Добавлено записей: {added_count}\n"
            f"💰 Общая сумма: {total_amount:,} ₽\n"
        )
        
        if failed_lines:
            report += f"\n⚠️ Ошибки при добавлении:\n" + "\n".join(failed_lines[:5])
            if len(failed_lines) > 5:
                report += f"\n... и еще {len(failed_lines) - 5} ошибок"
        
        # Добавляем детали добавленных расходов (первые 10)
        if added_count > 0:
            report += "\n\n📋 Добавленные расходы:\n"
            for i, (amount, description) in enumerate(expenses[:10], 1):
                report += f"{i}. {amount:,} ₽ - {description}\n"
            if len(expenses) > 10:
                report += f"... и еще {len(expenses) - 10} расходов"
        
        await message.answer(report)
        del admin_states[message.from_user.id]

    @dp.callback_query(F.data == "list_expenses")
    async def handle_list_expenses(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        expenses = await get_expenses()
        
        if not expenses:
            keyboard = [
                [InlineKeyboardButton(text="🔙 Назад", callback_data="expenses_menu")]
            ]
            markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            await callback.message.edit_text("📋 Расходов пока нет", reply_markup=markup)
            await callback.answer()
            return
        
        text = "📋 **Последние расходы:**\n\n"
        keyboard = []
        for expense in expenses[:20]:  # Показываем последние 20
            date_display = datetime.strptime(expense['date'], "%Y-%m-%d").strftime("%d.%m.%Y")
            text += f"• {date_display} - {expense['amount']:,} ₽"
            if expense.get('category'):
                text += f" ({expense['category']})"
            if expense.get('description'):
                text += f" - {expense['description']}"
            text += f" | ID: {expense['id']}\n"
            
            # Добавляем кнопки редактирования и удаления для каждого расхода
            keyboard.append([
                InlineKeyboardButton(
                    text=f"✏️ Редактировать",
                    callback_data=f"edit_expense_{expense['id']}"
                ),
                InlineKeyboardButton(
                    text=f"🗑 Удалить",
                    callback_data=f"delete_expense_{expense['id']}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="expenses_menu")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()

    @dp.callback_query(F.data == "expenses_by_month")
    async def handle_expenses_by_month(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        expenses_by_month = await get_expenses_by_month()
        
        if not expenses_by_month:
            keyboard = [
                [InlineKeyboardButton(text="🔙 Назад", callback_data="expenses_menu")]
            ]
            markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            await callback.message.edit_text("📊 Расходов по месяцам пока нет", reply_markup=markup)
            await callback.answer()
            return
        
        text = "📊 **Расходы по месяцам:**\n\n"
        for month_data in expenses_by_month[:12]:  # Последние 12 месяцев
            month_name = datetime.strptime(month_data['month'], '%Y-%m').strftime('%B %Y')
            text += f"• {month_name}: {month_data['total']:,} ₽ ({month_data['count']} расходов)\n"
        
        keyboard = [
            [InlineKeyboardButton(text="🔙 Назад", callback_data="expenses_menu")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()
    
    @dp.callback_query(F.data.regexp(r"^edit_expense_\d+$"))
    async def handle_edit_expense_button(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        expense_id = int(callback.data.split("_")[2])
        expense = await get_expense_by_id(expense_id)
        
        if not expense:
            await callback.message.edit_text("❌ Расход не найден")
            await callback.answer()
            return
        
        date_display = datetime.strptime(expense['date'], "%Y-%m-%d").strftime("%d.%m.%Y")
        text = (
            f"✏️ **Редактирование расхода ID {expense_id}**\n\n"
            f"📅 Дата: {date_display}\n"
            f"💰 Сумма: {expense['amount']:,} ₽\n"
            f"📂 Категория: {expense.get('category') or 'Не указана'}\n"
            f"📝 Описание: {expense.get('description') or 'Не указано'}\n\n"
            f"Выберите, что хотите изменить:"
        )
        
        keyboard = [
            [InlineKeyboardButton(text="📅 Изменить дату", callback_data=f"edit_expense_date_{expense_id}")],
            [InlineKeyboardButton(text="💰 Изменить сумму", callback_data=f"edit_expense_amount_{expense_id}")],
            [InlineKeyboardButton(text="📂 Изменить категорию", callback_data=f"edit_expense_category_{expense_id}")],
            [InlineKeyboardButton(text="📝 Изменить описание", callback_data=f"edit_expense_description_{expense_id}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="list_expenses")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()
    
    @dp.callback_query(F.data == "expenses_menu")
    async def handle_expenses_menu_callback(callback: types.CallbackQuery):
        """Обработчик для возврата в меню расходов"""
        if not await is_admin(callback.from_user.id):
            return
        
        text = "📉 **Управление расходами**\n\nВыберите действие:"
        keyboard = [
            [InlineKeyboardButton(text="➕ Добавить расход", callback_data="add_expense")],
            [InlineKeyboardButton(text="📝 Массовое добавление", callback_data="add_expenses_bulk")],
            [InlineKeyboardButton(text="📋 Список расходов", callback_data="list_expenses")],
            [InlineKeyboardButton(text="📊 Расходы по месяцам", callback_data="expenses_by_month")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()
    
    @dp.callback_query(F.data.regexp(r"^edit_expense_(date|amount|category|description)_\d+$"))
    async def handle_edit_expense_field(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        parts = callback.data.split("_")
        field = parts[2]
        expense_id = int(parts[3])
        
        field_names = {
            "date": "дату",
            "amount": "сумму",
            "category": "категорию",
            "description": "описание"
        }
        
        field_hints = {
            "date": "в формате ДД.ММ.ГГГГ (например, 15.11.2024) или '-' для сегодняшней даты",
            "amount": "в рублях (число)",
            "category": "категорию расхода (например, Аренда, Зарплата) или '-' для удаления",
            "description": "описание расхода или '-' для удаления"
        }
        
        admin_states[callback.from_user.id] = {
            "state": f"editing_expense_{field}",
            "expense_id": expense_id
        }
        
        await callback.message.edit_text(
            f"✏️ Введите новую {field_names[field]}:\n\n"
            f"Подсказка: {field_hints[field]}"
        )
        await callback.answer()
    
    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state", "").startswith("editing_expense_"))
    async def handle_edit_expense_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states[message.from_user.id]
        expense_id = state["expense_id"]
        expense_state = state["state"]
        
        field = expense_state.replace("editing_expense_", "")
        
        try:
            update_params = {}
            
            if field == "date":
                text = message.text.strip()
                if text == "-":
                    formatted_date = date.today().strftime("%Y-%m-%d")
                else:
                    date_obj = datetime.strptime(text, "%d.%m.%Y")
                    formatted_date = date_obj.strftime("%Y-%m-%d")
                update_params["expense_date"] = formatted_date
            
            elif field == "amount":
                amount = int(message.text.strip())
                if amount < 0:
                    await message.answer("❌ Сумма не может быть отрицательной!")
                    return
                update_params["amount"] = amount
            
            elif field == "category":
                category = message.text.strip()
                if category == "-":
                    category = None
                update_params["category"] = category
            
            elif field == "description":
                description = message.text.strip()
                if description == "-":
                    description = None
                update_params["description"] = description
            
            success = await update_expense(expense_id, **update_params)
            
            if success:
                expense = await get_expense_by_id(expense_id)
                date_display = datetime.strptime(expense['date'], "%Y-%m-%d").strftime("%d.%m.%Y")
                await message.answer(
                    f"✅ Расход успешно обновлен!\n\n"
                    f"📅 Дата: {date_display}\n"
                    f"💰 Сумма: {expense['amount']:,} ₽\n"
                    f"📂 Категория: {expense.get('category') or 'Не указана'}\n"
                    f"📝 Описание: {expense.get('description') or 'Не указано'}"
                )
            else:
                await message.answer("❌ Ошибка при обновлении расхода")
            
            del admin_states[message.from_user.id]
        
        except ValueError as e:
            if field == "date":
                await message.answer("❌ Неверный формат даты. Используйте формат ДД.ММ.ГГГГ или '-'")
            elif field == "amount":
                await message.answer("❌ Введите корректное число!")
            else:
                await message.answer(f"❌ Ошибка: {str(e)}")
        except Exception as e:
            await message.answer(f"❌ Ошибка при обновлении расхода: {str(e)}")
            print(f"Ошибка обновления расхода: {e}")
            del admin_states[message.from_user.id]
    
    @dp.callback_query(F.data.regexp(r"^delete_expense_\d+$"))
    async def handle_delete_expense(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        expense_id = int(callback.data.split("_")[2])
        expense = await get_expense_by_id(expense_id)
        
        if not expense:
            await callback.message.edit_text("❌ Расход не найден")
            await callback.answer()
            return
        
        # Показываем подтверждение
        date_display = datetime.strptime(expense['date'], "%Y-%m-%d").strftime("%d.%m.%Y")
        text = (
            f"🗑 **Удаление расхода ID {expense_id}**\n\n"
            f"📅 Дата: {date_display}\n"
            f"💰 Сумма: {expense['amount']:,} ₽\n"
            f"📂 Категория: {expense.get('category') or 'Не указана'}\n"
            f"📝 Описание: {expense.get('description') or 'Не указано'}\n\n"
            f"Вы уверены, что хотите удалить этот расход?"
        )
        
        keyboard = [
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_expense_{expense_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="list_expenses")
            ]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()
    
    @dp.callback_query(F.data.regexp(r"^confirm_delete_expense_\d+$"))
    async def handle_confirm_delete_expense(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        expense_id = int(callback.data.split("_")[3])
        success = await delete_expense(expense_id)
        
        if success:
            await callback.message.edit_text(f"✅ Расход ID {expense_id} успешно удален!")
        else:
            await callback.message.edit_text("❌ Ошибка при удалении расхода")
        
        await callback.answer()

    # Обработчики экспорта таблицы
    @dp.message(F.text == "📄 Выгрузить таблицу")
    async def handle_export_table(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        text = "📄 **Выгрузка сводной таблицы бронирований**\n\nВыберите период для экспорта:"
        keyboard = [
            [InlineKeyboardButton(text="📅 За все время", callback_data="export_all_time")],
            [InlineKeyboardButton(text="📆 По месяцам", callback_data="export_by_month")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.answer(text, reply_markup=markup)

    @dp.callback_query(F.data == "export_all_time")
    async def handle_export_all_time(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        await callback.message.edit_text("⏳ Генерация PDF таблицы за все время...")
        await callback.answer()
        
        try:
            filepath = await generate_bookings_pdf(period_name="За все время")
            
            if filepath:
                with open(filepath, 'rb') as pdf_file:
                    await callback.message.answer_document(
                        document=FSInputFile(filepath, filename=f"bookings_all_time_{datetime.now().strftime('%Y%m%d')}.pdf"),
                        caption="📄 Сводная таблица бронирований за все время"
                    )
                # Удаляем временный файл
                os.remove(filepath)
            else:
                await callback.message.answer("❌ Нет данных для экспорта")
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка при генерации PDF: {str(e)}")
            print(f"Ошибка генерации PDF: {e}")

    @dp.callback_query(F.data == "export_by_month")
    async def handle_export_by_month_menu(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        # Получаем список месяцев с бронированиями
        revenue_by_month = await get_revenue_by_month()
        
        if not revenue_by_month:
            await callback.message.edit_text("❌ Нет данных для экспорта")
            await callback.answer()
            return
        
        text = "📆 **Выберите месяц для экспорта:**\n\n"
        keyboard = []
        
        for month_data in revenue_by_month[:12]:  # Последние 12 месяцев
            month_obj = datetime.strptime(month_data['month'], '%Y-%m')
            month_name = month_obj.strftime('%B %Y')
            month_display = month_obj.strftime('%m.%Y')
            text += f"• {month_name}: {month_data['bookings']} бронирований\n"
            keyboard.append([InlineKeyboardButton(
                text=f"📅 {month_name}",
                callback_data=f"export_month_{month_data['month']}"
            )])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="export_back")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()

    @dp.callback_query(F.data.regexp(r"^export_month_\d{4}-\d{2}$"))
    async def handle_export_month(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        month_str = callback.data.split("_")[-1]
        month_obj = datetime.strptime(month_str, '%Y-%m')
        month_name = month_obj.strftime('%B %Y')
        
        # Определяем начало и конец месяца
        start_date = f"{month_str}-01"
        if month_obj.month == 12:
            end_date = f"{month_obj.year + 1}-01-01"
        else:
            end_date = f"{month_obj.year}-{month_obj.month + 1:02d}-01"
        
        await callback.message.edit_text(f"⏳ Генерация PDF таблицы за {month_name}...")
        await callback.answer()
        
        try:
            filepath = await generate_bookings_pdf(
                start_date=start_date,
                end_date=end_date,
                period_name=month_name
            )
            
            if filepath:
                with open(filepath, 'rb') as pdf_file:
                    await callback.message.answer_document(
                        document=FSInputFile(filepath, filename=f"bookings_{month_str}.pdf"),
                        caption=f"📄 Сводная таблица бронирований за {month_name}"
                    )
                # Удаляем временный файл
                os.remove(filepath)
            else:
                await callback.message.answer("❌ Нет данных для экспорта")
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка при генерации PDF: {str(e)}")
            print(f"Ошибка генерации PDF: {e}")

    @dp.callback_query(F.data == "export_back")
    async def handle_export_back(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        text = "📄 **Выгрузка сводной таблицы бронирований**\n\nВыберите период для экспорта:"
        keyboard = [
            [InlineKeyboardButton(text="📅 За все время", callback_data="export_all_time")],
            [InlineKeyboardButton(text="📆 По месяцам", callback_data="export_by_month")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()

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
            async with aiosqlite.connect(DB_PATH) as db:
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
            if time_obj.minute != 0:
                await message.answer("❌ Время должно быть указано целыми часами (например, 16:00).")
                return
            if time_obj.hour < OPEN_HOUR or time_obj.hour >= CLOSE_HOUR:
                await message.answer(
                    f"❌ Можно бронировать только с {OPEN_HOUR:02d}:00 до {CLOSE_HOUR:02d}:00."
                )
                return
            formatted_time = time_obj.strftime("%H:%M")
            
            # Обновляем время в базе данных
            async with aiosqlite.connect(DB_PATH) as db:
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
            async with aiosqlite.connect(DB_PATH) as db:
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
            if duration < 1 or duration > MAX_BOOKING_DURATION:
                await message.answer(
                    f"❌ Длительность должна быть от 1 до {MAX_BOOKING_DURATION} часов"
                )
                return
            state = admin_states[message.from_user.id]
            start_time = datetime.strptime(state["time"], "%H:%M")
            max_duration_for_time = CLOSE_HOUR - start_time.hour
            if max_duration_for_time <= 0:
                await message.answer("❌ На это время нельзя забронировать.")
            return
            if duration > max_duration_for_time:
                await message.answer(
                    f"❌ Для времени {state['time']} доступно максимум {max_duration_for_time} ч."
                )
            return
            admin_states[message.from_user.id] = {
                "state": "creating_booking_name",
                "date": state["date"],
                "time": state["time"],
                "guests": state["guests"],
                "duration": duration
            }
            await message.answer("👤 Введите имя клиента:")
        except ValueError:
            await message.answer("❌ Введите корректное число часов")

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
            async with aiosqlite.connect(DB_PATH) as db:
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
            async with aiosqlite.connect(DB_PATH) as db:
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

    # Обработчики для сохранения текстов
    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "waiting_for_info_text")
    async def handle_info_text_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        new_text = message.text.strip()
        if not new_text:
            await message.answer("❌ Текст не может быть пустым!")
            return
        
        try:
            await set_setting("info_text", new_text)
            await message.answer("✅ Текст 'Информация' успешно обновлен!")
        except Exception as e:
            await message.answer(f"❌ Ошибка при сохранении: {str(e)}")
        
        del admin_states[message.from_user.id]

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "waiting_for_help_text")
    async def handle_help_text_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        new_text = message.text.strip()
        if not new_text:
            await message.answer("❌ Текст не может быть пустым!")
            return
        
        try:
            await set_setting("help_text", new_text)
            await message.answer("✅ Текст 'Помощь' успешно обновлен!")
        except Exception as e:
            await message.answer(f"❌ Ошибка при сохранении: {str(e)}")
        
        del admin_states[message.from_user.id]

    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state") == "waiting_for_welcome_text")
    async def handle_welcome_text_input(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        new_text = message.text.strip()
        if not new_text:
            await message.answer("❌ Текст не может быть пустым!")
            return
        
        try:
            await set_setting("welcome_text", new_text)
            await message.answer("✅ Приветствие успешно обновлено!")
        except Exception as e:
            await message.answer(f"❌ Ошибка при сохранении: {str(e)}")
        
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

    @dp.message(F.text == "🔧 Расширенные настройки")
    async def handle_advanced_settings(message: types.Message):
        if not await is_admin(message.from_user.id):
            await message.answer("❌ У вас нет доступа к расширенным настройкам")
            return
        
        # Проверяем, является ли пользователь супер-администратором
        if not await is_super_admin(message.from_user.id):
            await message.answer("❌ Только супер-администратор может управлять расширенными настройками")
            return
        
        text = "🔧 **Расширенные настройки бота**\n\nВыберите, что хотите настроить:" 
        keyboard = [
            [InlineKeyboardButton(text="📝 Редактировать текст 'Информация'", callback_data="edit_info_text")],
            [InlineKeyboardButton(text="❓ Редактировать текст 'Помощь'", callback_data="edit_help_text")],
            [InlineKeyboardButton(text="👋 Редактировать приветствие", callback_data="edit_welcome_text")],
            [InlineKeyboardButton(text="📸 Управление медиа", callback_data="manage_media")],
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

    @dp.callback_query(F.data.regexp(r"^confirm_\d+$"))
    async def handle_confirm_booking(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        try:
            booking_id = int(callback.data.split("_")[1])
        except (ValueError, IndexError):
            await callback.answer("❌ Ошибка: неверный формат данных")
            return
        
        async with aiosqlite.connect(DB_PATH) as db:
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
                        # get_booking_by_id возвращает: b.*, u.name, u.phone, u.telegram_id, u.username
                        # Индексы: 0-9 из bookings, 10=name, 11=phone, 12=telegram_id, 13=username
                        telegram_id = booking[12] if len(booking) > 12 else None
                        if telegram_id:
                            await notify_user(telegram_id, notification_text)
                    
                    await callback.message.edit_text("✅ Бронирование подтверждено!")
                else:
                    await callback.message.edit_text("❌ Ошибка при подтверждении бронирования")

    @dp.callback_query(F.data.regexp(r"^cancel_\d+$"))
    async def handle_cancel_booking(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        try:
            booking_id = int(callback.data.split("_")[1])
        except (ValueError, IndexError):
            await callback.answer("❌ Ошибка: неверный формат данных")
            return
        
        async with aiosqlite.connect(DB_PATH) as db:
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
                        # get_booking_by_id возвращает: b.*, u.name, u.phone, u.telegram_id, u.username
                        # Индексы: 0-9 из bookings, 10=name, 11=phone, 12=telegram_id, 13=username
                        telegram_id = booking[12] if len(booking) > 12 else None
                        if telegram_id:
                            await notify_user(telegram_id, notification_text)
                    
                    await callback.message.edit_text("❌ Бронирование отменено!")
                else:
                    await callback.message.edit_text("❌ Ошибка при отмене бронирования")

    @dp.callback_query(F.data.regexp(r"^edit_\d+$"))
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

    @dp.callback_query(F.data.regexp(r"^edit_(date|time|guests|duration|price)_\d+$"))
    async def handle_edit_booking_field(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        # Конкретное поле для редактирования (edit_date_123, edit_time_123, etc.)
        parts = callback.data.split("_")
        field = parts[1]
        booking_id = int(parts[2])
        
        # Сохраняем состояние редактирования
        admin_states[callback.from_user.id] = {
            "state": f"editing_{field}",
            "booking_id": booking_id
        }
        
        prompts = {
            "date": "📅 Введите новую дату в формате ДД.ММ.ГГГГ (например, 15.11.2024):",
            "time": "🕐 Введите новое время в формате ЧЧ:ММ (например, 16:00):",
            "guests": "👥 Введите новое количество гостей (число):",
            "duration": "⏱ Введите новую длительность (число часов):",
            "price": "💰 Введите новую стоимость (в рублях):"
        }
        prompt = prompts.get(field, "Введите новое значение:")
        await callback.message.edit_text(prompt)
        await callback.answer()

    # Обработчик удаления медиа должен быть ПЕРЕД обработчиком удаления бронирования
    @dp.callback_query(F.data.regexp(r"^delete_(info|help|welcome)_(photo|video)$"))
    async def handle_delete_media(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может управлять медиа")
            return
        
        parts = callback.data.split("_")
        section = parts[1]
        media_type = parts[2]
        
        section_names = {
            "info": "Информация",
            "help": "Помощь",
            "welcome": "Приветствие"
        }
        media_names = {
            "photo": "фото",
            "video": "видео"
        }
        
        section_name = section_names.get(section, section)
        media_name = media_names.get(media_type, media_type)
        
        try:
            await delete_media_setting(section, media_type)
            await callback.answer(f"✅ {media_name.capitalize()} удалено из '{section_name}'")
            
            # Обновляем меню медиа для этого раздела
            # Проверяем, есть ли уже медиа
            photo_id = await get_media_setting(section, "photo")
            video_id = await get_media_setting(section, "video")
            
            text = f"📸 **Медиа для '{section_name}'**\n\n"
            if photo_id:
                text += "📷 Фото: ✅ Загружено\n"
            else:
                text += "📷 Фото: ❌ Не загружено\n"
            
            if video_id:
                text += "🎥 Видео: ✅ Загружено\n"
            else:
                text += "🎥 Видео: ❌ Не загружено\n"
            
            text += "\nВыберите действие:"
            
            keyboard = []
            if photo_id:
                keyboard.append([InlineKeyboardButton(text="📷 Изменить фото", callback_data=f"add_{section}_photo")])
                keyboard.append([InlineKeyboardButton(text="🗑 Удалить фото", callback_data=f"delete_{section}_photo")])
            else:
                keyboard.append([InlineKeyboardButton(text="📷 Добавить фото", callback_data=f"add_{section}_photo")])
            
            if video_id:
                keyboard.append([InlineKeyboardButton(text="🎥 Изменить видео", callback_data=f"add_{section}_video")])
                keyboard.append([InlineKeyboardButton(text="🗑 Удалить видео", callback_data=f"delete_{section}_video")])
            else:
                keyboard.append([InlineKeyboardButton(text="🎥 Добавить видео", callback_data=f"add_{section}_video")])
            
            keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="manage_media")])
            
            markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception as e:
            await callback.answer(f"❌ Ошибка при удалении: {str(e)}")

    @dp.callback_query(F.data.regexp(r"^delete_\d+$"))
    async def handle_delete_booking(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        # Дополнительная проверка - убеждаемся, что это действительно число
        # и не медиа (медиа имеет формат delete_info_photo, delete_help_video и т.д.)
        parts = callback.data.split("_")
        if len(parts) != 2:
            return  # Не наш формат (медиа имеет 3 части: delete_info_photo)
        
        # Проверяем, что вторая часть - это число
        if not parts[1].isdigit():
            return  # Не число, пропускаем (возможно, это удаление медиа)
        
        try:
            booking_id = int(parts[1])
        except ValueError:
            return  # На всякий случай, если что-то пошло не так
        
        async with aiosqlite.connect(DB_PATH) as db:
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
        keyboard = []
        
        for admin in admins:
            try:
                created_at = datetime.strptime(admin[5], "%Y-%m-%dT%H:%M:%S.%f").strftime("%d.%m.%Y %H:%M")
            except:
                try:
                    created_at = datetime.strptime(admin[5], "%Y-%m-%dT%H:%M:%S").strftime("%d.%м.%Y %H:%M")
                except:
                    created_at = admin[5] if admin[5] else "Не указано"
            
            role_emoji = "👑" if admin[4] == "super_admin" else "👤"
            role_name = "Супер-администратор" if admin[4] == "super_admin" else "Администратор"
            username = admin[2] if admin[2] else "Не указан"
            name = admin[3] if admin[3] else "Не указано"
            
            text += f"{role_emoji} **{name}** (@{username})\n"
            text += f"🆔 ID: {admin[1]} | Роль: {role_name}\n"
            text += f"📅 Добавлен: {created_at}\n\n"
        
            # Добавляем кнопку изменения роли (нельзя менять свою роль)
            if admin[1] != callback.from_user.id:
                if admin[4] == "super_admin":
                    btn_text = f"🔽 Понизить {name[:20]}"
                    callback_data = f"change_role_{admin[1]}_admin"
                else:
                    btn_text = f"⬆️ Повысить {name[:20]}"
                    callback_data = f"change_role_{admin[1]}_super_admin"
                keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=callback_data)])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="settings_back")])
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
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM admins WHERE telegram_id = ?", (admin_id,))
            await db.commit()
        
        await callback.message.edit_text("✅ Администратор успешно удален!")

    @dp.callback_query(F.data.regexp(r"^change_role_\d+_(admin|super_admin)$"))
    async def handle_change_admin_role(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может изменять роли")
            return
        
        # Парсим callback data: change_role_{admin_id}_{new_role}
        # Формат: change_role_123456789_super_admin или change_role_123456789_admin
        match = re.match(r'^change_role_(\d+)_(admin|super_admin)$', callback.data)
        
        if not match:
            await callback.answer("❌ Ошибка формата данных")
            return
        
        admin_id = int(match.group(1))
        new_role = match.group(2)  # "admin" или "super_admin"
        
        # Нельзя менять свою роль
        if admin_id == callback.from_user.id:
            await callback.answer("❌ Вы не можете изменить свою собственную роль!")
            return
        
        # Обновляем роль в базе данных
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE admins SET role = ? WHERE telegram_id = ?", (new_role, admin_id))
            await db.commit()
        
        role_name = "супер-администратором" if new_role == "super_admin" else "администратором"
        await callback.answer(f"✅ Роль успешно изменена на {role_name}!")
        
        # Обновляем список администраторов
        admins = await get_all_admins()
        text = "👥 **Список администраторов:**\n\n"
        keyboard = []
        
        for admin in admins:
            try:
                created_at = datetime.strptime(admin[5], "%Y-%m-%dT%H:%M:%S.%f").strftime("%d.%m.%Y %H:%M")
            except:
                try:
                    created_at = datetime.strptime(admin[5], "%Y-%m-%dT%H:%M:%S").strftime("%d.%м.%Y %H:%M")
                except:
                    created_at = admin[5] if admin[5] else "Не указано"
            
            role_emoji = "👑" if admin[4] == "super_admin" else "👤"
            role_name_display = "Супер-администратор" if admin[4] == "super_admin" else "Администратор"
            username = admin[2] if admin[2] else "Не указан"
            name = admin[3] if admin[3] else "Не указано"
            
            text += f"{role_emoji} **{name}** (@{username})\n"
            text += f"🆔 ID: {admin[1]} | Роль: {role_name_display}\n"
            text += f"📅 Добавлен: {created_at}\n\n"
            
            # Добавляем кнопку изменения роли (нельзя менять свою роль)
            if admin[1] != callback.from_user.id:
                if admin[4] == "super_admin":
                    btn_text = f"🔽 Понизить {name[:20]}"
                    callback_data = f"change_role_{admin[1]}_admin"
                else:
                    btn_text = f"⬆️ Повысить {name[:20]}"
                    callback_data = f"change_role_{admin[1]}_super_admin"
                keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=callback_data)])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="settings_back")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=markup)

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

    # Обработчики для расширенных настроек
    @dp.callback_query(F.data == "edit_info_text")
    async def handle_edit_info_text(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может редактировать настройки")
            return
        
        # Получаем текущий текст
        current_text = await get_setting("info_text")
        
        admin_states[callback.from_user.id] = {"state": "waiting_for_info_text"}
        
        text = f"📝 **Редактирование текста 'Информация'**\n\nТекущий текст:\n\n{current_text}\n\nОтправьте новый текст:"
        await callback.message.edit_text(text)

    @dp.callback_query(F.data == "edit_help_text")
    async def handle_edit_help_text(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может редактировать настройки")
            return
        
        # Получаем текущий текст
        current_text = await get_setting("help_text")
        
        admin_states[callback.from_user.id] = {"state": "waiting_for_help_text"}
        
        text = f"❓ **Редактирование текста 'Помощь'**\n\nТекущий текст:\n\n{current_text}\n\nОтправьте новый текст:"
        await callback.message.edit_text(text)

    @dp.callback_query(F.data == "edit_welcome_text")
    async def handle_edit_welcome_text(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может редактировать настройки")
            return
        
        # Получаем текущий текст
        current_text = await get_setting("welcome_text")
        
        admin_states[callback.from_user.id] = {"state": "waiting_for_welcome_text"}
        
        text = f"👋 **Редактирование приветствия**\n\nТекущий текст:\n\n{current_text}\n\nОтправьте новый текст:"
        await callback.message.edit_text(text)

    @dp.callback_query(F.data == "manage_media")
    async def handle_manage_media(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может управлять медиа")
            return
        
        text = "📸 **Управление медиа**\n\nВыберите раздел для управления медиа:"
        keyboard = [
            [InlineKeyboardButton(text="ℹ️ Медиа для 'Информация'", callback_data="media_info")],
            [InlineKeyboardButton(text="❓ Медиа для 'Помощь'", callback_data="media_help")],
            [InlineKeyboardButton(text="👋 Медиа для приветствия", callback_data="media_welcome")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="advanced_settings_back")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text(text, reply_markup=markup)

    @dp.callback_query(F.data.regexp(r"^media_(info|help|welcome)$"))
    async def handle_media_section(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может управлять медиа")
            return
        
        section = callback.data.split("_")[1]  # info, help, или welcome
        section_names = {
            "info": "Информация",
            "help": "Помощь",
            "welcome": "Приветствие"
        }
        section_name = section_names.get(section, section)
        
        # Проверяем, есть ли уже медиа
        photo_id = await get_media_setting(section, "photo")
        video_id = await get_media_setting(section, "video")
        
        text = f"📸 **Медиа для '{section_name}'**\n\n"
        if photo_id:
            text += "📷 Фото: ✅ Загружено\n"
        else:
            text += "📷 Фото: ❌ Не загружено\n"
        
        if video_id:
            text += "🎥 Видео: ✅ Загружено\n"
        else:
            text += "🎥 Видео: ❌ Не загружено\n"
        
        text += "\nВыберите действие:"
        
        keyboard = []
        if photo_id:
            keyboard.append([InlineKeyboardButton(text="📷 Изменить фото", callback_data=f"add_{section}_photo")])
            keyboard.append([InlineKeyboardButton(text="🗑 Удалить фото", callback_data=f"delete_{section}_photo")])
        else:
            keyboard.append([InlineKeyboardButton(text="📷 Добавить фото", callback_data=f"add_{section}_photo")])
        
        if video_id:
            keyboard.append([InlineKeyboardButton(text="🎥 Изменить видео", callback_data=f"add_{section}_video")])
            keyboard.append([InlineKeyboardButton(text="🗑 Удалить видео", callback_data=f"delete_{section}_video")])
        else:
            keyboard.append([InlineKeyboardButton(text="🎥 Добавить видео", callback_data=f"add_{section}_video")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="manage_media")])
        
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text(text, reply_markup=markup)

    @dp.callback_query(F.data.regexp(r"^add_(info|help|welcome)_(photo|video)$"))
    async def handle_add_media(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        if not await is_super_admin(callback.from_user.id):
            await callback.answer("❌ Только супер-администратор может управлять медиа")
            return
        
        parts = callback.data.split("_")
        section = parts[1]  # info, help, или welcome
        media_type = parts[2]  # photo или video
        
        section_names = {
            "info": "Информация",
            "help": "Помощь",
            "welcome": "Приветствие"
        }
        media_names = {
            "photo": "фото",
            "video": "видео"
        }
        
        section_name = section_names.get(section, section)
        media_name = media_names.get(media_type, media_type)
        
        # Сохраняем состояние для ожидания медиа
        admin_states[callback.from_user.id] = {
            "state": f"waiting_for_{section}_{media_type}",
            "section": section,
            "media_type": media_type
        }
        
        text = f"📸 **Добавление {media_name} для '{section_name}'**\n\n"
        if media_type == "photo":
            text += "📷 Отправьте фото, которое будет отображаться в разделе '{section_name}'"
        else:
            text += "🎥 Отправьте видео, которое будет отображаться в разделе '{section_name}'"
        
        await callback.message.edit_text(text)

    @dp.callback_query(F.data == "advanced_settings_back")
    async def handle_advanced_settings_back(callback: types.CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        text = "🔧 **Расширенные настройки бота**\n\nВыберите, что хотите настроить:"
        keyboard = [
            [InlineKeyboardButton(text="📝 Редактировать текст 'Информация'", callback_data="edit_info_text")],
            [InlineKeyboardButton(text="❓ Редактировать текст 'Помощь'", callback_data="edit_help_text")],
            [InlineKeyboardButton(text="👋 Редактировать приветствие", callback_data="edit_welcome_text")],
            [InlineKeyboardButton(text="📸 Управление медиа", callback_data="manage_media")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text(text, reply_markup=markup)

    # Обработчики для получения фото
    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state", "").endswith("_photo"))
    async def handle_photo_upload(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states.get(message.from_user.id, {})
        if not state or "state" not in state:
            return
        
        if not message.photo:
            await message.answer("❌ Пожалуйста, отправьте фото!")
            return
        
        # Получаем самое большое фото (последнее в списке)
        photo = message.photo[-1]
        file_id = photo.file_id
        
        section = state.get("section")
        media_type = state.get("media_type")
        
        section_names = {
            "info": "Информация",
            "help": "Помощь",
            "welcome": "Приветствие"
        }
        section_name = section_names.get(section, section)
        
        try:
            if not file_id:
                await message.answer("❌ Ошибка: не удалось получить file_id фотографии")
                del admin_states[message.from_user.id]
                return
            
            # Проблема: file_id из админ-бота нельзя использовать в основном боте
            # Решение: скачать файл и отправить через основной бот для получения правильного file_id
            if MAIN_BOT_TOKEN:
                # Создаем временный бот для скачивания файла
                async with Bot(token=ADMIN_BOT_TOKEN) as admin_bot_temp:
                    async with Bot(token=MAIN_BOT_TOKEN) as main_bot_temp:
                        try:
                            # Скачиваем файл через админ-бота
                            file = await admin_bot_temp.get_file(file_id)
                            
                            # Скачиваем файл в память
                            from io import BytesIO
                            
                            async with aiohttp.ClientSession() as session:
                                url = f"https://api.telegram.org/file/bot{ADMIN_BOT_TOKEN}/{file.file_path}"
                                async with session.get(url) as resp:
                                    file_data = await resp.read()
                            
                            # Отправляем файл через основной бот для получения правильного file_id
                            
                            # Создаем временный файл в памяти
                            photo_file = BytesIO(file_data)
                            photo_file.name = "photo.jpg"
                            
                            # Отправляем через основной бот (себе)
                            sent_message = await main_bot_temp.send_photo(
                                chat_id=ADMIN_USER_ID,
                                photo=FSInputFile(photo_file, filename="photo.jpg"),
                                caption="Временное сообщение для получения file_id"
                            )
                            
                            # Получаем правильный file_id из отправленного сообщения
                            new_file_id = sent_message.photo[-1].file_id
                            
                            # Сохраняем правильный file_id
                            await set_media_setting(section, new_file_id, "photo")
                            
                            # Удаляем временное сообщение
                            await main_bot_temp.delete_message(chat_id=ADMIN_USER_ID, message_id=sent_message.message_id)
                            
                            await message.answer(f"✅ Фото успешно добавлено в раздел '{section_name}'!")
                        except Exception as e:
                            print(f"Ошибка при конвертации file_id: {e}")
                            # Если не получилось, просто сохраняем оригинальный file_id
                            await set_media_setting(section, file_id, "photo")
                            await message.answer(f"⚠️ Фото сохранено, но может не отображаться в основном боте\n📷 File ID: {file_id[:50]}...")
            else:
                # Если токен основного бота не задан, просто сохраняем
                await set_media_setting(section, file_id, "photo")
                await message.answer(f"✅ Фото успешно добавлено в раздел '{section_name}'!\n📷 File ID: {file_id[:50]}...")
        except Exception as e:
            print(f"Ошибка при сохранении фото: {e}")
            await message.answer(f"❌ Ошибка при сохранении фото: {str(e)}")
        
        del admin_states[message.from_user.id]

    # Обработчики для получения видео
    @dp.message(lambda message: admin_states.get(message.from_user.id, {}).get("state", "").endswith("_video"))
    async def handle_video_upload(message: types.Message):
        if not await is_admin(message.from_user.id):
            return
        
        state = admin_states.get(message.from_user.id, {})
        if not state or "state" not in state:
            return
        
        if not message.video:
            await message.answer("❌ Пожалуйста, отправьте видео!")
            return
        
        file_id = message.video.file_id
        
        section = state.get("section")
        media_type = state.get("media_type")
        
        section_names = {
            "info": "Информация",
            "help": "Помощь",
            "welcome": "Приветствие"
        }
        section_name = section_names.get(section, section)
        
        try:
            if not file_id:
                await message.answer("❌ Ошибка: не удалось получить file_id видео")
                del admin_states[message.from_user.id]
                return
            
            # Проблема: file_id из админ-бота нельзя использовать в основном боте
            # Решение: скачать файл и отправить через основной бот для получения правильного file_id
            if MAIN_BOT_TOKEN:
                # Создаем временный бот для скачивания файла
                async with Bot(token=ADMIN_BOT_TOKEN) as admin_bot_temp:
                    async with Bot(token=MAIN_BOT_TOKEN) as main_bot_temp:
                        try:
                            # Скачиваем файл через админ-бота
                            file = await admin_bot_temp.get_file(file_id)
                            
                            # Скачиваем файл в память
                            from io import BytesIO
                            
                            async with aiohttp.ClientSession() as session:
                                url = f"https://api.telegram.org/file/bot{ADMIN_BOT_TOKEN}/{file.file_path}"
                                async with session.get(url) as resp:
                                    file_data = await resp.read()
                            
                            # Отправляем файл через основной бот для получения правильного file_id
                            
                            # Создаем временный файл в памяти
                            video_file = BytesIO(file_data)
                            video_file.name = "video.mp4"
                            
                            # Отправляем через основной бот (себе)
                            sent_message = await main_bot_temp.send_video(
                                chat_id=ADMIN_USER_ID,
                                video=FSInputFile(video_file, filename="video.mp4"),
                                caption="Временное сообщение для получения file_id"
                            )
                            
                            # Получаем правильный file_id из отправленного сообщения
                            new_file_id = sent_message.video.file_id
                            
                            # Сохраняем правильный file_id
                            await set_media_setting(section, new_file_id, "video")
                            
                            # Удаляем временное сообщение
                            await main_bot_temp.delete_message(chat_id=ADMIN_USER_ID, message_id=sent_message.message_id)
                            
                            await message.answer(f"✅ Видео успешно добавлено в раздел '{section_name}'!")
                        except Exception as e:
                            print(f"Ошибка при конвертации file_id видео: {e}")
                            # Если не получилось, просто сохраняем оригинальный file_id
                            await set_media_setting(section, file_id, "video")
                            await message.answer(f"⚠️ Видео сохранено, но может не отображаться в основном боте\n🎥 File ID: {file_id[:50]}...")
            else:
                # Если токен основного бота не задан, просто сохраняем
                await set_media_setting(section, file_id, "video")
                await message.answer(f"✅ Видео успешно добавлено в раздел '{section_name}'!\n🎥 File ID: {file_id[:50]}...")
        except Exception as e:
            print(f"Ошибка при сохранении видео: {e}")
            await message.answer(f"❌ Ошибка при сохранении видео: {str(e)}")
        
        del admin_states[message.from_user.id]

    print("🔐 Админ-бот ЧиллиВили запущен!")
    print("✅ Система управления готова к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
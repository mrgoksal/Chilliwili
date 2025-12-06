import aiosqlite
import sqlite3
from typing import Optional, List, Dict
from datetime import datetime, date, timedelta

OPEN_HOUR = 10
CLOSE_HOUR = 22
OPEN_TIME_STR = f"{OPEN_HOUR:02d}:00"
CLOSE_TIME_STR = f"{CLOSE_HOUR:02d}:00"
MAX_BOOKING_DURATION = CLOSE_HOUR - OPEN_HOUR

DB_PATH = "chillivili.db"

async def init_db():
    """Инициализация базы данных для антикафе"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                created_at TEXT
            )
        ''')
        
        # Таблица бронирований
        await db.execute('''
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                guests INTEGER NOT NULL,
                duration INTEGER NOT NULL,
                total_price INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                notes TEXT,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Таблица временных слотов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS time_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time TEXT NOT NULL UNIQUE
            )
        ''')
        
        # Таблица зон антикафе
        await db.execute('''
            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                capacity INTEGER NOT NULL
            )
        ''')
        
        # Таблица настроек бота
        await db.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT,
                setting_type TEXT DEFAULT 'text',
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        
        # Таблица расходов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount INTEGER NOT NULL,
                category TEXT,
                description TEXT,
                created_at TEXT
            )
        ''')
        
        # Таблица правил ценообразования
        await db.execute('''
            CREATE TABLE IF NOT EXISTS price_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                price_per_hour INTEGER NOT NULL,
                price_per_extra_guest INTEGER NOT NULL,
                extra_guest_payment_type TEXT NOT NULL DEFAULT 'per_booking',
                max_guests_included INTEGER NOT NULL DEFAULT 8,
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        
        await db.commit()
        
        # Миграции: добавляем недостающие колонки
        try:
            await db.execute('ALTER TABLE users ADD COLUMN username TEXT')
            await db.commit()
        except sqlite3.OperationalError:
            # Колонка уже существует, игнорируем ошибку
            pass
        
        try:
            await db.execute('ALTER TABLE bookings ADD COLUMN notes TEXT')
            await db.commit()
        except sqlite3.OperationalError:
            # Колонка уже существует, игнорируем ошибку
            pass
        
        # Добавляем базовые временные слоты
        await db.execute(
            "DELETE FROM time_slots WHERE time < ? OR time >= ?",
            (OPEN_TIME_STR, CLOSE_TIME_STR)
        )
        times = [f"{hour:02d}:00" for hour in range(OPEN_HOUR, CLOSE_HOUR)]
        for time in times:
            await db.execute('INSERT OR IGNORE INTO time_slots (time) VALUES (?)', (time,))
        
        # Добавляем базовые зоны
        zones = [('Зона 1', 10), ('Зона 2', 15), ('Зона 3', 20)]
        for zone in zones:
            await db.execute('INSERT OR IGNORE INTO zones (name, capacity) VALUES (?, ?)', zone)
        
        await db.commit()
        
        # Инициализируем настройки по умолчанию
        await init_default_settings()

async def get_or_create_user(telegram_id: int, username: str = None, name: str = None) -> int:
    """Получить или создать пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute(
                    "INSERT INTO users (telegram_id, username, name, created_at) VALUES (?, ?, ?, ?)",
                    (telegram_id, username, name, datetime.now().isoformat())
                )
                await db.commit()
                async with db.execute("SELECT last_insert_rowid()") as cursor:
                    return (await cursor.fetchone())[0]
            return user[0]

async def get_user_by_telegram_id(telegram_id: int) -> Optional[dict]:
    """Получить пользователя по telegram_id"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                columns = [column[0] for column in cursor.description]
                return dict(zip(columns, row))
    return None

async def get_available_dates() -> List[str]:
    """Получить доступные даты (следующие 7 дней)"""
    dates = []
    for i in range(7):
        date_obj = date.today() + timedelta(days=i)
        dates.append(date_obj.strftime("%Y-%m-%d"))
    return dates

async def get_available_times(selected_date: str) -> List[str]:
    """Получить доступные временные слоты для выбранной даты"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем все временные слоты
        async with db.execute("SELECT time FROM time_slots ORDER BY time") as cursor:
            all_times = [row[0] for row in await cursor.fetchall()]
        all_times = [t for t in all_times if OPEN_TIME_STR <= t < CLOSE_TIME_STR]
        
        # Получаем забронированные времена с длительностью для выбранной даты
        async with db.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (selected_date,)) as cursor:
            existing_bookings = await cursor.fetchall()
        
        # Получаем бронирования с предыдущего дня, которые могут продолжаться на текущий день
        prev_date = (datetime.strptime(selected_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        async with db.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (prev_date,)) as cursor:
            prev_day_bookings = await cursor.fetchall()
        
        # Создаем множество заблокированных временных слотов
        blocked_times = set()
        
        # Блокируем времена для бронирований текущего дня
        for booking_time, booking_duration in existing_bookings:
            start_time = datetime.strptime(booking_time, '%H:%M')
            end_time = start_time + timedelta(hours=booking_duration)
            
            # Блокируем час ДО начала брони (зазор перед бронированием)
            buffer_start = start_time - timedelta(hours=1)
            if buffer_start.hour >= 0:
                blocked_times.add(buffer_start.strftime('%H:%M'))
            
            # Блокируем саму бронь (все часы от начала до конца)
            for i in range(0, booking_duration):
                blocked_time = start_time + timedelta(hours=i)
                blocked_times.add(blocked_time.strftime('%H:%M'))
            
            # Блокируем час ПОСЛЕ окончания брони (зазор после бронирования)
            buffer_end = end_time
            if buffer_end.hour < 24:
                blocked_times.add(buffer_end.strftime('%H:%M'))
        
        # Блокируем времена для бронирований предыдущего дня, которые продолжаются на текущий день
        for booking_time, booking_duration in prev_day_bookings:
            start_time = datetime.strptime(booking_time, '%H:%M')
            start_hour = start_time.hour
            
            # Проверяем, переходит ли бронирование через полночь
            # Если start_hour + duration >= 24, то бронирование продолжается на следующий день
            if start_hour + booking_duration >= 24:
                # Рассчитываем, до какого часа на следующий день продолжается бронирование
                hours_into_next_day = (start_hour + booking_duration) % 24
                
                # Блокируем час ДО начала брони на следующий день (зазор перед бронированием)
                # Но так как это уже следующий день, зазор учитываем только для времени окончания
                # Блокируем время с 00:00 до времени окончания + 1 час буфера после
                end_hour = hours_into_next_day
                
                # Блокируем все часы от 00:00 до окончания + 1 час буфера
                for hour in range(0, min(end_hour + 1, 24)):
                    blocked_times.add(f"{hour:02d}:00")
        
        # Фильтруем слоты по правилу "бронь не раньше чем за 1 час" для сегодняшней даты
        available = [time for time in all_times if time not in blocked_times]
        today_str = date.today().strftime("%Y-%m-%d")
        if selected_date == today_str:
            now = datetime.now()
            current_hour = now.hour
            current_minute = now.minute
            
            # Ближайший доступный слот должен быть минимум через 1 час
            if current_minute == 0:
                min_hour = current_hour + 1
            else:
                min_hour = current_hour + 2
            
            if min_hour >= CLOSE_HOUR:
                available = []
            else:
                cutoff_str = f"{min_hour:02d}:00"
                available = [t for t in available if t >= cutoff_str]
        
        # Финальная фильтрация по рабочему времени
        available = [t for t in available if OPEN_TIME_STR <= t < CLOSE_TIME_STR]
        
        return available

async def get_available_zones() -> List[Dict]:
    """Получить доступные зоны"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM zones") as cursor:
            rows = await cursor.fetchall()
            return [{"id": row[0], "name": row[1], "capacity": row[2]} for row in rows]

async def create_booking(user_id: int, date: str, time: str, guests: int, duration: int, zone_id: int = None, notes: str = None) -> int:
    """Создать бронирование"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Расчет цены (стандартная цена 800 руб/час)
        price_per_hour = 800
        total_price = duration * price_per_hour
        
        # Добавляем доплату за гостей сверх 8 человек
        if guests > 8:
            extra_guests = guests - 8
            extra_charge = extra_guests * 500  # 500р за каждого сверх 8 человек на всё время
            total_price += extra_charge
        
        await db.execute("""
            INSERT INTO bookings (user_id, date, time, guests, duration, total_price, notes, created_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, date, time, guests, duration, total_price, notes, datetime.now().isoformat()))
        await db.commit()
        
        async with db.execute("SELECT last_insert_rowid()") as cursor:
            return (await cursor.fetchone())[0]

async def get_user_bookings(user_id: int) -> List[Dict]:
    """Получить бронирования пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT * FROM bookings 
            WHERE user_id = ? AND status != 'cancelled'
            ORDER BY date DESC, time DESC
        """, (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [{"id": row[0], "date": row[2], "time": row[3], "guests": row[4], 
                    "duration": row[5], "total_price": row[6], "status": row[7], 
                    "notes": row[8]} for row in rows]

async def cancel_booking(booking_id: int, user_id: int) -> bool:
    """Отменить бронирование"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            UPDATE bookings 
            SET status = 'cancelled' 
            WHERE id = ? AND user_id = ? AND status != 'cancelled'
        """, (booking_id, user_id)) as cursor:
            await db.commit()
            return cursor.rowcount > 0

async def create_booking_by_admin(
    date: str, 
    time: str, 
    guests: int, 
    duration: int, 
    name: str, 
    phone: str = None,
    telegram_id: int = None,
    total_price: int = None,
    status: str = "confirmed"
) -> int:
    """Создать бронирование админом (не изменяет существующие данные пользователя)"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Расчет цены если не указана
        if total_price is None:
            total_price = await calculate_booking_price(guests, duration, date, time)
        
        # Получаем или создаем пользователя
        if telegram_id:
            user_id = await get_or_create_user(telegram_id, None, name)
            # Обновляем имя и телефон только если они не заданы
            async with db.execute("SELECT name, phone FROM users WHERE id = ?", (user_id,)) as cursor:
                user_data = await cursor.fetchone()
                if user_data:
                    current_name, current_phone = user_data
                    # Обновляем только если текущие значения пустые или None
                    new_name = name if (not current_name or current_name == "Пользователь") else current_name
                    new_phone = phone if (not current_phone or current_phone == "Не указан") else current_phone
                    await db.execute(
                        "UPDATE users SET name = ?, phone = ? WHERE id = ?",
                        (new_name, new_phone, user_id)
                    )
        else:
            # Создаем пользователя без telegram_id для внешних бронирований
            await db.execute("""
                INSERT INTO users (name, phone, telegram_id, username, created_at) 
                VALUES (?, ?, ?, ?, ?)
            """, (name, phone or "Не указан", None, None, datetime.now().isoformat()))
            await db.commit()
            async with db.execute("SELECT last_insert_rowid()") as cursor:
                user_id = (await cursor.fetchone())[0]
        
        # Создаем бронирование
        await db.execute("""
            INSERT INTO bookings (user_id, date, time, guests, duration, total_price, status, created_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, date, time, guests, duration, total_price, status, datetime.now().isoformat()))
        await db.commit()
        
        async with db.execute("SELECT last_insert_rowid()") as cursor:
            return (await cursor.fetchone())[0]

async def get_booking_by_id(booking_id: int) -> Optional[Dict]:
    """Получить бронирование по ID"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT b.*, u.name as user_name 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            WHERE b.id = ?
        """, (booking_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"id": row[0], "user_id": row[1], "date": row[2], "time": row[3],
                       "guests": row[4], "duration": row[5], "total_price": row[6],
                       "status": row[7], "notes": row[8], "user_name": row[10]}
    return None

async def get_daily_bookings(selected_date: str) -> List[Dict]:
    """Получить все бронирования на определенную дату"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT b.*, u.name as user_name 
            FROM bookings b 
            JOIN users u ON b.user_id = u.id 
            WHERE b.date = ? AND b.status != 'cancelled'
            ORDER BY b.time
        """, (selected_date,)) as cursor:
            rows = await cursor.fetchall()
            return [{"id": row[0], "time": row[3], "guests": row[4], "duration": row[5],
                    "total_price": row[6], "user_name": row[10]} for row in rows]

async def update_user_phone(telegram_id: int, phone: str) -> bool:
    """Обновить телефон пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            UPDATE users SET phone = ? WHERE telegram_id = ?
        """, (phone, telegram_id)) as cursor:
            await db.commit()
            return cursor.rowcount > 0

async def get_statistics(days: int = 30) -> Dict:
    """Получить статистику бронирований"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Общая статистика
        async with db.execute("""
            SELECT 
                COUNT(*) as total_bookings,
                SUM(total_price) as total_revenue,
                AVG(guests) as avg_guests,
                AVG(duration) as avg_duration
            FROM bookings 
            WHERE status != 'cancelled' 
            AND date >= date('now', '-{} days')
        """.format(days)) as cursor:
            stats = await cursor.fetchone()
        
        # Статистика по дням недели
        async with db.execute("""
            SELECT 
                strftime('%w', date) as day_of_week,
                COUNT(*) as bookings_count
            FROM bookings 
            WHERE status != 'cancelled' 
            AND date >= date('now', '-{} days')
            GROUP BY strftime('%w', date)
            ORDER BY bookings_count DESC
        """.format(days)) as cursor:
            by_day = await cursor.fetchall()
        
        return {
            'total_bookings': stats[0] or 0,
            'total_revenue': stats[1] or 0,
            'avg_guests': stats[2] or 0,
            'avg_duration': stats[3] or 0,
            'by_day': by_day
        }

# Функции для совместимости с существующими ботами
def get_db():
    """Синхронное подключение к базе данных"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

async def is_admin(telegram_id: int) -> bool:
    """Проверить, является ли пользователь администратором"""
    # Для совместимости - всегда возвращаем True для тестирования
    # В реальной системе здесь должна быть проверка в таблице администраторов
    return True

async def is_super_admin(telegram_id: int) -> bool:
    """Проверить, является ли пользователь супер-администратором"""
    # Для совместимости - всегда возвращаем True для тестирования
    # В реальной системе здесь должна быть проверка в таблице администраторов
    return True

async def get_all_admin_ids() -> List[int]:
    """Получить список всех telegram_id администраторов"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, существует ли таблица admins
        async with db.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='admins'
        """) as cursor:
            table_exists = await cursor.fetchone()
        
        if not table_exists:
            # Если таблицы нет, возвращаем ADMIN_USER_ID из переменной окружения (для обратной совместимости)
            import os
            admin_id_env = os.getenv("ADMIN_USER_ID")
            if admin_id_env and admin_id_env.isdigit():
                return [int(admin_id_env)]
            return []
        
        # Получаем все telegram_id администраторов
        async with db.execute("SELECT telegram_id FROM admins") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows if row[0] is not None]

# Функции для работы с настройками бота
async def get_setting(key: str, default_value: str = "") -> str:
    """Получить настройку по ключу"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT setting_value FROM bot_settings WHERE setting_key = ?", (key,)) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else default_value

async def set_setting(key: str, value: str, setting_type: str = "text") -> bool:
    """Установить настройку"""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("""
                INSERT OR REPLACE INTO bot_settings (setting_key, setting_value, setting_type, updated_at) 
                VALUES (?, ?, ?, ?)
            """, (key, value, setting_type, datetime.now().isoformat()))
            await db.commit()
            return True
        except Exception as e:
            print(f"Ошибка при сохранении настройки {key}: {e}")
            return False

async def get_all_settings() -> Dict[str, Dict]:
    """Получить все настройки"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT setting_key, setting_value, setting_type FROM bot_settings") as cursor:
            rows = await cursor.fetchall()
            return {row[0]: {"value": row[1], "type": row[2]} for row in rows}

async def set_media_setting(media_type: str, file_id: str, file_type: str = "photo") -> bool:
    """Сохранить медиа (file_id) в настройки"""
    # media_type может быть: info, help, welcome
    # file_type может быть: photo, video
    key = f"{media_type}_{file_type}"
    return await set_setting(key, file_id, file_type)

async def get_media_setting(media_type: str, file_type: str = "photo") -> str:
    """Получить file_id медиа из настроек"""
    key = f"{media_type}_{file_type}"
    return await get_setting(key, "")

async def delete_media_setting(media_type: str, file_type: str = "photo") -> bool:
    """Удалить медиа из настроек"""
    key = f"{media_type}_{file_type}"
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("DELETE FROM bot_settings WHERE setting_key = ?", (key,))
            await db.commit()
            return True
        except Exception as e:
            print(f"Ошибка при удалении медиа {key}: {e}")
            return False

async def init_default_settings():
    """Инициализация настроек по умолчанию"""
    default_settings = {
        "info_text": """🏠 Антикафе «ЧиллиВили»

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
Только бронируй заранее, особенно в выходные 😉""",
        
        "help_text": """🏠 Антикафе «ЧиллиВили» - справка

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

📍 По всем вопросам поддержка 24/7: @ChilliWiliKirov""",
        
        "welcome_text": """🏠 Добро пожаловать в антикафе «ЧиллиВили»!

Привет, {first_name}! 👋

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

Выберите действие из меню ниже:"""
    }
    
    for key, value in default_settings.items():
        await set_setting(key, value, "text")
    
    # Инициализация цен по умолчанию
    price_settings = {
        "price_per_hour": "800",  # Цена за час до 8 человек
        "price_per_extra_guest": "500",  # Цена за каждого дополнительного гостя (сверх 8)
        "max_guests_included": "8"  # Количество гостей, включенных в базовую цену
    }
    
    for key, value in price_settings.items():
        # Проверяем, не установлена ли уже цена
        existing = await get_setting(key, "")
        if not existing:
            await set_setting(key, value, "number")

# Функции для работы с ценами
async def get_price_per_hour() -> int:
    """Получить цену за час"""
    return int(await get_setting("price_per_hour", "800"))

async def get_price_per_extra_guest() -> int:
    """Получить цену за дополнительного гостя"""
    return int(await get_setting("price_per_extra_guest", "500"))

async def get_max_guests_included() -> int:
    """Получить максимальное количество гостей, включенных в базовую цену"""
    return int(await get_setting("max_guests_included", "8"))

async def set_price_per_hour(price: int) -> bool:
    """Установить цену за час"""
    return await set_setting("price_per_hour", str(price), "number")

async def set_price_per_extra_guest(price: int) -> bool:
    """Установить цену за дополнительного гостя"""
    return await set_setting("price_per_extra_guest", str(price), "number")

async def set_max_guests_included(count: int) -> bool:
    """Установить максимальное количество гостей, включенных в базовую цену"""
    return await set_setting("max_guests_included", str(count), "number")

async def calculate_booking_price(guests: int, duration: int, booking_date: str = None, booking_time: str = None) -> int:
    """Рассчитать стоимость бронирования с учетом правил ценообразования"""
    # Сначала проверяем, есть ли специальное правило для этой даты и времени
    if booking_date and booking_time:
        rule = await get_price_rule_for_booking(booking_date, booking_time)
        if rule:
            price_per_hour = rule['price_per_hour']
            price_per_extra = rule['price_per_extra_guest']
            max_included = rule['max_guests_included']
            payment_type = rule['extra_guest_payment_type']
        else:
            # Используем стандартные настройки
            price_per_hour = await get_price_per_hour()
            price_per_extra = await get_price_per_extra_guest()
            max_included = await get_max_guests_included()
            payment_type = 'per_booking'  # По умолчанию
    else:
        # Используем стандартные настройки
        price_per_hour = await get_price_per_hour()
        price_per_extra = await get_price_per_extra_guest()
        max_included = await get_max_guests_included()
        payment_type = 'per_booking'  # По умолчанию
    
    base_price = duration * price_per_hour
    
    if guests > max_included:
        extra_guests = guests - max_included
        if payment_type == 'per_hour':
            extra_price = extra_guests * price_per_extra * duration
        else:  # per_booking
            extra_price = extra_guests * price_per_extra
        return base_price + extra_price
    
    return base_price

# Функции для работы с расходами
async def add_expense(expense_date: str, amount: int, category: str = None, description: str = None) -> int:
    """Добавить расход"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO expenses (date, amount, category, description, created_at) 
            VALUES (?, ?, ?, ?, ?)
        """, (expense_date, amount, category, description, datetime.now().isoformat()))
        await db.commit()
        
        async with db.execute("SELECT last_insert_rowid()") as cursor:
            return (await cursor.fetchone())[0]

async def get_expenses(start_date: str = None, end_date: str = None, category: str = None) -> List[Dict]:
    """Получить расходы за период"""
    async with aiosqlite.connect(DB_PATH) as db:
        query = "SELECT * FROM expenses WHERE 1=1"
        params = []
        
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        
        if category:
            query += " AND category = ?"
            params.append(category)
        
        query += " ORDER BY id DESC, date DESC"
        
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

async def get_expenses_by_month(year: int = None, month: int = None) -> List[Dict]:
    """Получить расходы по месяцам"""
    async with aiosqlite.connect(DB_PATH) as db:
        if year and month:
            # Конкретный месяц
            start_date = f"{year}-{month:02d}-01"
            if month == 12:
                end_date = f"{year+1}-01-01"
            else:
                end_date = f"{year}-{month+1:02d}-01"
            query = """
                SELECT 
                    strftime('%Y-%m', date) as month,
                    SUM(amount) as total_amount,
                    COUNT(*) as count
                FROM expenses 
                WHERE date >= ? AND date < ?
                GROUP BY month
            """
            async with db.execute(query, (start_date, end_date)) as cursor:
                rows = await cursor.fetchall()
                return [{"month": row[0], "total": row[1] or 0, "count": row[2]} for row in rows]
        else:
            # Все месяцы
            query = """
                SELECT 
                    strftime('%Y-%m', date) as month,
                    SUM(amount) as total_amount,
                    COUNT(*) as count
                FROM expenses 
                GROUP BY month
                ORDER BY month DESC
            """
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [{"month": row[0], "total": row[1] or 0, "count": row[2]} for row in rows]

async def update_expense(expense_id: int, expense_date: str = None, amount: int = None, category: str = None, description: str = None) -> bool:
    """Обновить расход"""
    async with aiosqlite.connect(DB_PATH) as db:
        updates = []
        params = []
        
        if expense_date is not None:
            updates.append("date = ?")
            params.append(expense_date)
        if amount is not None:
            updates.append("amount = ?")
            params.append(amount)
        if category is not None:
            updates.append("category = ?")
            params.append(category)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        
        if not updates:
            return False
        
        params.append(expense_id)
        query = f"UPDATE expenses SET {', '.join(updates)} WHERE id = ?"
        
        async with db.execute(query, params) as cursor:
            await db.commit()
            return cursor.rowcount > 0

async def get_expense_by_id(expense_id: int) -> Optional[Dict]:
    """Получить расход по ID"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            
            columns = [column[0] for column in cursor.description]
            return dict(zip(columns, row))

async def delete_expense(expense_id: int) -> bool:
    """Удалить расход"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,)) as cursor:
            await db.commit()
            return cursor.rowcount > 0

# Функции для работы с правилами ценообразования
async def get_price_rule_for_booking(booking_date: str, booking_time: str) -> Optional[Dict]:
    """Получить правило ценообразования для конкретной даты и времени"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT * FROM price_rules 
            WHERE start_date <= ? AND end_date >= ?
            AND start_time <= ? AND end_time > ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (booking_date, booking_date, booking_time, booking_time)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            
            columns = [column[0] for column in cursor.description]
            return dict(zip(columns, row))

async def add_price_rule(
    start_date: str,
    end_date: str,
    start_time: str,
    end_time: str,
    price_per_hour: int,
    price_per_extra_guest: int,
    extra_guest_payment_type: str = 'per_booking',
    max_guests_included: int = 8
) -> int:
    """Добавить правило ценообразования"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO price_rules (
                start_date, end_date, start_time, end_time,
                price_per_hour, price_per_extra_guest,
                extra_guest_payment_type, max_guests_included,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            start_date, end_date, start_time, end_time,
            price_per_hour, price_per_extra_guest,
            extra_guest_payment_type, max_guests_included,
            datetime.now().isoformat(), datetime.now().isoformat()
        ))
        await db.commit()
        
        async with db.execute("SELECT last_insert_rowid()") as cursor:
            return (await cursor.fetchone())[0]

async def get_all_price_rules() -> List[Dict]:
    """Получить все правила ценообразования"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT * FROM price_rules 
            ORDER BY start_date DESC, start_time DESC
        """) as cursor:
            rows = await cursor.fetchall()
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

async def get_price_rule_by_id(rule_id: int) -> Optional[Dict]:
    """Получить правило по ID"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM price_rules WHERE id = ?", (rule_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            
            columns = [column[0] for column in cursor.description]
            return dict(zip(columns, row))

async def update_price_rule(
    rule_id: int,
    start_date: str = None,
    end_date: str = None,
    start_time: str = None,
    end_time: str = None,
    price_per_hour: int = None,
    price_per_extra_guest: int = None,
    extra_guest_payment_type: str = None,
    max_guests_included: int = None
) -> bool:
    """Обновить правило ценообразования"""
    async with aiosqlite.connect(DB_PATH) as db:
        updates = []
        params = []
        
        if start_date is not None:
            updates.append("start_date = ?")
            params.append(start_date)
        if end_date is not None:
            updates.append("end_date = ?")
            params.append(end_date)
        if start_time is not None:
            updates.append("start_time = ?")
            params.append(start_time)
        if end_time is not None:
            updates.append("end_time = ?")
            params.append(end_time)
        if price_per_hour is not None:
            updates.append("price_per_hour = ?")
            params.append(price_per_hour)
        if price_per_extra_guest is not None:
            updates.append("price_per_extra_guest = ?")
            params.append(price_per_extra_guest)
        if extra_guest_payment_type is not None:
            updates.append("extra_guest_payment_type = ?")
            params.append(extra_guest_payment_type)
        if max_guests_included is not None:
            updates.append("max_guests_included = ?")
            params.append(max_guests_included)
        
        if not updates:
            return False
        
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(rule_id)
        
        query = f"UPDATE price_rules SET {', '.join(updates)} WHERE id = ?"
        
        async with db.execute(query, params) as cursor:
            await db.commit()
            return cursor.rowcount > 0

async def delete_price_rule(rule_id: int) -> bool:
    """Удалить правило ценообразования"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("DELETE FROM price_rules WHERE id = ?", (rule_id,)) as cursor:
            await db.commit()
            return cursor.rowcount > 0

# Функции для статистики
async def get_revenue_by_month(year: int = None, month: int = None) -> List[Dict]:
    """Получить выручку по месяцам"""
    async with aiosqlite.connect(DB_PATH) as db:
        if year and month:
            # Конкретный месяц
            start_date = f"{year}-{month:02d}-01"
            if month == 12:
                end_date = f"{year+1}-01-01"
            else:
                end_date = f"{year}-{month+1:02d}-01"
            query = """
                SELECT 
                    strftime('%Y-%m', date) as month,
                    SUM(total_price) as total_revenue,
                    COUNT(*) as bookings_count
                FROM bookings 
                WHERE date >= ? AND date < ? AND status != 'cancelled'
                GROUP BY month
            """
            async with db.execute(query, (start_date, end_date)) as cursor:
                rows = await cursor.fetchall()
                return [{"month": row[0], "revenue": row[1] or 0, "bookings": row[2]} for row in rows]
        else:
            # Все месяцы
            query = """
                SELECT 
                    strftime('%Y-%m', date) as month,
                    SUM(total_price) as total_revenue,
                    COUNT(*) as bookings_count
                FROM bookings 
                WHERE status != 'cancelled'
                GROUP BY month
                ORDER BY month DESC
            """
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [{"month": row[0], "revenue": row[1] or 0, "bookings": row[2]} for row in rows]

async def get_bookings_for_export(start_date: str = None, end_date: str = None) -> List[Dict]:
    """Получить все бронирования с данными пользователей для экспорта"""
    async with aiosqlite.connect(DB_PATH) as db:
        query = """
            SELECT 
                b.id,
                b.date,
                b.time,
                b.guests,
                b.duration,
                b.total_price,
                b.status,
                b.notes,
                b.created_at,
                u.name,
                u.phone,
                u.telegram_id,
                u.username
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            WHERE b.status != 'cancelled'
        """
        params = []
        
        if start_date:
            query += " AND b.date >= ?"
            params.append(start_date)
        
        if end_date:
            query += " AND b.date <= ?"
            params.append(end_date)
        
        query += " ORDER BY b.date ASC, b.time ASC"
        
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in rows] 
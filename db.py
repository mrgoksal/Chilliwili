import aiosqlite
from typing import Optional, List, Dict
from datetime import datetime, date, timedelta

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
        
        await db.commit()
        
        # Добавляем базовые временные слоты
        times = ['09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00', '21:00', '22:00']
        for time in times:
            await db.execute('INSERT OR IGNORE INTO time_slots (time) VALUES (?)', (time,))
        
        # Добавляем базовые зоны
        zones = [('Зона 1', 10), ('Зона 2', 15), ('Зона 3', 20)]
        for zone in zones:
            await db.execute('INSERT OR IGNORE INTO zones (name, capacity) VALUES (?, ?)', zone)
        
        await db.commit()

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
        
        # Получаем забронированные времена для выбранной даты
        async with db.execute("""
            SELECT time FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (selected_date,)) as cursor:
            booked_times = [row[0] for row in await cursor.fetchall()]
        
        # Возвращаем доступные времена
        return [time for time in all_times if time not in booked_times]

async def get_available_zones() -> List[Dict]:
    """Получить доступные зоны"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM zones WHERE is_active = 1") as cursor:
            rows = await cursor.fetchall()
            return [{"id": row[0], "name": row[1], "capacity": row[2], "price": row[3]} for row in rows]

async def create_booking(user_id: int, date: str, time: str, guests: int, duration: int, zone_id: int = None, notes: str = None) -> int:
    """Создать бронирование"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Расчет цены
        if zone_id:
            async with db.execute("SELECT price_per_hour FROM zones WHERE id = ?", (zone_id,)) as cursor:
                price_per_hour = (await cursor.fetchone())[0]
        else:
            price_per_hour = 500  # Стандартная цена
        
        total_price = guests * duration * price_per_hour
        
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
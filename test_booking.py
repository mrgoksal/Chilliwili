#!/usr/bin/env python3
"""
Тестовый скрипт для проверки создания бронирования через бота
"""

import sqlite3
from datetime import datetime, timedelta

def test_bot_booking():
    """Тестируем создание бронирования через бота"""
    print("🧪 Тестирование создания бронирования через бота...")
    
    # Подключаемся к базе данных
    conn = sqlite3.connect('chillivili.db')
    cur = conn.cursor()
    
    # Тестовые данные
    test_telegram_id = 555469646  # Ваш Telegram ID
    test_name = "Тестовый Пользователь"
    test_phone = "Не указан"
    test_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    test_time = "15:00"
    test_guests = 2
    test_duration = 2
    test_total_price = test_guests * test_duration * 500
    
    try:
        # 1. Создаем или находим пользователя
        print(f"1. Создание/поиск пользователя с Telegram ID: {test_telegram_id}")
        cur.execute("SELECT id, name, phone FROM users WHERE telegram_id = ?", (test_telegram_id,))
        user = cur.fetchone()
        
        if user:
            db_user_id, db_name, db_phone = user
            print(f"   ✅ Пользователь найден: ID={db_user_id}, Имя={db_name}")
            # Обновляем имя если оно изменилось
            if db_name != test_name:
                cur.execute("UPDATE users SET name = ? WHERE id = ?", (test_name, db_user_id))
                print(f"   📝 Имя обновлено: {db_name} → {test_name}")
            user_id = db_user_id
        else:
            print(f"   ➕ Создаем нового пользователя")
            cur.execute(
                "INSERT INTO users (name, phone, telegram_id, created_at) VALUES (?, ?, ?, ?)",
                (test_name, test_phone, test_telegram_id, datetime.now().isoformat())
            )
            user_id = cur.lastrowid
            print(f"   ✅ Пользователь создан: ID={user_id}")
        
        # 2. Проверяем доступность времени
        print(f"2. Проверка доступности времени: {test_date} {test_time}")
        cur.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (test_date,))
        existing_bookings = cur.fetchall()
        
        if existing_bookings:
            print(f"   ⚠️  Найдены существующие брони на эту дату:")
            for booking in existing_bookings:
                print(f"      - {booking[0]} (длительность: {booking[1]}ч)")
        else:
            print(f"   ✅ Время свободно")
        
        # 3. Создаем тестовое бронирование
        print(f"3. Создание тестового бронирования")
        cur.execute(
            "INSERT INTO bookings (user_id, date, time, guests, duration, total_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (user_id, test_date, test_time, test_guests, test_duration, test_total_price, datetime.now().isoformat())
        )
        conn.commit()
        booking_id = cur.lastrowid
        print(f"   ✅ Бронирование создано: ID={booking_id}")
        print(f"   📊 Детали: {test_date} {test_time}, {test_guests} гостей, {test_duration}ч, {test_total_price}₽")
        
        # 4. Проверяем результат
        print(f"4. Проверка результата")
        cur.execute("""
            SELECT b.id, b.date, b.time, b.guests, b.duration, b.total_price, b.status, 
                   u.name, u.phone, u.telegram_id
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            WHERE b.id = ?
        """, (booking_id,))
        
        result = cur.fetchone()
        if result:
            print(f"   ✅ Бронирование найдено в базе:")
            print(f"      ID: {result[0]}")
            print(f"      Дата/время: {result[1]} {result[2]}")
            print(f"      Гости: {result[3]}")
            print(f"      Длительность: {result[4]}ч")
            print(f"      Стоимость: {result[5]}₽")
            print(f"      Статус: {result[6]}")
            print(f"      Пользователь: {result[7]} ({result[8]})")
            print(f"      Telegram ID: {result[9]}")
        else:
            print(f"   ❌ Бронирование не найдено в базе")
        
        # 5. Очистка тестовых данных
        print(f"5. Очистка тестовых данных")
        cur.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        print(f"   🗑️  Тестовое бронирование удалено")
        
        print("\n✅ Тест завершен успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    test_bot_booking() 
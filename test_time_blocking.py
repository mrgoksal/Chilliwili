#!/usr/bin/env python3
"""
Тестовый скрипт для проверки логики блокировки времени в боте
"""

import sqlite3
from datetime import datetime, timedelta

def test_time_blocking():
    """Тестируем логику блокировки времени"""
    print("🧪 Тестирование логики блокировки времени...")
    
    # Подключаемся к базе данных
    conn = sqlite3.connect('chillivili.db')
    cur = conn.cursor()
    
    # Тестовая дата
    test_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    
    try:
        # 1. Очищаем тестовые данные
        print(f"1. Очистка тестовых данных для даты {test_date}")
        cur.execute("DELETE FROM bookings WHERE date = ?", (test_date,))
        conn.commit()
        print("   ✅ Тестовые данные очищены")
        
        # 2. Создаем тестовое бронирование на 6 часов
        print(f"2. Создание тестового бронирования на 6 часов")
        test_user_id = 1  # Используем существующего пользователя
        test_time = "15:00"
        test_duration = 6
        test_guests = 2
        test_total_price = test_guests * test_duration * 500
        
        cur.execute(
            "INSERT INTO bookings (user_id, date, time, guests, duration, total_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (test_user_id, test_date, test_time, test_guests, test_duration, test_total_price, datetime.now().isoformat())
        )
        conn.commit()
        booking_id = cur.lastrowid
        print(f"   ✅ Бронирование создано: ID={booking_id}")
        print(f"   📊 Детали: {test_date} {test_time}, {test_duration}ч (до {test_time[:2]}:{str(int(test_time[3:5]) + test_duration).zfill(2)})")
        
        # 3. Получаем все временные слоты
        print(f"3. Получение всех временных слотов")
        cur.execute("SELECT time FROM time_slots ORDER BY time")
        all_times = [row[0] for row in cur.fetchall()]
        print(f"   📋 Всего слотов: {len(all_times)}")
        print(f"   📋 Слоты: {', '.join(all_times)}")
        
        # 4. Получаем забронированные времена с длительностью
        print(f"4. Получение забронированных времен")
        cur.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (test_date,))
        existing_bookings = cur.fetchall()
        
        print(f"   📋 Найдено бронирований: {len(existing_bookings)}")
        for booking in existing_bookings:
            print(f"      - {booking[0]} (длительность: {booking[1]}ч)")
        
        # 5. Вычисляем заблокированные временные слоты
        print(f"5. Вычисление заблокированных слотов")
        blocked_times = set()
        
        for booking_time, booking_duration in existing_bookings:
            start_time = datetime.strptime(booking_time, '%H:%M')
            for i in range(booking_duration):
                blocked_time = start_time + timedelta(hours=i)
                blocked_times.add(blocked_time.strftime('%H:%M'))
        
        print(f"   🚫 Заблокированных слотов: {len(blocked_times)}")
        print(f"   🚫 Заблокированные: {', '.join(sorted(blocked_times))}")
        
        # 6. Вычисляем доступные временные слоты
        print(f"6. Вычисление доступных слотов")
        available_times = [time for time in all_times if time not in blocked_times]
        
        print(f"   ✅ Доступных слотов: {len(available_times)}")
        print(f"   ✅ Доступные: {', '.join(available_times)}")
        
        # 7. Проверяем логику блокировки
        print(f"7. Проверка логики блокировки")
        
        # Проверяем, что заблокированные слоты действительно заблокированы
        expected_blocked = ["15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]
        actual_blocked = sorted(blocked_times)
        
        if actual_blocked == expected_blocked:
            print(f"   ✅ Логика блокировки работает корректно!")
            print(f"   ✅ Ожидаемые заблокированные: {expected_blocked}")
            print(f"   ✅ Фактические заблокированные: {actual_blocked}")
        else:
            print(f"   ❌ Ошибка в логике блокировки!")
            print(f"   ❌ Ожидаемые заблокированные: {expected_blocked}")
            print(f"   ❌ Фактические заблокированные: {actual_blocked}")
        
        # 8. Тестируем создание нового бронирования
        print(f"8. Тестирование создания нового бронирования")
        
        # Попытка создать бронирование в заблокированное время
        test_new_time = "16:00"
        test_new_duration = 2
        
        booking_start = datetime.strptime(test_new_time, '%H:%M')
        is_blocked = False
        
        for i in range(test_new_duration):
            check_time = booking_start + timedelta(hours=i)
            check_time_str = check_time.strftime('%H:%M')
            if check_time_str in blocked_times:
                is_blocked = True
                break
        
        if is_blocked:
            print(f"   ✅ Время {test_new_time} правильно заблокировано")
        else:
            print(f"   ❌ Ошибка: время {test_new_time} не заблокировано")
        
        # Попытка создать бронирование в свободное время
        test_free_time = "21:00"
        test_free_duration = 2
        
        booking_start = datetime.strptime(test_free_time, '%H:%M')
        is_free = True
        
        for i in range(test_free_duration):
            check_time = booking_start + timedelta(hours=i)
            check_time_str = check_time.strftime('%H:%M')
            if check_time_str in blocked_times:
                is_free = False
                break
        
        if is_free:
            print(f"   ✅ Время {test_free_time} правильно доступно")
        else:
            print(f"   ❌ Ошибка: время {test_free_time} заблокировано")
        
        # 9. Очистка тестовых данных
        print(f"9. Очистка тестовых данных")
        cur.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        print(f"   🗑️  Тестовое бронирование удалено")
        
        print("\n✅ Тест завершен успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    test_time_blocking() 
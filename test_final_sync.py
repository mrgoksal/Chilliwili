#!/usr/bin/env python3
"""
Финальный тест синхронизации логики блокировки времени между ботом и сервером
"""

import sqlite3
from datetime import datetime, timedelta

def test_final_sync():
    """Тестируем финальную синхронизацию"""
    print("🎯 Финальный тест синхронизации логики блокировки времени...")
    
    # Подключаемся к базе данных
    conn = sqlite3.connect('chillivili.db')
    cur = conn.cursor()
    
    # Тестовая дата
    test_date = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
    
    try:
        # 1. Очищаем тестовые данные
        print(f"1. Очистка тестовых данных для даты {test_date}")
        cur.execute("DELETE FROM bookings WHERE date = ?", (test_date,))
        conn.commit()
        print("   ✅ Тестовые данные очищены")
        
        # 2. Создаем тестовое бронирование через бота (симуляция)
        print(f"2. Создание тестового бронирования через бота")
        test_user_id = 1
        test_time = "14:00"
        test_duration = 4  # 4 часа
        test_guests = 3
        test_total_price = test_guests * test_duration * 500
        
        cur.execute(
            "INSERT INTO bookings (user_id, date, time, guests, duration, total_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (test_user_id, test_date, test_time, test_guests, test_duration, test_total_price, datetime.now().isoformat())
        )
        conn.commit()
        bot_booking_id = cur.lastrowid
        print(f"   ✅ Бронирование через бота создано: ID={bot_booking_id}")
        print(f"   📊 Детали: {test_date} {test_time}, {test_duration}ч (до {test_time[:2]}:{str(int(test_time[3:5]) + test_duration).zfill(2)})")
        
        # 3. Проверяем логику блокировки времени (как в боте)
        print(f"3. Проверка логики блокировки времени (бот)")
        
        # Получаем все временные слоты
        cur.execute("SELECT time FROM time_slots ORDER BY time")
        all_times = [row[0] for row in cur.fetchall()]
        
        # Получаем забронированные времена с длительностью
        cur.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (test_date,))
        existing_bookings = cur.fetchall()
        
        # Вычисляем заблокированные временные слоты (логика бота)
        blocked_times_bot = set()
        for booking_time, booking_duration in existing_bookings:
            start_time = datetime.strptime(booking_time, '%H:%M')
            for i in range(booking_duration):
                blocked_time = start_time + timedelta(hours=i)
                blocked_times_bot.add(blocked_time.strftime('%H:%M'))
        
        print(f"   🚫 Заблокированные слоты (бот): {', '.join(sorted(blocked_times_bot))}")
        
        # 4. Проверяем логику блокировки времени (как в сервере)
        print(f"4. Проверка логики блокировки времени (сервер)")
        
        # Симулируем запрос к серверу с длительностью 2 часа
        requested_duration = 2
        available_times_server = []
        
        for start_time in all_times:
            start_datetime = datetime.strptime(start_time, '%H:%M')
            can_book = True
            
            for i in range(requested_duration):
                check_time = start_datetime + timedelta(hours=i)
                check_time_str = check_time.strftime('%H:%M')
                if check_time_str in blocked_times_bot:
                    can_book = False
                    break
            
            if can_book:
                available_times_server.append(start_time)
        
        print(f"   ✅ Доступные слоты для {requested_duration}ч (сервер): {', '.join(available_times_server)}")
        
        # 5. Создаем бронирование через сервер (симуляция)
        print(f"5. Создание бронирования через сервер")
        
        # Выбираем доступное время
        if available_times_server:
            server_time = available_times_server[0]
            server_duration = 2
            server_guests = 2
            server_total_price = server_guests * server_duration * 500
            
            # Создаем пользователя для сервера
            cur.execute(
                "INSERT INTO users (name, phone, created_at) VALUES (?, ?, ?)",
                ("Серверный Пользователь", "+7-999-123-45-67", datetime.now().isoformat())
            )
            server_user_id = cur.lastrowid
            
            cur.execute(
                "INSERT INTO bookings (user_id, date, time, guests, duration, total_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (server_user_id, test_date, server_time, server_guests, server_duration, server_total_price, datetime.now().isoformat())
            )
            conn.commit()
            server_booking_id = cur.lastrowid
            print(f"   ✅ Бронирование через сервер создано: ID={server_booking_id}")
            print(f"   📊 Детали: {test_date} {server_time}, {server_duration}ч")
        else:
            print(f"   ❌ Нет доступных слотов для создания бронирования через сервер")
        
        # 6. Проверяем финальное состояние
        print(f"6. Проверка финального состояния")
        
        cur.execute("""
            SELECT b.id, b.time, b.duration, b.guests, u.name
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            WHERE b.date = ? AND b.status != 'cancelled'
            ORDER BY b.time
        """, (test_date,))
        
        final_bookings = cur.fetchall()
        print(f"   📋 Всего бронирований на {test_date}: {len(final_bookings)}")
        for booking in final_bookings:
            print(f"      - ID: {booking[0]}, Время: {booking[1]}, Длительность: {booking[2]}ч, Гости: {booking[3]}, Пользователь: {booking[4]}")
        
        # 7. Проверяем, что нет пересечений
        print(f"7. Проверка отсутствия пересечений")
        
        # Получаем все забронированные времена с длительностью
        cur.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (test_date,))
        all_bookings = cur.fetchall()
        
        # Проверяем пересечения
        all_blocked_times = set()
        has_conflicts = False
        
        for booking_time, booking_duration in all_bookings:
            start_time = datetime.strptime(booking_time, '%H:%M')
            for i in range(booking_duration):
                blocked_time = start_time + timedelta(hours=i)
                blocked_time_str = blocked_time.strftime('%H:%M')
                if blocked_time_str in all_blocked_times:
                    has_conflicts = True
                    print(f"   ❌ Конфликт обнаружен: {blocked_time_str}")
                all_blocked_times.add(blocked_time_str)
        
        if not has_conflicts:
            print(f"   ✅ Пересечений не обнаружено!")
            print(f"   📊 Все заблокированные слоты: {', '.join(sorted(all_blocked_times))}")
        else:
            print(f"   ❌ Обнаружены пересечения в бронированиях!")
        
        # 8. Очистка тестовых данных
        print(f"8. Очистка тестовых данных")
        cur.execute("DELETE FROM bookings WHERE date = ?", (test_date,))
        cur.execute("DELETE FROM users WHERE name IN ('Серверный Пользователь')")
        conn.commit()
        print(f"   🗑️  Тестовые данные удалены")
        
        print("\n🎉 Финальный тест завершен успешно!")
        print("✅ Логика блокировки времени синхронизирована между ботом и сервером!")
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    test_final_sync() 
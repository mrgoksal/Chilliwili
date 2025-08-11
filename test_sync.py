#!/usr/bin/env python3
"""
Тестовый скрипт для проверки синхронизации между мини-приложением и ботом
"""

import sqlite3
from datetime import datetime

def test_database_sync():
    """Проверяем синхронизацию данных в базе"""
    conn = sqlite3.connect('chillivili.db')
    cur = conn.cursor()
    
    print("🔍 Проверка синхронизации базы данных...")
    
    # Проверяем структуру таблиц
    print("\n📋 Структура таблицы users:")
    cur.execute("PRAGMA table_info(users)")
    columns = cur.fetchall()
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
    print("\n📋 Структура таблицы bookings:")
    cur.execute("PRAGMA table_info(bookings)")
    columns = cur.fetchall()
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
    # Показываем последние бронирования
    print("\n📅 Последние бронирования:")
    cur.execute("""
        SELECT b.id, b.date, b.time, b.guests, b.duration, b.status, 
               u.name, u.phone, u.telegram_id
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        ORDER BY b.created_at DESC
        LIMIT 5
    """)
    
    bookings = cur.fetchall()
    if bookings:
        for booking in bookings:
            print(f"  ID: {booking[0]}, Дата: {booking[1]} {booking[2]}, "
                  f"Гости: {booking[3]}, Длительность: {booking[4]}ч, "
                  f"Статус: {booking[5]}, Имя: {booking[6]}, "
                  f"Телефон: {booking[7]}, Telegram ID: {booking[8]}")
    else:
        print("  Нет бронирований")
    
    # Показываем пользователей
    print("\n👥 Пользователи:")
    cur.execute("SELECT id, name, phone, telegram_id, created_at FROM users ORDER BY created_at DESC LIMIT 5")
    users = cur.fetchall()
    if users:
        for user in users:
            print(f"  ID: {user[0]}, Имя: {user[1]}, Телефон: {user[2]}, "
                  f"Telegram ID: {user[3]}, Создан: {user[4]}")
    else:
        print("  Нет пользователей")
    
    conn.close()
    print("\n✅ Проверка завершена!")

if __name__ == "__main__":
    test_database_sync() 
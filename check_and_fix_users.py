"""
Скрипт для проверки и исправления данных в таблице users.
Проверяет, не перепутались ли колонки name и phone.
"""
import asyncio
import aiosqlite

DB_PATH = "chillivili.db"

async def check_and_fix_users():
    """Проверяет структуру и данные таблицы users"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем структуру таблицы
        async with db.execute("PRAGMA table_info(users)") as cursor:
            columns = await cursor.fetchall()
            print("📋 Структура таблицы users:")
            print("="*60)
            for col in columns:
                print(f"  {col[1]}: {col[2]} (nullable: {col[3] == 0})")
            print("="*60)
            print()
        
        # Получаем первые 10 записей для анализа
        async with db.execute("""
            SELECT id, name, phone, telegram_id, username, created_at 
            FROM users 
            LIMIT 10
        """) as cursor:
            rows = await cursor.fetchall()
            
            print("📊 Примеры данных из таблицы users (первые 10 записей):")
            print("="*60)
            for row in rows:
                user_id, name, phone, tg_id, username, created_at = row
                print(f"ID: {user_id}")
                print(f"  name: '{name}'")
                print(f"  phone: '{phone}'")
                print(f"  telegram_id: {tg_id}")
                print(f"  username: '{username}'")
                print("-"*60)
            
            print()
            
            # Анализируем проблему
            print("🔍 Анализ проблемы:")
            print("="*60)
            
            # Проверяем, похожи ли значения в name на телефоны
            async with db.execute("""
                SELECT COUNT(*) 
                FROM users 
                WHERE name IS NOT NULL 
                AND name LIKE '%+%' 
                OR (name LIKE '%[0-9][0-9][0-9]%' AND LENGTH(name) BETWEEN 10 AND 15)
            """) as cursor:
                phone_like_names = (await cursor.fetchone())[0]
            
            # Проверяем, похожи ли значения в phone на имена
            async with db.execute("""
                SELECT COUNT(*) 
                FROM users 
                WHERE phone IS NOT NULL 
                AND phone NOT LIKE '%+%' 
                AND phone NOT LIKE '%[0-9]%'
                AND LENGTH(phone) > 5
            """) as cursor:
                name_like_phones = (await cursor.fetchone())[0]
            
            print(f"Записей, где name похож на телефон: {phone_like_names}")
            print(f"Записей, где phone похож на имя: {name_like_phones}")
            
            if phone_like_names > 0 or name_like_phones > 0:
                print("\n⚠️ Обнаружена проблема: колонки name и phone могут быть перепутаны!")
                print("\nХотите исправить? Это обменяет местами name и phone для всех записей.")
                print("(Запустите скрипт с параметром --fix для автоматического исправления)")
            else:
                print("\n✅ Колонки выглядят корректно")
            
            # Показываем статистику
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                total_users = (await cursor.fetchone())[0]
            
            async with db.execute("SELECT COUNT(*) FROM users WHERE name IS NULL OR name = ''") as cursor:
                empty_names = (await cursor.fetchone())[0]
            
            async with db.execute("SELECT COUNT(*) FROM users WHERE phone IS NULL OR phone = ''") as cursor:
                empty_phones = (await cursor.fetchone())[0]
            
            print(f"\n📊 Статистика:")
            print(f"  Всего пользователей: {total_users}")
            print(f"  Пустых имен: {empty_names}")
            print(f"  Пустых телефонов: {empty_phones}")

if __name__ == "__main__":
    asyncio.run(check_and_fix_users())


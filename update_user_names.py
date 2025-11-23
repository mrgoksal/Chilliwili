"""
Скрипт для обновления имен пользователей в базе данных.
Использует Telegram Bot API для получения актуальных имен пользователей.
"""
import asyncio
import aiosqlite
import os
from dotenv import load_dotenv
from aiogram import Bot

load_dotenv()

DB_PATH = "chillivili.db"
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def update_user_names():
    """Обновляет имена пользователей из Telegram API"""
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не установлен в .env файле")
        return
    
    bot = Bot(token=BOT_TOKEN)
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Получаем всех пользователей с telegram_id
            async with db.execute("""
                SELECT id, telegram_id, name, username 
                FROM users 
                WHERE telegram_id IS NOT NULL
            """) as cursor:
                users = await cursor.fetchall()
            
            updated_count = 0
            not_found_count = 0
            
            for user_id, telegram_id, current_name, current_username in users:
                try:
                    # Получаем информацию о пользователе через Telegram API
                    chat_member = await bot.get_chat(telegram_id)
                    
                    new_name = chat_member.full_name or "Пользователь"
                    new_username = chat_member.username
                    
                    # Обновляем, если имя None или пустое
                    if not current_name or current_name == "None" or current_name == "Пользователь":
                        await db.execute(
                            "UPDATE users SET name = ?, username = ? WHERE id = ?",
                            (new_name, new_username, user_id)
                        )
                        updated_count += 1
                        print(f"✅ Обновлен пользователь {telegram_id}: {new_name}")
                    elif new_username and (not current_username or current_username == "None"):
                        # Обновляем только username, если имя уже есть
                        await db.execute(
                            "UPDATE users SET username = ? WHERE id = ?",
                            (new_username, user_id)
                        )
                        print(f"✅ Обновлен username для {telegram_id}: @{new_username}")
                    
                except Exception as e:
                    not_found_count += 1
                    print(f"⚠️ Не удалось получить данные для {telegram_id}: {e}")
            
            await db.commit()
            print(f"\n📊 Статистика:")
            print(f"✅ Обновлено пользователей: {updated_count}")
            print(f"⚠️ Не найдено: {not_found_count}")
            print(f"📝 Всего проверено: {len(users)}")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    print("🔄 Начинаю обновление имен пользователей...")
    asyncio.run(update_user_names())
    print("✅ Готово!")


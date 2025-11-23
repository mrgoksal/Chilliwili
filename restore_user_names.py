"""
Скрипт для восстановления имен пользователей из базы данных.
Попытается восстановить имена через Telegram API, если есть telegram_id.
"""
import asyncio
import aiosqlite
import os
from dotenv import load_dotenv
from aiogram import Bot

load_dotenv()

DB_PATH = "chillivili.db"
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def restore_user_names():
    """Восстанавливает имена пользователей из Telegram API"""
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не установлен в .env файле")
        return
    
    bot = Bot(token=BOT_TOKEN)
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Получаем всех пользователей с None именем, но с telegram_id
            async with db.execute("""
                SELECT id, telegram_id, name, username 
                FROM users 
                WHERE (name IS NULL OR name = 'None' OR name = 'Пользователь')
                AND telegram_id IS NOT NULL
            """) as cursor:
                users = await cursor.fetchall()
            
            if not users:
                print("✅ Нет пользователей с пустыми именами для восстановления")
                return
            
            print(f"📊 Найдено {len(users)} пользователей с пустыми именами\n")
            
            updated_count = 0
            failed_count = 0
            skipped_count = 0
            
            for user_id, telegram_id, current_name, current_username in users:
                try:
                    # Пытаемся получить информацию о пользователе через Telegram API
                    chat = await bot.get_chat(telegram_id)
                    
                    new_name = chat.full_name or "Пользователь"
                    new_username = chat.username
                    
                    # Обновляем имя и username
                    await db.execute(
                        "UPDATE users SET name = ?, username = ? WHERE id = ?",
                        (new_name, new_username, user_id)
                    )
                    updated_count += 1
                    print(f"✅ [{updated_count}] Обновлен пользователь ID {user_id} (TG: {telegram_id}): {new_name}")
                    if new_username:
                        print(f"   Username: @{new_username}")
                    
                    # Небольшая задержка, чтобы не перегружать API
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    error_msg = str(e)
                    if "chat not found" in error_msg.lower() or "user not found" in error_msg.lower():
                        failed_count += 1
                        print(f"⚠️ [{failed_count}] Пользователь ID {user_id} (TG: {telegram_id}) не найден в Telegram")
                    elif "blocked" in error_msg.lower() or "forbidden" in error_msg.lower():
                        skipped_count += 1
                        print(f"⏭️ [{skipped_count}] Пользователь ID {user_id} (TG: {telegram_id}) заблокировал бота")
                    else:
                        failed_count += 1
                        print(f"❌ [{failed_count}] Ошибка для пользователя ID {user_id} (TG: {telegram_id}): {error_msg}")
            
            await db.commit()
            
            print(f"\n{'='*50}")
            print(f"📊 ИТОГОВАЯ СТАТИСТИКА:")
            print(f"{'='*50}")
            print(f"✅ Успешно обновлено: {updated_count}")
            print(f"⚠️ Не найдено в Telegram: {failed_count}")
            print(f"⏭️ Заблокировали бота: {skipped_count}")
            print(f"📝 Всего обработано: {len(users)}")
            print(f"{'='*50}")
            
            # Проверяем, остались ли еще пользователи с None именами
            async with db.execute("""
                SELECT COUNT(*) 
                FROM users 
                WHERE (name IS NULL OR name = 'None' OR name = 'Пользователь')
                AND telegram_id IS NOT NULL
            """) as cursor:
                remaining = (await cursor.fetchone())[0]
            
            if remaining > 0:
                print(f"\n⚠️ Осталось {remaining} пользователей с пустыми именами")
                print("   (возможно, они удалили аккаунт или заблокировали бота)")
            else:
                print("\n✅ Все пользователи с telegram_id имеют имена!")
            
            # Проверяем пользователей без telegram_id
            async with db.execute("""
                SELECT COUNT(*) 
                FROM users 
                WHERE telegram_id IS NULL
            """) as cursor:
                external_users = (await cursor.fetchone())[0]
            
            if external_users > 0:
                print(f"\n📌 Найдено {external_users} пользователей без telegram_id")
                print("   (это могут быть внешние бронирования, созданные админом)")
            
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bot.session.close()

if __name__ == "__main__":
    print("🔄 Начинаю восстановление имен пользователей...")
    print("="*50)
    asyncio.run(restore_user_names())
    print("\n✅ Готово!")


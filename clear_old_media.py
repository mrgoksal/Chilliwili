#!/usr/bin/env python3
"""
Скрипт для очистки старых медиа из базы данных
Удаляет старые file_id, которые были получены из админ-бота
"""

import asyncio
from db import delete_media_setting, get_all_settings

async def clear_old_media():
    """Очистка старых медиа"""
    print("🧹 Очистка старых медиа из базы данных...\n")
    
    # Получаем все настройки
    all_settings = await get_all_settings()
    
    # Находим все медиа
    media_keys = [k for k in all_settings.keys() if '_photo' in k or '_video' in k]
    
    if not media_keys:
        print("✅ Старых медиа не найдено")
        return
    
    print(f"📋 Найдено медиа: {media_keys}\n")
    
    # Удаляем все медиа
    sections = ["info", "help", "welcome"]
    media_types = ["photo", "video"]
    
    deleted = []
    for section in sections:
        for media_type in media_types:
            key = f"{section}_{media_type}"
            if key in media_keys:
                try:
                    await delete_media_setting(section, media_type)
                    deleted.append(key)
                    print(f"✅ Удалено: {key}")
                except Exception as e:
                    print(f"❌ Ошибка при удалении {key}: {e}")
    
    print(f"\n🎉 Удалено {len(deleted)} медиа файлов")
    print("💡 Теперь вы можете загрузить новые фото/видео через админ-панель")

if __name__ == "__main__":
    asyncio.run(clear_old_media())

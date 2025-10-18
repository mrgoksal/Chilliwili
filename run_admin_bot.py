#!/usr/bin/env python3
"""
Скрипт для запуска admin_bot.py с правильными переменными окружения
"""
import os
import asyncio

# Устанавливаем переменные окружения
os.environ['API_TOKEN'] = '8301933923:AAEWob9zepOD9bm-gIby59ldmlBq3SHmzWg'
os.environ['ADMIN_BOT_TOKEN'] = '8466893643:AAFJGHuMBZXSN4wQaW4sgfVbIO7H70PrHWs'
os.environ['ADMIN_USER_ID'] = '555469646'

# Импортируем и запускаем admin_bot
if __name__ == "__main__":
    print("🔐 Запуск админ-бота ЧиллиВили...")
    print("✅ Переменные окружения установлены")
    
    # Импортируем main функцию из admin_bot
    from admin_bot import main
    
    # Запускаем бота
    asyncio.run(main())


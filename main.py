#!/usr/bin/env python3
"""
Главный файл для запуска ботов ЧиллиВили
Запускает основной бот и админ-бот одновременно
"""

# Загрузка .env файла в самом начале
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ python-dotenv не установлен. Переменные окружения могут не загружаться из .env файла")
except Exception as e:
    print(f"⚠️ Ошибка загрузки .env: {e}")

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('chillivili_bots.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Импорты ботов
try:
    from bot import main as bot_main
    from admin_bot import main as admin_bot_main
except ImportError as e:
    logger.error(f"Ошибка импорта модулей: {e}")
    logger.error("Убедитесь, что файлы bot.py и admin_bot.py находятся в той же директории")
    sys.exit(1)

class BotManager:
    """Менеджер для управления бота"""
    
    def __init__(self):
        self.tasks = []
        self.shutdown_event = asyncio.Event()
        
    async def start_bots(self):
        """Запуск всех ботов"""
        logger.info("🚀 Запуск системы ЧиллиВили...")
        
        try:
            # Создаем задачи для каждого бота
            bot_task = asyncio.create_task(
                self._run_with_error_handling(bot_main, "Основной бот")
            )
            admin_bot_task = asyncio.create_task(
                self._run_with_error_handling(admin_bot_main, "Админ-бот")
            )
            
            self.tasks = [bot_task, admin_bot_task]
            
            logger.info("✅ Основной бот запущен")
            logger.info("✅ Админ-бот запущен")
            logger.info("🎉 Система ЧиллиВили полностью готова к работе!")
            
            # Ждем завершения всех задач
            await asyncio.gather(*self.tasks, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при запуске: {e}")
            raise
    
    async def _run_with_error_handling(self, bot_func, bot_name):
        """Запуск бота с обработкой ошибок"""
        try:
            await bot_func()
        except Exception as e:
            logger.error(f"❌ Ошибка в {bot_name}: {e}")
            # Не прерываем выполнение других ботов
            return
    
    async def shutdown(self):
        """Корректное завершение работы"""
        logger.info("🛑 Завершение работы системы...")
        
        # Отменяем все задачи
        for task in self.tasks:
            if not task.done():
                task.cancel()
        
        # Ждем завершения отмены
        await asyncio.gather(*self.tasks, return_exceptions=True)
        
        logger.info("✅ Система корректно завершена")

def setup_signal_handlers(bot_manager):
    """Настройка обработчиков сигналов для корректного завершения"""
    def signal_handler(signum, frame):
        logger.info(f"📡 Получен сигнал {signum}, завершение работы...")
        asyncio.create_task(bot_manager.shutdown())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

async def main():
    """Главная функция"""
    bot_manager = BotManager()
    
    # Настройка обработчиков сигналов
    setup_signal_handlers(bot_manager)
    
    try:
        await bot_manager.start_bots()
    except KeyboardInterrupt:
        logger.info("📡 Получен сигнал прерывания")
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка: {e}")
    finally:
        await bot_manager.shutdown()

if __name__ == "__main__":
    print("🏠 ЧиллиВили - Система управления бронированиями")
    print("=" * 50)
    print("🚀 Запуск ботов...")
    print("📝 Логи сохраняются в файл: chillivili_bots.log")
    print("🛑 Для остановки нажмите Ctrl+C")
    print("=" * 50)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Работа остановлена пользователем")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(1)

import os
import json
import asyncio
import logging
import signal
import aiohttp
import traceback
import platform
from dotenv import load_dotenv
from config import app_config as config
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update, Message, ErrorEvent
from aiogram.exceptions import TelegramAPIError
from config import Config
from datetime import datetime
from bot_controller import BotController
from pathlib import Path
from state_manager import StateManager
from rss_parser import AsyncRSSParser
from image_generator import AsyncImageGenerator
from yandex_gpt import AsyncYandexGPT
from telegram_interface import AsyncTelegramBot
from visual_interface import UIBuilder
from typing import Optional, Dict, Any, Union
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

logger = logging.getLogger('AsyncMain')
load_dotenv()

async def shutdown(loop, controller, connector):
    """Корректное завершение работы"""
    logger.info("Shutting down...")
    try:
        if controller:
            await controller.stop()
    except Exception as e:
        logger.error(f"Controller shutdown error: {str(e)}")
    
    if connector:
        try:
            await connector.close()
            logger.info("TCP connector closed")
        except Exception as e:
            logger.error(f"Error closing connector: {str(e)}")
    
    try:
        loop.stop()
    except RuntimeError:
        pass

def setup_logging(debug_mode: bool = False) -> None:
    """Настройка логирования"""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    level = logging.DEBUG if debug_mode else logging.INFO
    
    # Основной лог (ротация по размеру)
    file_handler = RotatingFileHandler(
        filename=os.path.join(log_dir, 'rss_bot.log'),
        maxBytes=10*1024*1024,  # 10 MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    
    # Лог ошибок (ротация по дням)
    error_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, 'errors.log'),
        when='midnight',
        backupCount=7,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(logging.Formatter(log_format))
    
    # Консольный вывод
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    
    # Настройка корневого логгера
    logging.basicConfig(
        level=level,
        format=log_format,
        handlers=[file_handler, error_handler, console_handler]
    )
    
    # Уменьшаем уровень логирования для шумных библиотек
    for lib in ['asyncio', 'aiohttp', 'PIL']:
        logging.getLogger(lib).setLevel(logging.WARNING)

async def test_bot_commands(bot: AsyncTelegramBot, owner_id: int):
    """Проверка возможности отправки сообщений"""
    try:
        await bot.bot.send_message(
            chat_id=owner_id,
            text="🤖 Бот успешно запущен и готов к работе! Для добавления новых RSS используйте команду /rss_add",
            parse_mode="HTML"
        )
        return True
    except Exception as e:
        logger.error(f"Test message failed: {str(e)}")
        return False

async def run_bot():
    logger.info("===== ASYNC BOT STARTING =====")
    
    # Инициализация конфигурации
    config = Config()
    setup_logging(config.DEBUG_MODE)
    logger.info("Configuration loaded successfully")
    
    # Проверка обязательных параметров
    if not config.TOKEN:
        logger.critical("TELEGRAM_TOKEN is required but not set")
        return
        
    if not config.CHANNEL_ID:
        logger.critical("CHANNEL_ID is required but not set")
        return
    
    # Инициализация StateManager
    state_manager = StateManager(config.STATE_FILE, config.MAX_ENTRIES_HISTORY, config)
    logger.info("State manager initialized")
    
    # Создаем TCP коннектор для aiohttp
    connector = aiohttp.TCPConnector(
        force_close=True,
        enable_cleanup_closed=True,
        limit=0
    )
    
    # Инициализируем переменные для управления ресурсами
    session: Optional[aiohttp.ClientSession] = None
    telegram_bot: Optional[AsyncTelegramBot] = None
    controller: Optional[BotController] = None
    polling_task: Optional[asyncio.Task] = None
    
    try:
        # Создаем aiohttp сессию
        session = aiohttp.ClientSession(connector=connector)
        logger.info("Created aiohttp session")
        
        # Инициализация Telegram бота
        telegram_bot = AsyncTelegramBot(
            token=config.TOKEN,
            channel_id=config.CHANNEL_ID,
            config=config
        )
        logger.info("Telegram bot initialized")

        # Блокировка не-владельцев
        @telegram_bot.dp.message()
        async def global_blocker(message: Message):
            if message.from_user.id != config.OWNER_ID:
                await message.answer("⛔ Доступ запрещен")
                return
        
        # Установка меню команд
        await telegram_bot.setup_commands()
        logger.info("Telegram commands menu initialized")
        
        # Проверка возможности отправки сообщений
        if config.OWNER_ID and not await test_bot_commands(telegram_bot, config.OWNER_ID):
            logger.error("Bot can't send messages, check TOKEN and OWNER_ID")
        
        # Инициализация компонентов
        rss_parser = AsyncRSSParser(session, config.PROXY_URL)
        yandex_gpt = AsyncYandexGPT(config, session)
        image_generator = AsyncImageGenerator(config)
        logger.info("All components initialized")
        
        # Создание контроллера
        controller = BotController(
            config=config,
            state_manager=state_manager,
            rss_parser=rss_parser,
            image_generator=image_generator,
            yandex_gpt=yandex_gpt,
            telegram_bot=telegram_bot
        )
        logger.info("Bot controller created")
        
        # Передаем контроллер в Telegram бота
        telegram_bot.controller = controller
        logger.info("Controller linked to Telegram bot")
        
        # Запуск контроллера
        if not await controller.start():
            raise RuntimeError("Failed to start bot controller")
        logger.info("RSS processing task started")
        
        # Инициализация диспетчера для обработки команд Telegram
        dp = telegram_bot.dp
        
        # Обработчик ошибок
        @dp.errors()
        async def errors_handler(event: ErrorEvent):
            logger.error(f"Update {event.update} caused error: {event.exception}")
            return True
        
        # Запуск обработки команд Telegram
        if telegram_bot and telegram_bot.bot:
            polling_task = asyncio.create_task(
                dp.start_polling(
                    telegram_bot.bot,
                    allowed_updates=dp.resolve_used_update_types()
                ),
                name="telegram_polling"
            )
            logger.info("Telegram polling task started")
        else:
            logger.warning("Skipping Telegram polling setup - bot not available")
        
        # Для Windows используем альтернативную обработку Ctrl+C
        if platform.system() == 'Windows':
            logger.info("Windows detected, using alternative signal handling")
            # Создаем задачу для отслеживания Ctrl+C
            async def windows_shutdown_handler():
                try:
                    while True:
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    logger.info("Ctrl+C received, shutting down")
                    await shutdown(asyncio.get_running_loop(), controller, connector)
            
            shutdown_task = asyncio.create_task(windows_shutdown_handler())
        else:
            # Для Unix-систем используем стандартные сигналы
            loop = asyncio.get_running_loop()
            for s in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    s, 
                    lambda s=s: asyncio.create_task(shutdown(loop, controller, connector))
                )
        
        logger.info("Bot started successfully. Press Ctrl+C to stop.")
        
        # Основной цикл ожидания
        try:
            while True:
                await asyncio.sleep(3600)  # Спим по 1 часу
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
            
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.critical(f"Fatal error in main loop: {str(e)}\n{traceback.format_exc()}")
    finally:
        logger.info("===== SHUTDOWN SEQUENCE STARTED =====")
        
        # Остановка контроллера
        if controller:
            try:
                await controller.stop()
                logger.info("Controller stopped")
            except Exception as e:
                logger.error(f"Error stopping controller: {str(e)}")
        
        # Отмена задачи опроса Telegram
        if polling_task and not polling_task.done():
            polling_task.cancel()
            try:
                await polling_task
                logger.info("Telegram polling task cancelled")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error cancelling polling task: {str(e)}")
        
        # Закрытие Telegram бота
        if telegram_bot:
            try:
                await telegram_bot.close()
                logger.info("Telegram bot closed")
            except Exception as e:
                logger.error(f"Error closing Telegram bot: {str(e)}")
        
        # Закрытие aiohttp сессии
        if session:
            try:
                await session.close()
                logger.info("aiohttp session closed")
            except Exception as e:
                logger.error(f"Error closing aiohttp session: {str(e)}")
        
        # Закрытие коннектора
        if connector:
            try:
                await connector.close()
                logger.info("TCP connector closed")
            except Exception as e:
                logger.error(f"Error closing connector: {str(e)}")
        
        logger.info("===== ASYNC BOT STOPPED =====")

if __name__ == "__main__":
    # Создаем новый цикл событий
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Запускаем основную корутину
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.critical(f"Top-level error: {str(e)}\n{traceback.format_exc()}")
    finally:
        # Гарантированная очистка ресурсов
        try:
            # Отменяем все оставшиеся задачи
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            
            # Ожидаем завершения задач
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            
            # Останавливаем и закрываем цикл
            loop.stop()
            loop.close()
            logger.info("Event loop stopped and closed")
        except Exception as e:
            logger.error(f"Error during final cleanup: {str(e)}")
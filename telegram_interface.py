from collections import deque
import os
import logging
from state_manager import StateManager
from typing import Optional, List
from aiogram import Bot, Dispatcher
from aiogram.types import Message, BotCommand, InputFile, FSInputFile, MenuButtonCommands
from aiogram.enums import MenuButtonType
from aiogram.filters import Command
from config import Config
from bot_controller import BotController

logger = logging.getLogger('AsyncTelegramBot')

class AsyncTelegramBot:
    def __init__(self, token: str, channel_id: str, config: Config):
        self.token = token
        self.channel_id = channel_id
        self.config = config
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.controller: Optional[BotController] = None
        
        self._register_handlers()
    
    async def setup_commands(self) -> None:
        """Устанавливает меню команд в строке ввода"""
        commands = [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="status", description="Статус бота"),
            BotCommand(command="stats", description="Статистика"),
            BotCommand(command="rss_list", description="Список RSS-лент"),
            BotCommand(command="rss_add", description="Добавить RSS"),
            BotCommand(command="rss_remove", description="Удалить RSS"),
            BotCommand(command="pause", description="Приостановить"),
            BotCommand(command="resume", description="Возобновить"),
            BotCommand(command="settings", description="Текущие настройки"),
            BotCommand(command="set", description="Изменить параметр"),
            BotCommand(command="clear_history", description="Очистить историю постов"),
        ]
        await self.bot.set_my_commands(commands)
        await self.bot.set_chat_menu_button(menu_button=MenuButtonCommands(type=MenuButtonType.COMMANDS))
        
    async def send_post(
        self,
        title: str,
        description: str,
        link: str,
        image_path: Optional[str] = None
    ) -> bool:
        """Отправляет пост в Telegram канал с подробным логированием"""
        try:
            # Сокращаем заголовок для логов
            log_title = title[:50] + "..." if len(title) > 50 else title
            
            # Форматируем текст поста
            post_text = f"<b>{title}</b>\n\n{description}\n\n<a href='{link}'>Читать далее</a>"
            
            # Отправляем сообщение
            if image_path:
                if not os.path.exists(image_path):
                    logger.error(f"Image not found: {image_path}")
                    return False
                    
                photo = FSInputFile(image_path)
                await self.bot.send_photo(
                    chat_id=self.channel_id,
                    photo=photo,
                    caption=post_text,
                    parse_mode="HTML"
                )
                logger.info(f"Photo post sent: {log_title}")
            else:
                await self.bot.send_message(
                    chat_id=self.channel_id,
                    text=post_text,
                    parse_mode="HTML"
                )
                logger.info(f"Text post sent: {log_title}")
                
            return True
        except Exception as e:
            logger.error(f"Failed to send post '{title[:30]}...': {str(e)}")
            
            # Логируем дополнительные детали для распространенных ошибок
            if "Chat not found" in str(e):
                logger.critical("CHANNEL_ID is invalid. Check channel permissions.")
            elif "Forbidden" in str(e):
                logger.critical("Bot has no access to the channel. Add bot as admin.")
            elif "Too Many Requests" in str(e):
                logger.warning("Telegram API rate limit exceeded. Reducing posting frequency.")
                
            return False
    
    def _register_handlers(self) -> None:
        self.dp.message.register(self.handle_start, Command("start", "help"))
        self.dp.message.register(self.handle_status, Command("status"))
        self.dp.message.register(self.handle_stats, Command("stats"))
        self.dp.message.register(self.handle_rss_list, Command("rss_list"))
        self.dp.message.register(self.handle_rss_add, Command("rss_add"))
        self.dp.message.register(self.handle_rss_remove, Command("rss_remove"))
        self.dp.message.register(self.handle_pause, Command("pause"))
        self.dp.message.register(self.handle_resume, Command("resume"))
        self.dp.message.register(self.handle_settings, Command("settings"))
        self.dp.message.register(self.handle_set, Command("set"))
        self.dp.message.register(self.handle_clear_history, Command("clear_history"))

    async def is_owner(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id == self.config.OWNER_ID

    async def handle_start(self, message: Message) -> None:
        help_text = (
            "🤖 <b>RSS Bot with AI Enhancement</b>\n\n"
            "📋 <b>Доступные команды:</b>\n"
            "/status - Статус бота\n"
            "/stats - Статистика\n"
            "/rss_list - Список RSS\n"
            "/rss_add [url] - Добавить RSS\n"
            "/rss_remove [N] - Удалить RSS\n"
            "/pause - Приостановить\n"
            "/resume - Продолжить\n"
            "/settings - Настройки\n"
            "/set [param] [value] - Изменить параметр\n\n"
            "Примеры:\n"
            "<code>/rss_add https://example.com/rss</code>\n"
            "<code>/set POSTS_PER_HOUR 10</code>"
        )
        await message.answer(help_text, parse_mode="HTML")

    async def handle_status(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if self.controller is None:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        status = self.controller.get_status_text()
        await message.answer(status, parse_mode="HTML")

    async def handle_stats(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if self.controller is None or not hasattr(self.controller, 'stats'):
            await message.answer("⚠️ Статистика недоступна")
            return
            
        stats = (
            "📊 <b>Статистика:</b>\n"
            f"Постов: {self.controller.stats.get('posts_sent', 0)}\n"
            f"Ошибок: {self.controller.stats.get('errors', 0)}\n"
            f"Изображений: {self.controller.stats.get('images_generated', 0)}\n"
            f"Дубликатов отклонено: {self.controller.stats.get('duplicates_rejected', 0)}\n"
            f"Использований YandexGPT: {self.controller.stats.get('yagpt_used', 0)}\n"
            f"Ошибок YandexGPT: {self.controller.stats.get('yagpt_errors', 0)}"
        )
        await message.answer(stats, parse_mode="HTML")

    async def handle_rss_list(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        lines: List[str] = ["📚 <b>Текущие RSS-ленты:</b>"]
        for i, url in enumerate(self.config.RSS_URLS, 1):
            lines.append(f"{i}. {url}")
        
        lines.append("\nДля удаления используйте:")
        lines.append("<code>/rss_remove [номер]</code>")
        lines.append("Пример: <code>/rss_remove 2</code>")
        
        await message.answer("\n".join(lines), parse_mode="HTML")

    async def handle_rss_add(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if message.text is None:
            await message.answer("❌ Неверный формат команды")
            return
            
        args = message.text.split()
        if len(args) < 2:
            await message.answer("❌ Укажите URL RSS-ленты")
            return
        
        new_url = args[1]
        if new_url in self.config.RSS_URLS:
            await message.answer("⚠️ Эта RSS-лента уже есть в списке")
            return
        
        self.config.RSS_URLS.append(new_url)
        await message.answer(f"✅ RSS-лента добавлена: {new_url}")

    async def handle_rss_remove(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if message.text is None:
            await message.answer("❌ Неверный формат команды")
            return
            
        args = message.text.split()
        if len(args) < 2:
            await message.answer("❌ Укажите номер RSS-ленты для удаления")
            return
        
        try:
            index = int(args[1]) - 1
            if 0 <= index < len(self.config.RSS_URLS):
                removed = self.config.RSS_URLS.pop(index)
                await message.answer(f"✅ RSS-лента удалена: {removed}")
            else:
                await message.answer("❌ Неверный номер RSS-ленты")
        except ValueError:
            await message.answer("❌ Укажите корректный номер")

    async def handle_pause(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if self.controller is None:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        if self.controller.is_running:
            await self.controller.stop()
            await message.answer("⏸️ Публикации остановлены")
        else:
            await message.answer("ℹ️ Бот уже остановлен")

    async def handle_resume(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if self.controller is None:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        if not self.controller.is_running:
            await self.controller.start()
            await message.answer("▶️ Публикации возобновлены")
        else:
            await message.answer("ℹ️ Бот уже работает")

    async def handle_settings(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        source_mapping = {
            'template': 'Шаблоны',
            'original': 'Оригиналы',
            'none': 'Нет'
        }
        
        settings = (
            "⚙️ <b>Текущие настройки:</b>\n"
            f"YandexGPT: {'🟢 Вкл' if not self.config.DISABLE_YAGPT else '🔴 Выкл'}\n"
            f"Изображения: {'🟢 Вкл' if self.config.ENABLE_IMAGE_GENERATION else '🔴 Выкл'}\n"
            f"Источник изображений: {source_mapping.get(self.config.IMAGE_SOURCE, 'Неизвестно')}\n"
            f"Резервная генерация: {'🟢 Вкл' if self.config.IMAGE_FALLBACK else '🔴 Выкл'}\n"
            f"Постов/час: {self.config.POSTS_PER_HOUR}\n"
            f"Модель YandexGPT: {self.config.YAGPT_MODEL}"
        )
        await message.answer(settings, parse_mode="HTML")

    async def handle_set(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if message.text is None:
            await message.answer("❌ Неверный формат команды")
            return
            
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Используйте: /set [параметр] [значение]")
            return
        
        param = args[1].upper()
        value = " ".join(args[2:])
        
        ALLOWED_PARAMS = {
            'POSTS_PER_HOUR': {
                'type': int,
                'validator': lambda x: 1 <= x <= 60,
                'error_msg': 'Должно быть целое число от 1 до 60'
            },
            'MIN_DELAY_BETWEEN_POSTS': {
                'type': int,
                'validator': lambda x: x >= 10,
                'error_msg': 'Минимальная задержка 10 секунд'
            },
            'CHECK_INTERVAL': {
                'type': int,
                'validator': lambda x: x >= 60,
                'error_msg': 'Интервал проверки не менее 60 секунд'
            },
            'ENABLE_IMAGE_GENERATION': {
                'type': bool,
                'validator': None,
                'error_msg': ''
            },
            'DISABLE_YAGPT': {
                'type': bool,
                'validator': None,
                'error_msg': ''
            },
            'YAGPT_MODEL': {
                'type': str,
                'validator': lambda x: x in ['yandexgpt-lite', 'yandexgpt-pro'],
                'error_msg': 'Допустимые модели: yandexgpt-lite, yandexgpt-pro'
            },
            'YAGPT_TEMPERATURE': {
                'type': float,
                'validator': lambda x: 0.1 <= x <= 1.0,
                'error_msg': 'Температура должна быть от 0.1 до 1.0'
            }
        }
        
        if param not in ALLOWED_PARAMS:
            await message.answer(f"❌ Параметр {param} недоступен для изменения")
            return
        
        param_info = ALLOWED_PARAMS[param]
        param_type = param_info['type']
        
        try:
            if param_type is bool:
                converted_value = value.lower() in ['true', '1', 'yes', 'on']
            else:
                converted_value = param_type(value)
            
            if param_info['validator'] and not param_info['validator'](converted_value):
                raise ValueError(param_info['error_msg'])
            
            setattr(self.config, param, converted_value)
            await message.answer(f"✅ Параметр {param} обновлен на {value}")
            
            # Сохраняем изменения в .env файл
            self.config.save_to_env_file(param, str(converted_value))
        except (TypeError, ValueError) as e:
            await message.answer(f"❌ Ошибка: {str(e)}")

# Исправляем работу с историей сообщений
    async def handle_clear_history(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if self.controller is None:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        try:
            # Сбрасываем историю отправленных постов
            self.controller.state.state['sent_entries'] = {}
            await message.answer("✅ История отправленных постов очищена! Бот будет повторно отправлять новости.")
        except Exception as e:
            logger.error(f"Error clearing history: {str(e)}")
            await message.answer(f"❌ Ошибка при очистке истории: {str(e)}")

    async def close(self) -> None:
        await self.bot.session.close()
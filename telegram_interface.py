import asyncio
from collections import deque
import os
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from state_manager import StateManager
from typing import Optional, List, Dict, Any, Union
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.types import Message, BotCommand, InputFile, FSInputFile, MenuButtonCommands, CallbackQuery, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import MenuButtonType
from aiogram.filters import Command
from config import Config
from bot_controller import BotController
from visual_interface import UIBuilder
from aiogram.types import BufferedInputFile
from aiogram.types import Message as TelegramMessage

logger = logging.getLogger('AsyncTelegramBot')

class AsyncTelegramBot:
    def __init__(self, token: str, channel_id: str, config: Config):
        self.token = token
        self.channel_id = channel_id
        self.config = config
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.controller: Optional[BotController] = None
        self.ui = UIBuilder(config)
        
        self._register_handlers()
    
    async def setup_commands(self) -> None:
        """Устанавливает меню команд в строке ввода"""
        commands = [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="menu", description="Открыть панель управления"),
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
            BotCommand(command="params_list", description="Список всех параметров"),
            BotCommand(command="param_info", description="Информация о параметре"),
            BotCommand(command="set_all", description="Изменить любой параметр"),
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
        """Отправляет пост в Telegram канал"""
        try:
            post_text = f"<b>{title}</b>\n\n{description}\n\n<a href='{link}'>Читать далее</a>"
            
            if image_path:
                if not os.path.exists(image_path):
                    logger.error(f"Изображение не найдено: {image_path}")
                    return False
                    
                photo = FSInputFile(image_path)
                await self.bot.send_photo(
                    chat_id=self.channel_id,
                    photo=photo,
                    caption=post_text,
                    parse_mode="HTML"
                )
                logger.info(f"Отправлен пост с изображением: {title[:50]}...")
            else:
                await self.bot.send_message(
                    chat_id=self.channel_id,
                    text=post_text,
                    parse_mode="HTML"
                )
                logger.info(f"Отправлен текстовый пост: {title[:50]}...")
                
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки поста '{title[:30]}...': {str(e)}")
            return False
    
    def _register_handlers(self) -> None:
        self.dp.message.register(self.handle_start, Command("start", "help", "menu"))
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
        self.dp.message.register(self.handle_params_list, Command("params_list"))
        self.dp.message.register(self.handle_param_info, Command("param_info"))
        self.dp.message.register(self.handle_set_all, Command("set_all"))
        
        self.dp.callback_query.register(self.handle_callback)
    
    async def handle_callback(self, callback: CallbackQuery) -> None:
        """Основной обработчик callback'ов"""
        try:
            if not callback.message or not isinstance(callback.message, TelegramMessage):
                await callback.answer("Ошибка сообщения")
                return

            user_id = callback.from_user.id
            chat_id = callback.message.chat.id
            data = callback.data

            logger.debug(f"Callback от пользователя {user_id}: {data}")
            
            if data == "main_menu":
                await self.send_main_menu(user_id, chat_id)
            elif data == "main" or data == "main_menu":
                await self.send_main_menu(user_id, chat_id)
            elif data == "stats":
                await self.show_statistics(callback)
            elif data == "monitoring":
                await self.show_monitoring(callback)
            elif data == "settings":
                await self.show_settings_menu(callback)
            elif data == "settings_general":
                await self.show_general_settings(callback)
            elif data == "settings_images":
                await self.show_image_settings(callback)
            elif data == "settings_ai":
                await self.show_ai_settings(callback)
            elif data == "rss_list":
                await self.handle_rss_list(callback)
            elif data == "settings_rss":
                await self.show_rss_settings(callback)
            elif data == "settings_notify":
                await self.show_notify_settings(callback)
            elif data == "change_theme":
                await self.show_theme_selector(callback)
            elif data.startswith("set_theme_"):
                await self.set_theme(callback)
            elif data == "start_bot":
                await self.handle_start_bot(callback)
            elif data == "stop_bot":
                await self.handle_stop_bot(callback)
            elif data == "back_to_settings":
                await self.show_settings_menu(callback)
            
            # Основные настройки
            elif data == "settings_general":
                await self.show_general_settings(callback)
            elif data == "edit_general_settings":
                await self.edit_general_settings(callback)
            elif data.startswith("edit_general_"):
                await self.edit_general_param(callback)
            elif data.startswith("set_general_"):
                await self.set_general_param(callback)
            elif data == "save_general_settings":
                await self.save_general_settings(callback)
            elif data == "cancel_general_edit":
                await self.cancel_general_edit(callback)

            # AI настройки
            elif data == "settings_ai":
                await self.show_ai_settings(callback)
            elif data == "edit_ai_settings":
                await self.edit_ai_settings(callback)
            elif data == "save_ai_settings":
                await self.save_ai_settings(callback)
            elif data == "cancel_ai_edit":
                await self.cancel_ai_edit(callback)
            elif data.startswith("edit_ai_"):  # edit_ai_model, edit_ai_temp, edit_ai_tokens
                await self.edit_ai_param(callback)
            elif data.startswith("set_ai_model:"):
                await self.set_ai_model(callback)
            elif data.startswith("set_ai_temp:"):
                await self.set_ai_temp(callback)
            elif data == "set_ai_temp_custom":
                await self.set_ai_temp_custom(callback)
            elif data.startswith("set_ai_tokens:"):
                await self.set_ai_tokens(callback)
            elif data == "set_ai_tokens_custom":
                await self.set_ai_tokens_custom(callback)
            
            # RSS настройки
            # RSS настройки
            elif data == "settings_rss":
                await self.show_rss_settings(callback)
            elif data == "edit_rss_settings":
                await self.show_rss_settings(callback, edit_mode=True)
            elif data == "save_rss_settings":
                await callback.answer("Настройки RSS сохранены")
                await self.show_rss_settings(callback)
            elif data == "rss_add_start":
                await self.start_rss_add(callback)
            elif data == "rss_remove_start":
                await self.start_rss_remove(callback)
            elif data.startswith("rss_remove_"):
                await self.confirm_rss_remove(callback)
            elif data.startswith("rss_toggle_"):
                await self.toggle_rss_feed(callback)
            elif data == "rss_refresh":
                await self.refresh_rss_status(callback)

            else:
                logger.warning(f"Неизвестный callback: {data}")
                await callback.answer("Функция в разработке")

            await callback.answer()
        except Exception as e:
            logger.error(f"Ошибка обработки callback: {str(e)}", exc_info=True)
            await callback.answer("Ошибка обработки запроса")

    async def show_monitoring(self, callback: CallbackQuery) -> None:
        """Показывает панель мониторинга"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        stats = self.controller.get_status_text()
        await self.bot.send_message(
            chat_id=callback.message.chat.id,
            text=stats,
            parse_mode="HTML"
        )

    async def set_theme(self, callback: CallbackQuery) -> None:
        """Устанавливает тему оформления"""
        theme_name = callback.data.replace("set_theme_", "")
        if theme_name in self.ui.THEMES:
            self.ui.user_themes[callback.from_user.id] = self.ui.THEMES[theme_name]
            await callback.answer(f"Тема изменена на {theme_name}")
            await self.show_settings_menu(callback)
        else:
            await callback.answer("Неизвестная тема")

    async def show_general_settings(self, callback: CallbackQuery) -> None:
        """Показывает общие настройки"""
        text = (
            "⚙️ <b>Общие настройки</b>\n\n"
            f"• Интервал проверки: {self.config.CHECK_INTERVAL} сек\n"
            f"• Макс. постов за цикл: {self.config.MAX_POSTS_PER_CYCLE}\n"
            f"• Постов в час: {self.config.POSTS_PER_HOUR}\n"
            f"• Мин. задержка между постами: {self.config.MIN_DELAY_BETWEEN_POSTS} сек"
        )
        
        keyboard = await self.ui.back_to_settings()
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    async def show_ai_settings(self, callback: CallbackQuery, edit_mode: bool = False) -> None:
        """Показывает настройки AI"""
        text, keyboard = await self.ui.ai_settings_view(callback.from_user.id, edit_mode)
        
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )

    async def show_general_settings(self, callback: CallbackQuery, edit_mode: bool = False):
        """Показывает основные настройки"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        text, keyboard = await self.ui.general_settings_view(
            callback.from_user.id, 
            edit_mode
        )
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def edit_general_settings(self, callback: CallbackQuery):
        """Вход в режим редактирования"""
        await self.ui.start_general_edit(callback.from_user.id)
        await self.show_general_settings(callback, edit_mode=True)
    
    async def edit_general_param(self, callback: CallbackQuery):
        """Обработка выбора параметра"""
        param = callback.data.replace("edit_general_", "")
        keyboard = await self.ui.general_param_selector(callback.from_user.id, param)
        await callback.message.edit_text(f"Выберите значение для {param}:", reply_markup=keyboard)
    
    async def set_general_param(self, callback: CallbackQuery):
        """Установка значения параметра"""
        _, param, value = callback.data.split("_", 2)
        param = param.lower()
        value = float(value) if "." in value else int(value)
        
        await self.ui.update_general_setting(
            callback.from_user.id,
            param,
            value
        )
        await self.show_general_settings(callback, edit_mode=True)
        await callback.answer(f"Значение обновлено: {value}")
    
    async def save_general_settings(self, callback: CallbackQuery):
        """Сохранение изменений"""
        try:
            changes = await self.ui.save_general_settings(callback.from_user.id)
            if not changes:
                await callback.answer("Настройки не изменены")
                return
            
            # Применение изменений в конфигурации
            for param, value in changes.items():
                self.config.update_param(param, value)
            
            # Формирование отчета
            changes_text = "\n".join([f"• {param}: {value}" for param, value in changes.items()])
            text = f"✅ Основные настройки обновлены:\n\n{changes_text}"
            
            await callback.message.edit_text(text)
            await asyncio.sleep(3)
            await self.show_general_settings(callback)
            
        except Exception as e:
            logger.error(f"Ошибка сохранения: {str(e)}")
            await callback.answer("Ошибка сохранения настроек", show_alert=True)

    async def edit_ai_settings(self, callback: CallbackQuery) -> None:
        """Переходит в режим редактирования настроек AI"""
        await self.ui.start_ai_edit(callback.from_user.id)
        await self.show_ai_settings(callback, edit_mode=True)
        await callback.answer()

    async def edit_ai_param(self, callback: CallbackQuery) -> None:
        """Обрабатывает выбор параметра для редактирования"""
        param_type = callback.data.replace("edit_ai_", "")
        user_id = callback.from_user.id
        
        if param_type == "model":
            keyboard = await self.ui.ai_model_selector(user_id)
            text = "Выберите модель:"
        elif param_type == "temp":
            keyboard = await self.ui.ai_temp_selector(user_id)
            text = "Выберите температуру (0.1-1.0):"
        elif param_type == "tokens":
            keyboard = await self.ui.ai_tokens_selector(user_id)
            text = "Выберите максимальное количество токенов:"
        else:
            await callback.answer("Неизвестный параметр")
            return
        
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        await callback.answer()

    async def set_ai_model(self, callback: CallbackQuery) -> None:
        """Устанавливает выбранную модель"""
        model = callback.data.split(":")[1]
        await self.ui.update_ai_setting(callback.from_user.id, "model", model)
        await self.show_ai_settings(callback, edit_mode=True)
        await callback.answer(f"Модель изменена на {model}")

    async def set_ai_temp(self, callback: CallbackQuery) -> None:
        """Устанавливает температуру из предустановленных значений"""
        temp = float(callback.data.split(":")[1])
        await self.ui.update_ai_setting(callback.from_user.id, "temperature", temp)
        await self.show_ai_settings(callback, edit_mode=True)
        await callback.answer(f"Температура изменена на {temp}")

    async def set_ai_temp_custom(self, callback: CallbackQuery) -> None:
        """Запрашивает ручной ввод температуры"""
        await callback.message.answer(
            "Введите значение температуры (0.1-1.0):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="edit_ai_settings")]]
            )
        )
        # Здесь будет логика ожидания ввода пользователя (реализуется в handle_message)
        await callback.answer()

    async def set_ai_tokens(self, callback: CallbackQuery) -> None:
        """Устанавливает токены из предустановленных значений"""
        tokens = int(callback.data.split(":")[1])
        await self.ui.update_ai_setting(callback.from_user.id, "max_tokens", tokens)
        await self.show_ai_settings(callback, edit_mode=True)
        await callback.answer(f"Макс. токенов изменено на {tokens}")

    async def set_ai_tokens_custom(self, callback: CallbackQuery) -> None:
        """Запрашивает ручной ввод количества токенов"""
        await callback.message.answer(
            "Введите максимальное количество токенов (500-10000):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="edit_ai_settings")]]
            )
        )
        # Здесь будет логика ожидания ввода пользователя (реализуется в handle_message)
        await callback.answer()

    async def save_ai_settings(self, callback: CallbackQuery) -> None:
        """Сохраняет изменения настроек AI"""
        try:
            changes = await self.ui.save_ai_settings(callback.from_user.id)
            
            if not changes:
                await callback.answer("Настройки не изменены")
                await self.show_ai_settings(callback)
                return
            
            # Применяем изменения в конфигурации
            for param, value in changes.items():
                self.config.update_param(param, value)
                logger.info(f"Параметр {param} изменен на {value}")
            
            # Формируем сообщение об изменениях
            changes_text = "\n".join([f"• {param}: {value}" for param, value in changes.items()])
            text = f"✅ Настройки успешно обновлены:\n\n{changes_text}"
            
            await callback.message.edit_text(
                text=text,
                parse_mode="HTML"
            )
            await asyncio.sleep(3)
            await self.show_ai_settings(callback)
            
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек AI: {str(e)}")
            await callback.answer("Ошибка сохранения настроек", show_alert=True)

    async def cancel_ai_edit(self, callback: CallbackQuery) -> None:
        """Отменяет редактирование настроек AI"""
        await self.ui.cancel_ai_edit(callback.from_user.id)
        await self.show_ai_settings(callback)
        await callback.answer("Редактирование отменено")

    # RSS настройки
    async def show_rss_settings(self, callback: CallbackQuery):
        """Показывает настройки RSS"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        feeds = self.controller.get_rss_status()
        text, keyboard = await self.ui.rss_settings_view(feeds)
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def start_rss_add(self, callback: CallbackQuery):
        """Начало добавления RSS"""
        keyboard = await self.ui.rss_add_dialog()
        await callback.message.edit_text(
            "Введите URL новой RSS-ленты:",
            reply_markup=keyboard
        )
        # Ожидание ввода реализуется в handle_message
    
    async def start_rss_remove(self, callback: CallbackQuery):
        """Начало удаления RSS"""
        feeds = self.controller.get_rss_status()
        keyboard = await self.ui.rss_remove_selector(feeds)
        await callback.message.edit_text(
            "Выберите ленту для удаления:",
            reply_markup=keyboard
        )
    
    async def confirm_rss_remove(self, callback: CallbackQuery):
        """Подтверждение удаления RSS"""
        index = int(callback.data.split("_")[-1])
        feeds = self.controller.get_rss_status()
        
        if 0 <= index < len(feeds):
            removed = self.config.RSS_URLS.pop(index)
            await callback.answer(f"✅ RSS удалена: {removed}")
        else:
            await callback.answer("❌ Неверный индекс")
        
        await self.show_rss_settings(callback)
    
    async def toggle_rss_feed(self, callback: CallbackQuery):
        """Включение/выключение RSS-ленты"""
        try:
            # Извлечение индекса и действия
            parts = callback.data.split("_")
            index = int(parts[2])
            action = parts[3]
        except (IndexError, ValueError) as e:
            logger.error(f"Ошибка парсинга: {callback.data} - {str(e)}")
            await callback.answer("❌ Ошибка формата команды")
            return
        
        # Логика активации/деактивации
        success = await self.controller.toggle_rss_feed(index, action == "enable")
        
        if success:
            status = "активирована" if action == "enable" else "деактивирована"
            await callback.answer(f"✅ Лента {index+1} {status}")
        else:
            await callback.answer("❌ Ошибка изменения статуса")
        
        await self.show_rss_settings(callback)

    # В обработчик сообщений нужно добавить:
    async def handle_message(self, message: Message) -> None:
        """Обработчик текстовых сообщений"""
        if not await self.is_owner(message):
            return
            
        # Обработка ручного ввода значений
        if message.reply_to_message:
            reply_text = message.reply_to_message.text
            
            if "температуры" in reply_text:
                try:
                    temp = float(message.text)
                    if 0.1 <= temp <= 1.0:
                        await self.ui.update_ai_setting(message.from_user.id, "temperature", temp)
                        await self.show_ai_settings(message, edit_mode=True)
                        await message.answer(f"✅ Температура установлена: {temp}")
                    else:
                        await message.answer("❌ Значение должно быть между 0.1 и 1.0")
                except ValueError:
                    await message.answer("❌ Введите число (например: 0.7)")
                    
            elif "токенов" in reply_text:
                try:
                    tokens = int(message.text)
                    if 500 <= tokens <= 10000:
                        await self.ui.update_ai_setting(message.from_user.id, "max_tokens", tokens)
                        await self.show_ai_settings(message, edit_mode=True)
                        await message.answer(f"✅ Макс. токенов установлено: {tokens}")
                    else:
                        await message.answer("❌ Значение должно быть между 500 и 10000")
                except ValueError:
                    await message.answer("❌ Введите целое число (например: 2500)")

    async def show_rss_settings(self, callback: CallbackQuery, edit_mode: bool = False):
        """Показывает настройки RSS с возможностью редактирования"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        feeds = self.controller.get_rss_status()
        text, keyboard = await self.ui.rss_settings_view(feeds, edit_mode)
        await callback.message.edit_text(text, reply_markup=keyboard)

    async def show_notify_settings(self, callback: CallbackQuery) -> None:
        """Показывает настройки уведомлений"""
        text = (
            "🔔 <b>Настройки уведомлений</b>\n\n"
            "Здесь будут настройки уведомлений\n"
            "Функция в разработке"
        )
        
        keyboard = await self.ui.back_to_settings()
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def handle_start(self, message: Message) -> None:
        if not await self.enforce_owner_access(message):
            return
        await self.send_main_menu(message.from_user.id, message.chat.id)
    
    async def send_main_menu(self, user_id: int, chat_id: int) -> None:
        """Отправляет главное меню"""
        keyboard = await self.ui.main_menu(user_id)
        if not keyboard:
            return  # Уже обработано в ui
        
        await self.bot.send_message(
            chat_id=chat_id,
            text="🤖 <b>Управление RSS Ботом</b>\n\nВыберите действие:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def show_statistics(self, callback: CallbackQuery) -> None:
        """Отображает статистику"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        stats = self.controller.stats
        text, media = await self.ui.stats_visualization(stats)
        
        if media:
            await self.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=media.media,
                caption=text,
                parse_mode="HTML"
            )
        else:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                parse_mode="HTML"
            )
    
    async def show_settings_menu(self, callback: CallbackQuery) -> None:
        """Показывает меню настроек"""
        keyboard = await self.ui.settings_menu(callback.from_user.id)
        
        try:
            await callback.message.edit_text(
                "⚙️ <b>Настройки бота</b>\n\nВыберите категорию:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text="⚙️ <b>Настройки бота</b>\n\nВыберите категорию:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    
    async def show_image_settings(self, callback: CallbackQuery) -> None:
        """Показывает настройки изображений"""
        text, media = await self.ui.image_settings_view(callback.from_user.id)
        
        if media:
            await self.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=media.media,
                caption=text,
                parse_mode="HTML"
            )
        else:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                parse_mode="HTML"
            )
    
    async def show_theme_selector(self, callback: CallbackQuery) -> None:
        """Показывает выбор тем оформления"""
        keyboard = await self.ui.theme_selector(callback.from_user.id)
        
        try:
            await callback.message.edit_text(
                "🎨 <b>Выбор темы оформления</b>\n\nВыберите стиль интерфейса:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text="🎨 <b>Выбор темы оформления</b>\n\nВыберите стиль интерфейса:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    
    async def handle_start_bot(self, callback: CallbackQuery) -> None:
        """Обработка запуска бота"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        await self.ui.animated_processing(callback.message, "Запуск бота")
        
        if not self.controller.is_running:
            await self.controller.start()
            await callback.answer("✅ Бот успешно запущен")
    
    async def handle_stop_bot(self, callback: CallbackQuery) -> None:
        """Обработка остановки бота"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        await self.ui.animated_processing(callback.message, "Остановка бота")
        
        if self.controller.is_running:
            await self.controller.stop()
            await callback.answer("⏸ Бот остановлен")

    async def handle_status(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if not self.controller:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        status = self.controller.get_status_text()
        await message.answer(status, parse_mode="HTML")

    async def handle_stats(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if not self.controller or not hasattr(self.controller, 'stats'):
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

    async def handle_rss_list(self, callback: CallbackQuery) -> None:
        """Отправляет список RSS-лент как ответ на callback"""
        if not await self.enforce_owner_access(callback):
            return
            
        try:
            if not self.controller:
                await callback.answer("⚠️ Контроллер не подключен")
                return
                
            feeds = self.controller.get_rss_status()
            lines = ["📡 <b>Статус RSS-лент</b>\n"]
            
            for i, feed in enumerate(feeds, 1):
                status_icon = '🟢' if feed.get('active', True) else '🔴'
                error_icon = f" | ❗️ {feed.get('error_count', 0)}" if feed.get('error_count', 0) > 0 else ""
                last_check = f" | 📅 {feed.get('last_check', 'никогда')}" if feed.get('last_check') else ""
                lines.append(f"{i}. {status_icon} {feed['url'][:50]}...{error_icon}{last_check}")
            
            # Создаем клавиатуру с кнопкой "Назад"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")]
            ])
            
            # Отправляем сообщение
            await callback.message.answer(
                text="\n".join(lines),
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await callback.answer()
        except Exception as e:
            logger.error(f"Error showing RSS list: {str(e)}")
            await callback.answer("Ошибка получения списка лент", show_alert=True)
            
    async def is_owner(self, message: Message) -> bool:
        return message.from_user and message.from_user.id == self.config.OWNER_ID

    async def enforce_owner_access(self, message_or_callback: Union[Message, CallbackQuery]) -> bool:
        """Проверяет доступ и уведомляет о попытках несанкционированного доступа"""
        user_id = message_or_callback.from_user.id
        if user_id == self.config.OWNER_ID:
            return True
            
        # Логирование и уведомление
        username = f"@{message_or_callback.from_user.username}" if message_or_callback.from_user.username else "без username"
        logger.warning(f"Unauthorized access attempt: UserID={user_id} {username}")
        
        # Отправка предупреждения владельцу
        try:
            await self.bot.send_message(
                chat_id=self.config.OWNER_ID,
                text=f"⚠️ *Попытка доступа!*\n"
                    f"• Пользователь: {username}\n"
                    f"• ID: `{user_id}`\n"
                    f"• Команда: `{getattr(message_or_callback, 'text', message_or_callback.data)}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send owner alert: {e}")
        
        # Ответ нарушителю
        try:
            if isinstance(message_or_callback, Message):
                await message_or_callback.answer("🚫 Доступ запрещен!")
            else:
                await message_or_callback.answer("🚫 Доступ запрещен!", show_alert=True)
        except:
            pass
        
        return False

    async def handle_rss_add(self, message: Message) -> None:
        if not await self.is_owner(message):
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
        self.config.RSS_ACTIVE.append(True)  # Добавляем как активную
        await message.answer(f"✅ RSS-лента добавлена: {new_url}")

    async def handle_rss_remove(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            await message.answer("❌ Укажите номер RSS-ленты для удаления")
            return
        
        try:
            index = int(args[1]) - 1
            if 0 <= index < len(self.config.RSS_URLS):
                removed = self.config.RSS_URLS.pop(index)
                
                if index < len(self.config.RSS_ACTIVE):
                    self.config.RSS_ACTIVE.pop(index)
                
                await message.answer(f"✅ RSS-лента удалена: {removed}")
            else:
                await message.answer("❌ Неверный номер RSS-ленты")
        except ValueError:
            await message.answer("❌ Укажите корректный номер")

    async def handle_pause(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if not self.controller:
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
            
        if not self.controller:
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
            
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Используйте: /set [параметр] [значение]")
            return
        
        param = args[1].upper()
        value = " ".join(args[2:])
        
        ALLOWED_PARAMS = {
            'POSTS_PER_HOUR': {'type': int, 'validator': lambda x: 1 <= x <= 60, 'error_msg': 'Должно быть целое число от 1 до 60'},
            'MIN_DELAY_BETWEEN_POSTS': {'type': int, 'validator': lambda x: x >= 10, 'error_msg': 'Минимальная задержка 10 секунд'},
            'CHECK_INTERVAL': {'type': int, 'validator': lambda x: x >= 60, 'error_msg': 'Интервал проверки не менее 60 секунд'},
            'ENABLE_IMAGE_GENERATION': {'type': bool, 'validator': None},
            'DISABLE_YAGPT': {'type': bool, 'validator': None},
            'YAGPT_MODEL': {'type': str, 'validator': lambda x: x in ['yandexgpt-lite', 'yandexgpt-pro'], 'error_msg': 'Допустимые модели: yandexgpt-lite, yandexgpt-pro'},
            'YAGPT_TEMPERATURE': {'type': float, 'validator': lambda x: 0.1 <= x <= 1.0, 'error_msg': 'Температура должна быть от 0.1 до 1.0'}
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
            self.config.save_to_env_file(param, str(converted_value))
        except (TypeError, ValueError) as e:
            await message.answer(f"❌ Ошибка: {str(e)}")

    async def handle_clear_history(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if not self.controller:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        try:
            self.controller.state.state['sent_entries'] = {}
            await message.answer("✅ История отправленных постов очищена! Бот будет повторно отправлять новости.")
        except Exception as e:
            logger.error(f"Error clearing history: {str(e)}")
            await message.answer(f"❌ Ошибка при очистке истории: {str(e)}")

    async def handle_params_list(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        params = []
        for name in dir(self.config):
            if name.isupper() and not name.startswith('_') and not callable(getattr(self.config, name)):
                value = getattr(self.config, name)
                value_type = type(value).__name__
                
                if isinstance(value, (list, tuple)) and len(value) > 3:
                    display_value = f"{value[:3]}... ({len(value)} items)"
                elif isinstance(value, str) and len(value) > 50:
                    display_value = value[:50] + "..."
                else:
                    display_value = str(value)
                    
                params.append(f"• <b>{name}</b>: {display_value}")
        
        chunk_size = 15
        for i in range(0, len(params), chunk_size):
            chunk = params[i:i + chunk_size]
            response = "⚙️ <b>Доступные параметры:</b>\n\n" + "\n".join(chunk)
            if i + chunk_size < len(params):
                response += "\n\n<i>Продолжение следует...</i>"
            await message.answer(response, parse_mode="HTML")

    async def handle_param_info(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            await message.answer("❌ Укажите имя параметра")
            return
            
        param_name = args[1].upper()
        
        if not hasattr(self.config, param_name):
            await message.answer(f"❌ Параметр {param_name} не существует")
            return
            
        value = getattr(self.config, param_name)
        value_type = type(value).__name__
        
        type_description = {
            'int': 'целое число',
            'float': 'число с плавающей точкой',
            'bool': 'логическое значение (true/false)',
            'str': 'строка',
            'list': 'список значений (через запятую)',
            'tuple': 'кортеж чисел (через запятую)'
        }.get(value_type, value_type)
        
        examples = {
            int: "42",
            float: "3.14",
            bool: "true или false",
            str: "любая строка",
            list: "item1, item2, item3",
            tuple: "255, 255, 255"
        }.get(type(value), str(value))
        
        response = (
            f"ℹ️ <b>Информация о параметре:</b>\n\n"
            f"<b>Имя:</b> {param_name}\n"
            f"<b>Тип:</b> {value_type} ({type_description})\n"
            f"<b>Текущее значение:</b> {value}\n\n"
            f"<b>Примеры значений:</b>\n"
            f"{examples}\n\n"
            f"<b>Изменить командой:</b>\n"
            f"<code>/set_all {param_name} [новое_значение]</code>"
        )
        
        await message.answer(response, parse_mode="HTML")

    async def handle_set_all(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Используйте: /set_all [параметр] [значение]")
            return
            
        param_name = args[1].upper()
        new_value_str = " ".join(args[2:])
        
        if not hasattr(self.config, param_name):
            await message.answer(f"❌ Параметр {param_name} не существует")
            return
            
        current_value = getattr(self.config, param_name)
        value_type = type(current_value)
        
        try:
            if value_type is bool:
                converted_value = new_value_str.lower() in ['true', '1', 'yes', 'y', 't', 'on']
            elif value_type is int:
                converted_value = int(new_value_str)
            elif value_type is float:
                converted_value = float(new_value_str)
            elif value_type is list:
                converted_value = [item.strip() for item in new_value_str.split(',')]
            elif value_type is tuple:
                converted_value = tuple(map(int, new_value_str.split(',')))
            elif value_type is str:
                converted_value = new_value_str
            else:
                converted_value = value_type(new_value_str)
            
            setattr(self.config, param_name, converted_value)
            self.config.save_to_env_file(param_name, str(converted_value))
            
            response = (
                f"✅ <b>Параметр успешно обновлен!</b>\n\n"
                f"<b>Параметр:</b> {param_name}\n"
                f"<b>Старое значение:</b> {current_value}\n"
                f"<b>Новое значение:</b> {converted_value}\n\n"
            )
            
            critical_params = ['TOKEN', 'CHANNEL_ID', 'OWNER_ID', 'YANDEX_API_KEY']
            if param_name in critical_params:
                response += "⚠️ <i>Для применения изменений может потребоваться перезагрузка бота</i>"
            
            await message.answer(response, parse_mode="HTML")
        except (TypeError, ValueError) as e:
            await message.answer(
                f"❌ <b>Ошибка преобразования значения:</b>\n"
                f"Параметр: {param_name}\n"
                f"Требуемый тип: {value_type.__name__}\n"
                f"Ошибка: {str(e)}",
                parse_mode="HTML"
            )

    async def close(self) -> None:
        await self.bot.session.close()
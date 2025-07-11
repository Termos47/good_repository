from typing import Optional
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    InputMediaPhoto,
    CallbackQuery,
    FSInputFile
)
from aiogram.types import BufferedInputFile  # Добавить импорт
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import Config
import asyncio
import os
import logging
import matplotlib.pyplot as plt
import numpy as np
from io import BytesIO
from datetime import datetime

logger = logging.getLogger('VisualInterface')

class UIBuilder:
    THEMES = {
        'default': {
            'primary': '🔵',
            'success': '🟢',
            'warning': '🟡',
            'error': '🔴',
            'text': '⚪'
        },
        'dark': {
            'primary': '🌑',
            'success': '🌑',
            'warning': '🌕',
            'error': '🔥',
            'text': '⚪'
        },
        'colorful': {
            'primary': '🌈',
            'success': '✅',
            'warning': '⚠️',
            'error': '❌',
            'text': '📝'
        }
    }

    def __init__(self, config: Config):
        self.config = config
        self.user_themes = {}
    
    def get_theme(self, user_id: int) -> dict:
        return self.user_themes.get(user_id, self.THEMES['default'])
    
    async def main_menu(self, user_id: int) -> Optional[InlineKeyboardMarkup]:
        """Показывает главное меню только владельцу"""
        # Проверка прав доступа
        if user_id != self.config.OWNER_ID:
            return None
        
        theme = self.get_theme(user_id)
        
        # Основные кнопки меню
        buttons = [
            [
                InlineKeyboardButton(
                    text=f"{theme['primary']} Главная",
                    callback_data="main"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{theme['text']} Мониторинг",
                    callback_data="monitoring"
                ),
                InlineKeyboardButton(
                    text=f"{theme['text']} Настройки",
                    callback_data="settings"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{theme['text']} Статистика",
                    callback_data="stats"
                ),
                InlineKeyboardButton(
                    text=f"{theme['text']} RSS Ленты",
                    callback_data="rss_list"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{theme['success']} Запустить",
                    callback_data="start_bot"
                ),
                InlineKeyboardButton(
                    text=f"{theme['warning']} Остановить",
                    callback_data="stop_bot"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎨 Сменить тему",
                    callback_data="change_theme"
                )
            ]
        ]
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    async def back_to_settings(self) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(
            text="◀️ Назад",
            callback_data="settings"
        )
        return builder.as_markup()
    
    async def back_button(self) -> InlineKeyboardMarkup:
        """Кнопка 'Назад' для меню настроек"""
        builder = InlineKeyboardBuilder()
        builder.button(
            text="◀️ Назад",
            callback_data="settings"
        )
        return builder.as_markup()

    async def stats_visualization(self, stats: dict) -> tuple:
        """Генерирует визуализацию статистики"""
        try:
            # График активности по часам
            plt.figure(figsize=(10, 6))
            
            # Данные для графика (пример)
            hours = list(range(24))
            posts = [stats.get(f'hour_{h}', 0) for h in hours]
            
            plt.bar(hours, posts, color='#4CAF50')
            plt.title('Активность по часам')
            plt.xlabel('Часы')
            plt.ylabel('Посты')
            plt.xticks(hours)
            plt.grid(axis='y', alpha=0.5)
            
            summary = (
                "📊 <b>Статистика производительности</b>\n\n"
                f"▸ Постов отправлено: <b>{stats.get('posts_sent', 0)}</b>\n"
                f"▸ Ошибок: <b>{stats.get('errors', 0)}</b>\n"
                f"▸ Использований AI: <b>{stats.get('yagpt_used', 0)}</b>\n"
                f"▸ Изображений сгенерировано: <b>{stats.get('images_generated', 0)}</b>\n"
                f"▸ Среднее время цикла: <b>{stats.get('avg_processing_time', 0):.2f} сек</b>\n"
                f"▸ Аптайм: <b>{stats.get('uptime', '0:00')}</b>"
            )

            # В методе stats_visualization
            buf = BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight')
            buf.seek(0)
            image_data = buf.getvalue()  # Получаем байты
            photo = BufferedInputFile(image_data, filename="stats.png")  # Создаем InputFile
            return summary, InputMediaPhoto(media=photo, caption=summary)
            
        except Exception as e:
            logger.error(f"Stats visualization error: {str(e)}")
            return "📊 Статистика недоступна", None

    async def settings_menu(self, user_id: int) -> InlineKeyboardMarkup:
        theme = self.get_theme(user_id)
        builder = InlineKeyboardBuilder()
        
        builder.button(
            text=f"{theme['text']} Основные", 
            callback_data="settings_general"
        )
        builder.button(
            text=f"{theme['text']} Изображения", 
            callback_data="settings_images"
        )
        builder.button(
            text=f"{theme['text']} AI", 
            callback_data="settings_ai"
        )
        builder.button(
            text=f"{theme['text']} RSS", 
            callback_data="settings_rss"
        )
        builder.button(
            text=f"{theme['text']} Оповещения", 
            callback_data="settings_notify"
        )
        builder.button(
            text=f"{theme['primary']} Назад", 
            callback_data="main_menu"
        )
        
        builder.adjust(2, 2, 2, 1)
        return builder.as_markup()

    async def image_settings_view(self, user_id: int) -> tuple:
        """Возвращает визуальное представление настроек изображений"""
        theme = self.get_theme(user_id)
        text = (
            "🖼 <b>Текущие настройки изображений</b>\n\n"
            f"▸ Источник: <b>{self.config.IMAGE_SOURCE.capitalize()}</b>\n"
            f"▸ Резервная генерация: {'Вкл' if self.config.IMAGE_FALLBACK else 'Выкл'}\n"
            f"▸ Цвет текста: <code>{self.config.TEXT_COLOR}</code>\n"
            f"▸ Цвет обводки: <code>{self.config.STROKE_COLOR}</code>\n"
            f"▸ Ширина обводки: <b>{self.config.STROKE_WIDTH}px</b>"
        )
        
        # Создаем пример изображения
        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new('RGB', (400, 200), (40, 40, 60))
            draw = ImageDraw.Draw(img)
            
            # Загрузка шрифта
            font_path = os.path.join(self.config.FONTS_DIR, self.config.DEFAULT_FONT)
            font = ImageFont.truetype(font_path, 32) if os.path.exists(font_path) else ImageFont.load_default()
            
            # Текст с текущими настройками
            draw.text(
                (200, 100), 
                "Пример текста", 
                fill=tuple(self.config.TEXT_COLOR),
                stroke_fill=tuple(self.config.STROKE_COLOR),
                stroke_width=self.config.STROKE_WIDTH,
                font=font,
                anchor="mm"
            )
            
            # Сохраняем в буфер
            # В методе image_settings_view
            buf = BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            image_data = buf.getvalue()
            photo = BufferedInputFile(image_data, filename="preview.png")
            return text, InputMediaPhoto(media=photo, caption=text)
            
            return text, InputMediaPhoto(media=buf, caption=text)
        except Exception as e:
            logger.error(f"Preview generation failed: {str(e)}")
            return text, None

    async def theme_selector(self, user_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        
        for theme_name in self.THEMES:
            builder.button(
                text=f"{self.THEMES[theme_name]['primary']} {theme_name.capitalize()}",
                callback_data=f"set_theme_{theme_name}"
            )
        
        builder.button(
            text="◀️ Назад",
            callback_data="settings"
        )
        
        builder.adjust(2, 1)
        return builder.as_markup()

    async def progress_bar(self, current: int, total: int) -> str:
        """Генерирует текстовый прогресс-бар"""
        bar_length = 10
        filled = int(bar_length * current / total)
        empty = bar_length - filled
        return f"[{'■' * filled}{'□' * empty}] {current}/{total}"

    async def animated_processing(self, message, process_name: str, duration: int = 5):
        """Отображает анимированный процесс"""
        status_msg = await message.answer(f"🔄 {process_name}...")
        
        for i in range(1, 11):
            await asyncio.sleep(duration / 10)
            bar = "⬛" * i + "⬜" * (10 - i)
            await status_msg.edit_text(f"⏳ {process_name}\n{bar} {i*10}%")
        
        await status_msg.edit_text(f"✅ {process_name} завершено!")

    async def rss_feed_status(self, feeds: list) -> str:
        """Визуализация статуса RSS-лент"""
        lines = ["📡 <b>Статус RSS-лент</b>\n"]
        
        for feed in feeds:
            status_icon = '🟢' if feed.get('active', True) else '🔴'
            error_icon = f"❗️ {feed.get('error_count', 0)}" if feed.get('error_count', 0) > 0 else ""
            lines.append(f"{status_icon} {feed['url']} {error_icon}")
        
        return "\n".join(lines)
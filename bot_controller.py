import os
import asyncio
import logging
import time
import hashlib
import PIL
import aiofiles
import aiohttp
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Union
from urllib.parse import urlparse
from PIL import Image
from functools import lru_cache
from bs4 import BeautifulSoup
from state_manager import StateManager

logger = logging.getLogger('bot.controller')

class BotController:
    def __init__(self, config, state_manager, rss_parser, image_generator, yandex_gpt, telegram_bot):
        self.config = config
        self.state_manager = state_manager
        self.rss_parser = rss_parser
        self.image_generator = image_generator
        self.yandex_gpt = yandex_gpt
        self.telegram_bot = telegram_bot
        self._validate_config()
        self.hourly_stats = {f"hour_{h}": 0 for h in range(24)}
        
        # Инициализация логгера
        self.logger = logging.getLogger('bot.controller')
        
        # Инициализация асинхронных ресурсов
        self.session = None
        self.image_semaphore = None
        self.is_running = False
        self.cleanup_task = None
        self.rss_task = None
        self.last_post_time = 0.0
        
        # Статистика
        self.stats = {
            'start_time': datetime.now(),
            'posts_sent': 0,
            'last_check': None,
            'errors': 0,
            'last_post': None,
            'yagpt_used': 0,
            'yagpt_errors': 0,
            'image_errors': 0,
            'images_generated': 0,
            'images_deleted': 0,
            'storage_freed': 0.0,
            'last_cleanup': None,
            'cycles_completed': 0,
            'avg_processing_time': 0.0,
            'total_processing_time': 0.0,
            'max_feed_time': 0.0,
            'min_feed_time': float('inf'),
            'last_cleanup_result': "",
            'duplicates_rejected': 0
        }
        
        self.post_timestamps = []
        
        # Загрузка состояния
        try:
            # Проверяем существование файла состояния
            state_file_exists = os.path.exists(self.state_manager.state_file)
            
            # Загружаем состояние из менеджера
            self.state_manager.load_state()
            
            # Если файл состояния не существует - создаем новый
            if not state_file_exists:
                self.logger.warning(f"State file {self.state_manager.state_file} not found, creating new one")
                try:
                    self.state_manager.save_state()
                    self.logger.info(f"New state file created: {self.state_manager.state_file}")
                except Exception as e:
                    self.logger.error(f"Failed to create state file: {str(e)}")
            
            # Обновляем статистику из загруженного состояния
            if 'stats' in self.state_manager.state:
                self.stats.update(self.state_manager.state['stats'])
                self.logger.debug("Stats loaded from state")
        except Exception as e:
            self.logger.error(f"Error initializing state: {str(e)}", exc_info=True)
            
            # Создаем резервное состояние при ошибке
            try:
                self.state_manager.save_state()
                self.logger.warning("Created backup state after initialization error")
            except Exception as backup_error:
                self.logger.critical(f"Critical state error: {str(backup_error)}")
    
    def _validate_config(self):
        required = [
            'TOKEN', 
            'CHANNEL_ID',
            'RSS_URLS',
            'MAX_IMAGE_WIDTH',
            'MAX_IMAGE_HEIGHT'
        ]
        for param in required:
            if not hasattr(self.config, param) or not getattr(self.config, param):
                raise ValueError(f"Missing required config: {param}")
            
    async def start(self) -> bool:
        """Запуск основных процессов бота"""
        if self.is_running:
            logger.warning("Controller is already running")
            return False
            
        try:
            self.session = aiohttp.ClientSession()
            self.image_semaphore = asyncio.Semaphore(self.config.MAX_CONCURRENT_IMAGE_TASKS)
            self.is_running = True
            self.last_post_time = time.time()
            
            logger.info("Starting controller with %d RSS feeds", len(self.config.RSS_URLS))
            
            # Запуск основных задач
            self.rss_task = asyncio.create_task(self._rss_processing_loop())
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            
            return True
        except Exception as e:
            logger.error("Failed to start controller: %s", str(e), exc_info=True)
            await self._safe_shutdown()
            return False

    async def stop(self) -> bool:
        """Корректная остановка бота"""
        if not self.is_running:
            logger.warning("Controller is not running")
            return False
            
        logger.info("Stopping controller...")
        self.is_running = False
        
        try:
            # Отмена всех задач
            tasks = []
            if self.rss_task and not self.rss_task.done():
                self.rss_task.cancel()
                tasks.append(self.rss_task)
            if self.cleanup_task and not self.cleanup_task.done():
                self.cleanup_task.cancel()
                tasks.append(self.cleanup_task)
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # Сохранение состояния и очистка ресурсов
            await self._safe_shutdown()
            
            # ИСПРАВЛЕНИЕ: Сохраняем ВСЁ состояние перед выходом
            if self.state_manager:
                self.state_manager.save_state()
            
            logger.info("Controller stopped successfully")
            return True
        except Exception as e:
            logger.error("Error during shutdown: %s", str(e), exc_info=True)
            return False

    async def _safe_shutdown(self):
        """Безопасное освобождение ресурсов"""
        if self.session and not self.session.closed:
            await self.session.close()
        if hasattr(self.image_generator, 'shutdown'):
            self.image_generator.shutdown()

    async def _rss_processing_loop(self):
        """Основной цикл обработки RSS-лент"""
        last_save_time = time.time()
        
        while self.is_running:
            cycle_start = time.time()
            try:
                self.stats['last_check'] = datetime.now()
                
                # Получение и обработка новых постов
                new_posts = await self._fetch_all_feeds()
                await self._process_new_posts(new_posts)
                
                # Обновление статистики
                cycle_time = time.time() - cycle_start
                self._update_processing_stats(cycle_time)
                
                # Периодическое сохранение состояния
                if time.time() - last_save_time > 300:
                    self.state.save_state()
                    last_save_time = time.time()
                
                await asyncio.sleep(self.config.CHECK_INTERVAL)
                
            except asyncio.CancelledError:
                logger.info("RSS processing loop cancelled")
                break
            except Exception as e:
                logger.error("Error in RSS processing loop: %s", str(e), exc_info=True)
                await asyncio.sleep(min(60, self.config.CHECK_INTERVAL * 2))

    async def _fetch_all_feeds(self) -> List[Dict]:
        """Загрузка и парсинг всех RSS-лент"""
        new_posts = []
        
        for url in self.config.RSS_URLS:
            try:
                if not self.is_running:
                    break
                    
                logger.debug("Fetching feed: %s", url)
                feed_content = await self.rss_parser.fetch_feed(url)
                if feed_content:
                    entries = self.rss_parser.parse_entries(feed_content)
                    normalized_entries = [
                        e if isinstance(e, dict) else {'link': e, 'title': ''}
                        for e in entries if isinstance(e, (dict, str))
                    ]
                    new_posts.extend(normalized_entries)
            except Exception as e:
                logger.error("Error processing feed %s: %s", url, str(e), exc_info=True)
                self.stats['errors'] += 1
                
        return new_posts

    async def _process_new_posts(self, posts: List[Dict]):
        """Обработка новых постов с учетом ограничений"""
        for post in posts[:self.config.MAX_POSTS_PER_CYCLE]:
            if not self.is_running:
                break
                
            try:
                # Соблюдение минимального интервала между постами
                await self._enforce_post_delay()
                
                # Полная обработка поста
                await self._process_single_post(post)
            except Exception as e:
                logger.error("Error processing post: %s", str(e), exc_info=True)
                self.stats['errors'] += 1

    async def _enforce_post_delay(self):
        """Обеспечение минимальной задержки между постами"""
        time_since_last = time.time() - self.last_post_time
        if time_since_last < self.config.MIN_DELAY_BETWEEN_POSTS:
            delay = self.config.MIN_DELAY_BETWEEN_POSTS - time_since_last
            logger.debug("Waiting %.1f seconds before next post", delay)
            await asyncio.sleep(delay)

    def _update_processing_stats(self, cycle_time: float):
        """Обновление статистики обработки"""
        self.stats['total_processing_time'] += cycle_time
        self.stats['cycles_completed'] += 1
        self.stats['avg_processing_time'] = (
            self.stats['total_processing_time'] / self.stats['cycles_completed']
        )
        self.stats['max_feed_time'] = max(self.stats['max_feed_time'], cycle_time)
        self.stats['min_feed_time'] = min(self.stats['min_feed_time'], cycle_time)

    async def _process_single_post(self, post: Union[Dict, str]) -> bool:
        try:
            normalized_post = self._normalize_post(post)
            if not normalized_post:
                logger.error("Post normalization failed", extra={'post': str(post)[:100]})
                return False

            post_id = self._generate_post_id(normalized_post)
            normalized_post['post_id'] = post_id

            if self._should_skip_post(normalized_post):
                logger.debug("Skipping duplicate post", extra={'post_id': post_id})
                return False

            try:
                processed_content = await self._process_post_content(normalized_post)
                if not processed_content:
                    logger.error("Content processing returned empty result")
                    return False
            except Exception as e:
                logger.error("Content processing failed", 
                            exc_info=True,
                            extra={'post_id': post_id, 'error': str(e)})
                return False

            # Добавлены конкретные типы исключений для обработки изображений
            try:
                image_path = await self._get_post_image(normalized_post)
            except aiohttp.ClientError as e:
                logger.error("Image download failed", extra={'error': str(e)})
                if self.config.IMAGE_SOURCE == 'none':
                    image_path = None
                else:
                    return False
            except PIL.UnidentifiedImageError:
                logger.error("Invalid image file")
                return False
            except OSError as e:
                logger.error("Filesystem error", extra={'error': str(e)})
                return False

            try:
                success = await self._send_post_to_telegram(processed_content, normalized_post, image_path)
                if not success:
                    return False
                    
                self._update_stats_after_post(normalized_post)
                return True
            except Exception as e:
                logger.error("Post sending failed", exc_info=True)
                if image_path and os.path.exists(image_path):
                    try:
                        os.unlink(image_path)
                    except OSError as e:
                        logger.error("Failed to delete temp image", extra={'error': str(e)})
                return False
        except Exception as e:
            logger.critical("Unexpected error in post processing", exc_info=True)
            return False

    def _normalize_post(self, post: Union[Dict, str]) -> Dict:
        """Гарантированно возвращает Dict с минимально необходимыми полями"""
        if isinstance(post, str):
            return {
                'link': post,
                'title': '',
                'description': '',
                'pub_date': datetime.now().isoformat()
            }
        if isinstance(post, dict):
            # Обеспечиваем наличие обязательных полей
            post.setdefault('link', '')
            post.setdefault('title', '')
            post.setdefault('description', '')
            post.setdefault('pub_date', datetime.now().isoformat())
            return post
        raise ValueError(f"Invalid post type: {type(post)}")

    def _generate_post_id(self, post: Dict) -> str:
        stable_data = f"{post.get('link', '')}{post.get('title', '')}"
        return hashlib.md5(stable_data.encode()).hexdigest()

    def _should_skip_post(self, post: Dict) -> bool:
        post_id = post.get('post_id', '')
        if not post_id:
            return False
            
        if self.state.is_entry_sent(post_id):
            logger.debug("Skipping duplicate post: %s", post.get('title', '')[:50])
            self.stats['duplicates_rejected'] += 1
            return True
            
        return False
    
    async def _process_post_content(self, post: Dict) -> Dict[str, str]:
        """Обработка текста поста с возможным использованием AI"""
        if self.config.DISABLE_YAGPT or not self.yandex_gpt.active:
            return {
                'title': post.get('title', '')[:self.config.MAX_TITLE_LENGTH],
                'description': post.get('description', '')[:self.config.MAX_DESC_LENGTH]
            }
            
        try:
            result = await self.yandex_gpt.enhance(
                post.get('title', ''),
                post.get('description', '')
            )
            
            if not result:
                return {
                    'title': post.get('title', '')[:self.config.MAX_TITLE_LENGTH],
                    'description': post.get('description', '')[:self.config.MAX_DESC_LENGTH]
                }
                
            self.stats['yagpt_used'] += 1
            return {
                'title': result.get('title', post.get('title', ''))[:self.config.MAX_TITLE_LENGTH],
                'description': result.get('description', post.get('description', ''))[:self.config.MAX_DESC_LENGTH]
            }
        except Exception as e:
            logger.error("AI content enhancement failed: %s", str(e), exc_info=True)
            self.stats['yagpt_errors'] += 1
            
            # Автоматическое отключение YandexGPT при превышении лимита ошибок
            if self.config.AUTO_DISABLE_YAGPT and self.stats['yagpt_errors'] >= self.config.YAGPT_ERROR_THRESHOLD:
                self.yandex_gpt.active = False
                logger.warning("YandexGPT disabled due to error threshold")
                
            return {
                'title': post.get('title', '')[:self.config.MAX_TITLE_LENGTH],
                'description': post.get('description', '')[:self.config.MAX_DESC_LENGTH]
            }

    async def _get_post_image(self, post: Dict) -> Optional[str]:
        # Режим none - без изображений
        if self.config.IMAGE_SOURCE == 'none':
            return None

        # Режим original - принудительный поиск изображения
        if self.config.IMAGE_SOURCE == 'original':
            image_url = None
                
            # 1. Пробуем взять из RSS
            if post.get('image_url'):
                image_url = post['image_url']
                
            # 2. Если нет - парсим страницу
            if not image_url and post.get('link'):
                image_url = await self.rss_parser.extract_primary_image(post['link'])
                
            # 3. Скачиваем найденное изображение
            if image_url:
                return await self._download_image(image_url, post['post_id'])
                
            return None  # Не используем fallback!
        
        # Режим 'template' - стандартная логика с fallback
        # 1. Прямая ссылка из RSS
        if post.get('image_url'):
            image_path = await self._download_image(post['image_url'], post['post_id'])
            if image_path:
                return image_path
                
        # 2. Поиск в HTML-контенте
        if post.get('description'):
            image_url = await self._find_image_in_html(post['description'], post.get('link', ''))
            if image_url:
                image_path = await self._download_image(image_url, post['post_id'])
                if image_path:
                    return image_path
                    
        # 3. Fallback - генерация изображения
        if self.config.IMAGE_FALLBACK and self.config.ENABLE_IMAGE_GENERATION:
            return await self._generate_image_with_semaphore(post.get('title', ''))
                        
        return None

    async def _find_image_in_html(self, html_content: str, base_url: str) -> Optional[str]:
        """Поиск изображений в HTML-контенте с безопасной обработкой типов"""
        if not html_content:
            return None

        try:
            from bs4 import BeautifulSoup, Tag
            from bs4.element import NavigableString

            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 1. Проверка OpenGraph/twitter изображений
            for meta in soup.find_all('meta'):
                if isinstance(meta, Tag):
                    # Безопасное получение атрибутов
                    meta_property = meta.attrs.get('property', '') or meta.attrs.get('name', '')
                    if not isinstance(meta_property, str):
                        continue
                        
                    # Безопасное сравнение с приведением к нижнему регистру
                    meta_property_lower = meta_property.lower() if meta_property else ''
                    if meta_property_lower in {'og:image', 'twitter:image', 'og:image:url'}:
                        image_url = meta.attrs.get('content', '')
                        if isinstance(image_url, str) and image_url.strip():
                            normalized_url = self._normalize_image_url(image_url, base_url)
                            if normalized_url:
                                return normalized_url
            
            # 2. Поиск по img тегам
            for img in soup.find_all('img'):
                if isinstance(img, Tag):
                    src = img.attrs.get('src', '')
                    if not isinstance(src, str) or not src.strip():
                        continue
                        
                    # Безопасная проверка на служебные изображения
                    src_lower = src.lower() if src else ''
                    if any(bad_word in src_lower for bad_word in ['pixel', 'icon', 'logo', 'spacer', 'ad']):
                        continue
                        
                    normalized_url = self._normalize_image_url(src, base_url)
                    if normalized_url:
                        return normalized_url
            
            return None
            
        except Exception as e:
            logger.debug(f"HTML parsing error: {str(e)}")
            return None

    def _normalize_image_url(self, url: str, base_url: str) -> str:
        """Нормализация URL изображения"""
        if not isinstance(url, str):
            return ""
            
        if url.startswith(('http://', 'https://')):
            return url
        if url.startswith('//'):
            return f'https:{url}'
        if url.startswith('/'):
            if not base_url:
                base_url = self.config.RSS_URLS[0] if self.config.RSS_URLS else ""
            parsed = urlparse(base_url)
            return f"{parsed.scheme}://{parsed.netloc}{url}"
        return url

    async def _generate_image_with_semaphore(self, title: str) -> Optional[str]:
        """Генерация изображения с учетом семафора"""
        if not title:
            return None
            
        if self.image_semaphore:
            await self.image_semaphore.acquire()
            try:
                return await self._generate_image(title)
            finally:
                self.image_semaphore.release()
        return await self._generate_image(title)

    @lru_cache(maxsize=100)
    async def _generate_image(self, title: str) -> Optional[str]:
        """Генерация изображения с кэшированием"""
        try:
            logger.debug("Generating image for title: %s", title[:50])
            image_path = await self.image_generator.generate_image(title)
            if image_path:
                self.stats['images_generated'] += 1
                logger.info("Image generated: %s", image_path)
            return image_path
        except Exception as e:
            logger.error("Image generation failed: %s", str(e), exc_info=True)
            self.stats['image_errors'] += 1
            return None

    async def _download_image(self, url: str, post_id: str) -> Optional[str]:
        """Надежная загрузка изображения с проверками"""
        if not url or not self.session:
            return None
            
        try:
            # Создаем уникальное имя файла
            filename = f"{post_id}_{hashlib.md5(url.encode()).hexdigest()[:8]}.jpg"
            temp_path = os.path.join(self.config.OUTPUT_DIR, filename)
            
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=self.config.IMAGE_DOWNLOAD_TIMEOUT)
            ) as response:
                if response.status != 200:
                    logger.debug("Image download failed: HTTP %d", response.status)
                    return None
                    
                # Проверка типа содержимого
                content_type = response.headers.get('Content-Type', '')
                if not any(x in content_type for x in ['image/jpeg', 'image/png', 'image/webp']):
                    logger.debug("Invalid image content type: %s", content_type)
                    return None
                    
                # Проверка размера
                content_length = int(response.headers.get('Content-Length', 0))
                if content_length < 1024 or content_length > 5 * 1024 * 1024:  # 1KB - 5MB
                    logger.debug("Invalid image size: %d bytes", content_length)
                    return None
                    
                # Сохранение файла
                async with aiofiles.open(temp_path, 'wb') as f:
                    await f.write(await response.read())
                
                # Дополнительная проверка через Pillow
                try:
                    with Image.open(temp_path) as img:
                        if img.width < self.config.MIN_IMAGE_WIDTH or img.height < self.config.MIN_IMAGE_HEIGHT:
                            raise ValueError(f"Image too small: {img.width}x{img.height}")
                    return temp_path
                except Exception as e:
                    logger.debug("Image validation failed: %s", str(e))
                    os.unlink(temp_path)
                    return None
                    
        except Exception as e:
            logger.debug("Image download error: %s - %s", url, str(e))
            try:
                if 'temp_path' in locals():
                    os.unlink(temp_path)
            except:
                pass
            return None

    async def _send_post_to_telegram(self, content: Dict, post: Dict, image_path: Optional[str]) -> bool:
        """Отправка поста в Telegram канал с использованием правильного метода"""
        try:
            message_text = f"<b>{content['title']}</b>\n\n{content['description']}\n\n<a href='{post.get('link', '')}'>Читать далее</a>"
            
            if image_path and os.path.exists(image_path):
                # Используем метод send_post вместо send_photo
                success = await self.telegram_bot.send_post(
                    title=content['title'],
                    description=content['description'],
                    link=post.get('link', ''),
                    image_path=image_path
                )
            else:
                success = await self.telegram_bot.send_post(
                    title=content['title'],
                    description=content['description'],
                    link=post.get('link', ''),
                    image_path=None
                )
                
            if success:
                logger.info(f"Post sent successfully: {content['title'][:50]}...")
                return True
            else:
                logger.error(f"Failed to send post: {content['title'][:50]}...")
                return False
                
        except Exception as e:
            logger.error(f"Error sending post to Telegram: {str(e)}")
            return False

    def _update_stats_after_post(self, post: Dict):
        """Обновление статистики после успешной отправки поста"""
        self.state.add_sent_entry(post)
        self.stats['posts_sent'] += 1
        self.stats['last_post'] = datetime.now()
        self.last_post_time = time.time()
        
        # Обновление почасовой статистики
        hour = datetime.now().hour
        self.hourly_stats[f"hour_{hour}"] = self.hourly_stats.get(f"hour_{hour}", 0) + 1
        
        # Принудительное сохранение после первого поста
        try:
            self.state.save_state()
            self.logger.info("State saved after first post")
        except Exception as e:
            self.logger.error(f"Failed to save state: {str(e)}")

    async def _cleanup_loop(self):
        """Регулярная очистка устаревших данных"""
        logger.info("Starting cleanup loop")
        
        while self.is_running:
            try:
                await asyncio.sleep(12 * 3600)  # 12 часов
                
                logger.debug("Running cleanup cycle")
                deleted, freed = await self.image_generator.cleanup_old_images(24)
                
                self.stats['images_deleted'] += deleted
                self.stats['storage_freed'] += freed
                self.stats['last_cleanup'] = datetime.now()
                self.stats['last_cleanup_result'] = f"Deleted {deleted} files, freed {freed:.2f} MB"
                
                logger.info("Cleanup completed: %d files deleted, %.2f MB freed", deleted, freed)
                
            except asyncio.CancelledError:
                logger.info("Cleanup loop cancelled")
                break
            except Exception as e:
                logger.error("Cleanup error: %s", str(e), exc_info=True)

    @property
    def state(self) -> StateManager:
        return self.state_manager
        
    def get_status_text(self) -> str:
        """Генерация текста статуса для администратора"""
        status = "🟢 Работает" if self.is_running else "🔴 Остановлен"
        last_check = self.stats['last_check'].strftime("%Y-%m-%d %H:%M:%S") if self.stats.get('last_check') else "никогда"
        last_post = self.stats['last_post'].strftime("%Y-%m-%d %H:%M:%S") if self.stats.get('last_post') else "никогда"
        
        return (
            "📊 <b>Статус бота</b>\n\n"
            f"<b>Состояние:</b> {status}\n"
            f"<b>Последняя проверка RSS:</b> {last_check}\n"
            f"<b>Последний пост:</b> {last_post}\n"
            f"<b>Отправлено постов:</b> {self.stats.get('posts_sent', 0)}\n"
            f"<b>Ошибок:</b> {self.stats.get('errors', 0)}\n"
            f"<b>Дубликатов отклонено:</b> {self.stats.get('duplicates_rejected', 0)}\n"
            f"<b>Использований YandexGPT:</b> {self.stats.get('yagpt_used', 0)}\n"
            f"<b>Сгенерировано изображений:</b> {self.stats.get('images_generated', 0)}\n"
            f"<b>Лент в обработке:</b> {len(self.config.RSS_URLS)}"
        )
    
    def get_rss_status(self) -> List[Dict]:
        """Возвращает статус RSS-лент для визуализации"""
        return [
            {
                'url': url,
                'active': True,
                'error_count': 0,
                'last_check': datetime.now().isoformat()
            } for url in self.config.RSS_URLS
        ]
import json
import os
import asyncio
import logging
import time
import hashlib
import PIL
import aiofiles
import aiohttp
import re
import concurrent.futures
import psutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Union
from urllib.parse import urlparse
from PIL import Image
from functools import lru_cache
from bs4 import BeautifulSoup
from telegram import CallbackQuery
from rss_parser import AsyncRSSParser
from state_manager import StateManager

logger = logging.getLogger('bot.controller')

class BotController:
    def __init__(self, config, state_manager, rss_parser, image_generator, yandex_gpt, telegram_bot):
        self.config = config
        self.state_manager = StateManager(
            state_file=config.STATE_FILE,
            config=config
        )
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
        self.session_refresh_task = None
        self.task_monitor_task = None
        self.last_post_time = 0.0
        
        # Инициализация ProcessPoolExecutor для генерации изображений
        self.image_executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=config.IMAGE_GENERATION_WORKERS
        )
        
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
            
    def is_available(self) -> bool:
        """Проверяет, доступен ли сервис Yandex GPT"""
        return (self.config.YANDEX_API_KEY and 
                self.config.YANDEX_FOLDER_ID and 
                self.config.YANDEX_API_ENDPOINT and
                self.active)

    async def _create_session(self) -> aiohttp.ClientSession:
        """Создает новую aiohttp сессию"""
        return aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(
                force_close=True,
                enable_cleanup_closed=True,
                limit=0
            ),
            timeout=aiohttp.ClientTimeout(total=30)
        )
    
    async def _recreate_session(self):
        """Пересоздает HTTP-сессию и обновляет зависимости"""
        logger.critical("Recreating HTTP session due to closed state...")
        try:
            if self.session:
                await self.session.close()
            self.session = await self._create_session()
            
            # Обновляем сессии во всех зависимых компонентах
            self.rss_parser.session = self.session
            if self.yandex_gpt:
                self.yandex_gpt.session = self.session
                
            logger.info("HTTP session recreated successfully")
        except Exception as e:
            logger.error(f"Session recreation failed: {str(e)}")

    async def start(self) -> bool:
        """Запуск основных процессов бота"""
        if self.is_running:
            logger.warning("Controller is already running")
            return False
            
        try:
            self.session = await self._create_session()
            self.image_semaphore = asyncio.Semaphore(self.config.MAX_CONCURRENT_IMAGE_TASKS)
            self.is_running = True
            self.last_post_time = time.time()
            
            logger.info("Starting controller with %d RSS feeds", len(self.config.RSS_URLS))
            
            # Запуск основных задач
            self.rss_task = asyncio.create_task(self._rss_processing_loop())
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            self.session_refresh_task = asyncio.create_task(self._session_refresh_loop())
            self.task_monitor_task = asyncio.create_task(self._task_monitor_loop())
            
            self.rss_parser = AsyncRSSParser(
            session=self.session,
            proxy_url=self.config.PROXY_URL,
            on_session_recreate=self._recreate_session  # Передаем колбэк
            )
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
            if self.session_refresh_task and not self.session_refresh_task.done():
                self.session_refresh_task.cancel()
                tasks.append(self.session_refresh_task)
            if self.task_monitor_task and not self.task_monitor_task.done():
                self.task_monitor_task.cancel()
                tasks.append(self.task_monitor_task)
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # Закрытие сессии и очистка ресурсов
            await self._safe_shutdown()
            
            # Сохранение состояния
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
        if self.image_executor:
            self.image_executor.shutdown(wait=False)

    async def _session_refresh_loop(self):
        """Периодическое обновление HTTP-сессии"""
        logger.info("Starting session refresh loop")
        while self.is_running:
            try:
                await asyncio.sleep(3600)  # Каждый час
                
                if self.session:
                    logger.info("Refreshing HTTP session...")
                    await self.session.close()
                    self.session = await self._create_session()
                    
                    # Обновляем сессии в зависимых компонентах
                    self.rss_parser.session = self.session
                    if self.yandex_gpt:
                        self.yandex_gpt.session = self.session
                        
                    logger.info("HTTP session refreshed successfully")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Session refresh failed: {str(e)}")
                # Повторить через 5 минут при ошибке
                await asyncio.sleep(300)

    async def _task_monitor_loop(self):
        """Мониторинг и очистка асинхронных задач"""
        logger.info("Starting task monitor loop")
        max_tasks = 500  # Максимальное количество задач
        while self.is_running:
            try:
                await asyncio.sleep(300)  # Проверка каждые 5 минут
                await self._cleanup_tasks(max_tasks)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Task monitor error: {str(e)}")

    async def _cleanup_tasks(self, max_tasks: int):
        """Очистка завершенных и старых задач"""
        try:
            # Получаем все текущие задачи, кроме текущей
            current_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            task_count = len(current_tasks)
            
            if task_count <= max_tasks:
                return
                
            logger.warning(f"High task count: {task_count}/{max_tasks}, performing cleanup...")
            
            # Собираем завершенные задачи
            finished_tasks = [t for t in current_tasks if t.done()]
            
            # Собираем самые старые активные задачи для отмены
            tasks_to_cancel = sorted(
                [t for t in current_tasks if not t.done()],
                key=lambda t: t.get_name() if hasattr(t, 'get_name') else str(t),
                reverse=True
            )[:max(0, task_count - max_tasks)]
            
            # Отменяем выбранные задачи
            for task in tasks_to_cancel:
                if not task.done():
                    task.cancel()
            
            # Ждем завершения отмененных задач
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
            
            logger.info(f"Cleaned up {len(finished_tasks)} finished and {len(tasks_to_cancel)} old tasks")
        except Exception as e:
            logger.error(f"Task cleanup failed: {str(e)}")

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
                
                # Сохранение состояния каждые 5 минут
                if time.time() - last_save_time > 300:
                    self.state_manager.save_state()
                    last_save_time = time.time()
                    
                await asyncio.sleep(self.config.CHECK_INTERVAL)
                
            except asyncio.CancelledError:
                logger.info("RSS processing loop cancelled")
                break
            except Exception as e:
                logger.error("Error in RSS processing loop: %s", str(e), exc_info=True)
                await asyncio.sleep(min(60, self.config.CHECK_INTERVAL * 2))

    async def _fetch_all_feeds(self) -> List[Dict]:
        """Загрузка RSS-лент с детализированным логированием"""
        new_posts = []
        if not self.is_running:
            return new_posts
            
        logger.info("⏳ Начало загрузки RSS-лент")
        active_feeds = 0
        total_new = 0
        
        for i, url in enumerate(self.config.RSS_URLS):
            try:
                if not self.is_running:
                    break
                    
                # Пропуск неактивных лент
                if not self.config.RSS_ACTIVE[i]:
                    logger.debug("⏭ Лента отключена: %s", url)
                    continue

                # Двойная проверка состояния сессии
                if self.rss_parser.session.closed:
                    await self._recreate_session()
                    
                logger.debug("📥 Загрузка ленты: %s", url)
                start_time = time.time()
                feed_content = await self.rss_parser.fetch_feed(url)
                
                if not feed_content:
                    logger.info("🚫 Лента пуста: %s", url)
                    continue
                    
                # Парсинг записей
                entries = self.rss_parser.parse_entries(feed_content)
                if not entries:
                    logger.info("🔍 Нет новых записей в ленте: %s", url)
                    continue
                    
                # Нормализация и фильтрация
                valid_entries = []
                for entry in entries:
                    if isinstance(entry, dict) and entry.get('link'):
                        valid_entries.append(entry)
                    elif isinstance(entry, str) and entry.strip():
                        valid_entries.append({'link': entry, 'source': url})
                
                if not valid_entries:
                    logger.info("🔍 Нет валидных записей в ленте: %s", url)
                    continue
                    
                new_posts.extend(valid_entries)
                active_feeds += 1
                elapsed = time.time() - start_time
                logger.info("✅ Добавлено %d записей из %s (%.2f сек)", 
                            len(valid_entries), urlparse(url).netloc, elapsed)
                    
            except Exception as e:
                logger.error("⚠️ Ошибка обработки ленты %s: %s", url, str(e))
                self.stats['errors'] += 1
                
        total_new = len(new_posts)
        if total_new == 0:
            logger.info("🔍 Все ленты обработаны, новых постов не найдено")
        else:
            logger.info("📥 Всего загружено %d новых постов из %d лент", total_new, active_feeds)
        
        return new_posts

    async def _process_new_posts(self, posts: List[Dict]):
        """Обработка новых постов с улучшенным логированием"""
        if not posts:
            logger.info("🔍 Нет новых постов для обработки")
            return
            
        # Убрать ограничение на количество обрабатываемых постов
        max_to_process = len(posts)  # Обрабатываем ВСЕ посты
        
        duplicate_count = 0
        processed_count = 0
        skipped_count = 0
        start_time = time.time()
        
        logger.info(f"🔄 Начало обработки {max_to_process} новых постов")
        
        # Временный кеш для быстрой проверки дубликатов
        duplicate_cache = set()
        
        for i, post in enumerate(posts[:max_to_process]):
            if not self.is_running:
                break
                
            try:
                # Быстрая нормализация
                temp_post = self._quick_normalize(post)
                if not temp_post:
                    skipped_count += 1
                    continue
                    
                # Генерация ID
                post_id = self._generate_post_id(temp_post)
                
                # Проверка дубликата
                if self.state_manager.is_entry_sent(post_id):
                    duplicate_count += 1
                    duplicate_cache.add(post_id)
                    continue
                    
                # Задержка между постами
                await self._enforce_post_delay()
                
                # Обработка поста
                if await self._process_single_post(post):
                    processed_count += 1
                else:
                    skipped_count += 1
                    
            except Exception as e:
                logger.error("⚠️ Ошибка обработки поста: %s", str(e))
                skipped_count += 1
        
        # Итоговая статистика
        elapsed = time.time() - start_time
        total_skipped = duplicate_count + skipped_count
        
        logger.info("📊 Итоги обработки (%.2f сек):", elapsed)
        logger.info("   ✅ Успешно обработано: %d", processed_count)
        logger.info("   ⏭ Пропущено дубликатов: %d", duplicate_count)
        
        if skipped_count > 0:
            logger.info("   ⚠️ Пропущено по другим причинам: %d", skipped_count)
        
        logger.info("   🔄 Всего пропущено: %d", total_skipped)
        
        # Обновление статистики
        self.stats['duplicates_rejected'] += duplicate_count
        self.stats['posts_processed'] = self.stats.get('posts_processed', 0) + processed_count
        self.stats['posts_skipped'] = self.stats.get('posts_skipped', 0) + total_skipped

    def _quick_normalize(self, post: Union[Dict, str]) -> Optional[Dict]:
        """Быстрая нормализация только для проверки дубликатов"""
        if isinstance(post, dict) and post.get('link'):
            return {
                'link': post['link'],
                'title': post.get('title', '')
            }
        elif isinstance(post, str) and post:
            return {'link': post, 'title': ''}
        return None

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
        """Оптимизированная обработка поста"""
        image_path = None
        try:
            # 1. Нормализация поста
            normalized_post = self._normalize_post(post)
            if not normalized_post:
                return False

            # 2. Генерация ID
            post_id = self._generate_post_id(normalized_post)
            normalized_post['post_id'] = post_id
            original_title = normalized_post.get('title', '')[:50]
            logger.debug("🆔 Обработка поста: %s", original_title)

            # 3. Обработка контента
            processed_content = await self._process_post_content(normalized_post)
            if processed_content is None:
                return False

            # 4. Получение изображения
            if self.config.IMAGE_SOURCE != 'none':
                try:
                    image_path = await self._get_post_image(normalized_post)
                    if not image_path and self.config.IMAGE_SOURCE == 'required':
                        return False
                except Exception:
                    if self.config.IMAGE_SOURCE == 'required':
                        return False

            # 5. Отправка в Telegram
            processed_title = processed_content.get('title', '')[:50]
            success = await self._send_post_to_telegram(
                processed_content, 
                normalized_post, 
                image_path
            )
            
            if success:
                self._update_stats_after_post(normalized_post)
                logger.info("✅ Пост отправлен: %s", processed_title)
                return True
            return False

        except Exception as e:
            logger.error("⚠️ Ошибка обработки: %s", str(e))
            return False
            
        finally:
            # Очистка временных файлов
            if image_path and os.path.exists(image_path):
                try:
                    os.unlink(image_path)
                except OSError:
                    pass

    def _generate_content_hash(self, post: Dict) -> str:
        """Генерация MD5 хеша контента поста"""
        content = f"{post.get('title', '')}{post.get('description', '')}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def _normalize_post(self, post: Union[Dict, str]) -> Dict:
        """Нормализация поста в стандартный формат"""
        if isinstance(post, str):
            return {
                'link': post,
                'title': '',
                'description': '',
                'pub_date': datetime.now().isoformat()
            }
        if isinstance(post, dict):
            # Убедимся, что все необходимые поля присутствуют
            post.setdefault('link', '')
            post.setdefault('title', '')
            post.setdefault('description', '')
            post.setdefault('pub_date', datetime.now().isoformat())
            return post
        logger.error("Неподдерживаемый тип поста: %s", type(post))
        return None

    def _generate_post_id(self, post: Dict) -> str:
        """Генерация уникального ID на основе стабильных данных"""
        stable_data = f"{post.get('link', '')}{post.get('title', '')}"
        return hashlib.md5(stable_data.encode()).hexdigest()

    def _update_stats_after_post(self, post: Dict):
        """Обновление статистики после успешной отправки"""
        self.state_manager.add_sent_entry(post)
        self.stats['posts_sent'] += 1
        self.stats['last_post'] = datetime.now()
        self.last_post_time = time.time()
        
        # Обновление почасовой статистики
        hour = datetime.now().hour
        self.hourly_stats[f"hour_{hour}"] = self.hourly_stats.get(f"hour_{hour}", 0) + 1
        logger.debug("📊 Статистика обновлена: +1 пост")

    def _should_skip_post(self, post: Dict) -> bool:
        """Проверка на дубликат без индивидуального логирования"""
        post_id = post.get('post_id', '')
        if not post_id:
            return True
            
        # Проверка дубликата по ID
        if self.state_manager.is_entry_sent(post_id):
            return True
            
        # Проверка дубликата по хешу контента
        content_hash = self._generate_content_hash(post)
        if content_hash and self.state_manager.is_hash_sent(content_hash):
            return True
            
        return False
    
    async def _process_post_content(self, post: Dict) -> Optional[Dict[str, str]]:
        """Обработка контента с жёсткой фильтрацией ИИ-результатов"""
        try:
            title = post.get('title', '')
            description = post.get('description', '')
            
            # Если контент слишком короткий - сразу пропускаем пост
            if len(title) < 5 or len(description) < 20:
                logger.warning("Слишком короткий контент, пропуск поста")
                return None

            # Проверяем условия для использования ИИ
            use_ai = (
                self.config.ENABLE_YAGPT and
                self.yandex_gpt and
                self.yandex_gpt.active and
                self.yandex_gpt.is_available()
            )

            # Если ИИ отключен - используем оригинальный контент
            if not use_ai:
                return {
                    'title': self._truncate_text(title, self.config.MAX_TITLE_LENGTH),
                    'description': self._truncate_text(description, self.config.MAX_DESC_LENGTH)
                }

            # Вызов ИИ для улучшения контента
            logger.debug(f"Обработка через YandexGPT: {title[:50]}...")
            result = await self.yandex_gpt.enhance(title, description)
            
            # Если ИИ вернул None (плохой ответ) - пропускаем пост
            if result is None:
                logger.warning("ИИ вернул некачественный результат, пропуск поста")
                return None
                
            # Формируем обработанный контент
            processed_content = {
                'title': self._truncate_text(result.get('title', title), self.config.MAX_TITLE_LENGTH),
                'description': self._truncate_text(result.get('description', description), self.config.MAX_DESC_LENGTH)
            }
            
            # Жёсткая проверка на SEO-мусор
            if self._contains_low_quality_phrases(processed_content):
                logger.warning("Обнаружен SEO-мусор в результате ИИ, пропуск поста")
                return None
                
            return processed_content

        except Exception as e:
            logger.error(f"Ошибка обработки контента: {str(e)}", exc_info=True)
            return None  # Пропускаем пост при ошибках
        
    def _contains_low_quality_phrases(self, content: Dict) -> bool:
        """Определяет, содержит ли контент SEO-мусор"""
        # Ключевые фразы для пропуска
        skip_phrases = [
            "в интернете есть много сайтов",
            "посмотрите, что нашлось в поиске",
            "дополнительные материалы:",
            "смотрите также:",
            "читайте далее",
            "читайте также",
            "рекомендуем прочитать",
            "подробнее на сайте",
            "другие источники:",
            "больше информации можно найти",
            "в поиске найдены"
            "скидка"
            "скидки"
            "купон"
            "купоны"
            "акция"
            "акции"
        ]
        
        # Объединяем заголовок и описание
        full_text = f"{content['title']} {content['description']}".lower()
        
        # Проверяем наличие запрещённых фраз
        for phrase in skip_phrases:
            if phrase in full_text:
                return True
                
        # Проверяем на Markdown-ссылки
        if re.search(r'\[.*\]\(https?://[^\)]+\)', full_text):
            return True
            
        return False

    def _log_skipped_post(self, post: Dict, reason: str):
        """Логирует информацию о пропущенном посте"""
        log_data = {
            'timestamp': datetime.now().isoformat(),
            'original_title': post.get('title'),
            'original_description': post.get('description'),
            'link': post.get('link'),
            'reason': reason,
            'post_id': post.get('post_id', '')
        }
        
        try:
            # Создаём директорию для логов если её нет
            os.makedirs('logs', exist_ok=True)
            
            # Записываем в JSON Lines формат
            with open('logs/skipped_posts.json', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"Ошибка логирования пропущенного поста: {str(e)}")

    @staticmethod
    def _truncate_text(text: str, max_length: int) -> str:
        """Обрезает текст до указанной длины с учетом слов"""
        if len(text) <= max_length:
            return text
            
        # Ищем последний пробел перед максимальной длиной
        truncated = text[:max_length]
        if last_space := truncated.rfind(' '):
            truncated = truncated[:last_space]
            
        return truncated + '...'

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

    async def _generate_image(self, title: str) -> Optional[str]:
        """Генерация изображения в отдельном процессе"""
        try:
            logger.debug(f"Generating image for title: {title[:50]}")
            loop = asyncio.get_running_loop()
            
            # Выносим операцию в отдельный процесс
            image_path = await loop.run_in_executor(
                self.image_executor,
                self.image_generator._sync_generate_image,
                title
            )
            
            if image_path:
                self.stats['images_generated'] += 1
                logger.info(f"Image generated: {image_path}")
            return image_path
        except Exception as e:
            logger.error(f"Image generation failed: {str(e)}")
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

    def _remove_formatting(self, text: str) -> str:
        """
        Удаляет Markdown, HTML и служебные префиксы из текста
        :param text: Исходный текст
        :return: Очищенный текст
        """
        if not text:
            return ""
        
        # Удаление Markdown-разметки (**текст**, __текст__)
        text = re.sub(r'\*\*|\_\_', '', text)
        
        # Удаление HTML-тегов
        text = re.sub(r'<[^>]+>', '', text)
        
        # Удаление служебных префиксов
        text = re.sub(
            r'^(\s*[#\*\s]*(Заголовок|Title|Заг|Описание|Description|Опц|Desc)[\s:\-\—]*\s*[#\*\s]*)', 
            '', 
            text, 
            flags=re.IGNORECASE
        )
        
        # Удаление ведущих символов пунктуации
        text = re.sub(r'^[\s\:\-\—\#\*]+', '', text)
        
        # Удаление двойных пробелов
        text = re.sub(r'\s{2,}', ' ', text)
        
        return text.strip()

    async def _send_post_to_telegram(self, content: Dict, post: Dict, image_path: Optional[str]) -> bool:
        """Отправка поста в Telegram канал с очисткой форматирования"""
        try:
            logger.debug(f"Original title: {content.get('title', '')}")
            logger.debug(f"Original description: {content.get('description', '')}")
            # Функция для очистки текста от форматирования
            def clean_text(text: str) -> str:
                """Удаляет Markdown, HTML и служебные префиксы из текста"""
                if not text:
                    return ""
                
                # Удаление Markdown-разметки (**текст**, __текст__)
                text = re.sub(r'\*\*|\_\_', '', text)
                
                # Удаление HTML-тегов
                text = re.sub(r'<[^>]+>', '', text)
                
                # Удаление служебных префиксов (Заголовок:, Описание: и т.д.)
                text = re.sub(
                    r'^(\s*[#\*\s]*(Заголовок|Title|Заг|Описание|Description|Опц|Desc)[\s:\-\—]*\s*[#\*\s]*)', 
                    '', 
                    text, 
                    flags=re.IGNORECASE
                )
                
                # Удаление ведущих символов пунктуации
                text = re.sub(r'^[\s\:\-\—\#\*]+', '', text)
                
                # Удаление двойных пробелов
                text = re.sub(r'\s{2,}', ' ', text)
                
                return text.strip()

            # Очищаем заголовок и описание
            title = clean_text(content.get('title', ''))
            description = clean_text(content.get('description', ''))
            
            # Форматирование сообщения
            message_text = f"<b>{title}</b>\n\n{description}\n\n<a href='{post.get('link', '')}'>Читать далее</a>"
            logger.debug(f"Cleaned title: {title}")
            logger.debug(f"Cleaned description: {description}")
            # Отправка поста с изображением или без
            if image_path and os.path.exists(image_path):
                success = await self.telegram_bot.send_post(
                    title=title,
                    description=description,
                    link=post.get('link', ''),
                    image_path=image_path
                )
            else:
                success = await self.telegram_bot.send_post(
                    title=title,
                    description=description,
                    link=post.get('link', ''),
                    image_path=None
                )
                
            if success:
                logger.info(f"Post sent successfully: {title[:50]}...")
                return True
            else:
                logger.error(f"Failed to send post: {title[:50]}...")
                return False
                
        except Exception as e:
            logger.error(f"Error sending post to Telegram: {str(e)}")
            return False

    def _update_stats_after_post(self, post: Dict):
        """Обновление статистики после успешной отправки поста"""
        self.state_manager.add_sent_entry(post)
        self.stats['posts_sent'] += 1
        self.stats['last_post'] = datetime.now()
        self.last_post_time = time.time()
        
        # Обновление почасовой статистики
        hour = datetime.now().hour
        self.hourly_stats[f"hour_{hour}"] = self.hourly_stats.get(f"hour_{hour}", 0) + 1

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
    
    async def toggle_rss_feed(self, index: int, enable: bool) -> bool:
        """Активирует/деактивирует RSS-ленту"""
        try:
            if 0 <= index < len(self.config.RSS_URLS):
                self.config.RSS_ACTIVE[index] = enable
                self.config.save_to_env_file("RSS_ACTIVE", str(self.config.RSS_ACTIVE))
                
                # Обновляем парсер
                self.rss_parser.set_feed_status(
                    self.config.RSS_URLS[index], 
                    enable
                )
                return True
            return False
        except Exception as e:
            logger.error(f"Ошибка переключения ленты: {str(e)}")
            return False
            
    def update_rss_state(self, urls: List[str], active: List[bool]):
        """Обновляет состояние RSS лент"""
        self.config.RSS_URLS = urls
        self.config.RSS_ACTIVE = active
        self.config.save_to_env_file("RSS_URLS", json.dumps(urls))
        self.config.save_to_env_file("RSS_ACTIVE", json.dumps(active))
        
        # Обновляем статусы в парсере
        for i, url in enumerate(urls):
            self.rss_parser.set_feed_status(url, active[i])
    
    def get_rss_state(self) -> Tuple[List[str], List[bool]]:
        """Возвращает текущее состояние RSS"""
        
        return self.config.RSS_URLS, self.config.RSS_ACTIVE
    
    async def refresh_rss_status(self) -> bool:
        """Обновление статуса RSS лент. Возвращает True если были изменения"""
        changed = False
        
        # Если у парсера есть метод refresh_status
        if hasattr(self.rss_parser, 'refresh_status'):
            for url in self.config.RSS_URLS:
                if self.rss_parser.refresh_status(url):
                    changed = True
        
        # Если у парсера есть метод get_last_check
        if hasattr(self.rss_parser, 'get_last_check'):
            for url in self.config.RSS_URLS:
                last_check = self.rss_parser.get_last_check(url)
                if last_check:
                    # Проверяем, было ли обновление с момента последней проверки
                    if not self.stats.get(f'last_rss_check_{url}') or last_check > self.stats[f'last_rss_check_{url}']:
                        self.stats[f'last_rss_check_{url}'] = last_check
                        changed = True
        
        return changed
    
    def get_rss_status(self) -> List[Dict]:
        """Возвращает статус с учетом активности"""
        status_list = []
        
        for i, url in enumerate(self.config.RSS_URLS):
            status = {
                'url': url,
                'active': self.config.RSS_ACTIVE[i],
                'error_count': 0,
                'last_check': None
            }
            
            # Получаем количество ошибок
            if hasattr(self.rss_parser, 'get_error_count'):
                status['error_count'] = self.rss_parser.get_error_count(url)
            
            # Получаем время последней проверки
            if hasattr(self.rss_parser, 'get_last_check'):
                status['last_check'] = self.rss_parser.get_last_check(url)
            
            status_list.append(status)
        
        return status_list
            
    async def show_ai_settings(self, callback: CallbackQuery) -> None:
        """Показывает настройки AI (перенаправляем в Telegram интерфейс)"""
        # Этот метод теперь полностью реализован в Telegram интерфейсе
        pass
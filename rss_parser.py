from concurrent.futures import ProcessPoolExecutor
from logging import config
import re
from urllib.parse import urljoin
import feedparser
import logging
import aiohttp
import hashlib
from typing import Any, Dict, List, Optional, Union, Set, Callable
import asyncio
from defusedxml import ElementTree as ET
from io import BytesIO
from datetime import datetime
from dateutil import parser as date_parser
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger('AsyncRSSParser')

class AsyncRSSParser:
    MAX_ENCLOSURES = 20  # Максимальное количество вложений для обработки
    CONTENT_SELECTORS = [
        'article img',
        '.post-content img',
        '.article-body img',
        'main img',
        'figure img',
        'picture source',
        '[itemprop="image"]',
        '.content img',  # Добавлено для Хабра
        '.article img',  # Общие селекторы
        '.post__body img',  # Специально для Хабра
        '.story__content img'  # Для lenta.ru
    ]

    def __init__(
        self, 
        session: aiohttp.ClientSession, 
        proxy_url: Optional[str] = None, 
        on_session_recreate: Optional[Callable] = None  # Добавлен параметр
    ):
        self.session = session
        self.proxy_url = proxy_url
        self.on_session_recreate = on_session_recreate  # Инициализация атрибута
        self.timeout = aiohttp.ClientTimeout(total=30, sock_read=25)
        self.semaphore = asyncio.Semaphore(5)
        self.executor = ProcessPoolExecutor(max_workers=2)
        self.config = config
        self.feed_status = {}
        self.feed_errors = {}
        self.max_retries = 3  # Максимальное количество попыток
        self.retry_delay = 0.5  # Задержка между попытками в секундах

    def set_feed_status(self, url: str, active: bool):
        """Устанавливает статус активности для RSS-ленты"""
        self.feed_status[url] = active

    def refresh_status(self, url: str):
        """Сброс счетчика ошибок для ленты"""
        if url in self.feed_errors:
            del self.feed_errors[url]
            logger.info(f"RSS status reset for {url}")

    async def fetch_feed(self, url: str) -> Optional[Dict[str, Any]]:
        """Асинхронно загружает и парсит RSS-ленту с повторными попытками"""
        if not self.feed_status.get(url, True):
            return None
        
        # Проверяем состояние сессии перед выполнением запроса
        if self.session.closed:
            logger.critical("Session is closed! Attempting to recreate...")
            if self.on_session_recreate:
                await self.on_session_recreate()  # Вызываем колбэк восстановления
            else:
                logger.error("No session recreation callback available!")
                return None

        logger.info(f"Fetching RSS feed: {url}")
        
        for attempt in range(1, self.max_retries + 1):
            try:
                # Добавляем дополнительную проверку состояния сессии
                if self.session.closed:
                    logger.error("Session closed unexpectedly during retry")
                    return None
                    
                async with self.session.get(
                    url,
                    proxy=self.proxy_url if self.proxy_url else None,
                    timeout=self.timeout,
                    headers={'User-Agent': 'RSSBot/1.0'}
                ) as response:
                    if response.status != 200:
                        logger.error(f"HTTP error {response.status} for {url}")
                        return None

                    content = await response.read()
                    logger.debug(f"Raw content received for {url}, length: {len(content)} bytes")
                    return await self._safe_parse_feed(content)

            except aiohttp.ClientOSError as e:
                if "APPLICATION_DATA_AFTER_CLOSE_NOTIFY" in str(e) and attempt < self.max_retries:
                    logger.warning(f"SSL error detected, retrying ({attempt}/{self.max_retries}) for {url}")
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    self.feed_errors[url] = self.feed_errors.get(url, 0) + 1
                    logger.error(f"Error fetching {url}: {str(e)}", exc_info=True)
                    return None
                    
            except RuntimeError as e:
                if "Session is closed" in str(e):
                    logger.critical("Session closed during request processing")
                    return None
                else:
                    self.feed_errors[url] = self.feed_errors.get(url, 0) + 1
                    logger.error(f"RuntimeError fetching {url}: {str(e)}", exc_info=True)
                    return None
                    
            except Exception as e:
                self.feed_errors[url] = self.feed_errors.get(url, 0) + 1
                logger.error(f"Error fetching {url}: {str(e)}", exc_info=True)
                return None
        
        return None
        
    def get_error_count(self, url: str) -> int:
        """Возвращает количество ошибок для указанного URL"""
        return self.feed_errors.get(url, 0)

    async def _safe_parse_feed(self, xml_content: Any) -> Optional[Dict[str, Any]]:
        """Безопасный парсинг RSS с защитой от XXE и обработкой ошибок"""
        try:
            if xml_content is None:
                return None

            # Try direct feedparser parsing first (faster)
            try:
                parsed = feedparser.parse(xml_content)
                if parsed.get('entries'):
                    return parsed
            except Exception as e:
                logger.debug(f"Direct feedparser parsing failed, trying defusedxml: {str(e)}")

            # Fallback to defusedxml for security
            if isinstance(xml_content, bytes):
                try:
                    xml_content = xml_content.decode('utf-8')
                except UnicodeDecodeError:
                    xml_content = xml_content.decode('latin-1', errors='replace')

            cleaned_content = re.sub(
                r'<!DOCTYPE[^>[]*(\[[^]]*\])?>',
                '',
                xml_content,
                flags=re.IGNORECASE
            )

            try:
                xml_bytes = cleaned_content.encode('utf-8')
                parser = ET.DefusedXMLParser(
                    forbid_dtd=True,
                    forbid_entities=True,
                    forbid_external=True
                )
                tree = ET.parse(BytesIO(xml_bytes), parser=parser)
                root = tree.getroot()
                if root is not None:
                    return feedparser.parse(BytesIO(ET.tostring(root)))
            except Exception as e:
                logger.debug(f"DefusedXML parsing failed, falling back to feedparser: {str(e)}")

            # Final fallback
            return feedparser.parse(cleaned_content)

        except Exception as e:
            logger.error(f"Failed to parse feed content: {str(e)}")
            return None

    def parse_entries(self, feed_content: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Парсит содержимое RSS-ленты и извлекает записи"""
        entries = []
        if not feed_content or not isinstance(feed_content, dict) or 'entries' not in feed_content:
            logger.debug("No entries found in feed")
            return entries

        seen_guids: Set[str] = set()

        for entry in feed_content['entries']:
            try:
                # Генерация уникального идентификатора для записи
                guid = self._generate_entry_guid(entry)
                if guid in seen_guids:
                    continue
                seen_guids.add(guid)

                # Основные поля записи
                link = self._get_entry_link(entry)
                description = self._clean_html(getattr(entry, 'summary', getattr(entry, 'description', '')))

                # Извлекаем изображение
                image_url = self._extract_image_url(entry)
                if not image_url and link:
                    # Пробуем извлечь из HTML-описания с базовым URL
                    base_url = link if link else self._get_feed_base_url(feed_content)
                    image_url = self._extract_image_from_html(description, base_url)

                entry_data = {
                    'guid': guid,
                    'title': self._clean_text(getattr(entry, 'title', 'No title')),
                    'description': description,
                    'link': link,
                    'pub_date': self._get_pub_date(entry),
                    'image_url': image_url,
                    'author': self._get_author(entry),
                    'categories': self._get_categories(entry)
                }
                entries.append(entry_data)
            except Exception as e:
                logger.error(f"Error parsing entry: {str(e)}", exc_info=True)
                continue

        logger.debug(f"Parsed {len(entries)} entries from feed")
        return entries

    def _extract_image_from_html(self, html_content: str, base_url: str) -> Optional[str]:
        """Улучшенный поиск изображений в HTML-контенте"""
        if not html_content:
            return None

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            candidate_images = []
            
            # Расширенные селекторы для популярных платформ
            selectors = [
                'img',  # Все изображения
                'picture source[srcset]',
                '[data-src]',  # Lazy-loaded
                '.post-content img',
                '.article-body img',
                '.content img',
                '.post__body img',  # Специфично для Хабра
                '.story__content img'  # Lenta.ru
            ]
            
            for selector in selectors:
                for element in soup.select(selector):
                    src = self._get_image_src(element)
                    if not src:
                        continue
                        
                    normalized_url = self._normalize_image_url(src, base_url)
                    if not normalized_url:
                        continue
                        
                    # Проверка на релевантность
                    if self._is_relevant_image(element, normalized_url):
                        candidate_images.append(normalized_url)
            
            # Возвращаем первое релевантное изображение
            return candidate_images[0] if candidate_images else None
            
        except Exception as e:
            logger.debug(f"HTML content image extraction error: {str(e)}")
            return None

    @staticmethod
    def _get_image_src(element: Tag) -> Optional[str]:
        """Извлекает URL из различных атрибутов"""
        for attr in ['src', 'srcset', 'data-src', 'data-lazy-src']:
            if attr in element.attrs:
                value = element[attr]
                if isinstance(value, list):
                    value = value[0]
                return value.split()[0] if ' ' in value else value
        return None

    @staticmethod
    def _is_relevant_image(element: Tag, img_url: str) -> bool:
        """Определяет, является ли изображение релевантным"""
        # Фильтр по URL
        if any(bad in img_url.lower() for bad in ['pixel', 'icon', 'logo', 'spacer', 'ad', 'button', 'border']):
            return False
            
        # Фильтр по CSS-классам
        classes = element.get('class', [])
        if any(bad in cls.lower() for cls in classes for bad in ['icon', 'logo', 'ad', 'thumb', 'mini']):
            return False
            
        # Фильтр по размеру (если указан)
        width = element.get('width')
        height = element.get('height')
        try:
            if width and height:
                if int(width) < 300 or int(height) < 200:
                    return False
        except ValueError:
            pass
            
        return True

    async def extract_primary_image(self, url: str) -> Optional[str]:
        """Извлекает главное изображение со страницы с повторными попытками"""
        for attempt in range(1, self.max_retries + 1):
            try:
                async with self.session.get(
                    url,
                    timeout=self.timeout,
                    headers={'User-Agent': 'RSSBot/1.0'}  # Добавляем User-Agent
                ) as response:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')

                    # 1. Проверка OpenGraph и Twitter Card
                    if meta_image := self._find_meta_image(soup):
                        return meta_image

                    # 2. Поиск в основном контенте
                    if content_image := self._find_content_image(soup, url):
                        return content_image

                    # 3. Резервные варианты
                    return self._find_fallback_image(soup, url)

            except (aiohttp.ClientOSError, asyncio.TimeoutError, aiohttp.ServerDisconnectedError) as e:
                if attempt < self.max_retries:
                    logger.warning(f"Network error detected, retrying ({attempt}/{self.max_retries}) for {url}")
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    logger.error(f"Error extracting image from {url}: {str(e)}")
                    return None
                    
            except Exception as e:
                logger.error(f"Error extracting image from {url}: {str(e)}")
                return None
        
        return None

    def _find_meta_image(self, soup: BeautifulSoup) -> Optional[str]:
        """Ищет изображение в мета-тегах"""
        for meta in soup.find_all('meta'):
            if not isinstance(meta, Tag):
                continue
                
            prop = str(meta.get('property', '')).lower()
            name = str(meta.get('name', '')).lower()
            content = str(meta.get('content', ''))

            if any(p in prop for p in ['og:image', 'image']) or \
            any(n in name for n in ['twitter:image']):
                return content if content else None
        return None

    def _find_content_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Ищет изображение в основном контенте с приоритетом по положению и размеру"""
        candidate_images = []
        
        for selector in self.CONTENT_SELECTORS:
            for img in soup.select(selector):
                if not isinstance(img, Tag):
                    continue

                img_src = img.get('src') or img.get('srcset', '')
                img_src = str(img_src).split()[0] if img_src else ''
                
                if img_src and self._is_valid_image(img, img_src):
                    normalized_url = self._normalize_image_url(img_src, base_url)
                    if not normalized_url:
                        continue
                        
                    # Оценка релевантности изображения
                    relevance = self._image_relevance_score(img, normalized_url)
                    candidate_images.append((relevance, normalized_url))
        
        if not candidate_images:
            return None
        
        # Сортируем по релевантности (высший балл - первый)
        candidate_images.sort(key=lambda x: x[0], reverse=True)
        return candidate_images[0][1]

    @staticmethod
    def _image_relevance_score(img_tag: Tag, img_url: str) -> int:
        """Рассчитывает балл релевантности изображения"""
        score = 0
        
        # Бонус за специальные атрибуты
        if 'data-large-image' in img_tag.attrs:
            score += 50
            
        # Бонус за ключевые слова в URL
        keywords = ['main', 'featured', 'hero', 'cover', 'primary']
        if any(kw in img_url.lower() for kw in keywords):
            score += 30
        
        # Бонус за размеры (если указаны)
        try:
            width = int(img_tag.get('width', 0))
            height = int(img_tag.get('height', 0))
            area = width * height
            score += min(area // 1000, 40)  # Макс +40 баллов за большие размеры
        except:
            pass
        
        # Штраф за социальные иконки
        if 'social' in img_url.lower() or 'icon' in img_url.lower():
            score -= 20
            
        return score

    def _find_fallback_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Резервные методы поиска изображений"""
        # Логотип сайта
        if logo := soup.find('link', rel=['icon', 'shortcut icon']):
            if isinstance(logo, Tag) and (href := logo.get('href')):
                href = str(href)
                return self._normalize_image_url(href, base_url)

        # Первое подходящее изображение
        for img in soup.find_all('img'):
            if isinstance(img, Tag) and (src := img.get('src')):
                src = str(src)
                if self._is_valid_image(img, src):
                    return self._normalize_image_url(src, base_url)
        return None

    @staticmethod
    def _normalize_image_url(url: Optional[str], base_url: str) -> str:
        """Нормализует URL изображения"""
        if not url:
            return ""
        
        # Убрали str(url) - теперь url всегда строка
        if url.startswith(('http://', 'https://')):
            return url
        if url.startswith('//'):
            return f'https:{url}'
        return urljoin(base_url, url)

    @staticmethod
    def _is_valid_image(img_tag: Tag, img_url: str) -> bool:
        """Проверяет валидность изображения"""
        if not img_url or any(x in img_url.lower() for x in ['pixel', 'icon', 'logo', 'spacer', 'ad']):
            return False

        # Преобразуем атрибуты в строки перед обработкой
        width_str = str(img_tag.get('width', '0'))
        height_str = str(img_tag.get('height', '0'))
        
        # Удаляем нечисловые символы (например, 'px')
        width_str = re.sub(r'[^\d]', '', width_str)
        height_str = re.sub(r'[^\d]', '', height_str)
        
        try:
            width_int = int(width_str) if width_str else 0
            height_int = int(height_str) if height_str else 0
            return width_int >= 300 and height_int >= 200
        except ValueError:
            return True

    @staticmethod
    def _get_feed_base_url(feed_content: Any) -> str:
        """Получает базовый URL из фида"""
        if hasattr(feed_content, 'href'):
            return feed_content.href
        if hasattr(feed_content, 'link'):
            return feed_content.link
        return ''

    @staticmethod
    def _generate_entry_guid(entry: Any) -> str:
        """Генерирует уникальный идентификатор для записи"""
        if guid := getattr(entry, 'guid', None):
            return str(guid)
        return hashlib.md5(
            f"{entry.get('link','')}"
            f"{entry.get('title','')}"
            f"{entry.get('published','')}"
            f"{entry.get('updated','')}".encode()
        ).hexdigest()

    @staticmethod
    def _clean_text(text: str) -> str:
        """Очищает текст от лишних пробелов"""
        if not text:
            return ""
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _clean_html(html: str) -> str:
        """Удаляет HTML-теги из текста"""
        if not html:
            return ""
        return re.sub(r'<[^>]+>', '', html).strip()

    @staticmethod
    def _get_entry_link(entry: Any) -> Optional[str]:
        """Извлекает ссылку из записи"""
        if hasattr(entry, 'link'):
            return entry.link
        return None

    @staticmethod
    def _get_pub_date(entry: Any) -> str:
        """Извлекает дату публикации"""
        for attr in ['published', 'updated', 'pubDate', 'date']:
            if hasattr(entry, attr):
                try:
                    return date_parser.parse(str(getattr(entry, attr))).isoformat()
                except Exception:
                    continue
        return datetime.now().isoformat()

    def _extract_image_url(self, entry: Any) -> Optional[str]:
        """Извлекает URL изображения из записи с расширенной поддержкой форматов"""
        # 1. Проверка медиа-контента (Atom) - <media:content>
        if hasattr(entry, 'media_content'):
            for media in entry.media_content[:self.MAX_ENCLOSURES]:
                media_type = getattr(media, 'type', '')
                if media_type.startswith('image/'):
                    url = getattr(media, 'url', None)
                    if url:
                        return str(url)

        # 2. Проверка вложений (RSS) - <enclosure>
        if hasattr(entry, 'enclosures'):
            for enclosure in entry.enclosures[:self.MAX_ENCLOSURES]:
                enc_type = getattr(enclosure, 'type', '')
                if enc_type.startswith('image/'):
                    url = getattr(enclosure, 'url', getattr(enclosure, 'href', None))
                    if url:
                        return str(url)

        # 3. Проверка миниатюр (Media RSS) - <media:thumbnail>
        if hasattr(entry, 'media_thumbnail'):
            thumbnails = entry.media_thumbnail
            if not isinstance(thumbnails, list):
                thumbnails = [thumbnails]

            for thumb in thumbnails[:self.MAX_ENCLOSURES]:
                url = getattr(thumb, 'url', None)
                if url:
                    return str(url)

        # 4. Явно указанные изображения в стандартных полях
        for field_name in ['image', 'image_url', 'thumbnail']:
            if hasattr(entry, field_name):
                field_value = getattr(entry, field_name)
                if isinstance(field_value, str) and field_value.startswith('http'):
                    return field_value
                elif isinstance(field_value, dict) and 'url' in field_value:
                    return str(field_value['url'])

        # 5. Расширенная проверка структурированных данных
        for field in ['media:content', 'media:thumbnail', 'og:image']:
            if field in entry:
                value = entry[field]
                if isinstance(value, dict) and 'url' in value:
                    return str(value['url'])
                elif isinstance(value, list) and len(value) > 0:
                    first_item = value[0]
                    if isinstance(first_item, dict) and 'url' in first_item:
                        return str(first_item['url'])
                    elif isinstance(first_item, str):
                        return first_item
                elif isinstance(value, str):
                    return value

        # 6. Проверка вложенных элементов (для форматов типа JSON Feed)
        if hasattr(entry, 'attachments'):
            for attachment in entry.attachments[:self.MAX_ENCLOSURES]:
                if attachment.get('mime_type', '').startswith('image/'):
                    url = attachment.get('url')
                    if url:
                        return str(url)
                    
        # 7. Поиск изображения в HTML-контенте (для Habr и подобных)
        if hasattr(entry, 'description') and entry.description:
            description = getattr(entry, 'description', '')
            base_url = self._get_feed_base_url(entry) or ''
            image_url = self._extract_image_from_html(description, base_url)
            if image_url:
                return image_url

        return None

    @staticmethod
    def _get_author(entry: Any) -> Optional[str]:
        """Извлекает автора записи"""
        if hasattr(entry, 'author'):
            return entry.author
        return None

    @staticmethod
    def _get_categories(entry: Any) -> List[str]:
        """Извлекает категории записи"""
        if not hasattr(entry, 'tags'):
            return []

        categories = []
        for tag in entry.tags:
            if hasattr(tag, 'term'):
                categories.append(tag.term)
            elif isinstance(tag, dict) and 'term' in tag:
                categories.append(tag['term'])
            elif isinstance(tag, str):
                categories.append(tag)

        return categories
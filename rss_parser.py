import re
from urllib.parse import urljoin
import feedparser
import logging
import aiohttp
import hashlib
from typing import Any, Dict, List, Optional, Union, Set
import asyncio
from defusedxml import ElementTree as ET
from io import BytesIO
from datetime import datetime
from dateutil import parser as date_parser
from bs4 import BeautifulSoup, Tag
from bs4.element import _AttributeValue
from bs4._typing import _AttributeValue

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
        '[itemprop="image"]'
    ]

    def __init__(self, session: aiohttp.ClientSession, proxy_url: Optional[str] = None):
        self.session = session
        self.proxy_url = proxy_url
        self.timeout = aiohttp.ClientTimeout(total=15)

    async def fetch_feed(self, url: str) -> Optional[Dict[str, Any]]:
        """Асинхронно загружает и парсит RSS-ленту"""
        logger.info(f"Fetching RSS feed: {url}")
        try:
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

        except Exception as e:
            logger.error(f"Error fetching {url}: {str(e)}", exc_info=True)
            return None

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
        """Извлекает первую подходящую картинку из HTML-описания"""
        if not html_content:
            return None

        try:
            # Расширенные шаблоны для поиска изображений
            patterns = [
                r'<img[^>]+src="([^">]+)"',
                r'<meta[^>]+property="og:image"[^>]+content="([^">]+)"',
                r'<meta[^>]+name="twitter:image"[^>]+content="([^">]+)"',
                r'<source[^>]+srcset="([^">]+)"',
                r'<link[^>]+rel="image_src"[^>]+href="([^">]+)"'
            ]

            for pattern in patterns:
                img_match = re.search(pattern, html_content)
                if img_match:
                    image_url = img_match.group(1)

                    # Убираем параметры из URL (если есть)
                    clean_url = re.sub(r'\?.*$', '', image_url)

                    # Пропускаем маленькие или неявные изображения
                    if any(x in clean_url for x in ['pixel', 'icon', 'logo', 'spacer', 'ad']):
                        continue

                    # Преобразуем относительные пути
                    return self._normalize_image_url(clean_url, base_url)
        except Exception as e:
            logger.error(f"HTML image extraction error: {str(e)}")

        return None

    async def extract_primary_image(self, url: str) -> Optional[str]:
        """Извлекает главное изображение со страницы"""
        try:
            async with self.session.get(url, timeout=self.timeout) as response:
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

        except Exception as e:
            logger.error(f"Error extracting image from {url}: {str(e)}")
            return None

    def _find_meta_image(self, soup: BeautifulSoup) -> Optional[str]:
        """Ищет изображение в мета-тегах"""
        for meta in soup.find_all('meta'):
            if not isinstance(meta, Tag):
                continue

            prop = meta.get('property', '')
            if isinstance(prop, (str, _AttributeValue)):
                prop = str(prop).lower()
            else:
                prop = ''
                
            name = meta.get('name', '')
            if isinstance(name, (str, _AttributeValue)):
                name = str(name).lower()
            else:
                name = ''
                
            content = meta.get('content', '')
            if isinstance(content, (str, _AttributeValue)):
                content = str(content)
            else:
                content = ''

            if any(p in prop for p in ['og:image', 'image']) or \
               any(n in name for n in ['twitter:image']):
                return content if content else None
        return None

    def _find_content_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Ищет изображение в основном контенте"""
        for selector in self.CONTENT_SELECTORS:
            for img in soup.select(selector):
                if not isinstance(img, Tag):
                    continue

                img_src = img.get('src') or img.get('srcset', '')
                if isinstance(img_src, (str, _AttributeValue)):
                    img_src = str(img_src).split()[0] if img_src else ''
                else:
                    img_src = ''

                if img_src and self._is_valid_image(img, img_src):
                    return self._normalize_image_url(img_src, base_url)
        return None

    def _find_fallback_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Резервные методы поиска изображений"""
        # Логотип сайта
        if logo := soup.find('link', rel=['icon', 'shortcut icon']):
            if isinstance(logo, Tag) and (href := logo.get('href')):
                if isinstance(href, (str, _AttributeValue)):
                    href = str(href)
                    return self._normalize_image_url(href, base_url)

        # Первое подходящее изображение
        for img in soup.find_all('img'):
            if isinstance(img, Tag) and (src := img.get('src')):
                if isinstance(src, (str, _AttributeValue)):
                    src = str(src)
                    if self._is_valid_image(img, src):
                        return self._normalize_image_url(src, base_url)
        return None

    @staticmethod
    def _normalize_image_url(url: Union[str, _AttributeValue, None], base_url: str) -> str:
        """Нормализует URL изображения"""
        if not url:
            return ""
            
        url_str = str(url)
        
        if url_str.startswith(('http://', 'https://')):
            return url_str
        if url_str.startswith('//'):
            return f'https:{url_str}'
        return urljoin(base_url, url_str)

    @staticmethod
    def _is_valid_image(img_tag: Tag, img_url: str) -> bool:
        """Проверяет валидность изображения"""
        if not img_url or any(x in str(img_url).lower() for x in ['pixel', 'icon', 'logo', 'spacer', 'ad']):
            return False

        # Проверка размеров через атрибуты
        width = img_tag.get('width', '0')
        height = img_tag.get('height', '0')
        try:
            width_int = int(str(width)) if width else 0
            height_int = int(str(height)) if height else 0
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

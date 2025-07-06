import logging
import json
import re
import time
import aiohttp
import asyncio
from functools import lru_cache
from typing import Dict, Optional
import html

logger = logging.getLogger('AsyncYandexGPT')

class AsyncYandexGPT:
    def __init__(self, config, session: aiohttp.ClientSession):
        self.config = config
        self.session = session
        self.active = bool(config.YANDEX_API_KEY) and not config.DISABLE_YAGPT
        self.error_count = 0
        self.last_error_time = None
        
        # Добавлено для управления квотами
        self.rate_limit_remaining = 100  # Начальное значение
        self.rate_limit_reset = 0  # Время сброса квоты
        self.token_usage = 0  # Текущее использование токенов
    
    def _sanitize_prompt_input(self, text: str) -> str:
        """
        Экранирует специальные символы и предотвращает инъекции в промпт.
        Args:
            text: Входной текст для обработки
        Returns:
            Безопасный текст, готовый к использованию в промпте
        """
        if not isinstance(text, str):
            return ""
        
        # Экранирование HTML/XML
        sanitized = html.escape(text)
        
        # Замена специальных символов
        replacements = {
            '{': '{{',
            '}': '}}',
            '[': '【',
            ']': '】',
            '(': '（',
            ')': '）',
            '"': '\\"',
            "'": "\\'",
            '\n': ' ',
            '\r': ' ',
            '\t': ' '
        }
        
        for char, replacement in replacements.items():
            sanitized = sanitized.replace(char, replacement)
        
        # Удаление управляющих символов
        sanitized = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', sanitized)
        
        # Ограничение длины
        return sanitized[:5000]

    async def enhance(self, title: str, description: str) -> Optional[Dict]:
        # Проверка возможности восстановления после ошибки
        if not self.active and self.config.AUTO_DISABLE_YAGPT:
            if self.last_error_time and time.time() - self.last_error_time > 600:
                self.active = True
                self.error_count = 0
                logger.info("Re-enabling YandexGPT after cooldown period")
            else:
                return None
        
        # Проверка квоты токенов
        if self.token_usage > self.config.YAGPT_MAX_TOKENS * 0.9:
            logger.warning("Token usage limit approached, skipping YandexGPT")
            return None
            
        # Проверка rate limiting
        current_time = time.time()
        if current_time < self.rate_limit_reset:
            wait_time = self.rate_limit_reset - current_time
            logger.warning(f"Rate limit exceeded, waiting {wait_time:.1f}s")
            await asyncio.sleep(wait_time + 1)
        
        # Экранирование пользовательского ввода
        safe_title = self._sanitize_prompt_input(title)
        safe_description = self._sanitize_prompt_input(description)
        
        logger.debug(f"Sending to YandexGPT. Title: {safe_title[:50]}..., Desc: {safe_description[:50]}...")
        
        try:
            headers = {
                "Authorization": f"Api-Key {self.config.YANDEX_API_KEY}",
                "x-folder-id": self.config.YANDEX_FOLDER_ID,
                "Content-Type": "application/json"
            }
            
            payload = {
                "modelUri": f"gpt://{self.config.YANDEX_FOLDER_ID}/{self.config.YAGPT_MODEL}/latest",
                "completionOptions": {
                    "temperature": self.config.YAGPT_TEMPERATURE,
                    "maxTokens": self.config.YAGPT_MAX_TOKENS
                },
                "messages": [
                    {
                        "role": "user",
                        "text": self.config.YAGPT_PROMPT.format(title=safe_title, description=safe_description)
                    }
                ]
            }
            
            async with self.session.post(
                self.config.YANDEX_API_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                
                # Обработка заголовков rate limiting
                if 'X-RateLimit-Remaining' in response.headers:
                    self.rate_limit_remaining = int(
                        response.headers['X-RateLimit-Remaining']
                    )
                if 'X-RateLimit-Reset' in response.headers:
                    self.rate_limit_reset = int(
                        response.headers['X-RateLimit-Reset']
                    )
                
                if response.status == 429:  # Too Many Requests
                    retry_after = int(response.headers.get('Retry-After', 60))
                    logger.warning(f"Rate limited, retrying after {retry_after}s")
                    await asyncio.sleep(retry_after)
                    return await self.enhance(title, description)  # Рекурсивный повтор
                
                if response.status == 200:
                    data = await response.json()
                    logger.debug(f"YandexGPT response: {data}")
                    
                    # Обновляем использование токенов
                    if 'usage' in data.get('result', {}):
                        self.token_usage += data['result']['usage']['totalTokens']
                        logger.info(f"Token usage updated: {self.token_usage}/{self.config.YAGPT_MAX_TOKENS}")
                    
                    result = self.parse_response(data)
                    
                    if result:
                        logger.debug(f"YandexGPT processing successful")
                        return result
                    else:
                        logger.warning("Failed to parse YandexGPT response")
                        raise ValueError("Failed to parse YandexGPT response")
                else:
                    error_text = await response.text()
                    logger.error(f"YandexGPT API error. Status: {response.status}, Response: {error_text}")
                    self.error_count += 1
                    self.last_error_time = time.time()
                    
        except asyncio.TimeoutError:
            logger.error("YandexGPT request timed out")
            self.error_count += 1
            self.last_error_time = time.time()
        except Exception as e:
            logger.error(f"YandexGPT error: {str(e)}")
            self.error_count += 1
            self.last_error_time = time.time()
        
        # Проверяем порог ошибок для отключения
        if (self.config.AUTO_DISABLE_YAGPT and 
            self.error_count >= self.config.YAGPT_ERROR_THRESHOLD):
            logger.warning(f"Disabling YandexGPT after {self.error_count} errors")
            self.active = False
            
        return None

    def parse_response(self, data: Dict) -> Optional[Dict]:
        try:
            if not data.get('result') or not data['result'].get('alternatives'):
                logger.warning("No alternatives in YandexGPT response")
                return None
                
            text = data['result']['alternatives'][0]['message']['text']
            
            # Попытка извлечь JSON
            try:
                start_idx = text.find('{')
                end_idx = text.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    json_str = text[start_idx:end_idx+1]
                    result = json.loads(json_str)
                    if isinstance(result, dict) and 'title' in result and 'description' in result:
                        return {
                            'title': self._sanitize_text(result['title']),
                            'description': self._sanitize_text(result['description'])
                        }
            except (ValueError, json.JSONDecodeError, AttributeError) as e:
                pass
            
            # Расширенные шаблоны для извлечения данных
            patterns = [
                r'(?i)(?:title|заголовок)[\s:]*["\']?(.+?)["\']?(?:\n|$|\.)',
                r'(?i)(?:description|описание)[\s:]*["\']?(.+?)["\']?(?:\n|$|\.)',
                r'{"title"\s*:\s*"([^"]+)"[^}]*"description"\s*:\s*"([^"]+)"}',
                r'<title>(.+?)</title>\s*<description>(.+?)</description>',
                r'(?i)(?:заголовок|title):?\s*([^\n]+)\n+(?:описание|description):?\s*([^\n]+)'
            ]
            
            title_match = None
            desc_match = None
            
            # Поиск заголовка
            for pattern in patterns:
                match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                if match:
                    # Определяем группу с заголовком
                    for i in range(1, min(3, len(match.groups()) + 1)):
                        if match.group(i) and len(match.group(i).strip()) > 5:
                            title_match = match.group(i).strip()
                            break
                    if title_match:
                        break
            
            # Поиск описания
            if title_match:
                for pattern in patterns:
                    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                    if match and len(match.groups()) > 1:
                        # Ищем следующую группу после заголовка
                        for i in range(2, min(4, len(match.groups()) + 1)):
                            if match.group(i) and len(match.group(i).strip()) > 10:
                                desc_match = match.group(i).strip()
                                break
                        if desc_match:
                            break
            
            # Fallback: если не нашли структурированные данные
            if not title_match or not desc_match:
                parts = re.split(r'\n\n|\n-|\n•', text, maxsplit=1)
                if len(parts) >= 2:
                    title_match = parts[0].strip()
                    desc_match = parts[1].strip()
                else:
                    # Последняя попытка - первые 2 предложения
                    sentences = re.split(r'[.!?]\s+', text)
                    if len(sentences) > 1:
                        title_match = sentences[0]
                        desc_match = ' '.join(sentences[1:3])[:500]
                    else:
                        title_match = text[:100]
                        desc_match = text[100:500] if len(text) > 100 else ""
            
            return {
                'title': self._sanitize_text(title_match),
                'description': self._sanitize_text(desc_match)
            }
            
        except Exception as e:
            logger.error(f"YandexGPT parsing error: {str(e)}", exc_info=True)
            return None

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Sanitizes text for Telegram HTML parsing"""
        if not text:
            return ""
        
        # Удаляем непечатные символы
        sanitized = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', str(text))
        
        # Заменяем проблемные символы
        return (
            sanitized
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", "&apos;")
        )
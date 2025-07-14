import logging
import json
import re
import time
import aiohttp
import asyncio
from typing import Dict, Optional
import html

logger = logging.getLogger('AsyncYandexGPT')

class AsyncYandexGPT:
    def __init__(self, config, session: aiohttp.ClientSession):
        self.config = config
        self.session = session
        self.active = bool(config.YANDEX_API_KEY) and config.ENABLE_YAGPT
        
        # Инициализация статистики
        self.stats = {
            'yagpt_used': 0,
            'yagpt_errors': 0,
            'token_usage': 0
        }
        
        # Остальной код без изменений
        self.error_count = 0
        self.last_error_time = None
        self.token_usage = 0
        self.rate_limit_remaining = float('inf')
        self.rate_limit_reset = 0
        
        self.headers = {
            "Authorization": f"Api-Key {config.YANDEX_API_KEY}",
            "x-folder-id": self.config.YANDEX_FOLDER_ID,
            "Content-Type": "application/json"
        }
        logger.info(f"YandexGPT initialized. Active: {self.active}")

    def is_available(self) -> bool:
        """Проверяет, доступен ли сервис в текущий момент"""
        return self.active and self.error_count < self.config.YAGPT_ERROR_THRESHOLD

    def _sanitize_prompt_input(self, text: str) -> str:
        """Экранирует специальные символы и предотвращает инъекции в промпт"""
        if not isinstance(text, str):
            return ""
        
        sanitized = html.escape(text)
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
        
        sanitized = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', sanitized)
        return sanitized[:5000]

    async def enhance(self, title: str, description: str) -> Optional[Dict]:
        """
        Улучшает заголовок и описание с помощью Yandex GPT
        Возвращает словарь с улучшенными title и description или None при ошибке
        """
        if not self.active or not self.is_available():
            return None

        try:
            # Подсчет токенов (простая оценка)
            tokens = len(title.split()) + len(description.split())
            
            # Проверка на превышение лимита токенов
            if tokens > self.config.YAGPT_MAX_TOKENS * 0.9:  # Оставляем 10% запаса
                logger.warning(f"Content too long: {tokens}/{self.config.YAGPT_MAX_TOKENS} tokens")
                return None

            # Формирование промпта
            prompt = self.config.YAGPT_PROMPT.format(
                title=title,
                description=description
            )

            # Подготовка данных для запроса
            request_data = {
                "modelUri": f"gpt://{self.config.YANDEX_FOLDER_ID}/{self.config.YAGPT_MODEL}",
                "completionOptions": {
                    "stream": False,
                    "temperature": self.config.YAGPT_TEMPERATURE,
                    "maxTokens": self.config.YAGPT_MAX_TOKENS
                },
                "messages": [
                    {
                        "role": "user",
                        "text": prompt
                    }
                ]
            }

            # Отправка запроса
            async with self.session.post(
                self.config.YANDEX_API_ENDPOINT,
                headers={
                    "Authorization": f"Api-Key {self.config.YANDEX_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=request_data,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                
                if response.status != 200:
                    error = await response.text()
                    logger.error(f"Yandex GPT API error: {response.status} - {error}")
                    return None

                data = await response.json()
                result_text = data.get('result', {}).get('alternatives', [{}])[0].get('message', {}).get('text', '')
                
                # Логирование сырого ответа для отладки
                logger.debug(f"Raw Yandex GPT response: {result_text[:200]}...")

                # Парсинг результата с помощью специализированного метода
                parsed_response = self.parse_response({
                    'result': {
                        'alternatives': [{
                            'message': {'text': result_text}
                        }]
                    }
                })
                
                if parsed_response:
                    return parsed_response
                
                # Fallback: обработка вручную если автоматический парсинг не сработал
                logger.warning("Falling back to manual response parsing")
                
                # Удаление служебных префиксов
                cleaned_text = re.sub(
                    r'^(Заголовок|Описание|Title|Description)[:\s]*', 
                    '', 
                    result_text, 
                    flags=re.IGNORECASE | re.MULTILINE
                )
                
                # Разделение на строки
                lines = [line.strip() for line in cleaned_text.split('\n') if line.strip()]
                
                # Обработка случая пустого ответа
                if not lines:
                    return None
                    
                # Извлечение заголовка (первая непустая строка)
                enhanced_title = lines[0]
                
                # Извлечение описания (все остальные строки)
                enhanced_description = '\n'.join(lines[1:]) if len(lines) > 1 else description

                # Обрезаем до максимальной длины
                enhanced_title = enhanced_title[:self.config.MAX_TITLE_LENGTH]
                enhanced_description = enhanced_description[:self.config.MAX_DESC_LENGTH]

                # Обновление статистики
                self.stats['yagpt_used'] += 1
                
                return {
                    'title': enhanced_title,
                    'description': enhanced_description
                }

        except asyncio.TimeoutError:
            logger.error("Yandex GPT request timeout")
            return None
        except Exception as e:
            logger.error(f"Yandex GPT enhancement error: {str(e)}", exc_info=True)
            return None

    def is_low_quality_response(self, text: str) -> bool:
        """Определяет низкокачественный ответ ИИ"""
        if not text:
            return True
            
        quality_indicators = [
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
            r"\[.*\]\(https?://[^\)]+\)"  # Markdown ссылки
        ]
        
        text_lower = text.lower()
        return any(re.search(phrase, text_lower) for phrase in quality_indicators)

    def parse_response(self, data: Dict) -> Optional[Dict]:
        try:
            if not data.get('result') or not data['result'].get('alternatives'):
                logger.warning("No alternatives in YandexGPT response")
                return None
                
            text = data['result']['alternatives'][0]['message']['text']
            
            # Попытка прямого JSON парсинга
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
            except (ValueError, json.JSONDecodeError, AttributeError):
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
                        for i in range(2, min(4, len(match.groups()) + 1)):
                            if match.group(i) and len(match.group(i).strip()) > 10:
                                desc_match = match.group(i).strip()
                                break
                        if desc_match:
                            break
            
            # Fallback стратегии
            if not title_match or not desc_match:
                parts = re.split(r'\n\n|\n-|\n•', text, maxsplit=1)
                if len(parts) >= 2:
                    title_match = parts[0].strip()
                    desc_match = parts[1].strip()
                else:
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
        sanitized = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', str(text))
        return (
            sanitized
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", "&apos;")
        )
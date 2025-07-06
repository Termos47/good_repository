import os
import logging
import logging.config
from datetime import datetime
import re
import shutil
import traceback
from typing import Dict, Any, List, Optional, Tuple, Union
import sys
from dotenv import load_dotenv
import validators
import colorama  # Добавлен импорт colorama

load_dotenv()

# Инициализация colorama для поддержки цветов в Windows
colorama.init()

class StructuredFormatter(logging.Formatter):
    """Умный форматтер логов с адаптивным выводом для разных режимов"""
    
    # Форматы вывода
    FORMATS = {
        'production': '{timestamp} {level} - {message}',
        'debug': '{timestamp} {level} {module}:{lineno} - {message}{extra}',
        'error': '{timestamp} {level} {module}:{lineno} - {message}{extra}'
    }
    
    # ANSI коды цветов
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[41m',  # Red background
        'RESET': '\033[0m'       # Reset color
    }
    
    def __init__(self, debug_mode: bool = False, use_colors: bool = True):
        """
        Инициализация форматтера
        
        :param debug_mode: Включить режим отладки (подробные логи)
        :param use_colors: Использовать цветной вывод
        """
        super().__init__()
        self.debug_mode = debug_mode
        self.use_colors = use_colors
        self._init_colors()

    def _init_colors(self):
        """Настройка цветовой поддержки"""
        if not self.use_colors:
            # Отключаем все цвета
            self.COLORS = {k: '' for k in self.COLORS}
        elif sys.platform == 'win32':
            # Для Windows гарантируем инициализацию colorama
            try:
                colorama.init()
            except:
                self.use_colors = False
                self.COLORS = {k: '' for k in self.COLORS}
        
    def _colorize_level(self, levelname: str) -> str:
        """Добавляет цвет к уровню логирования"""
        color = self.COLORS.get(levelname, self.COLORS['RESET'])
        return f"{color}{levelname}{self.COLORS['RESET']}"

    def _get_extra_fields(self, record: logging.LogRecord) -> Dict[str, Any]:
        """Извлекает дополнительные поля из записи лога"""
        return {
            key: value
            for key, value in record.__dict__.items()
            if key not in logging.LogRecord.__dict__ and 
               not key.startswith('_') and 
               key != 'message'
        }

    def _format_extras(self, record: logging.LogRecord) -> str:
        """Форматирует дополнительные поля в строку"""
        # В production режиме показываем extras только для ошибок
        if not self.debug_mode and record.levelno < logging.ERROR:
            return ""
        
        extras = self._get_extra_fields(record)
        if not extras:
            return ""
        
        # Форматируем пары ключ=значение
        return f" | {' '.join(f'{k}={v}' for k, v in extras.items())}"

    def format(self, record: logging.LogRecord) -> str:
        """Форматирует запись лога в строку"""
        try:
            # Форматируем timestamp с миллисекундами
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            
            # Определяем стиль форматирования
            if self.debug_mode:
                style = 'debug'
            elif record.levelno >= logging.ERROR:
                style = 'error'
            else:
                style = 'production'
            
            # Собираем компоненты для форматирования
            components = {
                'timestamp': timestamp,
                'level': self._colorize_level(record.levelname),
                'message': record.getMessage(),
                'module': record.module,
                'lineno': record.lineno,
                'extra': self._format_extras(record)
            }
            
            # Форматируем основную строку
            log_line = self.FORMATS[style].format(**components)
            
            # Добавляем информацию об исключении если есть
            if record.exc_info:
                exc_text = self.formatException(record.exc_info)
                log_line += f"\n{exc_text}"
                
            return log_line
            
        except Exception as e:
            # Защита от ошибок в самом форматтере
            return (f"⚠️ Log formatting error: {type(e).__name__}: {e}\n"
                    f"Original message: {record.getMessage()}")

class ContextLoggerAdapter(logging.LoggerAdapter):
    """Адаптер логгера для добавления контекстной информации"""
    
    def __init__(self, logger: logging.Logger, context: Optional[Dict[str, Any]] = None):
        """
        Инициализация адаптера
        
        :param logger: Базовый логгер
        :param context: Контекстная информация
        """
        super().__init__(logger, context or {})
        self.context = context or {}

    def process(self, msg: str, kwargs: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Обрабатывает сообщение перед логированием
        
        :param msg: Исходное сообщение
        :param kwargs: Аргументы логирования
        :return: Модифицированные сообщение и аргументы
        """
        # Объединяем контекст адаптера с контекстом вызова
        extra = kwargs.get('extra', {})
        combined_extra = {**self.context, **extra}
        
        # Убираем технические поля, которые могут конфликтовать
        for key in ['name', 'msg', 'args']:
            combined_extra.pop(key, None)
            
        kwargs['extra'] = combined_extra
        return msg, kwargs

    def add_context(self, **new_context: Any) -> None:
        """Добавляет новую контекстную информацию"""
        self.context.update(new_context)

    def remove_context(self, *keys: str) -> None:
        """Удаляет указанные ключи из контекста"""
        for key in keys:
            if key in self.context:
                del self.context[key]
                
    def set_context(self, new_context: Dict[str, Any]) -> None:
        """Полностью заменяет текущий контекст"""
        self.context = new_context or {}
        
def setup_logging(
    debug_mode: bool = False,
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    use_colors: bool = True
) -> None:
    """
    Настройка адаптивного логирования с разными режимами для DEBUG и PRODUCTION
    
    :param debug_mode: Включить DEBUG уровень и подробные логи
    :param log_file: Путь к файлу логов (None для вывода только в консоль)
    :param max_bytes: Максимальный размер файла лога
    :param backup_count: Количество бэкапов логов
    :param use_colors: Использовать цветной вывод
    """
    log_level = 'DEBUG' if debug_mode else 'INFO'
    
    # Создаем директорию для логов если нужно
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # Конфигурация handlers
    handlers: Dict[str, Any] = {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'colored',
            'level': log_level
        }
    }
    
    if log_file:
        handlers['file'] = {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': log_file,
            'maxBytes': max_bytes,
            'backupCount': backup_count,
            'formatter': 'detailed',
            'encoding': 'utf-8'
        }
    
    # Конфигурация логирования
    logging_config: Dict[str, Any] = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'colored': {
                '()': 'config.StructuredFormatter',
                'debug_mode': debug_mode,
                'use_colors': use_colors
            },
            'detailed': {
                'format': (
                    '%(asctime)s | %(levelname)-8s | %(name)s | '
                    '%(filename)s:%(lineno)d | %(message)s'
                )
            }
        },
        'handlers': handlers,
        'loggers': {
            '': {  # root logger
                'handlers': list(handlers.keys()),
                'level': log_level,
                'propagate': False
            },
            'aiogram': {
                'level': 'DEBUG' if debug_mode else 'WARNING',
                'handlers': list(handlers.keys())
            },
            'aiohttp': {
                'level': 'DEBUG' if debug_mode else 'WARNING',
                'handlers': list(handlers.keys())
            },
            'asyncio': {
                'level': 'DEBUG' if debug_mode else 'WARNING',
                'handlers': list(handlers.keys())
            },
            'PIL': {
                'level': 'WARNING',
                'handlers': list(handlers.keys())
            }
        }
    }
    
    # Применяем конфигурацию
    logging.config.dictConfig(logging_config)
    
    # Перехват необработанных исключений
    def handle_exception(exc_type, exc_value, exc_traceback):
        # Пропускаем KeyboardInterrupt чтобы не выводить трейсбэк при Ctrl+C
        if issubclass(exc_type, KeyboardInterrupt):
            sys.exit(0)
            
        logger = logging.getLogger('unhandled')
        logger.critical(
            "Unhandled exception", 
            exc_info=(exc_type, exc_value, exc_traceback)
        )
        sys.exit(1)
    
    sys.excepthook = handle_exception

def get_logger(name: str, context: Optional[Dict] = None) -> ContextLoggerAdapter:
    """Получение логгера с контекстом"""
    logger = logging.getLogger(name)
    return ContextLoggerAdapter(logger, context or {})

# Инициализация логгера для самого config.py
logger = get_logger('ConfigManager')

class Config:
    """Класс для управления конфигурацией приложения"""
    
    def __init__(self):
        # Инициализация логгера с контекстом
        self.logger = get_logger('Config', {'context_module': 'config'})
        
        # Настройка базовых параметров
        self.DEBUG_MODE: bool = self.get_env_var('DEBUG_MODE', default=False, var_type=bool)
        setup_logging(self.DEBUG_MODE)
        
        # Настройка логгера для PIL
        pil_logger = get_logger('PIL')
        pil_logger.setLevel(logging.WARNING if not self.DEBUG_MODE else logging.DEBUG)
        
        # Основные параметры бота
        self.TOKEN: str = self.get_env_var('TELEGRAM_TOKEN', required=True)
        self.CHANNEL_ID: str = self._normalize_channel_id(
            self.get_env_var('CHANNEL_ID', required=True)
        )
        self.OWNER_ID: int = self.get_env_var('OWNER_ID', required=True, var_type=int)
        
        # Параметры RSS
        rss_urls = self.get_env_var(
            'RSS_URLS', 
            default="https://www.interfax.ru/rss.asp", 
            var_type=list
        )
        self.RSS_URLS: List[str] = self.validate_rss_urls(rss_urls)
        self.CHECK_INTERVAL: int = self.get_env_var('CHECK_INTERVAL', default=300, var_type=int)
        self.MAX_ENTRIES_HISTORY: int = self.get_env_var('MAX_ENTRIES_HISTORY', default=1000, var_type=int)
        
        # Параметры YandexGPT
        self.YANDEX_API_KEY: Optional[str] = self.get_env_var('YANDEX_API_KEY')
        self.YANDEX_FOLDER_ID: str = self.get_env_var('YANDEX_FOLDER_ID', required=True)
        self.YANDEX_API_ENDPOINT: str = self.get_env_var('YANDEX_API_ENDPOINT', default='https://llm.api.cloud.yandex.net/foundationModels/v1/completion')
        self.DISABLE_YAGPT: bool = self.get_env_var('DISABLE_YAGPT', default=False, var_type=bool)
        self.YAGPT_MODEL: str = self.get_env_var('YAGPT_MODEL', default='yandexgpt-lite')
        self.YAGPT_TEMPERATURE: float = self.get_env_var('YAGPT_TEMPERATURE', default=0.4, var_type=float)
        self.YAGPT_MAX_TOKENS: int = self.get_env_var('YAGPT_MAX_TOKENS', default=2500, var_type=int)
        self.YAGPT_PROMPT: str = self.get_env_var('YAGPT_PROMPT', default="")
        self.YAGPT_ERROR_THRESHOLD: int = self.get_env_var('YAGPT_ERROR_THRESHOLD', default=5, var_type=int)
        self.AUTO_DISABLE_YAGPT: bool = self.get_env_var('AUTO_DISABLE_YAGPT', default=True, var_type=bool)
        
        # Параметры контента
        self.MIN_TITLE_LENGTH: int = self.get_env_var('MIN_TITLE_LENGTH', default=40, var_type=int)
        self.MAX_TITLE_LENGTH: int = self.get_env_var('MAX_TITLE_LENGTH', default=120, var_type=int)
        self.MIN_DESC_LENGTH: int = self.get_env_var('MIN_DESC_LENGTH', default=80, var_type=int)
        self.MAX_DESC_LENGTH: int = self.get_env_var('MAX_DESC_LENGTH', default=300, var_type=int)

        # Параметры изображений
        self.ENABLE_IMAGE_GENERATION = self.get_env_var('ENABLE_IMAGE_GENERATION', default=True, var_type=bool)
        self.FONTS_DIR: str = self.get_env_var('FONTS_DIR', default='fonts')
        self.TEMPLATES_DIR: str = self.get_env_var('TEMPLATES_DIR', default='templates')
        self.OUTPUT_DIR: str = self.get_env_var('OUTPUT_DIR', default='temp_images')
        self.DEFAULT_FONT: str = self.get_env_var('DEFAULT_FONT', default='Montserrat-Bold.ttf')
        self.MAX_IMAGE_WIDTH: int = self.get_env_var('MAX_IMAGE_WIDTH', default=1200, var_type=int)
        self.MAX_IMAGE_HEIGHT: int = self.get_env_var('MAX_IMAGE_HEIGHT', default=800, var_type=int)
        self.TEXT_COLOR: tuple = self.parse_rgb(self.get_env_var('TEXT_COLOR', default="255,255,255"))
        self.STROKE_COLOR: tuple = self.parse_rgb(self.get_env_var('STROKE_COLOR', default="0,0,0"))
        self.STROKE_WIDTH: int = self.get_env_var('STROKE_WIDTH', default=2, var_type=int)
        self.TEXT_POSITION: str = self.get_env_var('TEXT_POSITION', default="center")
        self.TEXT_ALIGN: str = self.get_env_var('TEXT_ALIGN', default="center")
        self.MAX_TEXT_LINES: int = self.get_env_var('MAX_TEXT_LINES', default=3, var_type=int)
        
        # Управление ботом
        self.ENABLE_BOT_CONTROL = self.get_env_var('ENABLE_BOT_CONTROL', default=True, var_type=bool)
        self.STATE_FILE: str = self.get_env_var('STATE_FILE', default='bot_state.json')
        self.PROXY_URL: Optional[str] = self.get_sanitized_proxy()
        self.RSS_REQUEST_DELAY: float = self.get_env_var('RSS_REQUEST_DELAY', default=5.0, var_type=float)
        self.MAX_POSTS_PER_CYCLE: int = self.get_env_var('MAX_POSTS_PER_CYCLE', default=5, var_type=int)
        self.IMAGE_GENERATION_WORKERS = self.get_env_var('IMAGE_GENERATION_WORKERS', default=2, var_type=int)
        self.POSTS_PER_HOUR: int = self.get_env_var('POSTS_PER_HOUR', default=30, var_type=int)
        self.MIN_DELAY_BETWEEN_POSTS: int = self.get_env_var('MIN_DELAY_BETWEEN_POSTS', default=60, var_type=int)
        
        # Источники изображений
        self.IMAGE_SOURCE = self.get_env_var('IMAGE_SOURCE', default='template').lower()
        self.IMAGE_FALLBACK = self.get_env_var('IMAGE_FALLBACK', default=True, var_type=bool)
        
        # Контроль дубликатов
        self.MAX_SIMILAR_TITLE_CHECK = self.get_env_var('MAX_SIMILAR_TITLE_CHECK', default=100, var_type=int)
        self.TITLE_SIMILARITY_THRESHOLD = self.get_env_var('TITLE_SIMILARITY_THRESHOLD', default=0.85, var_type=float)
        self.REQUIRE_IMAGE_UNIQUENESS = self.get_env_var('REQUIRE_IMAGE_UNIQUENESS', default=True, var_type=bool)
        
        # Оптимизации производительности
        self.IMAGE_DOWNLOAD_TIMEOUT = self.get_env_var('IMAGE_DOWNLOAD_TIMEOUT', default=15, var_type=int)
        self.MIN_IMAGE_WIDTH = self.get_env_var('MIN_IMAGE_WIDTH', default=300, var_type=int)
        self.MIN_IMAGE_HEIGHT = self.get_env_var('MIN_IMAGE_HEIGHT', default=200, var_type=int)
        self.MAX_CONCURRENT_IMAGE_TASKS = self.get_env_var(
            'MAX_CONCURRENT_IMAGE_TASKS', 
            default=4, 
            var_type=int
        )
        
        # Создание необходимых директорий
        self.create_directories()
        
        # Принудительные настройки для режима 'original'
        if self.IMAGE_SOURCE == 'original':
            self.IMAGE_FALLBACK = False
            self.ENABLE_IMAGE_GENERATION = False
            
        if self.DEBUG_MODE:
            self.logger.debug("DEBUG MODE ENABLED", extra={'config': self.to_dict()})
        
        self.logger.info("Configuration loaded successfully", extra={'rss_feeds_count': len(self.RSS_URLS)})
    
    def to_dict(self) -> Dict[str, Any]:
        """Возвращает конфигурацию в виде словаря (без секретов)"""
        return {
            'DEBUG_MODE': self.DEBUG_MODE,
            'CHANNEL_ID': self.CHANNEL_ID,
            'OWNER_ID': self.OWNER_ID,
            'RSS_URLS_count': len(self.RSS_URLS),
            'CHECK_INTERVAL': self.CHECK_INTERVAL,
            'MAX_ENTRIES_HISTORY': self.MAX_ENTRIES_HISTORY,
            'YANDEX_API_ENDPOINT': self.YANDEX_API_ENDPOINT,
            'DISABLE_YAGPT': self.DISABLE_YAGPT,
            'YAGPT_MODEL': self.YAGPT_MODEL,
            'YAGPT_TEMPERATURE': self.YAGPT_TEMPERATURE,
            'YAGPT_MAX_TOKENS': self.YAGPT_MAX_TOKENS,
            'ENABLE_IMAGE_GENERATION': self.ENABLE_IMAGE_GENERATION,
            'MAX_IMAGE_WIDTH': self.MAX_IMAGE_WIDTH,
            'MAX_IMAGE_HEIGHT': self.MAX_IMAGE_HEIGHT,
            'ENABLE_BOT_CONTROL': self.ENABLE_BOT_CONTROL,
            'PROXY_URL': bool(self.PROXY_URL),
            'RSS_REQUEST_DELAY': self.RSS_REQUEST_DELAY,
            'MAX_POSTS_PER_CYCLE': self.MAX_POSTS_PER_CYCLE,
            'POSTS_PER_HOUR': self.POSTS_PER_HOUR,
            'MIN_DELAY_BETWEEN_POSTS': self.MIN_DELAY_BETWEEN_POSTS,
            'IMAGE_SOURCE': self.IMAGE_SOURCE,
            'IMAGE_FALLBACK': self.IMAGE_FALLBACK,
            'MAX_SIMILAR_TITLE_CHECK': self.MAX_SIMILAR_TITLE_CHECK,
            'TITLE_SIMILARITY_THRESHOLD': self.TITLE_SIMILARITY_THRESHOLD,
            'IMAGE_DOWNLOAD_TIMEOUT': self.IMAGE_DOWNLOAD_TIMEOUT,
            'MIN_IMAGE_WIDTH': self.MIN_IMAGE_WIDTH,
            'MIN_IMAGE_HEIGHT': self.MIN_IMAGE_HEIGHT,
            'MAX_CONCURRENT_IMAGE_TASKS': self.MAX_CONCURRENT_IMAGE_TASKS
        }

    def _normalize_channel_id(self, channel_id: str) -> str:
        """Нормализует ID канала для Telegram API"""
        clean_id = str(channel_id).strip().replace(' ', '').replace('@', '')
        
        if clean_id.startswith('@'):
            return clean_id
        
        try:
            channel_id_int = int(clean_id)
            if channel_id_int < 0 and not clean_id.startswith('-100'):
                return f"-100{abs(channel_id_int)}"
            return clean_id
        except ValueError:
            return f"@{clean_id}"

    def save_to_env_file(self, param: str, value: str) -> None:
        """Сохраняет параметр в .env файл с созданием резервной копии"""
        env_file = '.env'
        if not os.path.exists(env_file):
            self.logger.warning(".env file not found, skipping save")
            return
        
        try:
            # Создаем резервную копию
            backup_file = '.env.bak'
            shutil.copyfile(env_file, backup_file)
            
            with open(env_file, 'r') as f:
                lines = f.readlines()
            
            found = False
            new_lines = []
            for line in lines:
                if line.startswith(f'{param}='):
                    new_lines.append(f'{param}={value}\n')
                    found = True
                else:
                    new_lines.append(line)
            
            if not found:
                new_lines.append(f'{param}={value}\n')
            
            with open(env_file, 'w') as f:
                f.writelines(new_lines)
                
            self.logger.info(f"Updated .env parameter: {param}={value}")
        except Exception as e:
            self.logger.error(f"Failed to update .env file: {str(e)}", exc_info=True)

    @staticmethod
    def parse_rgb(rgb_str: str) -> tuple:
        """Парсит строку RGB в кортеж"""
        try:
            return tuple(map(int, rgb_str.split(',')))
        except Exception:
            return (255, 255, 255)

    def validate_rss_urls(self, urls: List[str]) -> List[str]:
        """Валидирует список URL RSS-лент"""
        valid_urls = []
        for url in urls:
            if validators.url(url):
                valid_urls.append(url)
            else:
                self.logger.error(f"Invalid RSS URL: {url}")
        return valid_urls

    def get_sanitized_proxy(self) -> Optional[str]:
        """Очищает и проверяет URL прокси"""
        proxy = self.get_env_var('PROXY_URL')
        if not proxy:
            return None
            
        proxy = re.sub(r'\s*#.*$', '', proxy).strip()
        
        if proxy and not re.match(r'^https?://', proxy):
            self.logger.warning(f"Proxy URL missing protocol: {proxy}")
            return None
            
        return proxy

    def create_directories(self):
        """Создает необходимые директории"""
        for directory in [
            self.FONTS_DIR,
            self.TEMPLATES_DIR,
            self.OUTPUT_DIR
        ]:
            try:
                os.makedirs(directory, exist_ok=True)
                self.logger.info(f"Directory ensured: {directory}")
            except Exception as e:
                self.logger.error(f"Error creating directory {directory}: {str(e)}")

    @classmethod
    def get_env_var(
        cls,
        name: str, 
        default: Any = None, 
        required: bool = False, 
        var_type: type = str
    ) -> Any:
        """Получает переменную окружения с преобразованием типа"""
        value = os.getenv(name, default)
        
        if required and value is None:
            logger.critical(f"Environment variable {name} is required but not set")
            sys.exit(1)
        
        if isinstance(value, str):
            value = value.strip().strip('"').strip("'")
        
        if value is None or value == "":
            return default
            
        try:
            if var_type is int:
                return int(value)
            elif var_type is float:
                return float(value)
            elif var_type is list:
                if isinstance(value, str):
                    value = value.strip('[]')
                    return [url.strip().strip('"\'') for url in value.split(',') if url.strip()]
                return value
            elif var_type is bool:
                if isinstance(value, str):
                    return value.lower() in ['true', '1', 'yes', 'y', 't', 'on']
                return bool(value)
            return value
        except (TypeError, ValueError) as e:
            logger.error(f"Error converting {name} to {var_type}: {str(e)}")
            return default

# Глобальный экземпляр конфигурации
app_config: Optional[Config] = None

def get_config() -> Config:
    """Возвращает глобальный экземпляр конфигурации"""
    global app_config
    if app_config is None:
        app_config = Config()
    return app_config

# Инициализация при импорте
if __name__ != "__main__":
    get_config()
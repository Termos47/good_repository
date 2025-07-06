import json
import os
import logging
from datetime import datetime
import hashlib
from typing import Dict, Any, Set, Tuple
import shutil
import re
from collections import OrderedDict

logger = logging.getLogger('StateManager')

class StateManager:
    """
    Менеджер состояния бота с защитой от повреждения данных и автоматическим восстановлением
    
    Особенности:
    - Безопасное сохранение через временные файлы
    - Автоматическое восстановление при повреждении файла состояния
    - Ограничение размера истории
    - Проверка целостности данных
    - Резервное копирование при ошибках
    - Поддержка миграции старых форматов состояния
    """
    
    def __init__(self, state_file: str = 'bot_state.json', max_entries: int = 1000):
        self.state_file = state_file
        self.max_entries = max_entries
        self.backup_dir = "state_backups"
        self.logger = logging.getLogger('StateManager')
        
        # Инициализация состояния по умолчанию
        self.state: Dict[str, Any] = {
            'sent_entries': OrderedDict(),
            'sent_hashes': OrderedDict(),
            'stats': {},
            'metadata': {
                'version': 1.2,
                'created_at': datetime.now().isoformat(),
                'last_modified': None
            }
        }
        
        self._ensure_backup_dir()
        self.load_state()

    def _ensure_backup_dir(self) -> None:
        """Создает директорию для резервных копий при необходимости"""
        os.makedirs(self.backup_dir, exist_ok=True)

    def _create_backup(self) -> str:
        """Создает резервную копию текущего состояния"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(self.backup_dir, f"state_backup_{timestamp}.json")
        if os.path.exists(self.state_file):
            shutil.copy2(self.state_file, backup_file)
            self.logger.info(f"Created state backup: {backup_file}")
        return backup_file

    def _validate_state(self, state: Dict[str, Any]) -> bool:
        """Проверяет целостность структуры состояния"""
        try:
            # Проверка наличия обязательных ключей
            required_keys = ['sent_entries', 'sent_hashes', 'stats', 'metadata']
            for key in required_keys:
                if key not in state:
                    self.logger.warning(f"Missing required key in state: {key}")
                    return False
                    
            # Проверка типов
            if not isinstance(state['sent_entries'], dict):
                self.logger.warning("sent_entries should be a dictionary")
                return False
                
            if not isinstance(state['sent_hashes'], dict):
                self.logger.warning("sent_hashes should be a dictionary")
                return False
                
            if not isinstance(state['stats'], dict):
                self.logger.warning("stats should be a dictionary")
                return False
                
            # Проверка формата записей
            for key, value in state['sent_entries'].items():
                if not re.match(r'^[a-f0-9]{32,64}$', key):
                    self.logger.warning(f"Invalid entry key format: {key}")
                    return False
                if not isinstance(value, str):
                    self.logger.warning(f"Invalid entry value type: {type(value)}")
                    return False
                    
            # Проверка формата хешей
            for key, value in state['sent_hashes'].items():
                if not re.match(r'^[a-f0-9]{64}$', key):
                    self.logger.warning(f"Invalid hash key format: {key}")
                    return False
                if not isinstance(value, str):
                    self.logger.warning(f"Invalid hash value type: {type(value)}")
                    return False
                    
            return True
        except Exception as e:
            self.logger.error(f"Validation error: {str(e)}")
            return False

    def load_state(self) -> None:
        """Безопасная загрузка состояния из файла с восстановлением при ошибках"""
        if not os.path.exists(self.state_file):
            self.logger.info("No state file found, starting with fresh state")
            return
            
        try:
            # Чтение с проверкой содержимого
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = f.read()
                
            # Проверка на пустой файл
            if not data.strip():
                raise ValueError("State file is empty")
                
            # Парсинг JSON
            loaded_state = json.loads(data)
            
            # Обработка старого формата состояния (v1.0)
            if 'sent_entries' in loaded_state and isinstance(loaded_state['sent_entries'], list):
                self.logger.warning("Legacy state format detected, converting to new format...")
                loaded_state = self._convert_legacy_state(loaded_state)
            
            # Для совместимости с предыдущими версиями
            if 'metadata' not in loaded_state:
                loaded_state['metadata'] = {
                    'version': 1.0,
                    'created_at': datetime.now().isoformat(),
                    'last_modified': None
                }
                
            # Добавляем отсутствующие разделы
            if 'sent_hashes' not in loaded_state:
                loaded_state['sent_hashes'] = OrderedDict()
                
            if 'stats' not in loaded_state:
                loaded_state['stats'] = {}
                
            # Обновление последнего изменения
            loaded_state['metadata']['last_modified'] = datetime.now().isoformat()
            
            # Преобразование в OrderedDict для сохранения порядка
            if isinstance(loaded_state['sent_entries'], dict):
                loaded_state['sent_entries'] = OrderedDict(
                    sorted(loaded_state['sent_entries'].items(), key=lambda x: x[1]))
                
            if isinstance(loaded_state['sent_hashes'], dict):
                loaded_state['sent_hashes'] = OrderedDict(
                    sorted(loaded_state['sent_hashes'].items(), key=lambda x: x[1]))
            
            # Проверка целостности
            if not self._validate_state(loaded_state):
                raise ValueError("Invalid state structure after conversion")
                
            self.state = loaded_state
            self.logger.info(f"State loaded successfully from {self.state_file}")
            self.logger.debug(f"State contains {len(self.state['sent_entries'])} entries and {len(self.state['sent_hashes'])} hashes")
            
        except Exception as e:
            # Создаем резервную копию поврежденного файла
            backup_file = self._create_backup()
            self.logger.error(f"Failed to load state: {str(e)}. Backup created: {backup_file}")
            
            # Создаем чистое состояние
            self.state = {
                'sent_entries': OrderedDict(),
                'sent_hashes': OrderedDict(),
                'stats': {},
                'metadata': {
                    'version': 1.2,
                    'created_at': datetime.now().isoformat(),
                    'last_modified': None,
                    'recovery_reason': f"Original state corrupted: {str(e)}"
                }
            }
            self.logger.info("Starting with fresh state after recovery")

    def _convert_legacy_state(self, legacy_state: Dict[str, Any]) -> Dict[str, Any]:
        """Конвертирует старый формат состояния в новый"""
        new_state = {
            'sent_entries': OrderedDict(),
            'sent_hashes': OrderedDict(),
            'stats': legacy_state.get('stats', {}),
            'metadata': {
                'version': 1.2,
                'created_at': datetime.now().isoformat(),
                'last_modified': None,
                'converted_from_legacy': True
            }
        }
        
        # Перенос записей из старого формата
        for entry in legacy_state.get('sent_entries', []):
            if isinstance(entry, dict) and 'post_id' in entry:
                post_id = entry['post_id']
                pub_date = entry.get('pub_date', datetime.now().isoformat())
                new_state['sent_entries'][post_id] = pub_date
        
        # Перенос хешей, если есть
        if 'entry_hashes' in legacy_state and isinstance(legacy_state['entry_hashes'], list):
            for hash_val in legacy_state['entry_hashes']:
                if re.match(r'^[a-f0-9]{64}$', hash_val):
                    new_state['sent_hashes'][hash_val] = datetime.now().isoformat()
        
        # Перенос дополнительных полей
        for key in ['yagpt_cache', 'yagpt_error_count', 'yagpt_active', 'user_settings']:
            if key in legacy_state:
                new_state[key] = legacy_state[key]
        
        return new_state

    def save_state(self) -> None:
        """Безопасное сохранение состояния через временный файл"""
        try:
            # Исправление: создаем директорию только если путь указан
            if os.path.dirname(self.state_file):
                os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            
            # Обновляем метаданные
            self.state['metadata']['last_modified'] = datetime.now().isoformat()
            self.state['metadata']['entries_count'] = len(self.state['sent_entries'])
            self.state['metadata']['hashes_count'] = len(self.state['sent_hashes'])
            
            # Создаем временный файл
            temp_file = f"{self.state_file}.tmp"
            
            # Сохраняем во временный файл
            with open(temp_file, 'w', encoding='utf-8') as f: 
                json.dump(self.state, f, indent=2, ensure_ascii=False)
                
            # Проверяем целостность временного файла
            with open(temp_file, 'r') as f:
                data = f.read()
                json.loads(data)  # Проверка на валидность JSON
                
            # Заменяем основной файл
            if os.path.exists(self.state_file):
                os.replace(temp_file, self.state_file)
            else:
                os.rename(temp_file, self.state_file)
                
            self.logger.info(f"State saved to {self.state_file}")
            
        except Exception as e:
            self.logger.error(f"Error saving state: {str(e)}", exc_info=True)
            # Создаем резервную копию при ошибке сохранения
            self._create_backup()

    def is_entry_sent(self, entry_id: str) -> bool:
        return entry_id in self.state.get('sent_entries', {})

    def normalize_url(self, url: str) -> str:
        """Нормализация URL для сравнения"""
        return url.lower().strip().replace("https://", "").replace("http://", "").rstrip('/')

    def find_similar_titles(self, title: str, threshold: float, max_check: int) -> bool:
        """Заглушка для проверки схожести заголовков"""
        # В реальной реализации здесь должна быть логика сравнения заголовков
        return False

    def add_sent_entry(self, post: Dict) -> None:
        """Добавление отправленного поста в историю"""
        post_id = post.get('post_id', '')
        if post_id:
            sent_entries = self.state.setdefault('sent_entries', OrderedDict())
            sent_entries[post_id] = datetime.now().isoformat()
            
            # Обновляем хеш для контента
            content_hash = self._generate_content_hash(post)
            if content_hash:
                self.state.setdefault('sent_hashes', OrderedDict())[content_hash] = datetime.now().isoformat()

    def _generate_content_hash(self, post: Dict) -> str:
        """Генерирует хеш для контента поста"""
        content = f"{post.get('title', '')}{post.get('description', '')}"
        return hashlib.sha256(content.encode()).hexdigest()

    def is_hash_sent(self, hash_value: str) -> bool:
        """Проверяет, был ли хеш контента уже обработан"""
        return hash_value in self.state.get('sent_hashes', {})

    def add_entry(self, entry_id: str, hash_value: str) -> None:
        """
        Добавляет запись в историю с проверкой данных
        
        :param entry_id: Уникальный ID записи (обычно URL)
        :param hash_value: SHA-256 хеш контента
        """
        # Проверка входных данных
        if not isinstance(entry_id, str) or not entry_id:
            self.logger.error(f"Invalid entry_id: {entry_id}")
            return
            
        if not isinstance(hash_value, str) or len(hash_value) != 64 or not re.match(r'^[a-f0-9]{64}$', hash_value):
            self.logger.error(f"Invalid hash format: {hash_value}")
            return
            
        # Добавляем с временной меткой
        timestamp = datetime.now().isoformat()
        self.state['sent_entries'][entry_id] = timestamp
        self.state['sent_hashes'][hash_value] = timestamp
        
        # Сохраняем состояние
        self.save_state()
        
        self.logger.debug(f"Added entry: {entry_id[:30]}... with hash: {hash_value[:10]}...")

    def cleanup_old_entries(self) -> None:
        """Очищает старые записи, сохраняя только последние max_entries"""
        current_count = len(self.state['sent_entries'])
        if current_count <= self.max_entries:
            return
            
        # Определяем сколько нужно удалить
        to_remove = current_count - self.max_entries
        
        # Удаляем старые записи из sent_entries
        entries_to_remove = list(self.state['sent_entries'].keys())[:to_remove]
        for key in entries_to_remove:
            del self.state['sent_entries'][key]
            
        # Собираем хеши для удаления
        hashes_to_remove = []
        for hash_val, timestamp in self.state['sent_hashes'].items():
            if timestamp < self.state['sent_entries'].peekitem(0)[1]:
                hashes_to_remove.append(hash_val)
                
        # Удаляем старые хеши
        for hash_val in hashes_to_remove[:to_remove]:
            del self.state['sent_hashes'][hash_val]
            
        self.logger.info(f"Cleaned up state: removed {len(entries_to_remove)} entries and {len(hashes_to_remove)} hashes")
        self.save_state()

    def update_stats(self, stats: Dict[str, Any]) -> None:
        """Обновляет статистику в состоянии"""
        if 'stats' not in self.state:
            self.state['stats'] = {}
        self.state['stats'].update(stats)

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику состояния"""
        return {
            'entries_count': len(self.state.get('sent_entries', {})),
            'hashes_count': len(self.state.get('sent_hashes', {})),
            'oldest_entry': next(iter(self.state['sent_entries'].values()), None) if self.state.get('sent_entries') else None,
            'newest_entry': next(reversed(self.state['sent_entries'].values()), None) if self.state.get('sent_entries') else None,
            'version': self.state.get('metadata', {}).get('version', 'unknown'),
            'last_modified': self.state.get('metadata', {}).get('last_modified', 'never')
        }
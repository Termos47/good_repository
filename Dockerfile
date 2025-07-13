# Базовый образ
FROM python:3.11-slim

# Установка системных зависимостей для работы с графикой
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Копирование только необходимых файлов
COPY requirements.txt .
COPY *.py ./
COPY *.json ./
COPY templates/ ./templates/
COPY fonts/ ./fonts/

# Установка зависимостей
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

# Создание необходимых директорий
RUN mkdir -p data logs state_backups temp_images

# Команда запуска
CMD ["python", "main.py"]

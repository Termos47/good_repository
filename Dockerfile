FROM python:3.13-slim

# Настройка окружения
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Установка системных зависимостей (с подавлением предупреждений)
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        apt-utils \
        libfreetype6-dev \
        libjpeg-dev \
        libopenjp2-7-dev \
        zlib1g-dev > /dev/null 2>&1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Создание и активация виртуального окружения
RUN python -m venv /opt/venv && \
    /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip==25.1.1 && \
    find /opt/venv -type d -name '__pycache__' -exec rm -rf {} +

ENV PATH="/opt/venv/bin:$PATH"

# Установка зависимостей Python
COPY requirements.txt .
RUN pip install --no-cache-dir --require-virtualenv -r requirements.txt && \
    pip check

# Копирование кода
COPY . .

# Создание рабочих директорий
RUN mkdir -p logs temp_images templates fonts

CMD ["python", "main.py"]
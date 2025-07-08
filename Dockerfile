FROM python:3.11-slim

# Настройка окружения для apt без предупреждений
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Установка системных зависимостей
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        apt-utils \
        libfreetype6-dev \
        libjpeg-dev \
        libopenjp2-7-dev \
        zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Создание изолированного окружения
RUN python -m venv /opt/venv && \
    /opt/venv/bin/python -m pip install --upgrade pip

# Активация окружения
ENV PATH="/opt/venv/bin:$PATH"

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Создание рабочих директорий
RUN mkdir -p logs temp_images templates fonts

CMD ["python", "main.py"]
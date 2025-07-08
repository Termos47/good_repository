FROM python:3.11-slim

# 1. Установка apt-utils перед основными пакетами
RUN apt-get update && \
    apt-get install -y --no-install-recommends apt-utils && \
    apt-get install -y --no-install-recommends \
        libfreetype6-dev \
        libjpeg-dev \
        libopenjp2-7-dev \
        zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

# 2. Настройка временной переменной для debconf
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# 3. Создание виртуального окружения
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 4. Обновление pip и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs temp_images templates fonts

CMD ["python", "main.py"]
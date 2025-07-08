FROM python:3.11-slim

# Настройка окружения для apt
ENV DEBIAN_FRONTEND=noninteractive

# Установка базовых зависимостей
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        apt-utils \
        libfreetype6-dev \
        libjpeg-dev \
        libopenjp2-7-dev \
        zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Создание виртуального окружения
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Установка зависимостей Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
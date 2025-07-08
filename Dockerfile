FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей для Pillow
RUN apt-get update && apt-get install -y \
    libfreetype6-dev \
    libjpeg-dev \
    libopenjp2-7-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Копирование зависимостей и установка
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Создание необходимых директорий
RUN mkdir -p logs temp_images templates fonts

CMD ["python", "main.py"]
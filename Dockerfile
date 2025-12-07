FROM python:3.12-slim

# Установка системных зависимостей для ffmpeg и moviepy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Установка рабочей директории
WORKDIR /app

# Копирование и установка Python-зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Создание временной директории для бота
RUN mkdir -p /app/temp

# Запуск бота
CMD ["python", "bot.py"]

FROM python:3.10-slim

# Устанавливаем системные библиотеки, необходимые для работы PaddleOCR и OpenCV
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем и устанавливаем Python-библиотеки
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь остальной код
COPY . .

# Открываем порт для FastAPI
EXPOSE 8000

# Запускаем сервер
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

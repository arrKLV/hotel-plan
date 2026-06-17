FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# сначала зависимости — кэш слоёв при неизменном requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# затем код
COPY . .

EXPOSE 8000

# PORT задаёт платформа (Railway/Render/Fly); локально дефолт 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

"""Конфигурация приложения. Грузит переменные окружения из .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# override=True: значения из .env имеют приоритет над пустыми/устаревшими переменными окружения
load_dotenv(BASE_DIR / ".env", override=True)

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-5")

# Instagram / Meta
IG_VERIFY_TOKEN = os.getenv("IG_VERIFY_TOKEN", "kazzhol_verify_123")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
IG_APP_SECRET = os.getenv("IG_APP_SECRET", "")
IG_ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID", "")
GRAPH_API_VERSION = "v21.0"

# Рабочие часы (Asia/Almaty)
WORK_HOURS_START = int(os.getenv("WORK_HOURS_START", "9"))
WORK_HOURS_END = int(os.getenv("WORK_HOURS_END", "20"))

# База данных
# Прод: Postgres (напр. Neon) — задать DATABASE_URL=postgresql://user:pass@host/db
# Локально: если DATABASE_URL пуст — используется SQLite-файл DB_PATH.
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Пути
DATA_DIR = BASE_DIR / "data"
KB_PATH = DATA_DIR / "knowledge_base.json"
DB_PATH = BASE_DIR / "kazzhol.db"

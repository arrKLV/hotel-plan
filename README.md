# KAZZHOL Instagram Agent (MVP)

![CI](https://github.com/arrKLV/hotel-plan/actions/workflows/ci.yml/badge.svg)

AI-администратор Instagram Direct для сети отелей KAZZHOL. Отвечает гостям 24/7 на RU/KZ/EN,
квалифицирует заявку (даты, гости, цель), складывает лид в дашборд и эскалирует горячее менеджеру.

## Что внутри
- `app/agent.py` — ядро на Claude (Sonnet + prompt caching, мультиязык, структурный вывод заявки)
- `app/instagram.py` — официальный Instagram Messaging API: вебхук, Send API, профиль гостя, индикаторы (прочитано/печатает)
- `app/storage.py` — SQLite: диалоги, сообщения, лиды, контроль 24ч-окна
- `app/main.py` — FastAPI: вебхук `/webhook` + инбокс менеджера `/` (двусторонняя переписка)
- `scripts/cli_chat.py` — тест агента в терминале без Instagram
- `data/knowledge_base.json` — база знаний (реальные данные с kazzhol.com)
- `Procfile` — деплой одной командой (Railway/Render/Fly/Heroku-совместимо)

## Как это работает (поток)
1. Гость пишет в Instagram Direct → Meta шлёт `POST /webhook`.
2. Подпись проверяется (HMAC-SHA256), имя гостя резолвится через Graph API.
3. Агент (Claude) отвечает на языке гостя, собирает заявку (отель, даты, гости, цель), ставит теплоту лида и при горячем/жалобе помечает эскалацию.
4. Ответ уходит гостю через Send API; всё пишется в SQLite.
5. Менеджер видит инбокс на `/`: диалоги, лиды, статус 24ч-окна, эскалации.
6. В любом диалоге менеджер может **ответить гостю сам** — бот замолкает (режим `human`), либо вернуть диалог боту.

## Быстрый старт (локально, без Instagram)
```bash
cd kazzhol-instagram-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # вставь ANTHROPIC_API_KEY
python -m scripts.cli_chat    # поговори с агентом в терминале
```

## Запуск веб-приложения + инбокс
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
Маршруты после старта:
- `/` — инбокс менеджера (диалоги, лиды, эскалации)
- `/demo` — интерактивная витрина агента
- `/healthz` — health-check для балансировщика

## Тесты
```bash
pip install -r requirements-dev.txt
pytest -q
```
CI (`.github/workflows/ci.yml`) гоняет import-smoke + pytest на каждый push/PR.

## Docker
```bash
docker build -t kazzhol-agent .
docker run -p 8000:8000 --env-file .env kazzhol-agent
```

## Деплой (прод)
Любой PaaS, читающий `Procfile` или `Dockerfile` (Railway / Render / Fly / Heroku):
1. Подключить git-репозиторий.
2. Задать env-переменные в панели: `ANTHROPIC_API_KEY`, `IG_VERIFY_TOKEN`,
   `IG_ACCESS_TOKEN`, `IG_APP_SECRET`, `IG_ACCOUNT_ID`.
3. Деплой — платформа сама поднимет процесс из `Procfile`.

`.env` и `*.db` в git не попадают (см. `.gitignore`) — секреты только через env платформы.

## Подключение реального Instagram (чек-лист Meta)
Канал бесплатный, но нужен официальный доступ. Шаги (делаются в Meta, не в коде):

1. Перевести Instagram отеля в **Professional (Business)** аккаунт.
2. На developers.facebook.com создать **Meta App** (тип Business).
3. Добавить продукт **Instagram** → получить **access token** аккаунта (OAuth).
   Вписать в `.env`: `IG_ACCESS_TOKEN`, `IG_APP_SECRET`, `IG_ACCOUNT_ID`.
4. Развернуть приложение на публичном HTTPS-домене (любой PaaS из раздела «Деплой»).
5. В настройках Webhooks указать `https://<домен-приложения>/webhook`,
   `Verify Token` = значение `IG_VERIFY_TOKEN`, подписаться на поле **messages**.
6. Пока приложение в Dev-режиме — переписка работает с аккаунтами-тестерами
   (свой аккаунт). Для боевого обслуживания чужого аккаунта (отеля) — пройти
   **App Review** на `instagram_business_manage_messages` + **Business Verification**.

### Важные ограничения политики Meta
- **Окно 24 часа**: свободно отвечать можно в течение 24ч после сообщения гостя.
- Не подтверждаем бронь автоматически — только готовим лид (менеджер подтверждает).
- Нельзя использовать неофициальный/приватный API или автоматизацию браузера (бан).

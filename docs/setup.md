# Setup — локальная разработка и деплой

## Требования

- Python 3.12
- [uv](https://docs.astral.sh/uv/) (пакетный менеджер)
- Docker + Docker Compose (для локального стека)
- Доступ к панели Remnawave (для `make smoke` и прод)

## Локальная разработка

```bash
cp .env.example .env
# заполни как минимум: APP__CRYPT_KEY, APP__JWT_SECRET, DATABASE__PASSWORD,
# REMNAWAVE__BASE_URL, REMNAWAVE__TOKEN, REMNAWAVE__WEBHOOK_SECRET
make install       # uv sync --extra dev
```

Сгенерировать Fernet-ключ:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Поднять весь стек (postgres, redis, web, worker, scheduler):

```bash
make up            # web слушает :8080, /health зелёный после миграций
make logs
make down          # -v: снести volume'ы
```

Или без Docker (нужен свой Postgres/Redis, адреса — в `.env`):

```bash
make migrate
make check         # lint + mypy + pytest
```

## Проверки

```bash
make check         # обязательный гейт: ruff + mypy + pytest
make smoke         # e2e против реальной панели: connect → probe версии →
                   # provision тестовой подписки → sync → revoke → cleanup
```

## Миграции

```bash
make revision m="add subscriptions"   # автоген (проверь диф глазами!)
make migrate                           # alembic upgrade head
```

`env.py` берёт URL БД из настроек (`src/core/config`), не из `alembic.ini`.
`transaction_per_migration = true` — каждая миграция в своей транзакции (безопаснее с enum/type).

## Прод (набросок; уточняется при написании бота)

- Reverse-proxy (nginx/Caddy) терминирует TLS и форвардит пути вебхуков; приложение биндится на 127.0.0.1.
- Контейнеры: `web` (FastAPI: вебхуки платежей/панели + health; владеет миграциями),
  `worker` (taskiq), `scheduler` (taskiq). Плюс postgres, redis/valkey.
- Секреты — из секрет-стора, не из образа. `APP__ENV=production`, `LOG__JSON=true`.
- Telegram webhook secret-token сверяется constant-time; апдейты — под семафором (~100).
- Бэкапы (pyzipper, зашифрованы) шлются в админ-чат по расписанию.

## Переменные окружения

Все переменные и safety-rails — в `.env.example` (с комментариями). Валидация — при старте:
плейсхолдеры (`change_me`), короткий Fernet-ключ, совпадение CRYPT/JWT/API — отвергаются.

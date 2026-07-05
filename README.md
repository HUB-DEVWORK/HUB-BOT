# VPN-shop base

Ядро (foundation) для Telegram-бота + web-миниаппы, продающих VPN-подписки (VLESS/XTLS)
и провижнящих их на панели **Remnawave**. Синтез архитектуры
[remnashop](https://github.com/snoups/remnashop) и широты
[bedolaga](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot).

> **Статус: только ядро.** Здесь нет хендлеров бота и UI миниаппы — они строятся поверх этой базы.
> База даёт: конфиг, модели БД, DAO, клиент Remnawave, абстракцию платежей, бизнес-сервисы, DI,
> фоновые задачи, i18n, тесты и документацию.

## Быстрый старт (локально)

```bash
cp .env.example .env      # заполни секреты (см. комментарии в файле)
make install              # uv sync --extra dev
make up                   # postgres + redis + web + worker + scheduler (docker)
# в другом терминале:
make smoke                # e2e-проверка клиента панели
```

Без Docker (только код):

```bash
make install
make migrate              # нужен запущенный Postgres из .env
make check                # lint + типы + тесты
```

## Что внутри

```
src/core/            конфиг, enums, money, i18n, exceptions, logging
src/application/     common/ (протоколы) · services/ · events/ · dto/
src/infrastructure/  database/ · remnawave/ · payments/ · taskiq/ · redis/ · di/ · services/
src/web/             тонкий FastAPI: вебхуки платежей/панели + health
docs/context/        ★ большой контекст-массив (из конкурентов) — читать первым
```

## Документация

| Файл | О чём |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Правила работы, инварианты, команды (для AI и людей) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Кольца, потоки данных, ключевые сущности |
| [docs/context/](docs/context/) | Домен Remnawave, lifecycle, платежи, рефералка, конкуренты, грабли |
| [docs/setup.md](docs/setup.md) | Локальная разработка и прод-деплой |
| [docs/adr/](docs/adr/) | Ключевые архитектурные решения |

## Технологии

Python 3.12 · aiogram 3 · SQLAlchemy 2.0 (async) · Alembic · PostgreSQL · Redis · taskiq ·
Dishka · FastAPI · httpx · pydantic-settings. Панель: Remnawave ≥ 2.8.0.

## Лицензия

MIT.

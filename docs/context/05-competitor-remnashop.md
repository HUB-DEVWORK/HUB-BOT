# 05 — Конкурент: remnashop (snoups)

Репо: https://github.com/snoups/remnashop · docs: remnashop.mintlify.app

**TL;DR:** эталон чистой архитектуры. Берём отсюда **структуру**: слоистость, протоколы,
UnitOfWork + DAO, единый платёжный ABC/роут, DI на Dishka, отложенная локализация событий.

## Стек

- Python 3.12 (`>=3.12,<3.13`).
- aiogram ~3.25 + **aiogram-dialog** ~2.5 (окна/виджеты/состояния).
- SQLAlchemy 2.0 async + Alembic ~1.16 (**40 миграций**, asyncpg).
- PostgreSQL + Redis 7 (FSM, брокер taskiq, кэш).
- FastAPI + uvicorn (web-слой), **Dishka** IoC, **taskiq** worker.
- **fluentogram** ~1.2 — Fluent (`.ftl`) i18n (EN/RU).
- adaptix/msgspec/orjson сериализация; pydantic-settings; cryptography + PyJWT + qrcode; loguru.
- Панель через `remnapy` (SDK Remnawave с GitHub).
- **15 платёжных шлюзов** сырым HTTP (без вендор-SDK).

## Карта каталогов (4 кольца)

```
src/core/            # framework-agnostic: config/ (per-concern), enums.py, constants, exceptions, types, logger, utils
src/application/      # dto/, events/, services/ (pricing, remnawave), common/ (protocols), use_cases/
  common/dao/         # protocol'ы репозиториев per-aggregate
  use_cases/          # CQRS: subscription/plan/promocode/referral/gateways/auth/user/... (commands/ + queries/)
src/infrastructure/   # database/(models,dao,migrations), payment_gateways/, services/, di/, redis/, taskiq/
  database/dao/base.py    # generic CRUD DAO
  services/remnawave.py   # клиент панели
  di/ioc.py + providers/  # Dishka
src/telegram/         # dispatcher, routers/(menu,subscription,dashboard,extra), middlewares, filters, widgets, states
src/web/              # app.py, endpoints/(payments, remnawave, telegram, health, public), schemas
assets/translations/  # Fluent .ftl (ru/en)
```

## Ключевые файлы (для сверки при реализации)

| Тема | Путь |
|---|---|
| Платёжный ABC | `src/infrastructure/payment_gateways/base.py` |
| Единый webhook-роут | `src/web/endpoints/payments.py` |
| Обработка платежа (use-case) | `src/application/use_cases/gateways/commands/payment.py` |
| Пример шлюза | `src/infrastructure/payment_gateways/{yookassa,cryptopay,telegram_stars}.py` |
| Клиент панели | `src/infrastructure/services/remnawave.py` |
| Протокол панели | `src/application/common/remnawave.py` |
| Покупка / синк | `src/application/use_cases/subscription/commands/{purchase,sync}.py` |
| Прайсинг | `src/application/services/pricing.py` |
| Generic DAO | `src/infrastructure/database/dao/base.py` |
| DI-контейнер | `src/infrastructure/di/ioc.py` + `providers/` |
| Alembic env | `src/infrastructure/database/migrations/env.py` |
| Конфиг | `src/core/config/{app,remnawave,...}.py` |
| Enums | `src/core/enums.py` |

## Паттерны, которые заимствуем

- **Кольца + протоколы**: бизнес зависит только от интерфейсов → мокабельно/тестируемо.
- **UnitOfWork + per-aggregate DAO** + generic base CRUD.
- **TrackableMixin** DTO: сериализуют только изменённые поля (JSONB-concat для частичных settings).
- **`.system`-актор**: обход RBAC для вебхуков/воркеров/сидинга.
- **Единый платёжный ABC + один роут** + DB-config шлюзов.
- **Отложенная локализация событий**: событие несёт `(key, kwargs)`, рендер — в локали получателя.
- **RBAC**: карта Role→Permission, `Role.includes` / иерархия в самом enum.
- Dishka: `Scope.APP` vs `Scope.REQUEST`; контейнер инжектится в worker.

## Чего НЕ берём

- Церемонию «один Interactor = один файл» на каждую операцию (переинжинирено для нашего размера).
- aiogram-dialog как основу всего UI — используем избирательно.
- Жёсткий пин к версии панели (2.7.x) — вместо этого capability-probe.

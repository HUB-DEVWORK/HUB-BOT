# CLAUDE.md — operating guide for AI in this repo

> Читается автоматически в начале сессии. Держи кратким и точным. Глубокий доменный контекст —
> в `docs/context/` (00→08). Сюда — правила работы, карта кода, инварианты, команды.

## Что это

**База (ядро)** для Telegram VPN-шоп бота + web-миниаппы на панели **Remnawave**. Продаём
VPN-подписки (VLESS/XTLS), провижним на панели. Синтез двух конкурентов: структура —
[remnashop](https://github.com/snoups/remnashop), широта — [bedolaga](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot).

**Сейчас пишем ТОЛЬКО ядро.** Бота (aiogram-хендлеры) и миниаппу (UI) — потом, поверх базы.
Оставляем швы (Protocol'ы, EventBus, DI, RBAC), чтобы подключить без переделки. Не тащи в базу
хендлеры бота, экраны миниаппы, геймификацию, CMS, web-админ — если не попросили явно.

Стек: Python 3.12 async · aiogram 3 · SQLAlchemy 2.0 async · Alembic · PostgreSQL · Redis ·
taskiq · Dishka · FastAPI · httpx. Панель — только Remnawave (≥2.8.0, capability-probe).

## Архитектура (4 кольца) — направление зависимостей ВНУТРЬ

```
web/  ─┐
        ├─► application/ ─► core/
infra/ ─┘        ▲
                 └── application зависит только от application/common (Protocol'ы)
```

- `src/core/` — framework-agnostic: config, enums, money, exceptions, constants, i18n, logging.
- `src/application/` — бизнес-ядро: `common/` (Protocol'ы = контракты), `services/`, `events/`, `dto/`.
  **Не** импортирует aiogram/FastAPI/SQLAlchemy напрямую — только протоколы.
- `src/infrastructure/` — адаптеры: `database/` (models+dao+migrations), `remnawave/`, `payments/`,
  `taskiq/`, `redis/`, `di/`, `services/`. Реализует протоколы из `application/common`.
- `src/web/` — тонкий FastAPI: вебхуки платежей/панели + health. **Не** держит бизнес-логику.

**Правило:** внешние системы (панель Remnawave, платёжные шлюзы, шина событий, уведомления)
подключаются к сервисам ТОЛЬКО через Protocol'ы из `application/common` — их тестируем с фейками,
их можно подменить. Персистентность — не swappable-адаптер: мы закоммичены на Postgres/SQLAlchemy,
поэтому сервисы типизируются против конкретного `UnitOfWork` и ORM-моделей (модель = доменная
сущность). Тестируемость сохраняется: тесты гоняют настоящий `UnitOfWork` против in-memory sqlite.
Не тащи в `application/services` httpx/aiogram/FastAPI/платёжные SDK — только через протоколы.

## Инварианты (НЕ нарушать — детали в `docs/context/07-gotchas.md`)

1. **Dual-write panel-first:** панель ПЕРВОЙ, вне DB-транзакции → локальный коммит. Неудача → retry-queue.
2. **Один panel-user на подписку**, постоянный `short_id` (не из мутабельного id). Partial-unique индекс.
3. **Деньги — целые minor-units** (`core/money.py`). `Decimal` только на границе шлюза.
4. **Идемпотентность вебхуков:** CAS(`allowed_from`) + UNIQUE(`external_id`,`gateway_type`) + `FOR UPDATE`.
   Вебхук: verify → enqueue → 200. Никогда не фулфиллить инлайн.
5. **Замороженные снапшоты** `plan_snapshot`+`pricing` на транзакции И подписке.
6. **UTC-aware datetimes** через `AwareDateTime`. Никаких наивных дат.
7. **Панель:** auth = и X-Api-Key, и Bearer; local → инжект `X-Forwarded-*`; версия — probe, не пин.
8. **Конфиг:** без плейсхолдеров (`change_me` отвергается), раздельные CRYPT/JWT/API ключи,
   44-символьный Fernet, креды шлюзов Fernet-шифрованы.
9. **Скидки:** `purchase_discount` одноразовый, `personal_discount` персистит, кап 100% → free-path.
10. **Рефералка at-most-once** через `is_issued`; мягкий фейл без активной подписки реферера.

## Команды

```
make install     # uv sync --extra dev
make check       # lint + typecheck + test  (ГЕЙТ перед коммитом)
make fmt         # автоформат + автофиксы
make up / down   # локальный стек (postgres, redis, web, worker, scheduler)
make migrate     # alembic upgrade head
make revision m="msg"   # автоген миграции
make smoke       # e2e против реальной панели (scripts/smoke.py)
```

## Как добавить…

- **Платёжный провайдер:** новый файл в `src/infrastructure/payments/gateways/`, наследуй
  `BasePaymentGateway`, добавь значение в `PaymentGatewayType` (`core/enums.py`), зарегистрируй в
  `GatewayFactory`, добавь seed-row. Роут и пайплайн менять НЕ нужно (single-route). См. `docs/context/03-payments.md`.
- **Эндпоинт панели:** метод в `src/infrastructure/remnawave/client.py` (типизированный DTO), при
  необходимости расширь Protocol в `src/application/common/remnawave.py`.
- **Модель БД:** файл в `src/infrastructure/database/models/`, зарегистрируй в `models/__init__.py`,
  `make revision m="..."`, проверь миграцию глазами, `make migrate`.
- **Фоновую задачу:** в `src/infrastructure/taskiq/tasks.py`; тяжёлое из вебхуков — только сюда.

## Соглашения кода

- Async везде. Типы обязательны (`mypy strict`). Ruff — форматтер и линтер.
- Модель-на-файл (не как bedolaga «всё в models.py»).
- Сервисы принимают зависимости через конструктор (Protocol'ы), не создают адаптеры внутри.
- Доменные события несут `(i18n_key, kwargs)`, не готовый текст (рендер в локали получателя).
- Комментарии — по делу, на языке окружающего кода. Не дублируй то, что видно из кода.

## Тесты

- Unit — на сервисы с фейками (`tests/fakes/`): `FakeRemnawaveClient`, `FakePaymentGateway`.
  Реальную панель/шлюзы НЕ дёргать.
- Клиент панели — через `respx` mock-transport.
- Integration — DAO/миграции против Postgres (или aiosqlite для быстрых).
- Обязательные кейсы: идемпотентность вебхука (дубль/late/out-of-order), кап 100%→free,
  one-shot purchase_discount, referral at-most-once, panel-first «remote orphan»→retry.

## Целевой репозиторий

`github.com/bini69-oi/BOT`. Ветка по умолчанию — `main`. Коммить/пушь только по просьбе.
Коммиты — **от имени человека**, без трейлера `Co-Authored-By: Claude` (`includeCoAuthoredBy: false`
в `.claude/settings.json`). Скилы проекта живут в `.claude/skills/` и коммитятся.

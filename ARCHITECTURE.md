# ARCHITECTURE

Слоистая (Clean-ish) архитектура в 4 кольца. Зависимости направлены **внутрь**: внешние кольца
знают о внутренних, не наоборот. Бизнес-логика зависит только от **протоколов**, поэтому
тестируется с фейками и не привязана к aiogram/FastAPI/SQLAlchemy.

```
┌─────────────────────────────────────────────────────────────┐
│  presentation                                                 │
│  ├─ web/        FastAPI: payment/panel webhooks, health       │
│  └─ (bot/       aiogram — БУДУЩЕЕ, не в базе)                  │
│           │ вызывает сервисы через DI                          │
│           ▼                                                    │
│  application/                                                  │
│  ├─ common/     Protocol'ы: UoW, DAO, RemnawaveClient,        │
│  │              PaymentGateway, EventBus, Notifier, Translator │
│  ├─ services/   PricingService, PurchaseService, ...          │
│  ├─ events/     доменные события (i18n_key, kwargs)           │
│  └─ dto/                                                       │
│           │ зависит только от common/ + core/                 │
│           ▼                                                    │
│  core/          config, enums, money, exceptions, i18n, log   │
└─────────────────────────────────────────────────────────────┘
        ▲ реализуют протоколы
┌───────┴─────────────────────────────────────────────────────┐
│  infrastructure/  database(models,dao,migrations),           │
│  remnawave(client,auth,webhook), payments(base,factory,      │
│  gateways), taskiq, redis, di(Dishka), services(backup,      │
│  notification, health)                                        │
└──────────────────────────────────────────────────────────────┘
```

## Кольца

- **core/** — чистые типы и утилиты. Ноль внешних побочек. `Money` (minor-units), enums, config
  (pydantic-settings), i18n-loader, исключения, логирование.
- **application/** — сердце. `common/` описывает **контракты** (Protocol). `services/` реализует
  бизнес-правила через эти контракты. Не импортирует конкретную инфраструктуру.
- **infrastructure/** — конкретика: SQLAlchemy DAO, httpx-клиент панели, платёжные шлюзы, taskiq,
  redis, Dishka-провайдеры. Реализует протоколы `application/common`.
- **presentation/** — `web/` (тонкий FastAPI для вебхуков). Бот и cabinet-API — позже.

## DI (композиционный корень, Dishka-ready)

База использует **композиционный корень** `src/infrastructure/di/container.py` (`AppContainer`):
строит App-синглтоны (engine, redis, клиент панели, `GatewayFactory`, event bus, сервисы) из
`Settings` и выдаёт свежий `UnitOfWork` на операцию (`container.uow()`). Его используют web-шов,
taskiq-worker и `scripts/smoke.py`.

Граф намеренно **Dishka-ready**: когда бот введёт request-scoped хендлеры, `AppContainer`
меняется на Dishka-провайдеры с тем же графом — `Scope.APP` для адаптеров/фабрик,
`Scope.REQUEST` для `UnitOfWork` — и контейнер инжектится и в aiogram-dispatcher, и в taskiq-worker,
чтобы фон гонял ту же бизнес-логику. Синтетический **SYSTEM**-актор (Role.SYSTEM, id -1) обходит
RBAC для вебхуков/воркеров/сидинга.

## Основные потоки данных

- **Покупка:** `web` (или бот) → `PurchaseService` → `PricingService` (цена) →
  `Transaction(PENDING)` → шлюз (инвойс) → вебхук → taskiq `ProcessPayment` → panel-first provision →
  `Subscription` → `EventBus` (рефералка/уведомления). См. `docs/context/02` и `03`.
- **Синк с панелью:** `RemnawaveService.sync_subscription` (reflection-копир) + reconcile-джоб +
  panel-write retry queue.
- **Вебхук панели:** `web` → `WebhookVerifier` (HMAC) → типизированное событие → обработчик
  (enable/disable/expiry/hwid/node) → локальный апдейт + уведомление.

## Ключевые сущности БД (кратко; полная схема — `docs/context/`)

`users` (баланс minor-units, referral_code, discounts, current_subscription_id) ·
`promo_groups`(+M2M) · `plans`/`plan_durations`/`plan_prices` (нормализовано) ·
`subscriptions` (**panel-user на подписку**, постоянный `short_id`, замороженный `plan_snapshot`) ·
`server_squads` (зеркало squads панели) · `transactions` (леджер, двойная идемпотентность) ·
`payment_gateways` (админ-конфиг, Fernet-шифр) · `promocodes`(+activations) ·
`referrals`+`referral_earnings` (леджер, `is_issued`) · `settings` (синглтон, JSONB-секции).

## Инварианты

Список — в [CLAUDE.md](CLAUDE.md#инварианты) и подробно в `docs/context/07-gotchas.md`.
Ключевые ADR — в [docs/adr/](docs/adr/).

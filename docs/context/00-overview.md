# 00 — Overview / Обзор

> **Что это за файл.** `docs/context/` — это большой «контекст-массив» для нейросети и для людей.
> Он собран из разбора двух ведущих открытых конкурентов —
> [remnashop](https://github.com/snoups/remnashop) (snoups) и
> [bedolaga](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot) (BEDOLAGA-DEV) —
> и описывает домен, потоки данных, платёжный пайплайн, мат-модель рефералки/промо и грабли.
> Прежде чем писать бота или миниаппу — прочитай эти файлы. Порядок: 00 → 01 → 02 → 03 → 04 → 07.

## Что мы строим

Чистый, хорошо организованный **фундамент («база», ядро)** для Telegram-бота + web-миниаппы,
которые продают VPN-подписки (VLESS/XTLS) и провижнят их на самохостовой панели **Remnawave**.

База — это синтез двух конкурентов: **слоистая архитектура от remnashop** +
**широта бизнес-фич от bedolaga**. Базу пишем **до** хендлеров бота и UI миниаппы:
она даёт конфиг, модели БД, DAO, клиент панели, абстракцию платежей, бизнес-сервисы, DI,
фоновые задачи и i18n. Хендлеры и UI подключаются к ней позже.

## Стек (зафиксирован)

- **Python 3.12**, полностью async.
- **aiogram 3** — бот (пишется позже; в базе только FSM-хранилище/типы).
- **SQLAlchemy 2.0 (async)** + **Alembic** + **PostgreSQL** (asyncpg).
- **Redis** — FSM, кэш, распределённые локи, pending-referral, сессии.
- **taskiq** (+ taskiq-redis) — фоновые задачи (worker + scheduler).
- **Dishka** — DI, инжектится и в будущий aiogram-dispatcher, и в taskiq-worker.
- **FastAPI** — тонкий web-шов (вебхуки платежей/панели + health); cabinet/mini-app API — позже.
- **httpx** — клиент Remnawave и платёжных шлюзов.
- **Панель — только Remnawave** (версия ≥ 2.8.0, с probe возможностей).

## Два конкурента — в двух абзацах

**remnashop**: Python 3.12, строгая Clean Architecture в 4 кольца
(core / application / infrastructure / telegram+web). Бизнес-логика — CQRS-стайл
use-cases `Interactor[In, Out]` с `.system`-актором для вебхуков/воркеров, RBAC через
карту Role→Permission, UnitOfWork + per-aggregate DAO-протоколы, TrackableMixin-DTO
(сериализуют только изменённые поля, JSONB-concat). Инфра: SQLAlchemy 2.0 async + 40 миграций,
Redis, taskiq worker/scheduler, Dishka DI, FastAPI web-слой, панель через `remnapy` SDK,
**15 платёжных шлюзов за одним ABC и одним webhook-роутом**. aiogram 3 + aiogram-dialog,
только webhook. Docker multi-stage uv, 4 контейнера. Пиннится к Remnawave 2.7.x.
Очень чисто, местами переинженерено, фич меньше.

**bedolaga**: Python 3.13, прагматичная слоистость (handlers / services / crud).
~130 сервис-модулей, ~75 CRUD-модулей, гигантский `PaymentService`-миксин из 24+ провайдеров.
Деньги — целые копейки везде. Богатый домен: баланс-кошелёк, промо-группы (приоритетные тиры
скидок, авто-назначение по тратам), мульти-тариф (**каждая подписка = свой panel-user**),
дневной биллинг, пакеты докупки трафика, per-squad цены/капасити, реферальная комиссия
с пополнений (тиры %/выводы/AML/партнёрка), геймификация (колесо/опросы/конкурсы),
CMS (новости/FAQ/лендинги/гостевые покупки), полный RBAC+ABAC web-админ с audit-log,
React+TS cabinet SPA, NaloGO налоговые чеки РФ. Alembic 94 миграции, APScheduler-синглтоны,
единый порт FastAPI. Remnawave 2.8.0+. Широко и обкатано, но разрослось и менее дисциплинированно.

## Наш выбор для базы

Четыре кольца, слоистость remnashop, прагматизм bedolaga:

- **core/** — framework-agnostic. pydantic-settings по concern'ам; enums; константы; исключения;
  Money (целые minor-units); i18n-loader; utils.
- **application/** — бизнес-ядро. Protocol'ы в `application/common` (UnitOfWork, per-aggregate DAO,
  RemnawaveClient, PaymentGateway, Notifier, EventBus, Translator). Сервис-классы с явными
  зависимостями (PricingService, PurchaseService, SubscriptionService, ReferralService,
  PromoService, NotificationService). Доменные события несут `(i18n_key, kwargs)`.
  Мы **не берём** церемонию remnashop «один Interactor = один файл»; держим композируемые
  методы сервисов, командный объект — только для платёжно-покупочного пайплайна.
- **infrastructure/** — конкретные адаптеры. SQLAlchemy DAO (generic base + per-aggregate),
  RemnawaveClient+Service, платёжные шлюзы, taskiq broker/tasks, Redis DAO, Dishka DI, backup, health.
- **web/** — тонкий FastAPI (вебхуки + health). Бот и cabinet API — позже, но швы готовы.

DI — Dishka: `Scope.APP` для SDK панели, брокера, gateway-factory; `Scope.REQUEST` для DAO,
UnitOfWork, сервисов. Контейнер инжектится и в aiogram, и в taskiq-worker, чтобы фоновые
джобы гоняли ту же бизнес-логику. Синтетический **SYSTEM**-актор (Role.SYSTEM, id -1) обходит
RBAC для вебхуков/воркеров/сидинга.

## Куда смотреть в базе

| Нужно | Файл(ы) базы |
|---|---|
| Конфиг/секреты | `src/core/config/` |
| Enums/деньги | `src/core/enums.py`, `src/core/money.py` |
| Контракты (интерфейсы) | `src/application/common/` |
| Бизнес-логика | `src/application/services/` |
| Модели БД | `src/infrastructure/database/models/` |
| Клиент/сервис панели | `src/infrastructure/remnawave/` |
| Платежи | `src/infrastructure/payments/` |
| Фоновые задачи | `src/infrastructure/taskiq/` |
| DI | `src/infrastructure/di/` |
| Вебхуки | `src/web/` |

## Куда смотреть в конкурентах (для реализующего AI)

- remnashop платёжный ABC + один роут: `src/infrastructure/payment_gateways/base.py`,
  `src/web/endpoints/payments.py`, `src/application/use_cases/gateways/commands/payment.py`.
- remnashop клиент панели: `src/infrastructure/services/remnawave.py` + `src/application/common/remnawave.py`.
- remnashop purchase/sync: `src/application/use_cases/subscription/commands/{purchase,sync}.py`.
- remnashop DI: `src/infrastructure/di/ioc.py` + `providers/`.
- bedolaga клиент панели: `app/external/remnawave_api.py` + `app/services/remnawave_service.py` + `subscription_service.py`.
- bedolaga прайсинг: `app/services/pricing_engine.py`.
- bedolaga платёжные миксины: `app/services/payment_service.py` + `app/services/payment/*.py`.
- bedolaga модели (деньги/тариф/промо-группы/мульти-тариф): `app/database/models.py`.
- bedolaga cabinet auth: `app/cabinet/auth/telegram_auth.py`.

**Правило:** структуру берём у remnashop, широту — у bedolaga.

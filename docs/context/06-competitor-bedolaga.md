# 06 — Конкурент: bedolaga (BEDOLAGA-DEV)

Репо: https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot

**TL;DR:** обкатанный «комбайн» с широчайшим доменом. Берём отсюда **широту бизнес-фич**:
баланс-кошелёк, промо-группы, мульти-тариф (один panel-user на подписку), реферальную
комиссию с пополнений, дисциплину копеек, гибкий auth панели, retry-очередь panel-write,
initData clock-skew, шифрованные бэкапы.

## Стек

- Python 3.13, полностью async.
- aiogram 3.x. FastAPI — вебхуки, платёжные колбэки, Cabinet API и web-админ (`app/webapi` + `app/webserver`).
- SQLAlchemy 2.x + Alembic (**94 миграции**). PostgreSQL + Redis.
- **APScheduler** (синглтоны) для расписаний. Единый порт FastAPI.
- **React + TS** cabinet SPA. NaloGO — налоговые чеки РФ.
- Панель Remnawave **2.8.0+**.

## Карта каталогов (прагматичная слоистость)

```
app/
  handlers/        # aiogram-хендлеры (bot UI)
  services/        # ~130 сервис-модулей (бизнес-логика по доменам)
    payment_service.py + payment/*.py   # PaymentService-миксин, 24+ провайдера
    pricing_engine.py                    # стакинг скидок
    remnawave_service.py, subscription_service.py
  crud/            # ~75 CRUD-модулей
  database/models.py                     # ВСЕ модели в одном файле (деньги/тариф/промо-группы/мульти-тариф)
  external/remnawave_api.py              # низкоуровневый клиент панели
  cabinet/auth/telegram_auth.py          # initData-валидация
  webapi/ , webserver/                   # Cabinet API + web-админ
```

## Домен (что заимствуем по фичам)

- **Баланс-кошелёк** (`balance_kopeks`) — пополнения, списания, реферальные начисления.
- **Промо-группы**: приоритетные тиры скидок, авто-назначение по сумме трат, M2M.
- **Мульти-тариф**: **каждая подписка = свой panel-user** (защита HWID). Постоянный `short_id`.
- **Дневной биллинг** (`is_daily`, списание с баланса по дням).
- **Пакеты докупки трафика** (traffic top-up).
- **Per-squad** цены/капасити/гейтинг.
- **Реферальная комиссия с ПОПОЛНЕНИЙ**: тирные %, выводы, AML, партнёрка.
- **Геймификация**: колесо/опросы/конкурсы (в базу не тащим, но знать о ней).
- **CMS**: новости/FAQ/лендинги/гостевые покупки.
- **RBAC + ABAC** web-админ с audit-log.

## Модель User (пример богатства полей)

`users`: `id`, `telegram_id` (BigInt unique, nullable для email-only), `auth_type`
(`telegram`/`email`), `username`, `first/last_name`, `status`, `language`, `balance_kopeks`,
`used_promocodes`, `has_had_paid_subscription`, `referred_by_id` (self-FK SET NULL),
`referral_code`, `remnawave_uuid`, `email`+`email_verified`+`email_verification_source`+
`password_hash`+`*_token/*_expires` (cabinet/OAuth).

## Паттерны, которые заимствуем

- **Целые копейки везде** (интеджер-деньги, `Decimal` только на границе).
- **`AwareDateTime`** TypeDecorator (UTC-aware) + partial-unique индексы + sequence-sync на старте.
- Гибкий **auth панели** (api_key/bearer/basic/caddy + CF-Access + cookie) + local-detection + retry-очередь.
- orjson-парсинг вебхука с fallback-реселиализацией (переживает переписывание тела прокси).
- **initData clock-skew** толеранс (иначе валидные юзеры отваливаются).
- **Шифрованные бэкапы** (pyzipper), роутинг уведомлений в админ-чат по типу события.
- JSON-i18n loader с правимыми оверрайдами.

## Чего НЕ берём (или откладываем)

- Разрастание: ~130 сервис-модулей / ~75 CRUD, `PaymentService`-god-object-миксин.
- Все модели в одном `models.py` — у нас модель-на-файл.
- Геймификацию/CMS/партнёрку/AML в **базу** — только знать, что есть; добавлять поверх.
- Ручной DI через kwargs миддлварей — у нас Dishka.

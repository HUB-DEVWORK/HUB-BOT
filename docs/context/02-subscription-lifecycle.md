# 02 — Жизненный цикл подписки

Это ядро продукта. Все состояния подписки и переходы между ними + правило dual-write с панелью.

## Состояния (`SubscriptionStatus`)

`trial → active → (expired | disabled | limited) → deleted`, плюс `pending` (ожидает оплаты).

## 1. Trial (пробный)

Пользователь с флагом `is_trial_available` получает пробную подписку — на панели создаётся
user с trial-лимитами. Строка `SubscriptionConversion` трекает воронку trial→paid.
Опционально: перенос остатка trial-дней в первый платный период.

## 2. Purchase (NEW) — покупка новой подписки

1. Пользователь выбирает план → длительность → squad'ы → валюту.
2. `PricingService` считает цену: скидки промо-групп + personal/purchase discount (кап 100%).
3. Создаётся `Transaction(status=PENDING, payment_id=UUID)` с **замороженными** снапшотами
   `plan_snapshot` + `pricing` (JSONB). Заморозка обязательна: если каталог изменят между
   инвойсом и оплатой — выдастся ровно то, что заказывали.
4. Выбранный шлюз создаёт инвойс.
5. По вебхуку taskiq-джоб `ProcessPayment`: CAS-переход `PENDING → COMPLETED`, затем
   `PurchaseService` провижнит Remnawave-user (**panel-first, вне DB-транзакции**), сохраняет
   `Subscription` с постоянным уникальным `short_id`, ставит `user.current_subscription_id`,
   публикует `UserPurchaseEvent`, best-effort реферальные награды + уведомления.

**100%-скидка (free)** → минует шлюз и завершается напрямую по free-path.

## 3. Renew (RENEW) — продление

Продлить `expire_at` на той же подписке/panel-user'е; прибавить длительность; обновить панель.

## 4. Change (CHANGE) — смена

Сменить план/squad'ы/лимиты на существующей подписке; обновить панель.

## 5. Expiry / disable — истечение/отключение

Панель шлёт события истечения; локальный статус → `expired`. Опционально autopay списывает
с баланса до истечения (`autopay_days_before`). Уход из обязательного канала может отключить
подписку (`disabled_by_channel_leave`).

## 6. Sync — синхронизация

Reflection-стайл `apply_sync` копирует изменённые поля local↔panel (маппинг `short_id`↔`uuid`).
Reconcile-джобы (`SyncFromRemnawave` / `SyncFromLocal`) чинят дрейф. Очередь ретраев
panel-write идемпотентно передрайвит неудавшиеся записи.

## Dual-write без 2PC (главное правило целостности)

Между локальной БД и панелью нет распределённой транзакции. Стандарт:

1. Вызвать панель **ПЕРВОЙ** (create/update user) **ВНЕ** DB-транзакции.
2. Затем сохранить локально и закоммитить.
3. Порядок операций: **grant → mark-issued → notify** (минимизирует окно рассинхрона).

Падение **после** удачного вызова панели, но до/во время локального коммита оставляет
**«remote orphan»** (на панели есть, локально нет). Митигация:
- документируем это окно;
- неудавшиеся panel-write после локального коммита идут в **panel-write retry queue**
  (taskiq-задача), которая идемпотентно передрайвит `sync_subscription`;
- есть **reconcile-джоб** для починки дрейфа в обе стороны.

## Почему один panel-user на подписку

`subscriptions.short_id` — **постоянный** уникальный суффикс на подписку
(НЕ выводить из мутабельного `id` вроде `f"sub{id}"`). Если две подписки одного юзера
маппятся на ОДНОГО panel-user'а — они делят наименьший HWID/device-лимит и портят друг друга.
Каждая `Subscription` = свой panel-user. Энфорсим partial-unique индексом.

## Диаграмма (текст)

```
choose plan/duration/squads/currency
        │
        ▼
PricingService (discounts, cap 100%)  ──100%──►  free-path (skip gateway) ─┐
        │ price>0                                                          │
        ▼                                                                  │
Transaction(PENDING, payment_id, frozen snapshot)                          │
        │                                                                  │
   gateway.create_payment → invoice URL / in-bot invoice                   │
        │                                                                  │
   (user pays) ──► webhook ──► enqueue ──► 200 fast                        │
        │                                                                  │
   ProcessPayment (worker):                                                │
     CAS PENDING→COMPLETED  +  SELECT FOR UPDATE                           │
        │                                                                  ▼
        ├──► PurchaseService.provision (panel-first, outside tx) ◄─────────┘
        ├──► persist Subscription(short_id), set current_subscription_id
        ├──► publish UserPurchaseEvent
        └──► EventBus: referral / notifications / analytics (best-effort)
```

Дальше: `03-payments.md` — детали платёжного пайплайна и идемпотентности.

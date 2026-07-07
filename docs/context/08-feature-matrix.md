# 08 — Скоуп фич: что заложено в базу

«База» ниже = что закладываем в ядро (без бота/миниаппы). Решения зафиксированы —
менять только через ADR.

| Фича | Решение в базе |
|---|---|
| Стиль архитектуры | 4 кольца + протоколы; сервисы вместо Interactor-per-file |
| Панель | Свой типизированный клиент Remnawave ≥2.8, **capability-probe** (не пин версии) |
| Деньги | **Целые minor-units** везде + Decimal только на границе шлюза |
| Подписки | Нормализованные тарифы/длительности/цены + **panel-user на подписку** + snapshot |
| Trial | Trial + `SubscriptionConversion` (трекинг конверсии) |
| Платежи | 1 ABC, 1 вебхук-роут, DB-config; провайдер = один файл; старт: `manual`+`stars`+`yookassa`+`cryptobot` |
| Идемпотентность | **Двойная** (payment_id CAS + external_id unique) + FOR UPDATE |
| Рефералы | **Комиссия с пополнений**, леджер `is_issued`, at-most-once; выводы/AML — отложить |
| Промокоды | Полный набор типов наград + per-user unique |
| Промо-группы | Приоритетные тиры скидок, сегментация |
| Скидки | personal (персистит) + purchase (one-shot) + кап 100% → free-path |
| Мульти-сервер | `server_squads` зеркало + цена/капасити/гейтинг per-squad |
| i18n | JSON-loader + отложенный рендер событий `(i18n_key, kwargs)`, EN/RU |
| Уведомления | Очередь + per-topic роутинг в админ-чат |
| Админка | RBAC (Role→Permission) в ядре; web-кабинет поверх REST `/api/admin` |
| Миниаппа/кабинет | initData-auth (HMAC), cabinet API `/api/cabinet` |
| Бэкапы | Шифрованные бэкапы (taskiq, в админ-чат) |
| Аналитика | События + хуки; конверсии, кампании по deep-link |
| Геймификация | Не в базе (швы через события) |
| CMS | Не в базе; контент-таблицы позже |
| Налоговые чеки | Хук после completion (реализация позже) |
| Фон | **taskiq** worker+scheduler + panel-write retry queue |
| Деплой | Docker uv; web+worker+scheduler; polling **и** webhook |
| Миграции | Alembic (async env, `transaction_per_migration`, seq-sync) |

## Реализовано поверх базы (по состоянию на 2026-07)

Таблица выше — исходный скоуп ядра. Что уже надстроено поверх него в проде:

| Область | Что сделано |
|---|---|
| Платежи | **21 живой провайдер** за одним ABC (yookassa, cryptobot, cryptomus, heleket, platega, robokassa, yoomoney, wata, freekassa, paypalych, cloudpayments, lava, mulenpay, kassa_ai, rollypay, riopay, severpay, aurapay, antilopay + manual/stars). Ещё 3 (tribute/paypear/overpay) — заготовки |
| Возвраты | `BasePaymentGateway.refund()`; API-рефанд у yookassa/cryptomus/heleket/cloudpayments, у прочих — record-only; CAS `COMPLETED→REFUNDED`, откат кошелька, опц. отзыв подписки, нотификация |
| Веб-покупка без TG | email-регистрация/логин (scrypt + JWT HS256 на `APP__JWT_SECRET`, refresh 7д с ротацией), dual-auth кабинет (Bearer ∥ tma initData), гостевая покупка по email, OAuth Google/Yandex, доставка ссылки письмом, standalone SPA на `/web` |
| Продажи | мультиканальный гейт (all/trial/buy), Redis smart-cart + автопокупка после пополнения, платный триал с переносом остатка дней |
| Надёжность | `RemnawaveResyncService` (ночная сверка, лечение дрейфа, сироты→DISABLED), panel-watchdog авто-техрежим, device-guard scan |
| Легальность | НалоGO-чеки (`NalogoClient`, lknpd income) |
| Маркетинг | S2S-постбэки (`wire_postback_events`, макросы), статус нод юзеру, конверсия trial→paid |
| Смена тарифа | `PurchaseType.CHANGE` с зачётом остатка дней + докупка трафика |

## Итог

- **Скелет** — дисциплина и тестируемость: кольца, протоколы, mypy strict.
- **Мясо** — баланс-кошелёк, промо-группы, мульти-тариф, рефералка с пополнений, бэкапы.
- Геймификация, CMS, AML — **поверх** базы, позже. Ядро даёт для них швы
  (события, протоколы, RBAC, DI).

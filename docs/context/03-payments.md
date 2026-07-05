# 03 — Платежи: единый пайплайн и идемпотентность

Дизайн: **SINGLE-ABC, DB-CONFIGURED, SINGLE-ROUTE** (модель remnashop, усиленная
идемпотентностью bedolaga). Цель — чтобы добавить N+1 провайдера = один файл + один enum +
один seed-row в БД.

## `BasePaymentGateway` (ABC) — `src/infrastructure/payments/base.py`

Конструируется из строки таблицы `payment_gateways` (settings JSONB, расшифрованный Fernet).
Инстанс кэшируется per-type и сам инвалидируется при смене `updated_at` строки настроек.

Абстрактные методы:

- `async create_payment(ctx: PaymentContext) -> PaymentResult`
  `ctx` несёт `transaction_id`/`payment_id` (UUID), `amount_minor`, `currency`, `description`,
  `user`, `return_url`; возвращает hosted invoice URL **или** in-bot invoice payload.
- `async handle_webhook(request: WebhookRequest) -> WebhookResult`
  парсит+верифицирует колбэк провайдера, возвращает `(payment_id | external_id, TransactionStatus)`.
  Кидает `PermissionError` (→403) при провале подписи/IP, `LookupError` (→404) при неизвестном платеже.
- свойство `capabilities`: `supports_refund`, `supports_recurrent`, `supports_saved_method`,
  `needs_http_webhook`, `currencies`.

Шаред-хелперы на базе:
- IP-allowlist (CF-Connecting-IP / X-Real-IP / X-Forwarded-For, с учётом trusted-proxy Cloudflare);
- верификаторы HMAC-SHA256 / MD5 / SHA1;
- orjson-парсинг тела с fallback-реселиализацией (compact/ascii) — переживает переписывание тела прокси
  (грабля из обработки CryptoBot у bedolaga).

## Registry & DI

`GatewayFactory` (`Scope.APP`) маппит `PaymentGatewayType → класс` (provide_all).
Добавить провайдера — drop-in одного файла + одно значение enum + seed-row в БД.
**По умолчанию активны только:** `manual` (админ-платёж, всегда) и `telegram_stars`.

## Единый webhook-роут

`POST /api/v1/payments/{gateway_type}` → `factory.get(gateway_type).handle_webhook(request)`.
При успехе — **ENQUEUE** taskiq-джоб (`handle_payment_transaction`) и **немедленно** вернуть 200.
Никакой тяжёлой работы инлайн (Telegram/шлюзы ретраят на не-200).
Telegram Stars — особый: HTTP-вебхука нет, подтверждение — in-bot `successful_payment` хендлер.

## Пайплайн обработки (taskiq worker, `ProcessPayment`)

1. **CAS-переход статуса:** `transition_status(payment_id, COMPLETED, allowed_from=(PENDING,))`
   — идемпотентно, дубли/поздние вебхуки = no-op. Refund только `allowed_from=(COMPLETED,)`.
2. **Concurrency-guard:** `SELECT ... FOR UPDATE` на строке транзакции (для guest/параллельных вебхуков).
3. **Fulfill:** `PurchaseService` выдаёт/продлевает/меняет подписку (panel-first) **или** кредитует
   `balance_minor` при пополнении. Замороженный `plan_snapshot`+`pricing` на транзакции → выдаём
   ровно заказанное, даже если каталог изменился.
4. **Side-effects через EventBus:** реферальная комиссия (best-effort), уведомления, аналитика/конверсия,
   пост-топап автоматика (возобновить приостановленную дневную подписку, авто-покупка сохранённой корзины,
   авто-продление истёкшей). Всё best-effort, изолировано от атомарного ядра.

## Идемпотентность = двойная

- `payment_id` UUID **UNIQUE** + CAS-переход (allowed_from);
- **UNIQUE(`external_id`, `gateway_type`)**.

Бесплатные (100%-скидка/промо) покупки минуют шлюз и завершаются напрямую.

## Деньги

Целые **minor-units** (копейки/центы) внутри везде; `Decimal` (ROUND_HALF_UP) **только** на границе
шлюза. Крипта/Stars → нужен `skip_amount_check`-толеранс (FX-неточность).

## Расширенные capability (опционально, плагом через флаги)

- сохранённые методы оплаты + recurrent/autopay (bedolaga);
- хук генерации налогового чека (после completion);
- ручной verification-poll (для провайдеров с ненадёжными вебхуками) — scheduled-задача,
  опрашивающая статус у провайдера.

Так абстракция остаётся единой, а «богатые» провайдеры опционально включают лишнее.

## Telegram Stars — особенности

- HTTP-вебхука нет — подтверждение через in-bot `successful_payment`.
- `pre_checkout_query` отвечать в секунды.
- Тест-покупки владельца авто-рефандить (`refund_star_payment`) и помечать `CANCELED`.

## Провайдеры конкурентов (справочно)

remnashop (15, чистый HTTP, без SDK): yookassa, yoomoney, cryptopay, cryptomus, heleket,
freekassa, robokassa, telegram_stars, platega, wata, valutix, mulen_pay, pay_master, url_pay.
bedolaga: 24+ через миксин `PaymentService` (YooKassa, Telegram Stars, Cryptomus, Tribute, и др.).

Мы копируем **дизайн** (single ABC / single route / DB-config), не тащим все 15/24 сразу.
Конкретный список для этого проекта — определяем позже; добавление уже дёшево.

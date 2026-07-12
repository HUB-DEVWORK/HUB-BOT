# Добавить платёжного провайдера

Дизайн платежей — single ABC / single route / DB-config (ADR-0004, `docs/context/03-payments.md`).
Добавление провайдера = **один файл + один enum + одна регистрация + seed-row**. Роут и пайплайн
`ProcessPayment` НЕ трогаем.

## Шаги

1. **Enum.** Добавь значение в `PaymentGatewayType` — `src/core/enums.py`.

2. **Класс шлюза.** Новый файл `src/infrastructure/payments/gateways/<name>.py`, наследуй
   `BasePaymentGateway` (`src/infrastructure/payments/base.py`). Реализуй:
   - `gateway_type` = новое значение enum;
   - `capabilities` → `GatewayCapabilities` (валюты, `needs_http_webhook`, refund/recurrent/saved);
   - `create_payment(ctx) -> PaymentResult` (hosted `REDIRECT` URL или `IN_BOT` payload);
   - `handle_webhook(request) -> WebhookResult` — верифицируй и верни `(payment_id|external_id, status)`.
     Используй шаред-хелперы базы: `verify_hmac`, `check_ip_allowlist`, `client_ip`, `parse_json`.
     Кидай `WebhookVerificationError` (→403) при провале подписи/IP, `NotFound` (→404) при неизвестном платеже.

3. **Регистрация.** Впиши класс в `_REGISTRY` в `src/infrastructure/payments/factory.py`.

4. **Seed-row.** Настройки провайдера — строка таблицы `payment_gateways` (`settings` JSONB,
   секреты Fernet-шифрованы через `SecretBox`). `is_active=true` включает шлюз.

5. **Тест.** Добавь `tests/unit/test_<name>_gateway.py` по образцу `test_manual_gateway.py`:
   успешный вебхук → `WebhookResult`, плохая подпись/IP → `WebhookVerificationError`.

## Инварианты (не нарушать)
- Никогда не фулфиллить в вебхуке — только verify → 200; фулфилмент делает воркер (идемпотентный CAS).
- Деньги — целые minor-units; `Decimal` только на границе шлюза; для крипты/Stars — толеранс сумм.
- Тело вебхука парсить с fallback-реселиализацией (прокси переписывают тело → ломают HMAC).

## Проверка
`make check` (ruff + mypy + pytest). Роут `POST /api/v1/payments/{gateway_type}` заработает автоматически.

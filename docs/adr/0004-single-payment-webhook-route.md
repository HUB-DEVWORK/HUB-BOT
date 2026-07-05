# 0004 — Единый платёжный ABC + один webhook-роут

**Статус:** Accepted

## Контекст

remnashop держит 15 провайдеров, bedolaga — 24+. У нас «будет куча» провайдеров. Нужен способ
добавлять их дёшево и единообразно, не размазывая логику по хендлерам (god-object bedolaga —
антипример).

## Решение

`BasePaymentGateway` (ABC): `create_payment`, `handle_webhook`, `capabilities`.
`GatewayFactory` маппит `PaymentGatewayType → класс`. Один динамический роут
`POST /api/v1/payments/{gateway_type}` → factory → `handle_webhook` → verify → **enqueue** taskiq →
200. Настройки провайдера — строка таблицы `payment_gateways` (JSONB, Fernet-шифр), правится
админом в рантайме.

Добавить провайдера N+1 = **один файл** (наследник ABC) + **одно** значение enum + **один** seed-row.
Роут и пайплайн не трогаются.

## Последствия

- (+) Дёшево масштабировать список провайдеров; единая идемпотентность и обработка.
- (+) Провайдеры-настройки без редеплоя (DB-config).
- (−) Общий ABC должен покрывать разнообразие (hosted-invoice vs in-bot Stars; HMAC vs IP-allowlist) —
  решается `capabilities` + шаред-хелперами на базе.
- Тяжёлую работу в вебхуке делать нельзя — только enqueue + 200 (шлюзы ретраят на не-200).

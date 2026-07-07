# Cabinet API contract (mini-app ↔ base)

The three templates are **pure frontend**. They read/write through this contract only —
never the raw DB or panel. The backend (`src/web/`, added later) implements these routes on
top of the existing services (`PurchaseService`, `PricingService`, `SubscriptionService`,
`ReferralService`, `PromoService`). Until then, `shared/api.js` serves `mock/mock-data.js`
so the templates preview standalone.

All money is **minor units** (kopeks/cents; Stars exponent 0 — `core/money.py`), all sizes are
**bytes**, all timestamps are **ISO-8601 UTC**. The client formats everything (`shared/format.js`),
so payloads stay locale/currency-agnostic.

## Auth

Every request carries the Telegram Mini Apps credential:

```
Authorization: tma <initData>
```

The backend validates `initData` (HMAC over the bot token — the seam already exists:
`APP__JWT_SECRET`, `cryptography` dep) and resolves the `User`. Outside Telegram the client
falls back to mock mode; `?mock=1` forces it.

Base URL: same origin by default. Override with `window.__CABINET_API_BASE__` if the API is
hosted elsewhere (then CORS must allow it — `WEB__CORS_ORIGINS`, never `*` with credentials).

---

## Reads

### `GET /api/cabinet/me`

The account + its current subscription. `subscription` is `null` (or `status:"none"`) when the
user has never bought/trialed.

```jsonc
{
  "user": {
    "id": 4210,
    "first_name": "Иван",
    "username": "ivan_petrov",
    "language": "ru",              // Locale (en|ru)
    "currency": "RUB",             // Currency
    "balance_minor": 15000,        // users.balance_minor
    "referral_code": "AB12CD",     // users.referral_code
    "personal_discount_pct": 10,   // users.personal_discount_pct
    "is_trial_available": false    // users.is_trial_available
  },
  "app": {                         // (fragment) bot-config driven UI hints
    "balance_enabled": true,       // hide the balance chip when false
    "payment_methods": [           // active online gateways -> extra pay chips
      { "id": "yookassa", "label": "Карта / СБП" }
    ]
  },
  "subscription": {
    "status": "active",            // SubscriptionStatus: trial|active|limited|expired|disabled|pending|none
    "is_trial": false,
    "plan_name": "Premium",        // from plan_snapshot
    "start_at": "2026-06-10T09:00:00Z",
    "expire_at": "2026-08-01T09:00:00Z",
    "device_limit": 5,             // subscriptions.device_limit
    "traffic": {
      "used_bytes": 32212254720,   // subscriptions.traffic_used_bytes
      "limit_bytes": 107374182400, // subscriptions.traffic_limit_bytes (0 -> unlimited)
      "unlimited": false
    },
    "subscription_url": "https://.../s/AB12CD", // subscriptions.subscription_url
    "crypto_link": "happ://add/https://.../s/AB12CD", // subscriptions.crypto_link (Happ)
    "autopay_enabled": true        // subscriptions.autopay_enabled
  }
}
```

### `GET /api/cabinet/plans`

Buyable catalogue. Prices are the **base** price; the client applies
`user.personal_discount_pct` for display. The server is the source of truth at purchase time
(discounts, promo-group tiers, cap 100% → free — `PricingService`).

```jsonc
{
  "currency": "RUB",
  "items": [
    {
      "public_code": "premium",    // plans.public_code
      "name": "Premium",
      "description": "5 устройств · для всей семьи",
      "type": "both",              // PlanType: traffic|devices|both|unlimited
      "traffic_limit_bytes": 107374182400, // 0/null -> unlimited
      "device_limit": 5,
      "is_current": true,          // matches the user's active subscription plan
      "durations": [               // plan_durations + plan_prices
        { "days": 30, "price_minor": 19900 },
        { "days": 90, "price_minor": 53900 },
        { "days": 365, "price_minor": 179900 }
      ]
    }
  ]
}
```

### `GET /api/cabinet/constructor`

Constructor-mode price list (used when `me.app.sales_mode == "constructor"`). The final price
is `period.price_minor + pack.price_minor`; Stars = `ceil(total / stars_rate)`.

```jsonc
{
  "currency": "RUB",
  "stars_rate": 200,               // kopeks per 1 star (STARS_RATE_RUB)
  "periods": [ { "id": 1, "days": 30, "months": 1, "price_minor": 9900 } ],
  "traffic_packs": [ { "id": 1, "gb": 100, "price_minor": 5000 } ] // gb 0 -> unlimited
}
```

### `GET /api/cabinet/referral`

```jsonc
{
  "code": "AB12CD",
  "link": "https://t.me/YourVPNBot?start=ref_AB12CD",
  "commission_percent": 25,        // users.referral_commission_percent (or global default)
  "invited_count": 7,
  "earnings_minor": 45000          // sum of referral_earnings ledger
}
```

---

## Writes

Writes are **idempotent** server-side and follow the base's rules (panel-first dual-write,
webhook fulfilment via taskiq). In mock mode `shared/api.js` returns canned responses.

### `POST /api/cabinet/promocode` — `{ "code": "WELCOME2026" }`

```jsonc
{ "ok": true, "reward": { "type": "balance", "amount_minor": 5000 }, "message": null }
// or: { "ok": false, "reward": null, "message": "invalid_or_used" }
```

### `POST /api/cabinet/purchase` — `{ "public_code": "premium", "days": 30 }`

Constructor mode sends `{ "period_id": 1, "pack_id": 2, "method": "stars" }` instead of
`plan_id`+`days` — the server assembles the price from the selected rows.

Creates a `Transaction(PENDING)` with frozen `plan_snapshot`+`pricing` and returns where to pay.
`payment_url` is `null` for balance/free-path (cap 100%) purchases (already fulfilled or fulfilled
on the returned invoice).

Actual response shapes by method:

```jsonc
{ "ok": true, "paid_with": "balance" }                          // balance or 100%-free path
{ "ok": true, "invoice_link": "https://t.me/$abc" }             // stars -> openInvoice()
{ "ok": true, "redirect_url": "https://yoomoney.ru/pay/..." }   // online gateway -> openLink()
```

### `GET /api/cabinet/devices`

HWID devices bound to the current subscription's panel user.

```jsonc
{ "items": [ { "hwid": "a1b2…", "platform": "iOS", "model": "iPhone 15", "created_at": "…" } ],
  "device_limit": 5 }
```

### `DELETE /api/cabinet/devices/{hwid}`  → `{ "ok": true }`

The «Кабинет» tab renders these as «Мои устройства» with one-tap unlink (skipped in mock mode).

### `POST /api/cabinet/subscription/reset-devices`  → `{ "ok": true }`

### (optional, later) `POST /api/cabinet/topup` — `{ "amount_minor": 50000, "gateway": "telegram_stars" }`
Returns `{ "payment_url": "..." }`.

---

## Errors

Non-2xx returns `{ "error": "<code>", "message": "<human>" }`. The client shows a retry state on
read failures and a toast on write failures. Codes are stable strings (e.g. `unauthorized`,
`invalid_or_used`, `plan_unavailable`, `insufficient_balance`).

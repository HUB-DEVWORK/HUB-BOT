/**
 * Standalone preview data.
 *
 * This is the exact shape the real cabinet API returns (see ../CONTRACT.md).
 * It is used only when the mini-app runs OUTSIDE Telegram (a plain browser) or with
 * `?mock=1`. In production the admin removes this <script> tag — `shared/api.js`
 * then talks to the live `/api/cabinet/*` endpoints instead.
 *
 * Raw values only (minor units, bytes, ISO-8601 UTC). All human formatting happens
 * client-side in `shared/format.js`, so the same payload renders correctly in any
 * locale/currency and in all three templates.
 */
window.__CABINET_MOCK__ = {
  // GET /api/cabinet/me
  me: {
    user: {
      id: 4210,
      first_name: "Иван",
      username: "ivan_petrov",
      language: "ru",
      currency: "RUB",
      balance_minor: 15000, // 150.00 ₽
      referral_code: "AB12CD",
      personal_discount_pct: 10,
      is_trial_available: false,
    },
    subscription: {
      status: "active", // trial | active | limited | expired | disabled | pending | none
      is_trial: false,
      plan_name: "Premium",
      start_at: "2026-06-10T09:00:00Z",
      expire_at: "2026-08-01T09:00:00Z",
      device_limit: 5,
      traffic: {
        used_bytes: 32212254720, // 30 GiB
        limit_bytes: 107374182400, // 100 GiB
        unlimited: false,
      },
      subscription_url: "https://sub.myvpn.example/s/AB12CD",
      crypto_link:
        "happ://add/https://sub.myvpn.example/s/AB12CD", // Happ deep-link
      autopay_enabled: true,
    },
  },

  // GET /api/cabinet/plans
  plans: {
    currency: "RUB",
    items: [
      {
        public_code: "basic",
        name: "Basic",
        description: "1 устройство · для одного гаджета",
        type: "both",
        traffic_limit_bytes: 53687091200, // 50 GiB
        device_limit: 1,
        is_current: false,
        durations: [
          { days: 30, price_minor: 9900 },
          { days: 90, price_minor: 26900 },
          { days: 180, price_minor: 49900 },
          { days: 365, price_minor: 89900 },
        ],
      },
      {
        public_code: "premium",
        name: "Premium",
        description: "5 устройств · для всей семьи",
        type: "both",
        traffic_limit_bytes: 107374182400, // 100 GiB
        device_limit: 5,
        is_current: true,
        durations: [
          { days: 30, price_minor: 19900 },
          { days: 90, price_minor: 53900 },
          { days: 180, price_minor: 99900 },
          { days: 365, price_minor: 179900 },
        ],
      },
      {
        public_code: "ultra",
        name: "Ultra",
        description: "10 устройств · безлимитный трафик",
        type: "unlimited",
        traffic_limit_bytes: 0, // 0 -> unlimited
        device_limit: 10,
        is_current: false,
        durations: [
          { days: 30, price_minor: 29900 },
          { days: 90, price_minor: 79900 },
          { days: 180, price_minor: 149900 },
          { days: 365, price_minor: 269900 },
        ],
      },
    ],
  },

  // GET /api/cabinet/referral
  referral: {
    code: "AB12CD",
    link: "https://t.me/YourVPNBot?start=ref_AB12CD",
    commission_percent: 25,
    invited_count: 7,
    earnings_minor: 45000, // 450.00 ₽
  },
};

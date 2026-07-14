/* Standalone-preview data (used outside Telegram or with ?mock=1).
   Shapes match /api/cabinet/* exactly. Demo values from the design spec. */
window.__MOCK__ = {
  me: {
    user: {
      id: 1,
      first_name: "Алексей",
      username: "alex_k",
      language: "ru",
      currency: "RUB",
      balance_minor: 15000,
      referral_code: "AB12CD",
      personal_discount_pct: 0,
      is_trial_available: false,
    },
    subscription: {
      status: "active",
      is_trial: false,
      plan_id: 1,
      plan_name: "VPN",
      start_at: "2026-06-10T00:00:00Z",
      expire_at: new Date(Date.now() + 23 * 864e5).toISOString(),
      device_limit: 5,
      traffic: { used_bytes: 44238914560, limit_bytes: 0, unlimited: true },
      subscription_url: "https://sub.vpn.app/u/8fk2m3n9x7q1w5e8r4t6y2u9a3",
      crypto_link: null,
    },
    app: { template: "a", title: "VPN", greeting: null, accent_color: null, bot_username: "bot_vpn4_bot", sales_mode: "plans" },
  },
  // Own key on purpose: without it __MOCK__["constructor"] resolves to Object.prototype.constructor.
  // Preview the constructor UI with ?mock=1&sales=constructor.
  constructor: {
    currency: "RUB",
    stars_rate: 200,
    periods: [
      { id: 1, days: 30, months: 1, price_minor: 9900 },
      { id: 2, days: 90, months: 3, price_minor: 24900 },
      { id: 3, days: 180, months: 6, price_minor: 44900 },
    ],
    traffic_packs: [
      { id: 1, gb: 50, price_minor: 0 },
      { id: 2, gb: 200, price_minor: 5000 },
      { id: 3, gb: 0, price_minor: 15000 },
    ],
  },
  plans: {
    currency: "RUB",
    items: [
      {
        id: 1,
        public_code: "vpn",
        name: "VPN",
        description: "Безлимитный трафик · до 5 устройств",
        traffic_limit_bytes: 0,
        device_limit: 5,
        is_current: true,
        durations: [
          { days: 30, months: 1, price_minor: 19900, price_stars: 149 },
          { days: 90, months: 3, price_minor: 49900, price_stars: 379 },
          { days: 365, months: 12, price_minor: 149000, price_stars: 1090 },
        ],
      },
    ],
  },
  referral: {
    code: "AB12CD",
    link: "https://t.me/bot_vpn4_bot?start=ref_AB12CD",
    bonus_days: 7,
    commission_percent: 10,
    invited_count: 3,
    earnings_minor: 0,
  },
  payments: {
    items: [
      { id: 3, type: "subscription_payment", status: "completed", amount_minor: 49900, currency: "RUB", method: "stars", created_at: "2026-06-10T09:30:00Z" },
      { id: 2, type: "deposit", status: "completed", amount_minor: 50000, currency: "RUB", method: "card", created_at: "2026-05-02T14:12:00Z" },
      { id: 1, type: "subscription_payment", status: "completed", amount_minor: 19900, currency: "RUB", method: "sbp", created_at: "2026-04-01T10:05:00Z" },
    ],
  },
  connection: {
    subscription_url: "https://sub.vpn.app/u/8fk2m3n9x7q1w5e8r4t6y2u9a3",
    expires_at: new Date(Date.now() + 23 * 864e5).toISOString(),
    deep_links: {
      happ: "happ://add/https://sub.vpn.app/u/8fk2...",
      v2raytun: "v2raytun://import/https://sub.vpn.app/u/8fk2...",
      hiddify: "hiddify://import/https://sub.vpn.app/u/8fk2...",
      streisand: "streisand://import/https://sub.vpn.app/u/8fk2...",
    },
    apps: [
      { key: "happ", label: "Happ", deep_link: "happ://add/https://sub.vpn.app/u/8fk2..." },
      { key: "v2raytun", label: "v2RayTun", deep_link: "v2raytun://import/https://sub.vpn.app/u/8fk2..." },
      { key: "hiddify", label: "Hiddify", deep_link: "hiddify://import/https://sub.vpn.app/u/8fk2..." },
      { key: "streisand", label: "Streisand", deep_link: "streisand://import/https://sub.vpn.app/u/8fk2..." },
    ],
  },
};

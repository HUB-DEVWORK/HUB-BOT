/**
 * Pure formatting helpers, framework-free. All templates share these so money,
 * traffic and dates look identical everywhere and match the backend's rules.
 *
 * Money is minor-units (kopeks/cents; Stars exponent 0) — see src/core/money.py.
 */
(function () {
  "use strict";
  const Cabinet = (window.Cabinet = window.Cabinet || {});

  // Minor-unit exponent per currency — mirrors core/enums.py::Currency.exponent.
  const EXPONENT = { RUB: 2, USD: 2, EUR: 2, USDT: 2, XTR: 0 };
  const SYMBOL = { RUB: "₽", USD: "$", EUR: "€", USDT: "USDT", XTR: "★" };

  const GIB = 1024 ** 3;

  function money(minor, currency = "RUB", locale) {
    const exp = EXPONENT[currency] ?? 2;
    const value = (Number(minor) || 0) / 10 ** exp;
    const loc = locale || (currency === "USD" || currency === "USDT" ? "en-US" : "ru-RU");
    const num = value.toLocaleString(loc, {
      minimumFractionDigits: exp,
      maximumFractionDigits: exp,
    });
    const sym = SYMBOL[currency] ?? currency;
    // Symbol trails for RUB/Stars, leads for USD/EUR — matches common conventions.
    if (currency === "USD" || currency === "EUR") return `${sym}${num}`;
    if (currency === "USDT") return `${num} ${sym}`;
    return `${num} ${sym}`;
  }

  function bytes(n) {
    n = Number(n) || 0;
    if (n <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
    const v = n / 1024 ** i;
    const digits = v >= 100 || i === 0 ? 0 : v >= 10 ? 1 : 2;
    return `${v.toFixed(digits)} ${units[i]}`;
  }

  function gib(n) {
    return (Number(n) || 0) / GIB;
  }

  function date(iso, locale = "ru-RU") {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return "—";
    return d.toLocaleDateString(locale, { day: "numeric", month: "long", year: "numeric" });
  }

  /** Whole days from now until `iso` (>= 0). null when no date. */
  function daysLeft(iso) {
    if (!iso) return null;
    const ms = new Date(iso).getTime() - Date.now();
    if (isNaN(ms)) return null;
    return Math.max(0, Math.ceil(ms / 86400000));
  }

  /** Percentage 0..100 of traffic used (0 when unlimited/no limit). */
  function trafficPct(usedBytes, limitBytes, unlimited) {
    if (unlimited || !limitBytes) return 0;
    return Math.min(100, Math.round((Number(usedBytes) / Number(limitBytes)) * 100));
  }

  Cabinet.fmt = { money, bytes, gib, date, daysLeft, trafficPct, EXPONENT, SYMBOL, GIB };
})();

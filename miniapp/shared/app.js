/**
 * Orchestrator: resolves locale + Telegram theme, loads the three read endpoints,
 * and normalizes them into a single camelCase view-model that every template renders.
 *
 * A template only needs: `Cabinet.boot({ render, onError })` and `Cabinet.actions.*`.
 * It never touches the raw API shapes — keeps presentation and data cleanly separated.
 */
(function () {
  "use strict";
  const Cabinet = (window.Cabinet = window.Cabinet || {});
  const { fmt, api, tg, i18n } = Cabinet;

  function resolveLocale(mePayload) {
    const q = new URLSearchParams(location.search).get("lang");
    const cand =
      q ||
      tg.userLanguage() ||
      (mePayload && mePayload.user && mePayload.user.language) ||
      "ru";
    return i18n.setLocale(cand.slice(0, 2).toLowerCase());
  }

  function discounted(minor, pct) {
    if (!pct) return minor;
    return Math.round((minor * (100 - pct)) / 100);
  }

  /** Merge the three payloads into the normalized model templates consume. */
  function buildModel(me, plans, referral) {
    const loc = i18n.getLocale();
    const dateLoc = loc === "ru" ? "ru-RU" : "en-US";
    const currency = (me.user && me.user.currency) || (plans && plans.currency) || "RUB";
    const pct = (me.user && me.user.personal_discount_pct) || 0;

    const sub = me.subscription;
    let subscription = null;
    if (sub && sub.status && sub.status !== "none") {
      const tr = sub.traffic || {};
      const daysLeft = fmt.daysLeft(sub.expire_at);
      const totalDays =
        sub.start_at && sub.expire_at
          ? Math.max(1, Math.round((new Date(sub.expire_at) - new Date(sub.start_at)) / 86400000))
          : null;
      const daysPct =
        daysLeft == null
          ? 0
          : totalDays
            ? Math.max(0, Math.min(100, Math.round((daysLeft / totalDays) * 100)))
            : Math.min(100, Math.round((daysLeft / 30) * 100));
      subscription = {
        status: sub.status,
        statusLabel: Cabinet.t("status." + sub.status),
        isTrial: !!sub.is_trial,
        planName: sub.plan_name || "—",
        startAt: sub.start_at,
        expireAt: sub.expire_at,
        expireLabel: fmt.date(sub.expire_at, dateLoc),
        daysLeft: daysLeft,
        totalDays: totalDays,
        daysPct: daysPct,
        trafficUsed: tr.used_bytes || 0,
        trafficLimit: tr.limit_bytes || 0,
        unlimited: !!tr.unlimited || !tr.limit_bytes,
        trafficUsedLabel: fmt.bytes(tr.used_bytes || 0),
        trafficLimitLabel: tr.unlimited || !tr.limit_bytes ? Cabinet.t("unlimited") : fmt.bytes(tr.limit_bytes),
        trafficPct: fmt.trafficPct(tr.used_bytes, tr.limit_bytes, tr.unlimited),
        deviceLimit: sub.device_limit,
        subscriptionUrl: sub.subscription_url || "",
        cryptoLink: sub.crypto_link || "",
        autopay: !!sub.autopay_enabled,
      };
    } else {
      subscription = { status: "none", statusLabel: Cabinet.t("status.none") };
    }

    const planItems = ((plans && plans.items) || []).map((p) => ({
      code: p.public_code,
      name: p.name,
      description: p.description || "",
      type: p.type,
      trafficLimit: p.traffic_limit_bytes,
      unlimited: !p.traffic_limit_bytes,
      trafficLabel: p.traffic_limit_bytes ? fmt.bytes(p.traffic_limit_bytes) : Cabinet.t("unlimited"),
      deviceLimit: p.device_limit,
      isCurrent: !!p.is_current,
      durations: (p.durations || []).map((d) => {
        const disc = discounted(d.price_minor, pct);
        return {
          days: d.days,
          months: Math.round(d.days / 30),
          priceMinor: d.price_minor,
          priceLabel: fmt.money(d.price_minor, currency),
          hasDiscount: disc !== d.price_minor,
          finalMinor: disc,
          finalLabel: fmt.money(disc, currency),
        };
      }),
    }));

    const ref = referral || {};
    return {
      locale: loc,
      currency,
      discountPct: pct,
      user: {
        firstName: (me.user && me.user.first_name) || "",
        username: (me.user && me.user.username) || "",
        balanceMinor: (me.user && me.user.balance_minor) || 0,
        balanceLabel: fmt.money((me.user && me.user.balance_minor) || 0, currency),
        referralCode: (me.user && me.user.referral_code) || "",
        isTrialAvailable: !!(me.user && me.user.is_trial_available),
      },
      subscription,
      plans: planItems,
      referral: {
        code: ref.code || "",
        link: ref.link || "",
        commissionPercent: ref.commission_percent || 0,
        invitedCount: ref.invited_count || 0,
        earningsMinor: ref.earnings_minor || 0,
        earningsLabel: fmt.money(ref.earnings_minor || 0, currency),
      },
    };
  }

  async function load() {
    const [me, plans, referral] = await Promise.all([api.getMe(), api.getPlans(), api.getReferral()]);
    resolveLocale(me);
    const model = buildModel(me, plans, referral);
    Cabinet.model = model;
    return model;
  }

  /**
   * Template entry point.
   * @param {(model) => void} render  called with the normalized view-model
   * @param {(err, retry) => void} [onError]
   */
  async function boot({ render, onError }) {
    tg.ready();
    tg.applyThemeVars();
    document.documentElement.lang = i18n.getLocale();
    try {
      const model = await load();
      document.documentElement.lang = model.locale;
      render(model);
    } catch (err) {
      console.error("[cabinet] load failed:", err);
      if (onError) onError(err, () => boot({ render, onError }));
    }
  }

  // ---- user actions (thin wrappers with haptics) ---------------------------
  const actions = {
    async copyLink(url) {
      const ok = await tg.copy(url);
      tg.haptic(ok ? "success" : "error");
      return ok;
    },
    openApp(cryptoLink, subUrl) {
      const link = cryptoLink || subUrl;
      tg.haptic("light");
      if (link) tg.openDeepLink(link);
    },
    shareReferral(link, text) {
      tg.haptic("light");
      tg.share(link, text);
    },
    async applyPromo(code) {
      tg.haptic("light");
      const r = await api.applyPromo(code);
      tg.haptic(r && r.ok ? "success" : "error");
      return r;
    },
    async purchase(code, days) {
      tg.haptic("light");
      const r = await api.purchase(code, days);
      if (r && r.payment_url) tg.openLink(r.payment_url);
      return r;
    },
    async resetDevices() {
      tg.haptic("warning");
      return api.resetDevices();
    },
  };

  Cabinet.load = load;
  Cabinet.boot = boot;
  Cabinet.actions = actions;
})();

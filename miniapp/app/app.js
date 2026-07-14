/* Mini-app v2 runtime: 3 tabs (Home / Connect / Account), 8 themes, RU/EN.
   Data: /api/cabinet/* with `Authorization: tma <initData>`; falls back to mock.js
   outside Telegram. Theme: admin's template (a..h) from /api/cabinet/config, override
   with ?variant= for preview; light/dark follows Telegram colorScheme. */

(function () {
  "use strict";

  const wa = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const inTg = !!(wa && wa.initData);
  const params = new URLSearchParams(location.search);
  const mock = params.get("mock") === "1" || !inTg;

  // ---------- i18n ----------
  const RU = {
    tabHome: "Главная", tabConnect: "Подключение", tabAccount: "Кабинет",
    active: "Подписка активна", inactive: "Нет подписки", trial: "Пробный период",
    daysLeft: "дней осталось", till: "до", renew: "Продлить", buy: "Купить",
    choosePlan: "Тариф", payMethod: "Оплата", refTitle: "Пригласи друга",
    refText: (d) => `+${d} дней тебе и другу`, share: "Поделиться",
    step1: "Скачай приложение", step1sub: "iOS · Android · macOS · Windows",
    download: "Скачать", step2: "Получи персональную ссылку",
    getLink: "Получить ссылку", openApp: "Открыть в приложении", copy: "Скопировать",
    copied: "Скопировано", step3: "Нажми «Подключить» в приложении",
    step3sub: "Приложение импортирует конфиг и включит защиту",
    profile: "Профиль", subscription: "Подписка", devices: "Устройства",
    myDevices: "Мои устройства",
    deviceRemoved: "Устройство отвязано",
    history: "История платежей", promo: "Промокод", promoPh: "Введи код",
    apply: "Применить", promoOk: "Промокод применён", support: "Поддержка",
    send: "Отпр.", supportPh: "Опишите вопрос…", supportHint: "Напишите нам — ответим здесь.",
    supportTyping: "печатает…", supportEscalated: "Подключаем оператора",
    balance: "Баланс", upTo: "до", noSub: "Сначала оформи подписку",
    payBalance: "С баланса", payStars: "Stars", trialBtn: "Попробовать бесплатно",
    bought: "Готово! Подписка активна", error: "Ошибка, попробуй ещё раз",
    version: "v2 · VLESS", loading: "Загрузка…",
    period: "Срок", traffic: "Трафик", unlimited: "∞ безлимит",
    soon: "Тарифы скоро появятся", soonSub: "Мы уже готовим планы — загляните позже.",
  };
  const EN = {
    ...RU,
    tabHome: "Home", tabConnect: "Connect", tabAccount: "Account",
    active: "Subscription active", inactive: "No subscription", trial: "Trial",
    daysLeft: "days left", till: "till", renew: "Renew", buy: "Buy",
    choosePlan: "Plan", payMethod: "Payment", refTitle: "Invite a friend",
    refText: (d) => `+${d} days for you and a friend`, share: "Share",
    step1: "Download the app", step1sub: "iOS · Android · macOS · Windows",
    download: "Download", step2: "Get your personal link",
    getLink: "Get link", openApp: "Open in app", copy: "Copy", copied: "Copied",
    step3: "Tap “Connect” in the app",
    step3sub: "The app imports the config and turns protection on",
    profile: "Profile", subscription: "Subscription", devices: "Devices",
    myDevices: "My devices",
    deviceRemoved: "Device unlinked",
    history: "Payment history", promo: "Promo code", promoPh: "Enter code",
    apply: "Apply", promoOk: "Promo applied", support: "Support",
    send: "Send", supportPh: "Describe your question…", supportHint: "Message us — we'll reply here.",
    supportTyping: "typing…", supportEscalated: "Connecting an operator",
    balance: "Balance", upTo: "up to", noSub: "Get a subscription first",
    payBalance: "Balance", payStars: "Stars", trialBtn: "Try for free",
    bought: "Done! Subscription is active", error: "Error, try again",
    loading: "Loading…",
    period: "Period", traffic: "Traffic", unlimited: "∞ unlimited",
    soon: "Plans coming soon", soonSub: "We're setting up plans — check back later.",
  };
  let T = RU;

  // ---------- api ----------
  function authHeaders() {
    return inTg ? { Authorization: `tma ${wa.initData}` } : {};
  }
  async function api(method, path, body) {
    if (mock) {
      const key = path.replace("/api/cabinet/", "").split("?")[0];
      await new Promise((r) => setTimeout(r, 150));
      if (method === "POST") return { ok: true };
      return window.__MOCK__[key] ?? {};
    }
    const res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error((await res.text()).slice(0, 200));
    return res.json();
  }

  // ---------- helpers ----------
  const $ = (sel) => document.querySelector(sel);
  function el(tag, attrs, kids) {
    const n = document.createElement(tag);
    if (attrs)
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else if (k === "html") n.innerHTML = v;
        else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v);
      }
    (kids || []).forEach((c) => c != null && n.append(c.nodeType ? c : String(c)));
    return n;
  }
  function toast(msg) {
    let t = $(".toast");
    if (!t) {
      t = el("div", { class: "toast" });
      document.body.append(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove("show"), 2000);
  }
  function money(minor) {
    const v = minor / 100;
    return (v % 1 ? v.toFixed(2) : v.toFixed(0)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " ₽";
  }
  function daysLeft(iso) {
    if (!iso) return null;
    return Math.max(0, Math.ceil((new Date(iso) - Date.now()) / 864e5));
  }
  function fmtDate(iso) {
    return iso ? new Date(iso).toLocaleDateString(T === RU ? "ru-RU" : "en-US", { day: "numeric", month: "long" }) : "—";
  }
  function haptic(kind) {
    try {
      if (!wa) return;
      if (kind === "ok") wa.HapticFeedback.notificationOccurred("success");
      else wa.HapticFeedback.impactOccurred("light");
    } catch {}
  }
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const ta = el("textarea", { style: "position:fixed;opacity:0" });
      ta.value = text;
      document.body.append(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    }
  }
  function detectPlatform() {
    const p = (wa && wa.platform) || "";
    if (p === "ios" || /iPhone|iPad/i.test(navigator.userAgent))
      return { name: "iOS", store: "https://apps.apple.com/app/happ-proxy-utility/id6504287215", client: "happ" };
    if (p === "android" || /Android/i.test(navigator.userAgent))
      return { name: "Android", store: "https://play.google.com/store/apps/details?id=com.happproxy", client: "happ" };
    if (/Mac/i.test(navigator.userAgent))
      return { name: "macOS", store: "https://apps.apple.com/app/happ-proxy-utility/id6504287215", client: "happ" };
    return { name: "Windows", store: "https://github.com/hiddify/hiddify-app/releases", client: "hiddify" };
  }

  // ---------- state ----------
  const state = { tab: "home", me: null, plans: null, constructor: null, referral: null, payments: null, connection: null, tariffSel: 0, planSel: 0, cPerSel: 0, cPackSel: 0, paySel: "stars", devices: undefined };
  // admin overrides: {scale, sections:[order], hidden:[keys], buttons:{key:{text,color}},
  // blocks:[{screen,title,text,icon,url,button_label,color}], buttons_extra:[{screen,label,url,color,style}]}
  let UI = {};

  function btnText(key, fallback) {
    const b = UI.buttons && UI.buttons[key];
    return (b && b.text) || fallback;
  }
  function btnStyle(key) {
    const b = UI.buttons && UI.buttons[key];
    return b && b.color ? `background:${b.color}` : "";
  }

  // Only these schemes may be opened — admin/`?ui=` links are attacker-influenceable, so
  // drop javascript:/data:/blob: etc. (defence in depth alongside the server-side validator).
  function safeUrl(u) {
    if (typeof u !== "string" || !u) return null;
    const s = u.trim();
    if (/^(https?:|tg:|mailto:|\/\/|\/)/i.test(s)) return s;  // http(s)/tg/mailto/relative only
    return null;
  }

  // Open an admin-defined link — Telegram links via the native opener, the rest in a tab.
  function openUrl(u) {
    const url = safeUrl(u);
    if (!url) return;
    haptic();
    const tg = url.startsWith("tg://") || /(?:^|\/\/)(?:t\.me|telegram\.me)\//.test(url);
    if (tg && wa && wa.openTelegramLink) wa.openTelegramLink(url);
    else if (wa && wa.openLink) wa.openLink(url);
    else window.open(url, "_blank");
  }

  // Open a VPN-client deep link (happ://, hiddify://import/, …). location.href to a custom
  // scheme is unreliable inside the Telegram WebView, so try the native opener first, then a
  // synthesised anchor click (survives the user-gesture better), then location as last resort.
  function openDeepLink(url) {
    if (!url) return;
    haptic();
    try {
      if (wa && wa.openLink) {
        wa.openLink(url);
        return;
      }
    } catch (_) {}
    try {
      const a = document.createElement("a");
      a.href = url;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (_) {
      location.href = url;
    }
  }

  // Admin custom blocks + standalone link-buttons for a given screen (home/connect/account).
  function customItems(screen) {
    const out = [];
    (UI.blocks || []).forEach((b) => {
      if ((b.screen || "home") !== screen) return;
      const kids = [];
      if (b.title) kids.push(el("b", { text: (b.icon ? b.icon + " " : "") + b.title }));
      if (b.text)
        kids.push(el("div", { class: "sub", style: "font-size:13px;margin-top:4px;white-space:pre-line", text: b.text }));
      if (b.url && b.button_label)
        kids.push(el("button", { class: "btn primary sm", style: "margin-top:12px;" + (b.color ? `background:${b.color}` : ""), onclick: () => openUrl(b.url), text: b.button_label }));
      if (kids.length) out.push(el("div", { class: "card fade" }, kids));
    });
    (UI.buttons_extra || []).forEach((x) => {
      if ((x.screen || "home") !== screen) return;
      if (!x.label || !x.url) return;
      out.push(el("button", { class: `btn ${x.style === "ghost" ? "ghost" : "primary"}`, style: x.color ? `background:${x.color}` : "", onclick: () => openUrl(x.url), text: x.label }));
    });
    return out;
  }

  // ---------- screens ----------
  function payChips(starsCount) {
    const me = state.me;
    return el("div", { class: "chips" }, [
      me && me.app.balance_enabled === false
        ? null
        : el("button", { class: `chip${state.paySel === "balance" ? " on" : ""}`, onclick: () => { state.paySel = "balance"; render(); }, text: `${T.payBalance} · ${me ? money(me.user.balance_minor) : ""}` }),
      el("button", { class: `chip${state.paySel === "stars" ? " on" : ""}`, onclick: () => { state.paySel = "stars"; render(); }, text: `⭐ ${T.payStars} · ${starsCount}` }),
      ...((me && me.app.payment_methods) || []).map((pm) =>
        el("button", {
          class: `chip${state.paySel === pm.id ? " on" : ""}`,
          onclick: () => { state.paySel = pm.id; render(); },
          text: `💳 ${pm.label}`,
        }),
      ),
    ]);
  }

  function orderSections(map) {
    const hidden = Array.isArray(UI.hidden) ? UI.hidden : [];
    const order = Array.isArray(UI.sections) && UI.sections.length
      ? UI.sections
      : ["status", "plans", "referral", "proxy", "custom"];
    const out = [];
    for (const key of order) if (map[key] && !hidden.includes(key)) out.push(...map[key]);
    for (const key of Object.keys(map)) if (!order.includes(key) && !hidden.includes(key)) out.push(...map[key]);
    return out;
  }

  function homeScreen() {
    const me = state.me;
    const sub = me && me.subscription;
    const usable = sub && ["active", "trial", "limited"].includes(sub.status);
    const left = usable ? daysLeft(sub.expire_at) : null;
    const total = 90;
    const sections = { status: [], plans: [], referral: [], proxy: [], custom: customItems("home") };
    const frag = sections.status;

    // Owner greeting (from admin config) — shown once at the very top of Home.
    const greeting = me && me.app && me.app.greeting;
    if (greeting) frag.push(el("div", { class: "card fade", text: greeting }));

    // status card
    frag.push(
      el("div", { class: "card fade" }, [
        el("div", { class: "row spread" }, [
          el("span", { class: "row", style: "gap:7px" }, [
            el("span", { class: `dot${usable ? "" : " off"}` }),
            el("b", { text: usable ? (sub.is_trial ? T.trial : T.active) : T.inactive }),
          ]),
          usable && sub.expire_at
            ? el("span", { class: "sub", style: "font-size:12.5px", text: `${T.till} ${fmtDate(sub.expire_at)}` })
            : null,
        ]),
        usable
          ? el("div", { style: "margin-top:14px" }, [
              el("div", { class: "row", style: "align-items:baseline;gap:8px" }, [
                el("span", { class: "big-num", text: left == null ? "∞" : left }),
                el("span", { class: "sub", text: T.daysLeft }),
              ]),
              el("div", { class: "prog", style: "margin-top:12px" }, [
                el("i", { style: `width:${left == null ? 100 : Math.min(100, (left / total) * 100)}%` }),
              ]),
            ])
          : el("div", { class: "sub", style: "margin-top:10px", text: T.noSub }),
        me && me.user.is_trial_available
          ? el("button", { class: "btn ghost", style: "margin-top:14px;" + btnStyle("trial"), onclick: activateTrial, text: "🎁 " + btnText("trial", T.trialBtn) })
          : null,
      ]),
    );

    // plans + pay
    const salesMode = params.get("sales") || (me && me.app.sales_mode) || "plans";
    if (salesMode === "constructor") {
      const c = state.constructor;
      const periods = (c && c.periods) || [];
      const packs = (c && c.traffic_packs) || [];
      const per = periods[state.cPerSel] || periods[0];
      const pack = packs[state.cPackSel] || packs[0];
      if (per && pack) {
        const frag = sections.plans;
        const total = per.price_minor + pack.price_minor;
        const stars = Math.max(1, Math.ceil(total / Math.max(1, c.stars_rate || 1)));
        frag.push(
          el("div", { class: "card fade" }, [
            el("div", { class: "h-cap", text: T.period }),
            el(
              "div",
              { class: "plans-row" },
              periods.map((p, i) =>
                el(
                  "div",
                  {
                    class: `plan-opt${(periods[state.cPerSel] ? state.cPerSel : 0) === i ? " on" : ""}`,
                    onclick: () => { state.cPerSel = i; haptic(); render(); },
                  },
                  [
                    el("div", { class: "m", text: p.days < 30 ? `${p.days} дн` : `${p.months} мес` }),
                    el("div", { class: "p", text: money(p.price_minor) }),
                  ],
                ),
              ),
            ),
            el("div", { class: "h-cap", style: "margin-top:14px", text: T.traffic }),
            el(
              "div",
              { class: "chips" },
              packs.map((t, i) =>
                el("button", {
                  class: `chip${(packs[state.cPackSel] ? state.cPackSel : 0) === i ? " on" : ""}`,
                  onclick: () => { state.cPackSel = i; haptic(); render(); },
                  text: (t.gb ? `${t.gb} ГБ` : T.unlimited) + (t.price_minor ? ` · +${money(t.price_minor)}` : ""),
                }),
              ),
            ),
            el("div", { class: "h-cap", style: "margin-top:14px", text: T.payMethod }),
            payChips(stars),
            el("button", {
              class: "btn primary",
              style: "margin-top:14px;" + btnStyle("renew"),
              onclick: () => submitPurchase({ period_id: per.id, pack_id: pack.id }),
              text: `${btnText("renew", usable ? T.renew : T.buy)} · ${money(total)}`,
            }),
          ]),
        );
      } else {
        // Constructor mode with no periods/packs configured yet — show a clear empty state
        // instead of a blank Home tab.
        sections.plans.push(
          el("div", { class: "card fade", style: "text-align:center" }, [
            el("div", { class: "h-cap", text: T.soon }),
            el("div", { class: "muted", style: "margin-top:6px", text: T.soonSub }),
          ]),
        );
      }
    }
    const allPlans = salesMode === "constructor" ? [] : (state.plans && state.plans.items) || [];
    const plan = allPlans[state.tariffSel] || allPlans[0];
    if (plan) {
      const frag = sections.plans;
      const durs = plan.durations;
      const selIdx = durs[state.planSel] ? state.planSel : 0;
      const sel = durs[selIdx];
      const base = durs[0] ? durs[0].price_minor / durs[0].days : 0;
      frag.push(
        el("div", { class: "card fade" }, [
          el("div", { class: "h-cap", text: T.choosePlan }),
          allPlans.length > 1
            ? el(
                "div",
                { class: "chips", style: "margin-bottom:10px" },
                allPlans.map((p, i) =>
                  el("button", {
                    class: `chip${(state.tariffSel || 0) === i ? " on" : ""}`,
                    onclick: () => { state.tariffSel = i; state.planSel = 0; haptic(); render(); },
                    text: p.name,
                  }),
                ),
              )
            : null,
          el(
            "div",
            { class: "plans-row" },
            durs.map((d, i) => {
              const disc = base ? Math.round((1 - d.price_minor / d.days / base) * 100) : 0;
              return el(
                "div",
                {
                  class: `plan-opt${i === selIdx ? " on" : ""}`,
                  style: "position:relative",
                  onclick: () => {
                    state.planSel = i;
                    haptic();
                    render();
                  },
                },
                [
                  i === 1 ? el("span", { class: "badge", text: "★" }) : null,
                  el("div", { class: "m", text: `${d.months} мес` }),
                  el("div", { class: "p", text: money(d.price_minor) }),
                  el("div", { class: "d", text: disc > 0 ? `−${disc}%` : "" }),
                ],
              );
            }),
          ),
          el("div", { class: "h-cap", style: "margin-top:14px", text: T.payMethod }),
          payChips(sel ? sel.price_stars : ""),
          el("button", { class: "btn primary", style: "margin-top:14px;" + btnStyle("renew"), onclick: () => purchase(plan, sel), text: `${btnText("renew", usable ? T.renew : T.buy)} · ${sel ? money(sel.price_minor) : ""}` }),
        ]),
      );
    }

    // referral
    if (state.referral) {
      const frag = sections.referral;
      const r = state.referral;
      frag.push(
        el("div", { class: "card fade row spread" }, [
          el("div", {}, [
            el("b", { text: "🎁 " + T.refTitle }),
            el("div", { class: "sub", style: "font-size:12.5px;margin-top:3px", text: T.refText(r.bonus_days) }),
          ]),
          el("button", {
            class: "btn primary sm",
            style: btnStyle("share"),
            onclick: () => {
              haptic();
              const url = `https://t.me/share/url?url=${encodeURIComponent(r.link)}`;
              wa && wa.openTelegramLink ? wa.openTelegramLink(url) : window.open(url);
            },
            text: btnText("share", T.share),
          }),
        ]),
      );
    }
    if (me && me.app.mtproto_proxy) {
      sections.proxy.push(
        el("div", { class: "card fade row spread" }, [
          el("b", { text: "🔌 " + (T === RU ? "MTProto-прокси" : "MTProto proxy") }),
          el("button", {
            class: "btn primary sm",
            style: btnStyle("connect_proxy"),
            onclick: () => {
              haptic();
              const u = me.app.mtproto_proxy;
              wa && wa.openTelegramLink ? wa.openTelegramLink(u) : window.open(u);
            },
            text: btnText("connect_proxy", T === RU ? "Подключить" : "Connect"),
          }),
        ]),
      );
    }
    return orderSections(sections);
  }

  function connectScreen() {
    const plat = detectPlatform();
    const conn = state.connection;
    const frag = [];
    frag.push(
      el("div", { class: "card fade" }, [
        el("div", { class: "step" }, [
          el("span", { class: "step-num", text: "1" }),
          el("div", { style: "flex:1" }, [
            el("b", { text: T.step1 }),
            el("div", { class: "sub", style: "font-size:12.5px;margin:3px 0 10px", text: T.step1sub }),
            el("button", { class: "btn ghost", onclick: () => (wa && wa.openLink ? wa.openLink(plat.store) : window.open(plat.store)), text: `${T.download} · ${plat.name}` }),
          ]),
        ]),
      ]),
    );
    frag.push(
      el("div", { class: "card fade" }, [
        el("div", { class: "step" }, [
          el("span", { class: "step-num", text: "2" }),
          el("div", { style: "flex:1" }, [
            el("b", { text: T.step2 }),
            conn
              ? el("div", { style: "margin-top:10px;display:grid;gap:9px" }, [
                  // Raw link + copy hidden when the owner enabled HIDE_SUBSCRIPTION_LINK;
                  // the one-tap import button stays so connecting still works (HIDE-1).
                  conn.hide_link
                    ? null
                    : el("div", { class: "link-box mono", text: conn.subscription_url }),
                  // One "Открыть в <App>" button per owner-enabled client (conn.apps). The user
                  // taps the app they actually have installed. Falls back to the old single
                  // button if the backend didn't send apps.
                  ...(Array.isArray(conn.apps) && conn.apps.length
                    ? conn.apps.map((a) =>
                        el("button", {
                          class: "btn primary",
                          style: btnStyle("open_app"),
                          onclick: () => openDeepLink(a.deep_link),
                          text: "⚡ " + (btnText("open_app", T.openApp) + " · " + a.label),
                        }),
                      )
                    : [
                        el("button", {
                          class: "btn primary",
                          style: btnStyle("open_app"),
                          onclick: () => {
                            const dl = conn.deep_links || {};
                            openDeepLink(dl[plat.client] || dl.happ || conn.subscription_url);
                          },
                          text: "⚡ " + btnText("open_app", T.openApp),
                        }),
                      ]),
                  conn.hide_link
                    ? null
                    : el("button", {
                        class: "btn ghost",
                        onclick: async () => {
                          (await copyText(conn.subscription_url)) && toast(T.copied);
                          haptic("ok");
                        },
                        text: T.copy,
                      }),
                ].filter(Boolean))
              : el("button", { class: "btn primary", style: "margin-top:10px;" + btnStyle("get_link"), onclick: loadConnection, text: btnText("get_link", T.getLink) }),
          ]),
        ]),
      ]),
    );
    frag.push(
      el("div", { class: "card fade" }, [
        el("div", { class: "step" }, [
          el("span", { class: "step-num", text: "3" }),
          el("div", {}, [
            el("b", { text: T.step3 }),
            el("div", { class: "sub", style: "font-size:12.5px;margin-top:3px", text: T.step3sub }),
          ]),
        ]),
      ]),
    );
    return frag.concat(customItems("connect"));
  }

  function accountScreen() {
    const me = state.me;
    if (!me) return [];
    const sub = me.subscription;
    const frag = [];
    frag.push(
      el("div", { class: "card fade row", style: "gap:12px" }, [
        el("div", {
          style:
            "width:46px;height:46px;border-radius:50%;background:var(--soft);color:var(--acc);display:grid;place-items:center;font-weight:800;font-size:17px",
          text: (me.user.first_name || "?").slice(0, 1).toUpperCase(),
        }),
        el("div", {}, [
          el("b", { text: me.user.first_name || "—" }),
          el("div", { class: "sub", style: "font-size:12.5px", text: me.user.username ? "@" + me.user.username : "" }),
        ]),
        el("div", { style: "margin-left:auto;text-align:right" }, [
          el("div", { class: "sub", style: "font-size:11px", text: T.balance }),
          el("b", { text: money(me.user.balance_minor) }),
        ]),
      ]),
    );
    frag.push(
      el("div", { class: "card fade" }, [
        el("div", { class: "li" }, [
          el("span", { class: "sub", text: T.subscription }),
          el("b", { text: sub && sub.expire_at ? `${T.till} ${fmtDate(sub.expire_at)}` : "—" }),
        ]),
        el("div", { class: "li" }, [
          el("span", { class: "sub", text: T.devices }),
          el("b", { text: sub && sub.device_limit ? `${T.upTo} ${sub.device_limit}` : "—" }),
        ]),
      ]),
    );
    // HWID devices: list + one-tap unbind (loaded lazily per tab visit)
    if (sub && sub.status && sub.status !== "none" && !mock) {
      if (state.devices === undefined) {
        state.devices = null;
        api("GET", "/api/cabinet/devices")
          .then((r) => { state.devices = r.items || []; render(); })
          .catch(() => { state.devices = []; });
      }
      if (state.devices && state.devices.length) {
        frag.push(
          el("div", { class: "card fade" }, [
            el("div", { class: "h-cap", text: T.myDevices }),
            ...state.devices.map((d) =>
              el("div", { class: "li" }, [
                el("span", { class: "sub", text: [d.platform, d.model].filter(Boolean).join(" · ") || d.hwid.slice(0, 12) }),
                el("button", {
                  class: "btn ghost sm",
                  text: "✕",
                  onclick: async () => {
                    try {
                      await api("DELETE", `/api/cabinet/devices/${encodeURIComponent(d.hwid)}`);
                      state.devices = null;
                      toast(T.deviceRemoved);
                      render();
                    } catch (e) {
                      toast(String(e.message || e));
                    }
                  },
                }),
              ]),
            ),
          ]),
        );
      }
    }
    // promo
    const inp = el("input", { class: "inp", placeholder: T.promoPh, maxlength: 32 });
    frag.push(
      el("div", { class: "card fade" }, [
        el("div", { class: "h-cap", text: T.promo }),
        el("div", { class: "row" }, [
          inp,
          el("button", {
            class: "btn primary sm",
            onclick: async () => {
              if (!inp.value.trim()) return;
              try {
                const r = await api("POST", "/api/cabinet/promocode", { code: inp.value.trim() });
                toast(r.ok ? T.promoOk : r.message || T.error);
                r.ok && haptic("ok");
                r.ok && load();
              } catch {
                toast(T.error);
              }
            },
            text: T.apply,
          }),
        ]),
      ]),
    );
    // history
    if (state.payments && state.payments.items.length) {
      frag.push(
        el("div", { class: "card fade" }, [
          el("div", { class: "h-cap", text: T.history }),
          ...state.payments.items.slice(0, 6).map((p) =>
            el("div", { class: "li" }, [
              el("span", { class: "sub", style: "font-size:12.5px", text: `${new Date(p.created_at).toLocaleDateString("ru-RU")} · ${p.method || p.type}` }),
              el("b", { text: money(p.amount_minor) }),
            ]),
          ),
        ]),
      );
    }
    if (me.app.mtproto_proxy) {
      frag.push(
        el("div", { class: "card fade row spread" }, [
          el("div", {}, [
            el("b", { text: "🔌 " + (T === RU ? "MTProto-прокси" : "MTProto proxy") }),
            el("div", { class: "sub", style: "font-size:12px;margin-top:2px",
                        text: T === RU ? "Telegram без блокировок" : "Telegram without blocks" }),
          ]),
          el("button", {
            class: "btn primary sm",
            onclick: () => {
              haptic();
              const u = me.app.mtproto_proxy;
              wa && wa.openTelegramLink ? wa.openTelegramLink(u) : window.open(u);
            },
            text: T === RU ? "Подключить" : "Connect",
          }),
        ]),
      );
    }
    // Support: inline chat (AI-backed via /api/cabinet/support; operator replies arrive here too).
    if (state.support === undefined) state.support = { messages: null, sending: false };
    if (!mock && state.support.messages === null) {
      state.support.messages = [];
      api("GET", "/api/cabinet/support")
        .then((r) => { state.support.messages = r.messages || []; render(); })
        .catch(() => {});
    }
    const supMsgs = state.support.messages || [];
    const supInp = el("input", { class: "inp", placeholder: T.supportPh, maxlength: 1000 });
    async function sendSupport() {
      const v = supInp.value.trim();
      if (!v || state.support.sending) return;
      supInp.value = "";
      state.support.messages = supMsgs.concat([{ from: "you", text: v }]);
      state.support.sending = true;
      haptic();
      render();
      try {
        const r = await api("POST", "/api/cabinet/support", { text: v });
        if (mock) {
          // Standalone preview: no backend — show a canned assistant reply.
          state.support.messages = state.support.messages.concat([
            { from: "support", text: "Спасибо за обращение! Это демо-режим — в боевом кабинете здесь ответит ИИ-поддержка." },
          ]);
        } else {
          // Live: refetch the full thread (user message + AI/operator replies) in order.
          try { state.support.messages = (await api("GET", "/api/cabinet/support")).messages || []; } catch {}
          if (r && r.ai_outcome === "escalate") toast(T.supportEscalated);
        }
      } catch (e) {
        toast((e.message || T.error).slice(0, 120));
      }
      state.support.sending = false;
      render();
    }
    frag.push(
      el("div", { class: "card fade" }, [
        el("div", { class: "h-cap", text: "🆘 " + T.support }),
        ...(supMsgs.length
          ? supMsgs.slice(-8).map((m) =>
              el("div", { style: `margin:4px 0;text-align:${m.from === "you" ? "right" : "left"}` }, [
                el("span", {
                  style:
                    "display:inline-block;max-width:85%;padding:7px 11px;border-radius:12px;" +
                    "font-size:13.5px;white-space:pre-line;text-align:left;" +
                    (m.from === "you"
                      ? "background:var(--acc);color:var(--accInk)"
                      : "background:var(--soft);color:var(--ink)"),
                  text: m.text,
                }),
              ]),
            )
          : [el("div", { class: "sub", style: "font-size:12.5px", text: T.supportHint })]),
        state.support.sending
          ? el("div", { class: "sub", style: "font-size:12px;margin-top:4px", text: T.supportTyping })
          : null,
        el("div", { class: "row", style: "margin-top:8px" }, [
          supInp,
          el("button", { class: "btn primary sm", text: T.send, onclick: sendSupport }),
        ]),
      ].filter(Boolean)),
    );
    frag.push(el("div", { class: "sub", style: "text-align:center;font-size:11px;opacity:.7", text: T.version }));
    return frag.slice(0, -1).concat(customItems("account"), frag.slice(-1));
  }

  // ---------- actions ----------
  async function purchase(plan, dur) {
    if (!dur) return;
    await submitPurchase({ plan_id: plan.id, days: dur.days });
  }

  async function submitPurchase(payload) {
    haptic();
    try {
      const method =
        state.paySel === "balance" && state.me && state.me.app.balance_enabled === false
          ? "stars"
          : state.paySel;
      const r = await api("POST", "/api/cabinet/purchase", { ...payload, method });
      if (r.redirect_url) {
        wa && wa.openLink ? wa.openLink(r.redirect_url) : window.open(r.redirect_url, "_blank");
        toast(T === RU ? "Оплати по открывшейся ссылке" : "Complete the payment in the opened page");
        setTimeout(load, 4000);
      } else if (r.invoice_link && wa && wa.openInvoice) {
        wa.openInvoice(r.invoice_link, (status) => {
          if (status === "paid") {
            toast(T.bought);
            haptic("ok");
            setTimeout(load, 1200);
          }
        });
      } else if (r.ok) {
        toast(T.bought);
        haptic("ok");
        load();
      }
    } catch (e) {
      toast((e.message || T.error).slice(0, 120));
    }
  }

  async function activateTrial() {
    haptic();
    try {
      await api("POST", "/api/cabinet/trial");
      toast(T.bought);
      haptic("ok");
      load();
    } catch (e) {
      toast((e.message || T.error).slice(0, 120));
    }
  }

  async function loadConnection() {
    haptic();
    try {
      state.connection = await api("GET", "/api/cabinet/connection");
      render();
    } catch {
      toast(T.noSub);
    }
  }

  // ---------- render ----------
  function render() {
    const screen = $("#screen");
    screen.innerHTML = "";
    const frag =
      state.tab === "home" ? homeScreen() : state.tab === "connect" ? connectScreen() : accountScreen();
    frag.filter(Boolean).forEach((n) => screen.append(n));
    document.querySelectorAll(".tabs button").forEach((b) => {
      b.classList.toggle("on", b.dataset.tab === state.tab);
    });
  }

  async function load() {
    try {
      const [me, plans, constructor, referral, payments] = await Promise.all([
        api("GET", "/api/cabinet/me"),
        api("GET", "/api/cabinet/plans"),
        api("GET", "/api/cabinet/constructor").catch(() => null), // pre-constructor backends
        api("GET", "/api/cabinet/referral"),
        api("GET", "/api/cabinet/payments"),
      ]);
      Object.assign(state, { me, plans, constructor, referral, payments });
      // Owner branding: title → document/tab title; greeting shown atop Home.
      // ?title=/?greeting= let the admin preview override the (mock) config.
      const title = params.get("title") || me.app.title;
      if (title) document.title = title;
      if (params.get("greeting") != null) me.app.greeting = params.get("greeting");
      // theme from admin config (?variant= wins for preview)
      const NAMES = { minimal: "a", private: "b", buddy: "c", native: "d",
                      terminal: "e", magazine: "f", neon: "g", pop: "h" };
      let variant = params.get("variant") || me.app.template || "a";
      variant = NAMES[variant] || variant;
      document.body.dataset.variant = /^[a-h]$/.test(variant) ? variant : "a";
      const accent = params.get("accent") || (!params.get("variant") ? me.app.accent_color : null);
      if (accent && /^#[0-9a-fA-F]{3,8}$/.test(accent)) {
        document.body.style.setProperty("--acc", accent);
      }
      try {
        UI = params.get("ui")
          ? JSON.parse(decodeURIComponent(escape(atob(params.get("ui")))))
          : (me.app.ui || {});
      } catch { UI = me.app.ui || {}; }
      if (UI.scale) document.documentElement.style.fontSize = `${(UI.scale / 100) * 100}%`;
      T = (params.get("lang") || me.user.language) === "en" ? EN : RU;
      document.documentElement.lang = T === EN ? "en" : "ru";
      render();
    } catch (e) {
      $("#screen").innerHTML = `<div class="skel">${T.error}</div>`;
    }
  }

  // ---------- boot ----------
  if (wa) {
    try {
      wa.ready();
      wa.expand();
    } catch {}
  }
  const scheme = params.get("mode") || (wa && wa.colorScheme) || "light";
  document.body.dataset.mode = scheme === "dark" ? "dark" : "light";
  if (wa && wa.onEvent) wa.onEvent("themeChanged", () => (document.body.dataset.mode = wa.colorScheme));

  document.querySelectorAll(".tabs button").forEach((b) => {
    b.addEventListener("click", () => {
      state.tab = b.dataset.tab;
      haptic();
      render();
      if (state.tab === "connect" && !state.connection && mock) state.connection = window.__MOCK__.connection, render();
    });
  });

  $("#screen").innerHTML = '<div class="skel"><div class="spinner"></div></div>';
  load();
})();

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
    history: "История платежей", promo: "Промокод", promoPh: "Введи код",
    apply: "Применить", promoOk: "Промокод применён", support: "Поддержка",
    balance: "Баланс", upTo: "до", noSub: "Сначала оформи подписку",
    payBalance: "С баланса", payStars: "Stars", trialBtn: "Попробовать бесплатно",
    bought: "Готово! Подписка активна", error: "Ошибка, попробуй ещё раз",
    version: "v2 · VLESS", loading: "Загрузка…",
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
    history: "Payment history", promo: "Promo code", promoPh: "Enter code",
    apply: "Apply", promoOk: "Promo applied", support: "Support",
    balance: "Balance", upTo: "up to", noSub: "Get a subscription first",
    payBalance: "Balance", payStars: "Stars", trialBtn: "Try for free",
    bought: "Done! Subscription is active", error: "Error, try again",
    loading: "Loading…",
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
  const state = { tab: "home", me: null, plans: null, referral: null, payments: null, connection: null, planSel: 1, paySel: "stars" };

  // ---------- screens ----------
  function homeScreen() {
    const me = state.me;
    const sub = me && me.subscription;
    const usable = sub && ["active", "trial", "limited"].includes(sub.status);
    const left = usable ? daysLeft(sub.expire_at) : null;
    const total = 90;
    const frag = [];

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
          ? el("button", { class: "btn ghost", style: "margin-top:14px", onclick: activateTrial, text: "🎁 " + T.trialBtn })
          : null,
      ]),
    );

    // plans + pay
    const plan = state.plans && state.plans.items[0];
    if (plan) {
      const durs = plan.durations;
      const sel = durs[state.planSel] || durs[0];
      const base = durs[0] ? durs[0].price_minor / durs[0].days : 0;
      frag.push(
        el("div", { class: "card fade" }, [
          el("div", { class: "h-cap", text: T.choosePlan }),
          el(
            "div",
            { class: "plans-row" },
            durs.map((d, i) => {
              const disc = base ? Math.round((1 - d.price_minor / d.days / base) * 100) : 0;
              return el(
                "div",
                {
                  class: `plan-opt${i === state.planSel ? " on" : ""}`,
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
          el("div", { class: "chips" }, [
            el("button", { class: `chip${state.paySel === "balance" ? " on" : ""}`, onclick: () => { state.paySel = "balance"; render(); }, text: `${T.payBalance} · ${me ? money(me.user.balance_minor) : ""}` }),
            el("button", { class: `chip${state.paySel === "stars" ? " on" : ""}`, onclick: () => { state.paySel = "stars"; render(); }, text: `⭐ ${T.payStars} · ${sel ? sel.price_stars : ""}` }),
          ]),
          el("button", { class: "btn primary", style: "margin-top:14px", onclick: () => purchase(plan, sel), text: `${usable ? T.renew : T.buy} · ${sel ? money(sel.price_minor) : ""}` }),
        ]),
      );
    }

    // referral
    if (state.referral) {
      const r = state.referral;
      frag.push(
        el("div", { class: "card fade row spread" }, [
          el("div", {}, [
            el("b", { text: "🎁 " + T.refTitle }),
            el("div", { class: "sub", style: "font-size:12.5px;margin-top:3px", text: T.refText(r.bonus_days) }),
          ]),
          el("button", {
            class: "btn primary sm",
            onclick: () => {
              haptic();
              const url = `https://t.me/share/url?url=${encodeURIComponent(r.link)}`;
              wa && wa.openTelegramLink ? wa.openTelegramLink(url) : window.open(url);
            },
            text: T.share,
          }),
        ]),
      );
    }
    return frag;
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
                  el("div", { class: "link-box mono", text: conn.subscription_url }),
                  el("button", {
                    class: "btn primary",
                    onclick: () => {
                      haptic();
                      location.href = conn.deep_links[plat.client] || conn.deep_links.happ;
                    },
                    text: "⚡ " + T.openApp,
                  }),
                  el("button", {
                    class: "btn ghost",
                    onclick: async () => {
                      (await copyText(conn.subscription_url)) && toast(T.copied);
                      haptic("ok");
                    },
                    text: T.copy,
                  }),
                ])
              : el("button", { class: "btn primary", style: "margin-top:10px", onclick: loadConnection, text: T.getLink }),
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
    return frag;
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
    frag.push(
      el("div", { class: "card fade row spread" }, [
        el("b", { text: "🆘 " + T.support }),
        el("button", {
          class: "btn ghost sm",
          onclick: () => {
            const bot = me.app.bot_username;
            const url = `https://t.me/${bot}`;
            wa && wa.openTelegramLink ? wa.openTelegramLink(url) : window.open(url);
          },
          text: "→",
        }),
      ]),
    );
    frag.push(el("div", { class: "sub", style: "text-align:center;font-size:11px;opacity:.7", text: T.version }));
    return frag;
  }

  // ---------- actions ----------
  async function purchase(plan, dur) {
    if (!dur) return;
    haptic();
    try {
      const r = await api("POST", "/api/cabinet/purchase", {
        plan_id: plan.id,
        days: dur.days,
        method: state.paySel === "balance" ? "balance" : "stars",
      });
      if (r.invoice_link && wa && wa.openInvoice) {
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
      const [me, plans, referral, payments] = await Promise.all([
        api("GET", "/api/cabinet/me"),
        api("GET", "/api/cabinet/plans"),
        api("GET", "/api/cabinet/referral"),
        api("GET", "/api/cabinet/payments"),
      ]);
      Object.assign(state, { me, plans, referral, payments });
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

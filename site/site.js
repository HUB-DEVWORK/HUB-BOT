/* Public site runtime: fetches /api/cabinet/public/landing, paints one of 8 themes,
   renders tariffs/features/FAQ, and points every «Личный кабинет» / buy CTA at either
   the web auth window (/web) or the Telegram bot — whatever the admin picked.
   ?variant= / ?mode= override the theme for the admin's live preview; falls back to a
   built-in demo when the API isn't reachable (standalone preview). */

(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var NAMES = { minimal: "a", private: "b", buddy: "c", native: "d", terminal: "e", magazine: "f", neon: "g", pop: "h" };

  var RU = {
    navPlans: "Тарифы", navFeatures: "Возможности", navFaq: "Вопросы", cabinet: "Личный кабинет",
    buy: "Купить подписку", statusOn: "Защита включена", daysShort: "дней", speed: "Скорость",
    plansTitle: "Тарифы", plansSub: "Оплата картой, СБП, криптой или Telegram Stars",
    featuresTitle: "Почему мы", faqTitle: "Частые вопросы", ctaBand: "Готовы подключиться?",
    choose: "Выбрать", month: "мес", perMonth: "/мес", from: "от", eyebrow: "Быстрый VPN без границ",
    heroTitle: "Свобода в сети — за одну минуту", trust1: "Без логов", trust2: "До 10 устройств", trust3: "Серверы по миру",
    heroSub: "Высокая скорость, стабильное соединение и простое подключение. Оформи подписку и подключись за пару кликов.",
  };
  var EN = {
    navPlans: "Plans", navFeatures: "Features", navFaq: "FAQ", cabinet: "Account",
    buy: "Get a plan", statusOn: "Protected", daysShort: "days", speed: "Speed",
    plansTitle: "Plans", plansSub: "Pay by card, SBP, crypto or Telegram Stars",
    featuresTitle: "Why us", faqTitle: "FAQ", ctaBand: "Ready to connect?",
    choose: "Choose", month: "mo", perMonth: "/mo", from: "from", eyebrow: "Fast VPN, no borders",
    heroTitle: "Online freedom in one minute", trust1: "No logs", trust2: "Up to 10 devices", trust3: "Servers worldwide",
    heroSub: "High speed, a stable connection and easy setup. Grab a plan and connect in a couple of clicks.",
  };
  var T = (params.get("lang") || (navigator.language || "ru").slice(0, 2)) === "en" ? EN : RU;

  var DEFAULT_FEATURES = [
    { icon: "⚡", title: RU === T ? "Максимальная скорость" : "Top speed", text: RU === T ? "Смотри видео в 4K и качай без лимитов" : "Stream 4K and download with no limits" },
    { icon: "🔒", title: RU === T ? "Никаких логов" : "No logs", text: RU === T ? "Мы не храним историю и не следим за тобой" : "We keep no history and never track you" },
    { icon: "📱", title: RU === T ? "Все устройства" : "Any device", text: RU === T ? "iOS, Android, Windows, macOS — до 10 сразу" : "iOS, Android, Windows, macOS — up to 10" },
    { icon: "🌍", title: RU === T ? "Серверы по миру" : "Global servers", text: RU === T ? "Десятки локаций, минимальный пинг" : "Dozens of locations, low ping" },
    { icon: "🛡", title: RU === T ? "Обход блокировок" : "Bypass blocks", text: RU === T ? "Современный протокол, незаметный для фильтров" : "A modern, filter-resistant protocol" },
    { icon: "💬", title: RU === T ? "Поддержка 24/7" : "24/7 support", text: RU === T ? "Ответим в Telegram в любое время" : "We answer in Telegram anytime" },
  ];
  var DEFAULT_FAQ = [
    { q: RU === T ? "Как начать?" : "How do I start?", a: RU === T ? "Нажми «Личный кабинет», оформи подписку и подключись по инструкции — займёт минуту." : "Tap Account, pick a plan and follow the setup — it takes a minute." },
    { q: RU === T ? "На скольких устройствах работает?" : "How many devices?", a: RU === T ? "Зависит от тарифа — до 10 устройств одновременно." : "Depends on the plan — up to 10 devices at once." },
    { q: RU === T ? "Какие способы оплаты?" : "Payment methods?", a: RU === T ? "Карта, СБП, криптовалюта и Telegram Stars." : "Card, SBP, crypto and Telegram Stars." },
  ];

  var DEMO = {
    enabled: true, template: "a", title: "VPN", accent_color: null, headline: "", subheadline: "",
    features: [], faq: [], cta_target: "web", bot_username: "", cabinet_url: "/web/", currency: "RUB",
    plans: [
      { name: "Старт", description: "Для одного устройства", durations: [{ months: 1, days: 30, price_minor: 19900 }, { months: 6, days: 180, price_minor: 99900 }, { months: 12, days: 365, price_minor: 179900 }] },
      { name: "Премиум", description: "5 устройств · для семьи", durations: [{ months: 1, days: 30, price_minor: 29900 }, { months: 6, days: 180, price_minor: 149900 }, { months: 12, days: 365, price_minor: 269900 }] },
    ],
  };

  var $ = function (s) { return document.querySelector(s); };
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") n.className = attrs[k];
      else if (k === "text") n.textContent = attrs[k];
      else if (k === "html") n.innerHTML = attrs[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c != null) n.append(c.nodeType ? c : String(c)); });
    return n;
  }
  function money(minor) {
    var v = minor / 100;
    return (v % 1 ? v.toFixed(2) : v.toFixed(0)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " ₽";
  }

  function applyI18n() {
    document.querySelectorAll("[data-i18n]").forEach(function (n) {
      var v = T[n.getAttribute("data-i18n")];
      if (v) n.textContent = v;
    });
    document.documentElement.lang = T === EN ? "en" : "ru";
  }

  function cabinetHref(cfg) {
    if (cfg.cta_target === "bot" && cfg.bot_username) return "https://t.me/" + cfg.bot_username;
    return cfg.cabinet_url || "/web/";
  }
  function openCabinet(href) {
    return function (e) { if (e) e.preventDefault(); window.open(href, href.charAt(0) === "/" ? "_self" : "_blank"); };
  }

  function renderPlans(cfg) {
    var grid = $("#planGrid");
    grid.innerHTML = "";
    var href = cabinetHref(cfg);
    (cfg.plans || []).forEach(function (p, idx) {
      var durs = p.durations || [];
      var state = { sel: durs.length > 1 ? 1 : 0 };
      var priceB = el("b", {});
      var perSpan = el("span", { class: "per" });
      var durRow = el("div", { class: "durs" }, durs.map(function (d, i) {
        return el("div", {
          class: "dur" + (i === state.sel ? " on" : ""),
          onclick: function () { state.sel = i; paint(); },
        }, [
          el("div", { text: (d.months || Math.round(d.days / 30)) + " " + T.month }),
          el("span", { class: "p", text: money(d.price_minor) }),
        ]);
      }));
      function paint() {
        var d = durs[state.sel] || durs[0] || { price_minor: 0, months: 1, days: 30 };
        var months = d.months || Math.round(d.days / 30) || 1;
        // marketing «per month» — round to whole rubles so it reads «от 167 ₽/мес»
        priceB.textContent = money(Math.round(d.price_minor / months / 100) * 100);
        perSpan.textContent = T.perMonth;
        [].forEach.call(durRow.children, function (c, i) { c.classList.toggle("on", i === state.sel); });
      }
      var card = el("div", { class: "card plan" + (idx === 1 ? " feat" : "") }, [
        idx === 1 ? el("span", { class: "badge", text: "★ HIT" }) : null,
        el("h3", { text: p.name }),
        el("div", { class: "desc", text: p.description || "" }),
        el("div", { class: "price" }, [priceB, perSpan]),
        durs.length > 1 ? durRow : null,
        el("div", { class: "spacer" }),
        el("a", { class: "btn primary", href: href, target: href.charAt(0) === "/" ? "_self" : "_blank", text: T.choose }),
      ]);
      paint();
      grid.append(card);
    });
  }

  function renderFeatures(cfg) {
    var grid = $("#featureGrid");
    grid.innerHTML = "";
    var items = (cfg.features && cfg.features.length) ? cfg.features : DEFAULT_FEATURES;
    items.forEach(function (f) {
      grid.append(el("div", { class: "card feat-card" }, [
        el("span", { class: "feat-ico", text: f.icon || "•" }),
        el("h3", { text: f.title || "" }),
        el("p", { text: f.text || "" }),
      ]));
    });
  }

  function renderFaq(cfg) {
    var list = $("#faqList");
    list.innerHTML = "";
    var items = (cfg.faq && cfg.faq.length) ? cfg.faq : DEFAULT_FAQ;
    items.forEach(function (f) {
      var item = el("div", { class: "card faq-item" }, [
        el("button", { class: "faq-q", text: f.q || "", onclick: function () { item.classList.toggle("open"); } }),
        el("div", { class: "faq-a", text: f.a || "" }),
      ]);
      list.append(item);
    });
  }

  function theme(cfg) {
    var v = params.get("variant") || cfg.template || "a";
    v = NAMES[v] || v;
    document.body.dataset.variant = /^[a-h]$/.test(v) ? v : "a";
    var accent = params.get("accent") || (!params.get("variant") ? cfg.accent_color : null);
    if (accent && /^#[0-9a-fA-F]{3,8}$/.test(accent)) {
      document.body.style.setProperty("--acc", accent);
      document.querySelector(".brand-mark") && (document.querySelector(".brand-mark").style.background = accent);
    }
    var mode = params.get("mode") || (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.body.dataset.mode = mode === "dark" ? "dark" : "light";
  }

  function fill(cfg) {
    var name = cfg.title || "VPN";
    document.title = name + (T === RU ? " — быстрый VPN" : " — fast VPN");
    $("#brandName").textContent = name;
    $("#footBrand").textContent = name;
    $("#heroEyebrow").textContent = cfg.greeting || T.eyebrow;
    $("#heroTitle").textContent = cfg.headline || T.heroTitle;
    $("#heroSub").textContent = cfg.subheadline || T.heroSub;
    $("#footYear").textContent = "© " + new Date().getFullYear() + " " + name;
    var trust = $("#heroTrust");
    trust.innerHTML = "";
    [T.trust1, T.trust2, T.trust3].forEach(function (x) {
      trust.append(el("span", {}, [el("span", { class: "dot" }), x]));
    });
    var href = cabinetHref(cfg);
    ["#navCabinet", "#heroCabinet", "#ctaBandBtn", "#footCabinet"].forEach(function (sel) {
      var a = $(sel);
      if (!a) return;
      a.href = href;
      a.setAttribute("target", href.charAt(0) === "/" ? "_self" : "_blank");
    });
  }

  function boot(cfg) {
    if (params.get("variant")) cfg.template = params.get("variant"); // admin preview wins
    theme(cfg);
    applyI18n();
    fill(cfg);
    renderPlans(cfg);
    renderFeatures(cfg);
    renderFaq(cfg);
  }

  // theme toggle (persisted)
  $("#themeToggle").addEventListener("click", function () {
    var next = document.body.dataset.mode === "dark" ? "light" : "dark";
    document.body.dataset.mode = next;
    try { localStorage.setItem("site_mode", next); } catch (e) {}
  });

  var saved = null;
  try { saved = localStorage.getItem("site_mode"); } catch (e) {}
  if (saved && !params.get("mode")) document.body.dataset.mode = saved;

  fetch("/api/cabinet/public/landing")
    .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
    .then(function (cfg) {
      if (cfg.enabled === false && !params.get("variant")) {
        // Landing disabled → send visitors straight to the cabinet/bot.
        location.replace(cabinetHref(cfg));
        return;
      }
      boot(cfg);
    })
    .catch(function () { boot(DEMO); });
})();

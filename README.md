<div align="center">

# 🛡 VPN Shop Constructor

**Конструктор Telegram-ботов для продажи VPN на базе [Remnawave](https://remna.st)**

Принимает оплату, выдаёт подписки, управляет пользователями — а весь бот собирается
и настраивается **через веб-кабинет, без правки кода и перезапусков**.

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](docker/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

[📖 Документация](docs/) · [🤖 Тестовый бот](https://t.me/bot_vpn4_bot) · [🖥 Демо-стенд](https://testbot.tvss-911.com/admin/) · [💬 Чат сообщества](https://t.me/vpnhub_community)

</div>

---

## 🧩 Что это?

**VPN Shop Constructor** — платформа из трёх частей поверх общего ядра:

- 🤖 **Telegram-бот** — продажи, триал, баланс, рефералка, тикеты. Меню бота
  (кнопки, цвета, вложенные экраны) собирается в конструкторе кабинета.
- 🖥 **Админ-кабинет** — веб-SPA на React: 14 разделов, «конструктор всего» —
  тарифы, акции, рассылки, пользователи, платёжки, кнопки бота, мини-аппа,
  серверы и 70+ параметров с **hot-reload**.
- 📱 **Mini App** — личный кабинет подписчика в Telegram: **8 визуальных тем**
  на выбор владельца, оплата Stars/с баланса, подключение в 3 шага.

> Синтез архитектуры [remnashop](https://github.com/snoups/remnashop) и широты
> [bedolaga](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot):
> строгое ядро в 4 кольца + прагматичный набор бизнес-фич.

<div align="center">
<img src="docs/assets/admin-dashboard.png" alt="Админ-кабинет — дашборд" width="920">
</div>

---

## ✨ Возможности

<table>
<tr>
<td width="50%" valign="top">

### 🤖 Бот

- 🧱 **Меню из конструктора** — кнопки, цвета (Bot API styles), custom emoji, вложенные экраны
- 🛒 Покупка: **с баланса** и **Telegram Stars** (нативный invoice)
- 🎁 Триал с лимитами из настроек
- 👥 Рефералка: `?start=ref_…`, бонусы обоим, % с пополнений
- 📈 Атрибуция рекламных кампаний по deep-link
- 🎫 Тикеты: текст боту → тикет в кабинете → ответ в личку
- 🔧 Режим техработ, блокировки, мультиязычность RU/EN

</td>
<td width="50%" valign="top">

### 🖥 Кабинет

- 📊 Дашборд: выручка, подписки, онлайн, аудит-лента
- 👤 Пользователи: поиск, фильтры, drawer-карточка (баланс, продление, HWID, бан)
- 💸 Тарифы + **конструктор цен** (периоды, пакеты трафика)
- 🏷 Промокоды, промогруппы, календарь акций РФ
- 📣 Рассылки с **живым прогрессом** и обходом лимитов Telegram
- ⏰ Умные напоминания о продлении
- 🌍 Серверы Remnawave: синк, нагрузка, «в продаже»
- ⚙️ 70+ настроек с **hot-reload** (секреты шифруются)

</td>
</tr>
</table>

---

## 📱 Mini App — 8 тем на выбор

Одна мини-аппа, восемь характеров: Минимал · Прайват · Бадди · Нативный ·
Терминал · Журнал · Неон · Поп. Владелец выбирает тему и акцентный цвет в кабинете —
применяется мгновенно. Светлая/тёмная — по теме Telegram.

<div align="center">
<img src="docs/assets/miniapp-minimal.png" alt="Минимал" width="23%">
<img src="docs/assets/miniapp-neon.png" alt="Неон" width="23%">
<img src="docs/assets/miniapp-terminal.png" alt="Терминал" width="23%">
<img src="docs/assets/miniapp-pop.png" alt="Поп" width="23%">
</div>

Внутри — три вкладки: **Главная** (статус, тарифы со скидками, оплата),
**Подключение** (стор по платформе → персональная ссылка → deep-link в
happ/v2raytun/hiddify/streisand), **Кабинет** (промокоды, история платежей, поддержка).

---

## 🧱 Конструктор кнопок бота

Дерево меню → редактор кнопки (тип, цвет, custom emoji) → **живое превью чата**.
Сохранил — бот уже отвечает новым меню, без перезапуска.

<div align="center">
<img src="docs/assets/admin-constructor.png" alt="Конструктор кнопок бота" width="920">
</div>

## 🎨 Выбор темы Mini App — живые превью

Каждая карточка и телефон справа — **настоящая мини-аппа** на мок-данных,
превью интерактивное.

<div align="center">
<img src="docs/assets/admin-miniapp.png" alt="Кастомизация Miniapp" width="920">
</div>

## ⚙️ Настройки с hot-reload

Человекочитаемые названия, поиск, категории. Изменения применяются сразу —
кэш конфига сбрасывается без рестарта процессов.

<div align="center">
<img src="docs/assets/admin-settings.png" alt="Настройки бота" width="920">
</div>

---

## 💳 Платёжные провайдеры

| | Провайдер | Способы | Статус |
|---|---|---|---|
| ⭐ | **Telegram Stars** | XTR-инвойсы в боте и мини-аппе | ✅ работает |
| 💼 | **Manual / баланс** | начисление админом, оплата с баланса | ✅ работает |
| 🏦 | YooKassa | карта, СБП | 🔌 UI-конфиг готов, drop-in |
| 🪙 | Cryptomus / CryptoBot | крипта | 🔌 UI-конфиг готов, drop-in |
| 💠 | Tribute | подписки | 🔌 UI-конфиг готов, drop-in |

Все провайдеры живут за одним ABC и одним вебхук-роутом — новый подключается
одним файлом ([гайд](.claude/skills/add-payment-gateway/SKILL.md)).
Комиссия каждого провайдера участвует в расчёте **чистой прибыли**
(оборот − комиссии − налог) в разделе «Платежи».

---

## 🚀 Быстрый старт

```bash
git clone https://github.com/bini69-oi/HUB-BOT.git && cd HUB-BOT
cp .env.example .env        # заполни секреты (см. комментарии в файле)
make install                # uv sync --extra dev
make up                     # postgres + redis + web + worker + scheduler (docker)
make bot                    # запустить бота (long polling)
```

Админ-кабинет: `http://localhost:8080/admin/` (логин/пароль — `ADMIN__USERNAME` /
`ADMIN__PASSWORD` из `.env`; `ADMIN__DEMO_ENABLED=true` включает публичный
read-only демо-вход одной кнопкой). Мини-аппа: `http://localhost:8080/app/?mock=1`.

Нет живой панели Remnawave? В комплекте **мок-панель** с теми же эндпоинтами:

```bash
uv run uvicorn scripts.mock_panel:app --port 3010
# .env: REMNAWAVE__BASE_URL=http://127.0.0.1:3010
```

Деплой на VPS (systemd + nginx + LE): [docs/deploy-test-server.md](docs/deploy-test-server.md).

---

## 🏗 Стек и архитектура

**Python 3.12** async · aiogram 3 · SQLAlchemy 2.0 · Alembic · PostgreSQL · Redis ·
taskiq · FastAPI · httpx — и **React 18 + TypeScript + Vite** для кабинета.
Мини-аппа — ваниль HTML/CSS/JS без сборки.

```
web/ (FastAPI: admin API, cabinet API, вебхуки)  ─┐
bot/ (aiogram)                                    ├─►  application/ (сервисы)  ─►  core/
taskiq (рассылки, напоминания, синк, бэкапы)      │         ▲
infrastructure/ (Postgres, Redis, Remnawave, платежи) ──────┘  (только через Protocol'ы)
```

Ключевые инварианты — panel-first dual-write, деньги в minor-units, идемпотентные
вебхуки, замороженные снапшоты цен — описаны в [CLAUDE.md](CLAUDE.md) и
[docs/context/](docs/context/). Тесты: `make check` (lint + mypy strict + pytest).

---

## 📚 Документация

| Файл | О чём |
|---|---|
| [docs/OVERVIEW.md](docs/OVERVIEW.md) | Карта проекта: компоненты, дерево, запуск |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Кольца, потоки данных, сущности БД |
| [docs/context/](docs/context/) | Домен Remnawave, платежи, рефералка, грабли |
| [docs/deploy-test-server.md](docs/deploy-test-server.md) | Деплой на VPS + живой стенд |
| [miniapp/CONTRACT.md](miniapp/CONTRACT.md) | Контракт cabinet API мини-аппы |
| [docs/adr/](docs/adr/) | Архитектурные решения |

---

## 💬 Сообщество

Вопросы, идеи, обмен опытом — [t.me/vpnhub_community](https://t.me/vpnhub_community).

---

<div align="center">

**MIT** · сделано для тех, кто продаёт VPN, а не настраивает ботов

</div>

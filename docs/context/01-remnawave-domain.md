# 01 — Remnawave: доменные понятия

Remnawave — самохостовая панель управления VPN. База общается с ней по HTTP API
(исходящие вызовы) и принимает от неё вебхуки (входящие события). Это критическая
внешняя система — большинство «дорогих» граблей проекта (см. `07-gotchas.md`) про неё.

## Объекты панели

- **User (panel-side)** — идентифицируется UUID; имеет `telegram_id`, `email`, `username`,
  `description`, лимит трафика (в **байтах**), стратегию трафика, дату истечения (`expire`),
  лимит устройств HWID, subscription URL, членство в squad'ах.
  **«Безлимит»** моделируется как `expire` = год 2099 / ~3650 дней и трафик `0`.
- **Internal squads** = продаваемые серверы/локации. У каждого squad'а UUID.
  Наша таблица `server_squads` зеркалит их (синк на старте) с локальной ценой, капасити,
  страной, гейтингом по промо-группе.
- **External squads** = группировки маршрутизации/выхода; подписка может нести один `external_squad` UUID.
- **Nodes** = реальные VPN-серверы; панель отдаёт статус/метрики/рестарт ноды.
  События up/down ноды приходят вебхуком.
- **HWID devices** = отпечатки устройств пользователя; панель энфорсит лимит устройств.
  **Именно поэтому** мульти-тариф требует **один panel-user на подписку** — иначе два тарифа
  схлопываются в одного user'а и делят/дерутся за наименьший HWID-лимит.
- **Subscription URL** = ссылка, которую импортирует клиент (Happ, v2rayNG и т.п.);
  revoke ссылки её ротирует (старая перестаёт работать → надо уведомить пользователя новой).

## Аутентификация к API панели (топ-1 источник падений)

Панель бывает развёрнута по-разному, поэтому auth — «движущаяся мишень». Поддерживаем:

- `Authorization: Bearer <token>` **и/или** `X-Api-Key: <token>` — **по умолчанию шлём оба**
  (разным деплоям нужно разное).
- Опционально за **Caddy** (secret-key), за **Cloudflare Access**
  (`CF-Access-Client-Id` / `CF-Access-Client-Secret`), или за nginx/Caddy secret-key **cookie**.
- **basic** auth (user/password) как отдельная стратегия.

### Local vs external — инъекция заголовков

Когда панель достаётся по «голому» http внутри docker-сети (bare host / docker service name /
приватный IP), панель со своей trust-логикой отвергнет запрос, если **не** прислать:

```
X-Forwarded-Proto: https
X-Forwarded-For: 127.0.0.1
X-Real-IP: 127.0.0.1
Host: localhost
```

плюс отключить TLS-verify. Это инкапсулируется в `ConnectionProfile`, вычисляемом один раз
при создании клиента. Внешний домен → `https`, verify on.

## Версии и capability-map

Не пиннить версию жёстко. На старте `try_connection()` пробит `get_metadata`, проверяет
версию `>= 2.8.0` и строит набор capability-флагов. Пример дрейфа:
**2.8.0 удалил `POST /system/tools/happ/encrypt`** — бизнес-код проверяет capability, а не версию.

## Вебхуки панели → бот

HMAC-валидируются секретом `REMNAWAVE__WEBHOOK_SECRET`. Типы событий:

- `user.*` — created / updated / enabled / disabled / deleted.
- `user_hwid_devices.*` — подключение устройства.
- `node.*` — up / down.
- `torrent_blocker.report` — репорт торрент-блокера.

**Грабли вебхуков:**
- `user.created` прилетает и для пользователей, которых **создавали не вы** →
  игнорировать, если не помечен `IMPORTED` (иначе двойное создание).
- `torrent_blocker.report` спамит → дедуп через Redis-лок
  `torrent_blocker_lock:{user}:{node}:{ip}` с TTL = длительность блока.
- Мелькание ноды up/down → коалесить.

## Эндпоинты, которые реально дёргают конкуренты

Клиент базы (`src/infrastructure/remnawave/client.py`) должен покрыть как минимум:

- users: `create_user`, `update_user`, `enable_user`, `disable_user`, `delete_user`,
  `get_user_by_uuid`, `get_user_by_telegram_id`, `get_user_by_email`,
  `reset_traffic`, `revoke_subscription`.
- hwid: `get_hwid_devices`, `delete_hwid_device`, `drop_connections`.
- squads: `get_internal_squads`, `get_external_squads`.
- nodes: `get_nodes`, node actions (restart/enable/disable).
- system: `get_stats`, `get_health`, `get_metadata` (версия + capability probe).

Все методы возвращают **типизированные DTO**, не сырые dict.

## Единицы и шаблоны

- Трафик: снаружи GB → внутрь **байты**.
- Безлимит: `expire` 2099 / 3650 дней, трафик 0.
- `username`/`description` панель-user'а — шаблонятся с постоянным суффиксом `short_id`
  подписки; `username` клампится по длине.

## Реальные имена полей API (проверено на живой панели)

Сверено read-only против рабочей панели (`scripts/check_panel.py`) — используй эти имена,
у клиента `_to_panel_user` уже под них выровнен:

- **User** (`GET /api/users`): `uuid`, `shortUuid` (НЕ `shortId`), `username`, `status`,
  `expireAt`, `trafficLimitBytes`, `userTraffic` (использованный трафик; НЕ `usedTrafficBytes`),
  `hwidDeviceLimit`, `subscriptionUrl`, `telegramId`, `activeInternalSquads` (список),
  `externalSquadUuid` (НЕ `activeExternalSquad`), `tag`, `trafficLimitStrategy`, `email`,
  `vlessUuid`/`trojanPassword`/`ssPassword` (секреты протоколов).
- **Internal squad** (`GET /api/internal-squads` → `response.internalSquads[]`): `uuid`, `name`,
  `info`, `inbounds`, `viewPosition`.
- **Node** (`GET /api/nodes`): `uuid`, `name`, `isConnected`, `countryCode`, `address`, `port`,
  `trafficUsedBytes`, `trafficLimitBytes`, `usersOnline`, `isDisabled`, `xrayUptime`.
- **Версия**: `GET /api/system/health` и `/api/system/stats` версию **НЕ** отдают → probe
  версии не должен ронять старт (`ensure_supported` при неизвестной версии лишь предупреждает).
- **Write-путь (create/update user) НЕ проверен** — на проде не тестировали; имена input-полей
  выровнять на тестовой панели перед провижинингом.

См. дальше: `02-subscription-lifecycle.md` — как всё это склеивается в покупку/продление/синк.

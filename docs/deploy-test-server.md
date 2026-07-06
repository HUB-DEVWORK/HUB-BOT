# Тестовый стенд — testbot.tvss-911.com

Сервер `94.183.238.41` (Ubuntu 24.04, 1 vCPU / 1 GB / 10 GB + swap). Всё живое:

| Что | Где |
|---|---|
| Админ-кабинет | https://testbot.tvss-911.com/admin/ (логин `root_admin`, пароль в `/opt/vpnshop/app/.env` → `ADMIN__PASSWORD`) |
| Мини-аппа | https://testbot.tvss-911.com/app/ (превью тем: `?mock=1&variant=a..h&mode=dark`) |
| Admin API | https://testbot.tvss-911.com/api/admin/… |
| Cabinet API | https://testbot.tvss-911.com/api/cabinet/… (auth `tma <initData>`) |
| Бот | **@bot_vpn4_bot** (long polling, menu button → мини-аппа) |
| Мок-панель Remnawave | `127.0.0.1:3010` (сабки публично: `https://testbot.tvss-911.com/sub/<short>`) |

## Раскладка на сервере

- `/opt/vpnshop/app` — код (+ `.venv`, `.env` с секретами, `admin/dist` — собранная SPA)
- `/opt/vpnshop/compose.dev.yml` — Postgres 16 + Redis 7 (docker, localhost-only)
- systemd: `vpnshop-web` (uvicorn :8000), `vpnshop-bot`, `vpnshop-worker`,
  `vpnshop-scheduler`, `vpnshop-mockpanel` (:3010) — все с `MemoryMax` под 1 GB
- nginx: 443 (LE-серт, авто-обновление certbot) → `/` → :8000, `/sub/` → :3010

## Деплой обновлений

```bash
./scripts/deploy.sh          # rsync + uv sync + alembic + restart + health
```

Репозиторий приватный, поэтому на сервер уходит rsync рабочей копии (не git pull).
SPA собирается локально (`npm run build --prefix admin`) — Node на сервере не нужен.

## Замена мок-панели на живую Remnawave

В `/opt/vpnshop/app/.env`:

```
REMNAWAVE__BASE_URL=https://panel.example.com
REMNAWAVE__TOKEN=<api token>
```

и `systemctl restart vpnshop-web vpnshop-worker vpnshop-bot`. Больше ничего не меняется —
мок реализует те же эндпоинты/схемы, что использует клиент.

## Логи

```bash
journalctl -u vpnshop-web -f      # api
journalctl -u vpnshop-bot -f      # бот
journalctl -u vpnshop-worker -f   # рассылки/бэкапы/синк
```

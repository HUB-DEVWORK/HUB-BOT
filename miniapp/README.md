# Mini-app templates

Three interchangeable **web mini-app** looks for the VPN subscriber cabinet. The VPN-service
admin picks one; end-users open it from the bot (Telegram Mini App). Same screens and data in all
three — only the visual/UX differs.

| Template | Vibe | Theme |
|---|---|---|
| **aurora** | Premium gradient / glassmorphism, liquid day-ring | light + dark |
| **slate** | Minimal, native Telegram grouped-list (adopts `themeParams`) | light + dark |
| **nebula** | Dark neon / cyber HUD, gamified, monospace numerics | dark only |

Vanilla HTML/CSS/JS — **no build step**. Telegram WebApp SDK for theme/haptics/links, with a
graceful fallback so every template also runs in a plain browser.

## Layout

```
miniapp/
├── CONTRACT.md            # the cabinet API the templates consume (source of truth)
├── templates.json         # manifest a future admin-panel reads to offer the choice
├── mock/
│   └── mock-data.js       # standalone-preview data (window.__CABINET_MOCK__)
├── shared/                # the engine — identical across templates, zero visuals
│   ├── format.js          # money (minor-units) · bytes · dates · days-left
│   ├── i18n.js            # RU/EN strings + Russian pluralization
│   ├── tg.js              # Telegram WebApp glue (+ no-op fallback)
│   ├── api.js             # contract client (live) / mock adapter (preview)
│   └── app.js             # loads /me,/plans,/referral → one normalized view-model
└── templates/
    ├── aurora/  (index.html · theme.css · view.js)
    ├── slate/   (index.html · theme.css · view.js)
    └── nebula/  (index.html · theme.css · view.js)
```

**Split of responsibility:** `shared/` fetches + normalizes data and exposes `Cabinet.boot()`,
`Cabinet.model`, `Cabinet.actions.*`, `Cabinet.t()`, `Cabinet.fmt.*`. Each template's `view.js` is
**presentation only** — it renders that model into its own DOM and wires taps to `Cabinet.actions`.
Adding a 4th look = one new folder, no engine changes.

## Preview locally

```bash
python3 -m http.server 8123 --directory miniapp
# open in a browser (mock mode kicks in automatically outside Telegram):
#   http://localhost:8123/templates/aurora/index.html
#   http://localhost:8123/templates/slate/index.html
#   http://localhost:8123/templates/nebula/index.html
```

Handy query params: `?mock=1` forces mock data, `?lang=en` / `?lang=ru` overrides the locale.

## Data flow

```
view.js ─calls→ Cabinet.boot({render})
                    │
        shared/app.js ─ GET /api/cabinet/{me,plans,referral}  (shared/api.js)
                    │        └─ no Telegram / ?mock=1 → window.__CABINET_MOCK__
                    ▼
        normalized view-model (camelCase, pre-formatted labels)
                    ▼
              render(model)  ← template paints its look
```

The contract and field-by-field mapping to the base models live in **[CONTRACT.md](CONTRACT.md)**.

## Wiring to the real backend (later)

The base ships **core only** — the cabinet API is a documented seam, not yet implemented
(see `docs/context/08-feature-matrix.md`). To go live:

1. Implement the `/api/cabinet/*` routes in `src/web/` over the existing services, validating
   Telegram `initData` (the `APP__JWT_SECRET` + `cryptography` seams already exist).
2. Serve the chosen template folder (+ `shared/` + `mock/` omitted) as static, and set the bot's
   Menu Button / WebApp URL to it. `templates.json.default` or an admin setting selects which.
3. Remove the `<script src="../../mock/mock-data.js">` line from the served `index.html`
   (or just leave it — `api.js` ignores mock once real `initData` is present).

Nothing else in the templates changes: same contract, same look.

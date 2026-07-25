# HUB-BOT — модульная система (design doc)

Статус: **черновик на утверждение**. Автор: Димка (бот). Дата: 2026-07-24.
Решение владельца: идём **вариантом 1 (запечённые модули)** с контрактом модуля,
совместимым с SoloBot, чтобы позже без переписывания добавить рантайм-установку.

---

## 1. Цель и границы

- Каждая крупная фича бота = самодостаточный **модуль** (папка): свой роутер,
  хуки, конфиг, миграция, i18n, middleware. Ядро о конкретных модулях не знает.
- **Фаза 1 (эта работа):** модули лежат в репозитории, грузятся на старте,
  включаются/выключаются конфигом. Установка нового = код + `./scripts/update.sh`.
- **Фаза 3 (потом, опционально):** `module_manager` — установка/выгрузка без
  пересборки (volume + динамическая загрузка). Требует отдельного ревью
  безопасности (запуск загруженного кода). В этот док не входит, но контракт
  проектируем так, чтобы её добавить не ломая модули.

Ориентир — SoloBot (`solo-brick`): `modules/<Name>/` с `__init__.py`
(`from .router import router`), `hooks.py` (`register_hook(...)`),
`migration.py`, `settings.py`, `texts.py`, `VERSION`. Ядро даёт шину хуков
(`register_hook` / `run_hooks` / `unregister_module_hooks`).

---

## 2. Контракт модуля

Папка `src/bot/modules/<module_name>/`:

```
__init__.py      # MANIFEST + точка входа register(app)
router.py        # aiogram Router модуля (опционально)
hooks.py         # @register_hook(...) в точки расширения ядра (опционально)
config.py        # список ParamSpec модуля (опционально)
migration.py     # идемпотентная миграция таблиц модуля (опционально)
texts.py         # i18n модуля: {"ru": {...}, "en": {...}} (опционально)
middleware.py    # middleware модуля (опционально)
VERSION          # строка версии
```

### 2.1 Манифест и точка входа

`__init__.py` объявляет `MANIFEST` и (опц.) `register()`:

```python
from src.bot.modules.api import ModuleManifest

MANIFEST = ModuleManifest(
    name="message_cleaner",          # уникальный код (== имя папки)
    title_ru="Очистка чата",
    title_en="Chat cleanup",
    version="1.0.0",
    router_priority=50,              # см. §3 (порядок роутеров)
    default_enabled=False,           # выключен, пока владелец не включит
    requires=(),                     # коды модулей-зависимостей
)

def register(reg):                   # необязательно; вызывается загрузчиком
    reg.add_router(router, priority=MANIFEST.router_priority)
    reg.add_config(CONFIG)           # ParamSpec'и модуля
    reg.add_migration(migrate)       # идемпотентная миграция
    reg.add_middleware(...)          # опц.
    # hooks.py импортируется автоматически -> @register_hook уже сработали
```

Минимальный модуль = только `router.py` + `MANIFEST`. Всё остальное опционально.

### 2.2 Флаг включения

Каждый модуль получает автоключ конфига `MODULE_<NAME>_ENABLED` (bool,
дефолт = `MANIFEST.default_enabled`). Ядро при загрузке пропускает выключенные
(роутер не включается, хуки не выполняются — см. §4).

---

## 3. Загрузка и порядок роутеров

**Проблема.** Сейчас `src/bot/handlers/__init__.py::build_router()` включает
роутеры в жёстком порядке: state-gated flows (`promo`, `withdraw`) раньше,
`reply_menu` перед `tickets`, а `actions` (catch-all `act:`) — **последним**.
Модульный роутер нельзя просто «добавить в конец» — он проиграет catch-all'у.

**Решение.** Вводим слоты приоритета. `build_router()` собирает:

```
[start, admin]                         -> ядро, приоритет ~0..10
[ module routers, отсортированные по priority ]   -> 20..80
[reply_menu]                           -> 90
[tickets]                              -> 95
[actions]  (catch-all)                 -> 100  (всегда последний)
```

- `router_priority` в манифесте задаёт место модуля в диапазоне 20..80.
- Модуль, которому нужно перехватывать до catch-all (свои `act:foo`),
  ставит специфичный `F.data.startswith("act:foo")` — он всё равно должен
  идти до `actions`, что гарантируется priority < 100.
- Загрузчик валидирует: два модуля с одинаковым priority -> стабильная
  сортировка по имени + предупреждение в лог.

Загрузчик: `src/bot/modules/loader.py`
- `discover()` — сканирует `src/bot/modules/*/`, импортирует пакет,
  читает `MANIFEST`, пропускает `default`/`api`/`loader`.
- `load(container)` — для включённых: вызывает `register()` (или дефолтную
  сборку из наличия `router`/`hooks`/`config`/`migration`), собирает роутеры,
  конфиг, миграции, хуки.
- Точка вызова: в `build_router()` (роутеры) + в `src/bot/main.py::run()`
  (миграции до старта polling, middleware) + при построении REGISTRY (конфиг).

---

## 4. Шина хуков (порт из SoloBot)

`src/bot/hooks.py` — тонкий порт SoloBot `hooks/hooks.py`:

```python
def register_hook(name, func=None): ...        # декоратор или прямой вызов
def unregister_module_hooks(module): ...        # для выгрузки (фаза 3)
async def run_hooks(name, *, require_enabled=True, **kwargs) -> list: ...
```

- `owner` хука вычисляется из `func.__module__` (`src.bot.modules.<name>`).
- `run_hooks` пропускает хуки выключенных модулей (проверка
  `MODULE_<NAME>_ENABLED`), ловит исключения и таймаут по хуку — один
  сломанный модуль не роняет ядро.
- Результаты — список truthy-возвратов (как у SoloBot).

### 4.1 Точки расширения (hook points) в HUB-BOT — стартовый набор

Ядро вызывает `run_hooks(...)` в этих местах (первые — под MVP):

| hook name         | где                              | что даёт модулю |
|-------------------|----------------------------------|-----------------|
| `main_menu`       | `menu_render.send_main_menu`     | добавить/переставить кнопки главного меню (сейчас это `SMART_EXTRAS` + ручные ноды — формализуем) |
| `admin_panel`     | `handlers/admin/home`            | пункт в админ-панели бота |
| `admin_stats`     | админ-статистика                 | свой блок цифр |
| `user_created`    | регистрация юзера                | реакция на нового пользователя |
| `purchase_paid`   | успешная оплата                  | пост-обработка покупки |
| `subscription_issued` | выдача подписки              | доп. действия при выдаче |

Дальше набор расширяем по мере переноса модулей (SoloBot их имеет ~2 десятка:
`about_menu`, `admin_user_edit`, `admin_key_edit`, …).

---

## 5. Конфиг модуля

- `REGISTRY` в `src/core/config_registry.py` сейчас — статический кортеж
  `ParamSpec`. Делаем сборку эффективного реестра через функцию:
  `get_registry() = CORE_REGISTRY + модульные specs (от включённых модулей)`.
- Модуль отдаёт свои `_p(...)`-specs в `config.py`; загрузчик добавляет их
  до первого чтения реестра (админ-UI и `bot_config.value()` их видят
  автоматически — новый раздел в конструкторе появляется сам).
- Плюс автоключ `MODULE_<NAME>_ENABLED`.

## 6. Миграции модуля

- Фаза 1 (запечённые): у модуля `migration.py::migrate(conn)` —
  **идемпотентный** `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`
  для своих таблиц. Загрузчик прогоняет их на старте (в `run()` до polling),
  фиксирует применённые версии в служебной таблице `module_migrations`
  (module, version). Так модуль не лезет в общий alembic-чейн и остаётся
  автономным — это же нужно для фазы 3.
- Общие (ядровые) таблицы по-прежнему живут в основном alembic
  (`src/infrastructure/database/migrations/versions`).

## 7. i18n модуля

- `texts.py`: `TEXTS = {"ru": {...}, "en": {...}}`. Хелпер модуля
  `t(key, lang)`; язык берём из существующего механизма бота. Ядровый
  `cabinet_text.py` не трогаем.

---

## 8. Безопасность

- Фаза 1: модули = код из нашего репозитория, доверенный (проходит ревью в PR).
  Отдельной песочницы не нужно.
- Фаза 3 (рантайм-установка): загрузка чужого кода — обязателен отдельный
  дизайн: подпись/whitelist источников, ограничение прав, ревью. **Без этого
  рантайм-менеджер не включаем.**
- Токены/ключи модули берут только через контейнер/конфиг, в лог не пишут
  (общее правило проекта).

---

## 9. MVP (первый заход)

Цель — доказать контракт на одном реальном модуле, минимально трогая ядро.

1. `src/bot/modules/api.py` — `ModuleManifest`, объект `Registrar` (add_router/
   add_config/add_migration/add_middleware).
2. `src/bot/modules/loader.py` — discover + load, интеграция в `build_router()`
   и `main.run()`.
3. `src/bot/hooks.py` — порт шины хуков.
4. Одна hook-точка вживую: `main_menu` в `menu_render.send_main_menu`.
5. **Переносим `message_cleaner`** (session-middleware + `UserMessageCleanupMiddleware`
   + 4 конфиг-ключа + экран «Очистка чата») из запечённой фичи в
   `modules/message_cleaner/` как эталон. Ключи -> `config.py` модуля,
   регистрация middleware -> `register()`.
6. `get_registry()` вместо прямого чтения `REGISTRY`.

**Критерии приёмки MVP:**
- бот стартует, `message_cleaner` работает как сейчас, но живёт в `modules/`;
- `MODULE_MESSAGE_CLEANER_ENABLED=false` полностью его отключает (роутер/
  middleware/хуки не активны), без падений;
- добавление пустого модуля-заглушки с одной кнопкой через `main_menu`-хук
  не требует правок ядра;
- все существующие тесты зелёные + тест дискавери загрузчика.

---

## 10. Этапы и объём (грубо)

- **Этап 1 (MVP, §9):** каркас + шина хуков + 1 hook-точка + перенос
  message_cleaner. Средний объём, низкий риск.
- **Этап 2:** формализуем `main_menu`/`admin_panel` полноценно, переносим
  ещё 1 модуль (кандидаты: notifications или menu-layout), расширяем hook-набор.
- **Этап 3 (опц.):** `module_manager` + рантайм-загрузка с volume и ревью
  безопасности. Крупный отдельный этап — только по явному решению.

---

## 11. Открытые вопросы (к владельцу)

1. Первый «настоящий» модуль после message_cleaner — какой нужен раньше
   (notifications / раскладка меню / что-то из SoloBot)?
2. Переносить ли часть модулей 1:1 из SoloBot (проверить лицензию solo-brick)
   или писать под HUB-BOT с нуля по образцу?
3. Нужен ли модулям свой раздел в веб-админке автоматически (список модулей +
   тумблеры), или на MVP хватит конфиг-ключей `MODULE_*_ENABLED`?

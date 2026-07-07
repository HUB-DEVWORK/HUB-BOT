"""Bot-config parameter registry — the catalog behind admin screen 13 («Настройки бота»).

Single source of truth for every hot-reloadable parameter: key, category, editor type,
default, secret flag and RU/EN display strings. The DB (``bot_config_values``) stores
only admin overrides; the settings screen and ``ConfigService`` merge the two. Adding a
parameter here requires NO migration.

Conventions:
- keys are SCREAMING_SNAKE_CASE and stable (they are the public API of the registry);
- ``secret`` params render as password inputs and are Fernet-encrypted at rest;
- money values are minor units, durations are days/hours, times are "HH:MM" MSK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.enums import ConfigCategory, ConfigParamType

BOOL = ConfigParamType.BOOL
INT = ConfigParamType.INT
STR = ConfigParamType.STR
SECRET = ConfigParamType.SECRET


@dataclass(frozen=True, slots=True)
class ParamSpec:
    key: str
    category: ConfigCategory
    type: ConfigParamType
    default: Any
    name_ru: str
    name_en: str
    desc_ru: str = ""
    desc_en: str = ""
    secret: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "secret", self.type is SECRET)


def _p(
    key: str,
    cat: ConfigCategory,
    typ: ConfigParamType,
    default: Any,
    ru: str,
    en: str,
    dru: str = "",
    den: str = "",
) -> ParamSpec:
    return ParamSpec(key, cat, typ, default, ru, en, dru, den)


C = ConfigCategory

REGISTRY: tuple[ParamSpec, ...] = (
    # --- MAIN ---------------------------------------------------------------
    _p(
        "BOT_USERNAME",
        C.MAIN,
        STR,
        "",
        "Username бота",
        "Bot username",
        "Без @; используется в ссылках (рефералка, кампании)",
        "Without @; used in deep links",
    ),
    _p(
        "ADMIN_IDS",
        C.MAIN,
        STR,
        "",
        "Администраторы",
        "Admin IDs",
        "Telegram ID через запятую — им доступна админка бота",
        "Comma-separated Telegram IDs",
    ),
    _p(
        "SUPPORT_CHAT_ID",
        C.MAIN,
        STR,
        "",
        "Группа поддержки",
        "Support chat ID",
        "ID группы, куда дублируются тикеты",
        "Group that mirrors support tickets",
    ),
    _p(
        "MAINTENANCE_MODE",
        C.MAIN,
        BOOL,
        False,
        "Режим техработ",
        "Maintenance mode",
        "Бот отвечает заглушкой всем, кроме админов",
        "Bot serves a stub to non-admins",
    ),
    _p(
        "MAINTENANCE_MESSAGE",
        C.MAIN,
        STR,
        "Ведутся технические работы, зайдите позже 🙏",
        "Текст техработ",
        "Maintenance text",
    ),
    _p(
        "LANGUAGE_SELECTION_ENABLED",
        C.MAIN,
        BOOL,
        False,
        "Выбор языка при старте",
        "Language picker on start",
        "Спрашивать язык при первом /start",
        "Ask language on first /start",
    ),
    _p("DEFAULT_LANGUAGE", C.MAIN, STR, "ru", "Язык по умолчанию", "Default language"),
    _p(
        "RULES_ACCEPT_REQUIRED",
        C.MAIN,
        BOOL,
        False,
        "Принятие правил",
        "Rules acceptance",
        "Требовать принять правила до покупки",
        "Require accepting rules before purchase",
    ),
    _p("RULES_TEXT", C.MAIN, STR, "", "Текст правил", "Rules text"),
    _p(
        "START_MESSAGE",
        C.MAIN,
        STR,
        "Привет! Это VPN-бот — выбери действие в меню.",
        "Приветствие /start",
        "/start greeting",
    ),
    _p(
        "WELCOME_IMAGE",
        C.MAIN,
        STR,
        "",
        "Лого / приветственное фото",
        "Welcome image (logo)",
        "file_id или URL картинки вверху /start. Задать из бота: /setlogo (ответом на фото).",
        "Photo file_id or URL at the top of /start. Set via the bot: /setlogo (reply to a photo).",
    ),
    _p(
        "WELCOME_STICKER",
        C.MAIN,
        STR,
        "",
        "Приветственный стикер",
        "Welcome sticker",
        "file_id стикера вверху /start. Задать из бота: /setsticker (ответом на стикер).",
        "Sticker file_id at the top of /start. Set via the bot: /setsticker (reply to a sticker).",
    ),
    _p(
        "ADMIN_PANEL_URL",
        C.MAIN,
        STR,
        "",
        "Ссылка на веб-админку",
        "Web admin URL",
        "URL веб-кабинета для кнопки в /admin боте, напр. https://…/admin/",
        "Web admin URL for the /admin button in the bot, e.g. https://…/admin/",
    ),
    _p(
        "MAIN_MENU_MODE",
        C.MAIN,
        STR,
        "inline",
        "Режим главного меню",
        "Main menu mode",
        "inline — кнопки под сообщением, reply — клавиатура",
        "inline or reply keyboard",
    ),
    _p(
        "TIMEZONE",
        C.MAIN,
        STR,
        "Europe/Moscow",
        "Часовой пояс",
        "Timezone",
        "Для расписаний рассылок и отчётов",
        "Used by schedules and reports",
    ),
    # --- SUBSCRIPTIONS & TRIAL ----------------------------------------------
    _p(
        "SALES_MODE",
        C.SUBSCRIPTIONS,
        STR,
        "plans",
        "Режим продаж",
        "Sales mode",
        "plans — готовые планы, constructor — конструктор",
        "plans or constructor",
    ),
    _p("TRIAL_ENABLED", C.SUBSCRIPTIONS, BOOL, True, "Пробный период", "Trial enabled"),
    _p("TRIAL_DURATION_DAYS", C.SUBSCRIPTIONS, INT, 3, "Длительность триала", "Trial days"),
    _p(
        "TRIAL_TRAFFIC_GB",
        C.SUBSCRIPTIONS,
        INT,
        10,
        "Трафик на триале (ГБ)",
        "Trial traffic GB",
        "0 — безлимит",
        "0 = unlimited",
    ),
    _p("TRIAL_DEVICE_LIMIT", C.SUBSCRIPTIONS, INT, 1, "Устройств на триале", "Trial devices"),
    _p(
        "TRIAL_ADD_REMAINING_DAYS_TO_PAID",
        C.SUBSCRIPTIONS,
        BOOL,
        False,
        "Перенос остатка триала",
        "Carry trial remainder",
        "Остаток триальных дней прибавляется к первой оплате",
        "Remaining trial days add to first paid period",
    ),
    _p(
        "DEFAULT_DEVICE_LIMIT", C.SUBSCRIPTIONS, INT, 3, "Устройств по умолчанию", "Default devices"
    ),
    _p(
        "DEFAULT_TRAFFIC_STRATEGY",
        C.SUBSCRIPTIONS,
        STR,
        "MONTH",
        "Сброс трафика",
        "Traffic reset",
        "NO_RESET / DAY / WEEK / MONTH — стратегия панели",
        "Panel traffic reset strategy",
    ),
    _p(
        "AUTO_RENEWAL_ENABLED",
        C.SUBSCRIPTIONS,
        BOOL,
        True,
        "Автопродление с баланса",
        "Auto-renewal",
        "Списывать с баланса до истечения",
        "Charge balance before expiry",
    ),
    _p(
        "AUTO_RENEWAL_DAYS_BEFORE",
        C.SUBSCRIPTIONS,
        INT,
        1,
        "Автопродление за (дней)",
        "Auto-renew days before",
    ),
    _p(
        "GRACE_ENABLED",
        C.SUBSCRIPTIONS,
        BOOL,
        False,
        "Grace-период",
        "Grace period",
        "Не отключать сразу: урезанный доступ на N дней",
        "Reduced access instead of cut-off",
    ),
    _p("GRACE_DAYS", C.SUBSCRIPTIONS, INT, 3, "Дней grace", "Grace days"),
    _p("GRACE_TRAFFIC_GB", C.SUBSCRIPTIONS, INT, 1, "Трафик в grace (ГБ)", "Grace traffic GB"),
    _p(
        "CONSTRUCTOR_EXTRA_DEVICE_PRICE",
        C.SUBSCRIPTIONS,
        INT,
        5000,
        "Цена доп. устройства (коп./30 дн)",
        "Extra device price (minor/30d)",
    ),
    _p("CONSTRUCTOR_MAX_DEVICES", C.SUBSCRIPTIONS, INT, 10, "Макс. устройств", "Max devices"),
    _p(
        "SUBSCRIPTION_MINI_APP_URL",
        C.SUBSCRIPTIONS,
        STR,
        "",
        "URL мини-аппы",
        "Mini-app URL",
        "Открывается кнопками «Продлить» и меню",
        "Opened by renew buttons and the menu",
    ),
    # --- PAYMENTS -------------------------------------------------------------
    _p(
        "MIN_DEPOSIT_AMOUNT",
        C.PAYMENTS,
        INT,
        5000,
        "Минимальное пополнение (коп.)",
        "Min deposit (minor)",
    ),
    _p(
        "MAX_DEPOSIT_AMOUNT",
        C.PAYMENTS,
        INT,
        10000000,
        "Максимальное пополнение (коп.)",
        "Max deposit (minor)",
    ),
    _p(
        "BALANCE_ENABLED",
        C.PAYMENTS,
        BOOL,
        True,
        "Кошелёк-баланс",
        "Wallet balance",
        "Разрешить пополнение и оплату с баланса",
        "Allow top-ups and balance payments",
    ),
    _p(
        "TAX_RATE_PERCENT",
        C.PAYMENTS,
        INT,
        6,
        "Ставка налога %",
        "Tax rate %",
        "Для расчёта чистой прибыли в разделе Платежи",
        "Feeds net-profit math",
    ),
    _p(
        "RECEIPTS_ENABLED",
        C.PAYMENTS,
        BOOL,
        False,
        "Чеки самозанятого (NaloGO)",
        "Self-employed receipts (NaloGO)",
    ),
    _p("NALOGO_INN", C.PAYMENTS, SECRET, "", "ИНН NaloGO", "NaloGO INN"),
    _p("NALOGO_PASSWORD", C.PAYMENTS, SECRET, "", "Пароль NaloGO", "NaloGO password"),
    _p(
        "STARS_RATE_RUB",
        C.PAYMENTS,
        INT,
        130,
        "Курс Stars (коп. за ★)",
        "Stars rate (minor per ★)",
        "Для пересчёта цен в Stars",
        "Converts RUB prices to Stars",
    ),
    _p(
        "PAYMENT_DESCRIPTION",
        C.PAYMENTS,
        STR,
        "Оплата VPN-подписки",
        "Назначение платежа",
        "Payment description",
    ),
    _p(
        "REFUND_ENABLED",
        C.PAYMENTS,
        BOOL,
        False,
        "Возвраты",
        "Refunds",
        "Разрешить возвраты из админки",
        "Allow refunds from the cabinet",
    ),
    # --- NOTIFICATIONS --------------------------------------------------------
    _p(
        "ADMIN_ALERTS_ENABLED",
        C.NOTIFICATIONS,
        BOOL,
        True,
        "Алерты администраторам",
        "Admin alerts",
        "Ошибки/события в личку админам",
        "DM critical events to admins",
    ),
    _p(
        "REPORT_GROUP_ID",
        C.NOTIFICATIONS,
        STR,
        "",
        "Группа отчётов",
        "Report group ID",
        "Форум-группа, куда бот пишет отчёты по топикам",
        "Forum group for topic reports",
    ),
    _p(
        "PAYMENT_NOTIFICATIONS_ENABLED",
        C.NOTIFICATIONS,
        BOOL,
        True,
        "Уведомления о платежах",
        "Payment notifications",
    ),
    _p(
        "PAYMENT_NOTIFICATIONS_TOPIC_ID",
        C.NOTIFICATIONS,
        INT,
        0,
        "Топик уведомлений о платежах",
        "Payments topic ID",
    ),
    _p(
        "REGISTRATION_NOTIFICATIONS_ENABLED",
        C.NOTIFICATIONS,
        BOOL,
        True,
        "Уведомления о регистрациях",
        "Registration notifications",
    ),
    _p(
        "EXPIRY_WARNING_DAYS",
        C.NOTIFICATIONS,
        STR,
        "3,1",
        "Предупреждение об истечении",
        "Expiry warning days",
        "За сколько дней напоминать (CSV)",
        "CSV day offsets",
    ),
    _p(
        "TRAFFIC_ALERT_PERCENT",
        C.NOTIFICATIONS,
        INT,
        80,
        "Порог уведомления о трафике %",
        "Traffic alert %",
    ),
    _p("DAILY_REPORT_ENABLED", C.NOTIFICATIONS, BOOL, True, "Ежедневный отчёт", "Daily report"),
    _p(
        "DAILY_REPORT_TIME",
        C.NOTIFICATIONS,
        STR,
        "21:00",
        "Время ежедневного отчёта",
        "Daily report time",
    ),
    _p("WEEKLY_REPORT_ENABLED", C.NOTIFICATIONS, BOOL, True, "Недельный отчёт", "Weekly report"),
    # --- REFERRAL ---------------------------------------------------------------
    _p("REFERRAL_ENABLED", C.REFERRAL, BOOL, True, "Реферальная программа", "Referral program"),
    _p(
        "REFERRAL_BONUS_RUB",
        C.REFERRAL,
        INT,
        5000,
        "Бонус за приглашённого (коп.)",
        "Invite bonus (minor)",
        "Начисляется после первой оплаты друга",
        "Granted after friend's first payment",
    ),
    _p(
        "REFERRAL_BONUS_DAYS",
        C.REFERRAL,
        INT,
        7,
        "Бонус дней обоим",
        "Bonus days for both",
        "+N дней подписки пригласившему и другу",
        "+N days to both inviter and friend",
    ),
    _p(
        "REFERRAL_WITHDRAWAL_ENABLED",
        C.REFERRAL,
        BOOL,
        False,
        "Вывод реф-заработка",
        "Referral withdrawals",
        "Кнопка «Вывести» в рефералке: заявка админу, выплата вручную",
        "«Withdraw» button: request to admin, manual payout",
    ),
    _p(
        "REFERRAL_WITHDRAWAL_MIN",
        C.REFERRAL,
        INT,
        50000,
        "Мин. сумма вывода (коп.)",
        "Min withdrawal (minor)",
        "Порог в копейках; 50000 = 500 ₽",
        "Threshold in kopeks; 50000 = 500 RUB",
    ),
    _p(
        "REFERRAL_PERCENT",
        C.REFERRAL,
        INT,
        10,
        "Процент с платежей рефералов",
        "Referral %",
        "Доля с каждого пополнения реферала",
        "Share of each referral top-up",
    ),
    _p("REFERRAL_SECOND_LEVEL_PERCENT", C.REFERRAL, INT, 0, "Процент 2-го уровня", "2nd level %"),
    _p("REFERRAL_MIN_WITHDRAWAL", C.REFERRAL, INT, 100000, "Мин. вывод (коп.)", "Min withdrawal"),
    # --- SECURITY ----------------------------------------------------------------
    _p(
        "BLACKLIST_CHECK_ENABLED",
        C.SECURITY,
        BOOL,
        False,
        "Проверка по чёрному списку",
        "Blacklist check",
        "Проверять юзеров по базе недоброжелателей",
        "Check users against the shared blacklist",
    ),
    _p(
        "BLACKLIST_UPDATE_INTERVAL_HOURS",
        C.SECURITY,
        INT,
        24,
        "Обновление чёрного списка (ч)",
        "Blacklist update interval (h)",
    ),
    _p(
        "TRAFFIC_MONITORING_ENABLED",
        C.SECURITY,
        BOOL,
        False,
        "Мониторинг аномального трафика",
        "Traffic anomaly monitoring",
    ),
    _p(
        "TRAFFIC_THRESHOLD_GB_PER_DAY",
        C.SECURITY,
        INT,
        100,
        "Порог аномалии трафика (ГБ/сут)",
        "Anomaly threshold GB/day",
    ),
    _p(
        "CHANNEL_SUB_REQUIRED",
        C.SECURITY,
        BOOL,
        False,
        "Обязательная подписка на канал",
        "Required channel subscription",
    ),
    _p("CHANNEL_SUB_ID", C.SECURITY, STR, "", "Канал для проверки", "Channel to check"),
    _p("HWID_DEVICE_LIMIT_ENABLED", C.SECURITY, BOOL, True, "Лимит HWID-устройств", "HWID limit"),
    _p("SESSION_TTL_HOURS", C.SECURITY, INT, 12, "Сессия админки (ч)", "Cabinet session TTL (h)"),
    # --- BACKUPS -------------------------------------------------------------------
    _p("BACKUP_ENABLED", C.BACKUPS, BOOL, True, "Автоматический бэкап", "Auto backup"),
    _p("BACKUP_INTERVAL_HOURS", C.BACKUPS, INT, 24, "Интервал бэкапа (ч)", "Backup interval (h)"),
    _p("BACKUP_TIME", C.BACKUPS, STR, "04:00", "Время бэкапа", "Backup time"),
    _p("BACKUP_KEEP_LAST", C.BACKUPS, INT, 7, "Хранить копий", "Keep last N"),
    _p(
        "BACKUP_SEND_TO_GROUP",
        C.BACKUPS,
        BOOL,
        True,
        "Бэкап в группу отчётов",
        "Send backup to report group",
    ),
    _p(
        "BACKUP_ENCRYPTION_PASSWORD",
        C.BACKUPS,
        SECRET,
        "",
        "Пароль шифрования бэкапа",
        "Backup encryption password",
    ),
    # --- BOT INTERFACE ---------------------------------------------------------------
    _p(
        "CONNECT_BUTTON_HAPP_REDIRECT_ENABLED",
        C.INTERFACE,
        BOOL,
        False,
        "HTTPS-редирект для Happ",
        "Happ HTTPS redirect",
        "Оборачивать happ:// в https-редирект (iOS)",
        "Wrap happ:// into an https redirect",
    ),
    _p(
        "HIDE_SUBSCRIPTION_LINK",
        C.INTERFACE,
        BOOL,
        False,
        "Скрывать ссылку подписки",
        "Hide subscription link",
        "Показывать только кнопку «Открыть в приложении»",
        "Show only the open-in-app button",
    ),
    _p(
        "SHOW_SERVER_COUNTRIES",
        C.INTERFACE,
        BOOL,
        True,
        "Показывать страны серверов",
        "Show server countries",
    ),
    _p(
        "SHOW_TRAFFIC_USAGE",
        C.INTERFACE,
        BOOL,
        True,
        "Показывать расход трафика",
        "Show traffic usage",
    ),
    _p(
        "SUPPORT_MODE",
        C.INTERFACE,
        STR,
        "tickets",
        "Канал поддержки",
        "Support mode",
        "tickets — в боте, redirect — на аккаунт, bot — отдельный бот, miniapp — чат в мини-аппе",
        "tickets / redirect / bot / miniapp",
    ),
    _p(
        "SUPPORT_REDIRECT_USERNAME",
        C.INTERFACE,
        STR,
        "",
        "Аккаунт поддержки",
        "Support account",
        "@username для режима redirect",
        "@username for redirect mode",
    ),
    _p(
        "BUTTON_COLOR_DEFAULT",
        C.INTERFACE,
        STR,
        "",
        "Цвет кнопок по умолчанию",
        "Default button color",
        "#HEX; пусто — стандартный",
        "#HEX; empty = default",
    ),
    _p(
        "MTPROTO_PROXY_ENABLED",
        C.INTERFACE,
        BOOL,
        False,
        "Кнопка MTProto-прокси",
        "MTProto proxy button",
        "Показывать кнопку прокси в боте и мини-аппе",
        "Show a proxy button in bot and mini-app",
    ),
    _p(
        "MTPROTO_PROXY_URL",
        C.INTERFACE,
        STR,
        "",
        "Ссылка MTProto-прокси",
        "MTProto proxy link",
        "t.me/proxy?server=…&port=…&secret=… (или tg://proxy?…)",
        "t.me/proxy?server=…&port=…&secret=… (or tg://proxy?…)",
    ),
)

_BY_KEY: dict[str, ParamSpec] = {p.key: p for p in REGISTRY}

# Display order of categories on the settings screen (mirrors the design).
CATEGORY_ORDER: tuple[ConfigCategory, ...] = (
    C.MAIN,
    C.SUBSCRIPTIONS,
    C.PAYMENTS,
    C.NOTIFICATIONS,
    C.REFERRAL,
    C.SECURITY,
    C.BACKUPS,
    C.INTERFACE,
)

CATEGORY_NAMES: dict[ConfigCategory, dict[str, str]] = {
    C.MAIN: {"ru": "Основные", "en": "General"},
    C.SUBSCRIPTIONS: {"ru": "Подписки и триал", "en": "Subscriptions & trial"},
    C.PAYMENTS: {"ru": "Платежи", "en": "Payments"},
    C.NOTIFICATIONS: {"ru": "Уведомления", "en": "Notifications"},
    C.REFERRAL: {"ru": "Реферальная программа", "en": "Referral"},
    C.SECURITY: {"ru": "Безопасность", "en": "Security"},
    C.BACKUPS: {"ru": "Бэкапы", "en": "Backups"},
    C.INTERFACE: {"ru": "Интерфейс бота", "en": "Bot interface"},
}


def spec(key: str) -> ParamSpec:
    """Registry lookup; raises KeyError for unknown keys (typo guard)."""
    return _BY_KEY[key]


def has(key: str) -> bool:
    return key in _BY_KEY


def coerce(key: str, raw: Any) -> Any:
    """Validate/coerce an incoming value to the parameter's declared type."""
    s = spec(key)
    if s.type is BOOL:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)
    if s.type is INT:
        return int(raw)
    return "" if raw is None else str(raw)

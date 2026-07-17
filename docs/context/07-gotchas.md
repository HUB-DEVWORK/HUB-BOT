# 07 — Грабли (реальные, из кода конкурентов + Remnawave)

Самое дорогое знание проекта. База обязана кодифицировать эти правила. Каждая строка —
«симптом → фикс».

1. **Dual-write без 2PC.** Create/update panel-user должен идти **ДО** локального коммита и
   **ВНЕ** транзакции — падение после удалённого вызова оставляет «remote orphan». Порядок
   grant→mark-issued→notify минимизирует окно; подстраховка — идемпотентная panel-write
   retry-очередь + reconcile-джоб.

2. **HWID-коллизия мульти-тарифа.** Если две подписки одного юзера маппятся на ОДНОГО
   Remnawave-user'а — они делят наименьший HWID/device-лимит и портят друг друга. Каждая
   `Subscription` = свой panel-user с постоянным уникальным суффиксом `short_id` (НЕ `f"sub{id}"`
   от мутабельного id). Энфорсить partial-unique индексом.

3. **Локальный доступ к панели по http.** Достаёшь панель по plain http внутри Docker —
   обязан инжектить `X-Forwarded-Proto=https`, `X-Forwarded-For=127.0.0.1`, `X-Real-IP=127.0.0.1`,
   часто `Host: localhost` + TLS-verify off, иначе proxy/trust-логика панели отвергнет.
   **Топ-1 причина падений подключения.**

4. **Auth панели — движущаяся мишень.** Поддержи `api_key`/`bearer`/`basic`/`caddy` + CF-Access +
   secret-key cookie. По умолчанию шли **и** `X-Api-Key`, **и** `Authorization: Bearer`.

5. **Дрейф версий Remnawave.** 2.8.0 убрал `POST /system/tools/happ/encrypt`. Никогда не пиннить
   жёстко — пробить версию на старте и гейтить фичи через capability-map. Таргет 2.8.0+.

6. **Идемпотентность вебхука платежа — обязательна.** Дубли/поздние/переупорядоченные колбэки —
   норма. CAS-переход статуса с `allowed_from` + UNIQUE(`external_id`, `method`) + `SELECT FOR UPDATE`.
   Никогда не фулфиллить инлайн — enqueue и вернуть 200 быстро (Telegram/шлюзы ретраят на не-200).

7. **Дрейф float у денег.** Целые minor-units (копейки/центы) внутри везде; `Decimal`
   (ROUND_HALF_UP) только на границе шлюза. Крипта/Stars → `skip_amount_check`-толеранс FX.

8. **Замороженные снапшоты обязательны.** Хранить `plan_snapshot` + `pricing` JSONB на **и**
   транзакции, **и** подписке — иначе правка каталога между инвойсом и оплатой выдаст не то.
   Промокодам нужен свой снапшот.

9. **Кварки подписи вебхука.** Прокси (Cloudflare) переписывают тело → ломается HMAC-over-raw-body.
   Верифицировать с несколькими fallback-реселиализациями JSON (compact/reserialized/ascii).
   YooKassa — не общий секрет, а **allowlist source-IP**: держать CIDR-список + учёт trusted-proxy CF.

10. **Telegram Stars — особый.** HTTP-вебхука нет — подтверждение через in-bot `successful_payment`;
    `pre_checkout_query` ответить за секунды; тест-покупки владельца авто-рефандить
    (`refund_star_payment`) и помечать `CANCELED`.

11. **initData stale-auth баг.** Telegram кэширует WebApp initData, `auth_date` может протухнуть.
    Валидировать HMAC(bot_token, 'WebAppData'), но давать окно clock-skew / max-age (~600s), иначе
    легитимные юзеры отваливаются.

12. **Флуд вебхуков Telegram.** Обрабатывать апдейты под `asyncio.Semaphore` (~100) с трекингом
    background-задач; secret-token заголовок сверять `secrets.compare_digest` (constant-time).

13. **Реферал at-most-once.** EXTRA_DAYS-выплата падает, если у реферера нет активной платной
    подписки — обработать мягко (эмитить событие-провал, не крашить). Флаг-леджер `is_issued`,
    платить на топап (или на первый платёж по стратегии), чтобы не задвоить на ретрае вебхука.

14. **Стакинг и одноразовость скидок.** `purchase_discount` потребляется (сброс в 0) на следующей
    платной покупке; `personal_discount` персистит; комбинированную скидку капать 100% и роутить
    100%-покупки через free-path мимо шлюза.

15. **Safety-rails конфига.** Отвергать плейсхолдер-секреты `change_me`; требовать 44-символьный
    base64 Fernet crypt-key; не переиспользовать `APP_CRYPT_KEY` как JWT-secret/API-key; шифровать
    креды шлюзов at-rest (settings JSONB) и прокидывать crypt-key в Alembic для миграций
    шифрованных колонок.

16. **Порядок сидинга на старте.** Создать дефолтные settings + дефолтные шлюзы **закоммиченными**
    до обслуживания первого запроса, иначе `get()` закэширует незакоммиченный синглтон.
    Синкнуть squads/servers и тарифы из панели/конфига на буте.

17. **Корректность таймзон.** Все datetime — UTC-aware (кастомный `AwareDateTime` TypeDecorator +
    коэрсинг), иначе сравнения молча ломаются; Alembic гонять с `transaction_per_migration` и
    asyncio.run-in-thread обёрткой, чтобы уживалось со структурным логированием.

18. **Спам вебхука torrent-blocker.** Дедупить повторные репорты Redis-локом
    `torrent_blocker_lock:{user}:{node}:{ip}` TTL = длительность блока; коалесить мелькание ноды up/down.

19. **Шум `user.created`.** Панель эмитит `user.created` и для юзеров, которых создавали не вы —
    игнорировать, если не помечен `IMPORTED`, иначе двойное создание. Импортированных/3x-ui юзеров
    узнавать по `tag == 'IMPORTED'`.

20. **Ловушка CORS-credentials.** `APP_ORIGINS='*'` выключает cookie-credentials для кабинета —
    для JWT-cookie auth нужны явные origins.

21. **Грабли revoke sub-link / IP-block (из прод-топологии).** Revoke подписки или IP-блок юзера
    может молча порвать активные соединения; `drop_connections` должен бить по всем нодам, а revoke
    ротирует sub-URL — уведомить юзера новой ссылкой.

22. **aiogram-dialog + taskiq.** Фоновым джобам, стартующим диалоги, нужен инжект DI-контейнера в
    воркер и пере-регистрация UserMiddleware+роутеров на dispatcher воркера (BgManagerFactory
    эмитит `AIOGD_UPDATE`), иначе bg-стартованные диалоги падают.

## Импорт из remnawave-shopbot (`shopbot_import.py`)

- Источник — SQLite `users.db`; **деньги float-рублями** — только `Decimal(str(x))`,
  иначе копеечные хвосты. Даты — naive MSK-строки (легаси-строки бывают UTC, ±3ч терпимо).
- Panel-uuid **адоптируются как есть** (`vpn_keys.remnawave_user_uuid` → наш
  `subscription.remnawave_uuid`) — панель не трогаем, подписчики работают сквозь переезд.
- Идемпотентность: юзер по `telegram_id`, подписка по `remnawave_uuid`, транзакция по
  `external_id`, промокод по `code`. Баланс НЕ перезаписывается при повторном импорте.
- `referred_by` у шопбота — telegram_id без FK: линкуем вторым проходом через
  `ReferralService.bind` (иначе комиссии не будут платиться).

## Вывод рефералки (`withdrawal_requests`)

- Деньги **списываются при создании заявки** (guarded debit) — pending-заявку нельзя
  потратить дважды; reject возвращает на баланс. «Доступно» = min(баланс,
  заработано по `referral_earnings` − уже выведенное/зарезервированное).

## Промокоды применяются мгновенно

- Все награды (баланс / скидка / дни / подписка) применяются в момент ввода кода —
  в т.ч. DURATION/SUBSCRIPTION через panel-first renew/grant. Отдельного
  «промокода в чекауте» нет: one-shot `purchase_discount` подхватится следующей покупкой.
- Gift-диплинк `?start=gift_<CODE>` — тот же движок (лимиты/срок/уникальность на юзера).

## Порядок способов оплаты (`PAYMENT_METHOD_ORDER`)
`core/payment_order.py::order_rank` — общий стабильный сорт для бота (`purchase._payment_methods`)
и мини-аппа (`cabinet.py` отдаёт `payment_order` + `pay_balance_label`/`pay_stars_label`). Id:
`balance`, `stars`, `<gateway type value>`. Незаданные идут после в дефолтном порядке.

## Оплата = продлить, не дублировать (1.2.14/1.4.0)
`resolve_purchase_type`: если у юзера есть подписка с `remnawave_uuid` (даже EXPIRED/DISABLED или
миграционная `plan_id=NULL`) — RENEW/CHANGE, а не NEW. `fulfill()` ПЕРЕ-резолвит тип под
`lock_for_update` (не доверяет замороженному `txn.purchase_type`), иначе поздний вебхук/Stars
плодит дубль-аккаунт на панели. Возврат подписки с баланса зачисляет деньги обратно на баланс.

## Обновления: не кирпичить (1.4.0)
Миграции с UNIQUE-индексом ОБЯЗАНЫ дедуплицировать до `create_index` (иначе `upgrade head` падает
→ web в цикле → обновление ложится). `update.sh` не называет профильные сервисы (caddy) в `up`
явно. Вебхук панели fail-closed при пустом секрете. `act_cabinet` чистит FSM (иначе «Промокод»→
«Назад» съедает след. сообщение). Кнопка-ссылка без схемы деградирует в bounce, не ломает меню.

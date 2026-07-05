# 04 — Рефералка, промокоды, промо-группы, скидки

Мат-модель монетизации/роста. Все суммы — в целых minor-units.

## Рефералы (two-level)

- Два уровня: `FIRST` / `SECOND`.
- `referral_code` уникален на пользователя; ловится из диплинка `?start=ref_<code>` на первом `/start`.
- pending-referral хранится в **Redis (TTL)** пока приглашённый не зарегистрируется, затем биндится
  (`referred_id` **UNIQUE** = один реферер на пользователя).
- **Комиссия платится с ПОПОЛНЕНИЯ** (модель bedolaga, лучше всего ложится на баланс-кошелёк):
  - первый платёж → процент первого платежа;
  - последующие → тирный recurring % (`{порог: процент}` по числу оплативших рефералов) или флэт %;
  - опц. флэт-бонус пригласителю + бонус за первый топап (гейт по мин. сумме);
  - число комиссионных на реферала — капается.
- Каждая выплата пишет `ReferralEarning(is_issued)` + `Transaction(referral_reward)`.
- Награды типа EXTRA_DAYS **мягко** падают, если у реферера нет активной платной подписки
  (эмитим событие-провал, не крашимся).
- Опц.: вывод/кэшаут с кулдауном + AML-скоринг риска (отложить).

**Идемпотентность выплат:** флаг `is_issued` в леджере + пороги — чтобы ретрай вебхука
не задвоил выплату (at-most-once).

## Промокоды

- `reward_type`: `balance` / `duration` / `traffic` / `devices` / `subscription` /
  `promo_group` / `personal_discount` / `purchase_discount`.
- `availability`: `all` / `new` / `existing` / `invited`.
- флаги: `first_purchase_only`, `max_activations`, `is_reusable`.
- активация — per-user **UNIQUE** (`promocode_activations`: UNIQUE(promocode_id, user_id)).
- **Порядок применения:** награда сначала применяется к **панели** (duration/traffic/devices
  прибавляются к текущей подписке, `0` = безлимит; subscription свапает план или создаёт user'а;
  discount ставит % на user'а), **затем** активация сохраняется атомарно.
- промокоду с наградой-подпиской нужен собственный `plan_snapshot`.

## Промо-группы

- Приоритетные тиры скидок (server / traffic / device / period %), связь M2M с пользователями.
- Эффективная группа = **наивысший приоритет**.
- Авто-назначение по суммарным тратам (`auto_assign_total_spent_minor`).
- `apply_discounts_to_addons` — распространять ли скидки на докупки.

## Скидки — правила стакинга

- `personal_discount` — **персистит**.
- `purchase_discount` — **одноразовый** (сбрасывается в 0 на следующей платной покупке).
- Комбинированная скидка капается **100%**; при 100% покупка идёт по **free-path** мимо шлюза.

## Формула цены (упрощённо)

```
base       = plan_price(currency, duration) + squads_addons + traffic_addons + device_addons
promo_pct  = effective_promo_group.discount_for(component)      # по компонентам
disc_pct   = min(100, promo_pct + personal_discount + purchase_discount)
final      = round_half_up(base * (100 - disc_pct) / 100)       # в minor-units
if final == 0: free-path (skip gateway)
```

Реализуется в `PricingService`; порядок компонентов и что скидывать (аддоны или нет) —
управляется промо-группой (`apply_discounts_to_addons`). Референс: bedolaga
`app/services/pricing_engine.py`.

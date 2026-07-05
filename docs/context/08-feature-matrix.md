# 08 — Feature-matrix: remnashop vs bedolaga → наша база

Сравнение + решение для базы. «База» ниже = что закладываем в ядро (без бота/миниаппы).

| Фича | remnashop | bedolaga | Наша база |
|---|---|---|---|
| Стиль архитектуры | Строгая Clean (4 кольца), CQRS Interactor, Dishka, UoW+DAO | Прагматичная слоистость (handlers/services/crud), ручной DI | **Кольца+протоколы remnashop**, но сервисы вместо Interactor-per-file |
| Панель | Remnawave (remnapy SDK), пин 2.7.x | Remnawave 2.8.0+, свой клиент | Свой типизированный клиент, **capability-probe** (не пин) |
| Деньги | — | Целые копейки везде | **Целые minor-units** + Decimal на границе |
| Подписки | Тарифы, длительности, цены (нормализовано) | Мульти-тариф (panel-user на подписку), дневной биллинг | Нормализовано + **panel-user на подписку** + snapshot |
| Trial | Есть | Есть + трекинг конверсии | Trial + `SubscriptionConversion` |
| Платежи | 15 шлюзов, 1 ABC, 1 роут, DB-config | 24+ через миксин | **Дизайн remnashop** (ABC/роут/DB-config) + saved-cards/autopay от bedolaga; старт: `manual`+`stars` |
| Идемпотентность | CAS + unique | CAS + unique + FOR UPDATE + snapshot | **Двойная** (payment_id CAS + external_id unique) + FOR UPDATE |
| Рефералы | Есть | С пополнений, тиры, выводы, AML, партнёрка | **С пополнений** (bedolaga), леджер `is_issued`, at-most-once; выводы/AML — отложить |
| Промокоды | Есть | 8 типов наград, availability, лимиты | Полный набор типов + per-user unique |
| Промо-группы | — | Приоритетные тиры, авто-назначение по тратам | **Есть** (сегментация) |
| Скидки | personal/purchase | personal(persist)/purchase(one-shot) | personal persist + purchase one-shot + кап 100%→free |
| Мульти-сервер | squads | per-squad цены/капасити/гейтинг | `server_squads` зеркало + цена/капасити/гейтинг |
| i18n | Fluent (.ftl), EN/RU | JSON, правимые оверрайды | **JSON-loader** (bedolaga) + отложенный рендер событий (remnashop), EN/RU |
| Уведомления | notification queue | per-topic роутинг в админ-чат | Очередь + per-topic роутинг |
| Админка | dashboard-роутеры бота | Полный RBAC+ABAC web-админ, audit-log | RBAC (Role→Permission) в ядре; web-админ — позже |
| Миниаппа/кабинет | Web API (initData/OAuth/email→JWT), фронт не в репо | React+TS SPA + Cabinet API | **Швы** под initData-auth; сам кабинет/миниаппа — позже |
| Бэкапы | — | pyzipper шифрованные, в админ-чат | Шифрованные бэкапы (taskiq) |
| Аналитика | statistics use-cases | Конверсии, кампании | События+хуки; дашборды — позже |
| Геймификация | — | Колесо/опросы/конкурсы | Не в базе (знать о наличии) |
| CMS | — | Новости/FAQ/лендинги/гостевые | Не в базе; контент-таблицы в `settings`/позже |
| Налоговые чеки | — | NaloGO (РФ) | Хук после completion (реализация позже) |
| Фон | taskiq worker+scheduler, SmartRetry | APScheduler-синглтоны | **taskiq** worker+scheduler + panel-write retry queue |
| Деплой | Docker uv, 4 контейнера, webhook-only | Единый порт FastAPI | Docker uv; web+worker+scheduler; polling **и** webhook |
| Миграции | Alembic 40 | Alembic 94 | Alembic (async env, `transaction_per_migration`, seq-sync) |

## Итог

- **Скелет** — от remnashop (дисциплина, тестируемость).
- **Мясо** — от bedolaga (баланс, промо-группы, мульти-тариф, рефералка с пополнений, бэкапы).
- **База ≠ весь bedolaga:** геймификация, CMS, партнёрка, AML, web-админ, миниаппа — **поверх** базы,
  позже. Ядро даёт для них швы (события, протоколы, RBAC, DI).

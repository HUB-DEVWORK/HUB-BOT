---
name: add-db-model
description: Add a new SQLAlchemy model + DAO + migration to the base. Use when asked to "add a table/model", "new entity", "добавить модель/таблицу в БД".
---

# Добавить модель БД

Postgres-first, но переносимо (тесты гоняют sqlite). Правила — `docs/context/07-gotchas.md`.

## Шаги

1. **Модель.** Новый файл `src/infrastructure/database/models/<name>.py`. Наследуй `Base`
   (+ `IntPk`, `TimestampMixin` при нужде) из `src/infrastructure/database/base.py`.
   - Деньги → `BigInt`, суффикс `_minor` (целые minor-units, ADR-0002).
   - Даты → `AwareDateTime` (UTC-aware, gotcha #17). Никаких наивных `datetime`.
   - JSON/массивы → `JsonB` (JSONB на PG, JSON на sqlite). UUID → `Uuid()`.
   - Enum-колонки → `Enum(MyEnum, native_enum=False, length=N)` (переносимо, без PG-enum-типов).
   - Partial-unique индексы — через `Index(..., postgresql_where=text(...), sqlite_where=text(...))`.

2. **Регистрация.** Добавь импорт + в `__all__` в `src/infrastructure/database/models/__init__.py`
   (иначе миграция/`create_all` не увидят таблицу).

3. **DAO.** Тонкий `class <Name>DAO(BaseDAO[<Model>])` в `src/infrastructure/database/dao/`
   (или в `catalog.py` для справочников). Добавь доменные запросы при необходимости.

4. **UnitOfWork.** Заведи атрибут в `src/infrastructure/database/uow.py` (`self.<name>s = <Name>DAO(session)`).

5. **Миграция.** `make revision m="add <name>"` при живом Postgres → **глазами проверь диф** →
   `make migrate`. (Autogenerate против PG корректнее рукописной — JSONB/partial-index.)

6. **Тест/фабрика.** При необходимости — helper в `tests/factories.py` и тест на новый флоу.

## Проверка
`make check`. Тесты используют `Base.metadata.create_all` на aiosqlite — держи модель переносимой
(кастомные типы с sqlite-вариантом), иначе тесты отвалятся.

"""In-bot admin panel: access gate, input parsing, crafted-callback hardening.

No DB here — these assert the pure guards/parsers and that malformed callback_data
answers the spinner instead of raising (same contract as test_bot_callback_guards).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.bot.handlers.admin import promos, users
from src.bot.handlers.admin._common import ClearStaleForm, IsAdmin, parse_ints, rub


class _FakeCb:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answers: list[tuple[Any, dict[str, Any]]] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


# --- access gate --------------------------------------------------------------


async def test_isadmin_filter_reflects_flag() -> None:
    f = IsAdmin()
    assert await f(object(), is_admin=True) is True
    assert await f(object(), is_admin=False) is False
    assert await f(object()) is False  # default: closed


class _FakeState:
    def __init__(self) -> None:
        self.cleared = False

    async def clear(self) -> None:
        self.cleared = True


async def test_clear_stale_form_clears_pending_state() -> None:
    mw = ClearStaleForm()

    async def _handler(event: Any, data: dict[str, Any]) -> str:
        return "ok"

    # A callback press (this mw is wired to callback_query only) abandons any pending
    # form -> state cleared before the handler runs, so a later stray number can't be
    # booked as a balance change.
    st = _FakeState()
    assert await mw(_handler, _FakeCb("admin:u:card:1"), {"state": st}) == "ok"
    assert st.cleared is True

    # No state in context -> no crash, handler still runs.
    assert await mw(_handler, _FakeCb("admin:menu"), {}) == "ok"


# --- helpers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "count", "expected"),
    [
        ("30", 1, [30]),
        ("30 100", 2, [30, 100]),
        ("30 100", 1, None),  # too many
        ("30", 2, None),  # too few
        ("30 abc", 2, None),  # non-numeric
        ("", 1, None),
        ("  ", 1, None),
        ("-5", 1, [-5]),  # sign parsed; range checks live in handlers
    ],
)
def test_parse_ints(text: str, count: int, expected: list[int] | None) -> None:
    assert parse_ints(text, count) == expected


def test_reward_value_balance_is_minor_units() -> None:
    assert promos._reward_value("balance", 500) == 50000  # ₽ -> minor
    assert promos._reward_value("days", 30) == 30  # days as-is
    assert promos._reward_value("trial", 7) == 7


def test_rub_formatting() -> None:
    assert rub(50000) == "500 ₽"
    assert rub(123456) == "1 235 ₽"


# --- crafted callbacks never raise --------------------------------------------


@pytest.mark.parametrize("data", ["admin:promo:k:bogus", "admin:promo:k:"])
async def test_promo_kind_bad_kind_answers(data: str) -> None:
    cb = _FakeCb(data)
    await promos.promo_kind(cb, state=object())  # type: ignore[arg-type]
    assert cb.answers == [(None, {})]


@pytest.mark.parametrize("data", ["admin:gift:k:bogus", "admin:gift:k:"])
async def test_gift_kind_bad_kind_answers(data: str) -> None:
    cb = _FakeCb(data)
    await promos.gift_kind(cb, state=object())  # type: ignore[arg-type]
    assert cb.answers == [(None, {})]


@pytest.mark.parametrize("data", ["admin:sale:t:abc", "admin:sale:t:"])
async def test_sale_toggle_bad_id_answers(data: str) -> None:
    cb = _FakeCb(data)
    await promos.sale_toggle(cb, container=object())  # type: ignore[arg-type]
    assert cb.answers == [(None, {})]


@pytest.mark.parametrize(
    "data",
    ["admin:u:bal:add", "admin:u:bal:add:abc", "admin:u:bal:xxx:1"],
)
async def test_balance_ask_bad_data_answers(data: str) -> None:
    cb = _FakeCb(data)
    await users.balance_ask(cb, state=object())  # type: ignore[arg-type]
    assert cb.answers == [(None, {})]


@pytest.mark.parametrize("data", ["admin:u:ext:abc:30", "admin:u:ext:1", "admin:u:ext:1:x"])
async def test_user_extend_bad_data_answers(data: str) -> None:
    cb = _FakeCb(data)
    await users.user_extend(cb, container=object())  # type: ignore[arg-type]
    assert cb.answers == [(None, {})]


@pytest.mark.parametrize("data", ["admin:u:ban:abc", "admin:u:ban:", "admin:u:card:xyz"])
async def test_user_ban_and_card_bad_id_answers(data: str) -> None:
    cb = _FakeCb(data)
    handler = users.user_ban if ":ban:" in data else users.user_card
    await handler(cb, container=object())  # type: ignore[arg-type]
    assert cb.answers == [(None, {})]

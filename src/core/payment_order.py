"""Shared payment-method ordering.

The bot payment screen and the mini-app both honour the operator's ``PAYMENT_METHOD_ORDER``
config (a comma-separated list of method ids, e.g. ``yookassa, balance, stars``). Ids listed
there come first, in that order; everything else keeps its original (default) order — Python's
``sorted``/``list.sort`` is stable, so equal-rank methods don't reshuffle.
"""

from __future__ import annotations

from collections.abc import Callable


def order_rank(order_csv: str) -> Callable[[str], int]:
    """Return a sort-key function ``method_id -> rank``; unlisted ids sort last, keeping order."""
    order = [s.strip().lower() for s in order_csv.split(",") if s.strip()]
    rank = {name: i for i, name in enumerate(order)}
    return lambda method_id: rank.get(method_id.lower(), len(order))

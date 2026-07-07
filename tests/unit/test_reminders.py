"""_human_hours: Russian noun agreement for the {time} placeholder."""

from __future__ import annotations

import pytest

from src.infrastructure.taskiq.tasks import _human_hours


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (0, "момент"),
        (1, "1 час"),
        (2, "2 часа"),
        (4, "4 часа"),
        (5, "5 часов"),
        (11, "11 часов"),
        (12, "12 часов"),
        (14, "14 часов"),
        (21, "21 час"),
        (22, "22 часа"),
        (24, "24 часа"),
    ],
)
def test_human_hours(hours: int, expected: str) -> None:
    assert _human_hours(hours) == expected

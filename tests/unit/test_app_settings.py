"""AppSettings.owner_ids parsing — must accept any shape an env/.env/installer emits.

Regression: the installer wrote APP__OWNER_IDS as a bare number (898…), but pydantic-settings
JSON-decoded it to an int and failed "not a list". NoDecode + this validator fix it.
"""

from __future__ import annotations

import pytest

from src.core.config.app import AppSettings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("898123456", [898123456]),  # bare number (the installer's output)
        ("898,123", [898, 123]),  # comma-separated
        ("898 123", [898, 123]),  # space-separated
        ("[897]", [897]),  # JSON list string
        ("[898, 123]", [898, 123]),  # JSON list with spaces
        ("", []),  # empty (operator skipped their id)
        ("   ", []),  # whitespace only
        (897, [897]),  # a lone int passed programmatically
        (None, []),
        ([1, 2], [1, 2]),  # already a list
    ],
)
def test_owner_ids_accepts_every_shape(raw: object, expected: list[int]) -> None:
    assert AppSettings(owner_ids=raw).owner_ids == expected  # type: ignore[arg-type]

"""Used-traffic parsing across the panel-version zoo.

The wire shape of a user's used traffic changed across Remnawave releases: modern
panels nest it in a ``userTraffic`` object, older ones send a plain number, the
oldest spell it ``trafficUsedBytes``/``usedTrafficBytes`` at the top level, and
numbers sometimes arrive as JSON strings. The live-traffic refresh writes whatever
we parse straight into the DB, so a missed shape doesn't just render wrong — it
overwrites good webhook-written values with 0 (the "трафик не отслеживается" bug).
"""

from src.infrastructure.remnawave.client import _used_bytes


def test_modern_object_shape() -> None:
    data = {"userTraffic": {"usedTrafficBytes": 5368709120, "lifetimeUsedTrafficBytes": 9999}}
    assert _used_bytes(data) == 5368709120


def test_modern_object_shape_with_string_numbers() -> None:
    data = {"userTraffic": {"usedTrafficBytes": "5368709120"}}
    assert _used_bytes(data) == 5368709120


def test_legacy_plain_number() -> None:
    assert _used_bytes({"userTraffic": 3221225472}) == 3221225472


def test_legacy_top_level_spellings() -> None:
    assert _used_bytes({"usedTrafficBytes": "1073741824"}) == 1073741824
    assert _used_bytes({"trafficUsedBytes": 42}) == 42


def test_unknown_object_keys_fall_back_to_top_level() -> None:
    data = {"userTraffic": {"up": 10, "down": 20}, "usedTrafficBytes": 777}
    assert _used_bytes(data) == 777


def test_zero_and_garbage_stay_zero() -> None:
    assert _used_bytes({"userTraffic": {"usedTrafficBytes": 0}}) == 0
    assert _used_bytes({"userTraffic": None}) == 0
    assert _used_bytes({}) == 0
    assert _used_bytes({"userTraffic": "not-a-number"}) == 0

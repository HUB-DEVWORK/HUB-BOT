"""3x-ui importer: synthetic x-ui.db -> panel users are CREATED + local schema, idempotently."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from src.application.dto.panel import PanelUser, ProvisionSpec
from src.application.services.threexui_import import (
    ThreexuiImportService,
    _short_id,
    probe,
    read_source,
)
from src.core.enums import SubscriptionStatus
from src.core.exceptions import RemnawaveError
from src.infrastructure.database.uow import UnitOfWork
from tests.fakes import FakeRemnawaveClient

VLESS_UUID = "0f9a87b2-51c3-4b0e-9a3f-6f0d9c1e2a3b"
NOW_MS = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
DAY_MS = 86_400_000
GIB = 1024**3


class _RecordingPanel(FakeRemnawaveClient):
    """FakeRemnawaveClient + captured specs (PanelUser does not carry ``extra``)."""

    def __init__(self) -> None:
        super().__init__()
        self.specs: list[ProvisionSpec] = []

    async def create_user(self, spec: ProvisionSpec) -> PanelUser:
        self.specs.append(spec)
        return await super().create_user(spec)


class _RejectingPanel(FakeRemnawaveClient):
    async def create_user(self, spec: ProvisionSpec) -> PanelUser:
        raise RemnawaveError("A019 username already exists")


def _client(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "",
        "password": "",
        "flow": "",
        "email": "",
        "limitIp": 0,
        "totalGB": 0,
        "expiryTime": 0,
        "enable": True,
        "subId": "",
        "comment": "",
        "reset": 0,
    }
    base.update(kw)
    return base


def _make_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE inbounds (
            id INTEGER PRIMARY KEY, user_id INTEGER, up INTEGER, down INTEGER, total INTEGER,
            remark TEXT, enable INTEGER, expiry_time INTEGER, listen TEXT, port INTEGER,
            protocol TEXT, settings TEXT, stream_settings TEXT, tag TEXT, sniffing TEXT
        );
        CREATE TABLE client_traffics (
            id INTEGER PRIMARY KEY, inbound_id INTEGER, enable INTEGER, email TEXT,
            up INTEGER, down INTEGER, expiry_time INTEGER, total INTEGER, reset INTEGER
        );
        """
    )
    vless_clients = [
        # tgId as int; shares subId with the trojan "alice-tr" -> ONE group, vless is primary
        _client(
            id=VLESS_UUID,
            email="alice@xui",
            tgId=111,
            subId="duo-sub-1",
            expiryTime=NOW_MS + 30 * DAY_MS,
            totalGB=10 * GIB,
        ),
        # tgId as string; expiryTime 0 -> never -> 2099 sentinel
        _client(email="bob", tgId="222", subId="bobsub", expiryTime=0),
        # tgId absent -> without_telegram; negative expiry -> countdown from migration
        _client(email="carol", subId="carolsub", expiryTime=-7 * DAY_MS),
        # already expired but enabled -> EXPIRED
        _client(email="dave", tgId=444, subId="davesub", expiryTime=NOW_MS - 5 * DAY_MS),
        # JSON still holds the countdown, client_traffics carries the materialized deadline
        _client(email="frank.x", tgId=666, subId="franksub", expiryTime=-DAY_MS),
    ]
    trojan_clients = [
        _client(
            password="sharedtrojan99",
            email="alice-tr",
            subId="duo-sub-1",
            expiryTime=NOW_MS + 20 * DAY_MS,
            totalGB=2 * GIB,
        ),
        # disabled -> DISABLED; trojan password >= 8 chars -> forwarded to the panel
        _client(password="evepassword99", email="eve", tgId=555, subId="evesub", enable=False),
    ]
    conn.executemany(
        "INSERT INTO inbounds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                1,
                1,
                0,
                0,
                0,
                "DE-Frankfurt",
                1,
                0,
                "",
                443,
                "vless",
                json.dumps({"clients": vless_clients, "decryption": "none"}),
                "{}",
                "in-1",
                "{}",
            ),
            (
                2,
                1,
                0,
                0,
                0,
                "NL-Amsterdam",
                1,
                0,
                "",
                8443,
                "trojan",
                json.dumps({"clients": trojan_clients}),
                "{}",
                "in-2",
                "{}",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO client_traffics VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, 1, 1, "alice@xui", 100, 200, NOW_MS + 30 * DAY_MS, 10 * GIB, 0),
            (2, 2, 1, "alice-tr", 10, 20, NOW_MS + 20 * DAY_MS, 2 * GIB, 0),
            (3, 1, 1, "bob", 0, 0, 0, 0, 0),
            (4, 1, 1, "carol", 0, 0, -7 * DAY_MS, 0, 0),  # not started -> still negative
            (5, 1, 1, "dave", 5, 5, NOW_MS - 5 * DAY_MS, 0, 0),
            (6, 1, 1, "frank.x", 1, 1, NOW_MS + 10 * DAY_MS, 0, 0),  # materialized, wins
            # eve has no traffic row on purpose
        ],
    )
    conn.commit()
    conn.close()


async def test_import_and_idempotent_rerun(uow: UnitOfWork, tmp_path: Path) -> None:
    db = tmp_path / "x-ui.db"
    _make_source(db)

    data = read_source(db)
    assert probe(data) == {
        "ok": True,
        "counts": {
            "inbounds": 2,
            "clients": 7,
            "groups": 6,
            "with_telegram": 5,
            "active": 4,  # dave expired, eve disabled
        },
    }
    assert probe({"inbounds": 0, "clients": [], "groups": []})["ok"] is False

    panel = _RecordingPanel()
    service = ThreexuiImportService(panel)
    async with uow:
        summary = await service.run(uow, data, squad_uuid="sq-test-1")
        await uow.commit()

    assert summary["users_created"] == 6
    assert summary["users_updated"] == 0
    assert summary["subscriptions"] == 6
    assert summary["panel_users_created"] == 6
    assert summary["without_telegram"] == 1  # carol
    assert summary["skipped"] == []
    assert len(panel.users) == 6  # one panel user per group

    specs = {spec.short_id: spec for spec in panel.specs}
    duo = specs["duo-sub-1"]  # primary = vless client, not the trojan one
    assert duo.telegram_id == 111
    assert duo.username == "alice_xui"
    assert duo.traffic_limit_bytes == 10 * GIB
    assert duo.internal_squads == ("sq-test-1",)
    assert duo.description == "3x-ui: alice@xui / DE-Frankfurt"
    assert duo.extra["tag"] == "XUI_IMPORT"
    assert duo.extra["shortUuid"] == "duo-sub-1"
    assert duo.extra["vlessUuid"] == VLESS_UUID
    assert duo.extra["status"] == "ACTIVE"
    assert specs["bobsub"].expire_at.year == 2099  # 0 = never
    assert specs["davesub"].extra["status"] == "EXPIRED"
    eve = specs["evesub"]
    assert eve.extra["status"] == "DISABLED"
    assert eve.extra["trojanPassword"] == "evepassword99"
    assert "vlessUuid" not in eve.extra
    assert specs["carolsub"].telegram_id is None

    async with uow:
        alice = await uow.users.find_one(telegram_id=111)
        assert alice is not None and alice.is_trial_available is False
        duo_sub = await uow.subscriptions.find_one(short_id="duo-sub-1")
        assert duo_sub is not None
        assert duo_sub.status is SubscriptionStatus.ACTIVE
        assert duo_sub.traffic_limit_bytes == 10 * GIB  # totalGB IS bytes, max over the group
        assert duo_sub.traffic_used_bytes == 330  # up+down summed over both clients
        assert duo_sub.remnawave_uuid is not None
        assert duo_sub.subscription_url == "https://panel.test/sub/duo-sub-1"
        assert duo_sub.internal_squads == ["sq-test-1"]
        assert duo_sub.plan_snapshot["source"] == "3x-ui"
        assert alice.current_subscription_id == duo_sub.id

        bob = await uow.users.find_one(telegram_id=222)  # tgId arrived as a string
        assert bob is not None
        bob_sub = await uow.subscriptions.find_one(short_id="bobsub")
        assert bob_sub is not None and bob_sub.expire_at is not None
        assert bob_sub.expire_at.year == 2099

        carol_sub = await uow.subscriptions.find_one(short_id="carolsub")
        assert carol_sub is not None and carol_sub.expire_at is not None
        countdown = carol_sub.expire_at - dt.datetime.now(dt.UTC)
        assert dt.timedelta(days=6, hours=23) < countdown < dt.timedelta(days=7, minutes=5)
        assert carol_sub.status is SubscriptionStatus.ACTIVE
        carol = await uow.users.get(carol_sub.user_id)
        assert carol is not None and carol.telegram_id is None

        dave_sub = await uow.subscriptions.find_one(short_id="davesub")
        assert dave_sub is not None and dave_sub.status is SubscriptionStatus.EXPIRED
        eve_sub = await uow.subscriptions.find_one(short_id="evesub")
        assert eve_sub is not None and eve_sub.status is SubscriptionStatus.DISABLED

        frank_sub = await uow.subscriptions.find_one(short_id="franksub")
        assert frank_sub is not None and frank_sub.expire_at is not None
        materialized = dt.datetime.fromtimestamp((NOW_MS + 10 * DAY_MS) / 1000, tz=dt.UTC)
        assert abs((frank_sub.expire_at - materialized).total_seconds()) < 1
        assert frank_sub.status is SubscriptionStatus.ACTIVE

        assert await uow.users.count() == 6

    # Re-run: subs refreshed in place, panel NOT called again, balance untouched.
    async with uow:
        alice = await uow.users.find_one(telegram_id=111)
        assert alice is not None
        alice.balance_minor = 777  # simulate post-import spending
        await uow.commit()
    async with uow:
        summary2 = await service.run(uow, read_source(db), squad_uuid="sq-test-1")
        await uow.commit()
    assert summary2["users_created"] == 0
    assert summary2["users_updated"] == 6
    assert summary2["panel_users_created"] == 0
    assert len(panel.specs) == 6  # create_user not called again
    assert len(panel.users) == 6
    async with uow:
        assert len(list(await uow.subscriptions.list())) == 6
        assert await uow.users.count() == 6
        alice = await uow.users.find_one(telegram_id=111)
        assert alice is not None and alice.balance_minor == 777


def _make_limit_source(path: Path) -> None:
    """Clients carrying per-client ``limitIp`` device caps, incl. a subId-less one."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE inbounds (
            id INTEGER PRIMARY KEY, user_id INTEGER, up INTEGER, down INTEGER, total INTEGER,
            remark TEXT, enable INTEGER, expiry_time INTEGER, listen TEXT, port INTEGER,
            protocol TEXT, settings TEXT, stream_settings TEXT, tag TEXT, sniffing TEXT
        );
        CREATE TABLE client_traffics (
            id INTEGER PRIMARY KEY, inbound_id INTEGER, enable INTEGER, email TEXT,
            up INTEGER, down INTEGER, expiry_time INTEGER, total INTEGER, reset INTEGER
        );
        """
    )
    clients = [
        # one group (shared subId "grpA"): 0 (unlimited) dominates the concrete cap 4
        _client(id=VLESS_UUID, email="capped@x", subId="grpA", limitIp=4),
        _client(email="unlim@x", subId="grpA", limitIp=0),
        # one group (shared subId "grpB"): no zero -> max of the concrete caps
        _client(email="b3@x", subId="grpB", limitIp=3),
        _client(email="b7@x", subId="grpB", limitIp=7),
        # NO subId -> short_id must derive from the stable email key for idempotent re-runs
        _client(email="noid@x", subId="", limitIp=2),
    ]
    conn.execute(
        "INSERT INTO inbounds VALUES (1,1,0,0,0,'DE',1,0,'',443,'vless',?,'{}','in-1','{}')",
        (json.dumps({"clients": clients, "decryption": "none"}),),
    )
    conn.commit()
    conn.close()


async def test_device_limit_and_subidless_idempotency(uow: UnitOfWork, tmp_path: Path) -> None:
    db = tmp_path / "x-ui-limits.db"
    _make_limit_source(db)

    panel = _RecordingPanel()
    service = ThreexuiImportService(panel)
    async with uow:
        summary = await service.run(uow, read_source(db), squad_uuid="sq-lim")
        await uow.commit()

    assert summary["panel_users_created"] == 3  # grpA, grpB, noid
    specs = {spec.short_id: spec for spec in panel.specs}
    assert specs["grpA"].device_limit == 0  # 0 (unlimited) dominates the concrete cap 4
    assert specs["grpB"].device_limit == 7  # no zero -> max concrete cap
    noid_short = _short_id("noid@x")  # deterministic, derived from the stable email key
    assert noid_short in specs
    assert specs[noid_short].device_limit == 2

    async with uow:
        grpa_sub = await uow.subscriptions.find_one(short_id="grpA")
        assert grpa_sub is not None and grpa_sub.device_limit == 0
        grpb_sub = await uow.subscriptions.find_one(short_id="grpB")
        assert grpb_sub is not None and grpb_sub.device_limit == 7
        noid_sub = await uow.subscriptions.find_one(short_id=noid_short)
        assert noid_sub is not None and noid_sub.device_limit == 2

    # Re-run: the subId-less group is refreshed in place, panel NOT called again.
    async with uow:
        summary2 = await service.run(uow, read_source(db), squad_uuid="sq-lim")
        await uow.commit()
    assert summary2["panel_users_created"] == 0
    assert summary2["users_updated"] == 3
    assert len(panel.users) == 3
    async with uow:
        assert len(list(await uow.subscriptions.list())) == 3


async def test_panel_rejection_skips_group(uow: UnitOfWork, tmp_path: Path) -> None:
    db = tmp_path / "x-ui.db"
    _make_source(db)

    service = ThreexuiImportService(_RejectingPanel())
    async with uow:
        summary = await service.run(uow, read_source(db))
        await uow.commit()

    assert summary["users_created"] == 0
    assert summary["subscriptions"] == 0
    assert summary["panel_users_created"] == 0
    assert len(summary["skipped"]) == 6
    assert all("панель отклонила" in reason for reason in summary["skipped"])
    async with uow:
        assert await uow.users.count() == 0


def _make_v3_source(path: Path) -> None:
    """3x-ui >= 3.x: clients live in normalized tables; settings JSON has no clients."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE inbounds (
            id INTEGER PRIMARY KEY, remark TEXT, protocol TEXT, settings TEXT
        );
        CREATE TABLE client_traffics (
            id INTEGER PRIMARY KEY, inbound_id INTEGER, enable BOOLEAN, email TEXT,
            up INTEGER, down INTEGER, expiry_time INTEGER, total INTEGER, reset INTEGER
        );
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY, email TEXT, sub_id TEXT, uuid TEXT, password TEXT,
            total_gb INTEGER, expiry_time INTEGER, enable BOOLEAN, tg_id INTEGER,
            comment TEXT
        );
        CREATE TABLE client_inbounds (
            client_id INTEGER, inbound_id INTEGER, flow_override TEXT, created_at INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO inbounds VALUES (6, 'CDN-XHTTP', 'vless', ?)",
        (json.dumps({"clients": []}),),  # v3: migrated inbound, JSON is empty
    )
    conn.execute(
        "INSERT INTO clients VALUES (1, 'v3user', 'v3subid12345678', ?, '', "
        f"{5 * GIB}, {NOW_MS + 30 * DAY_MS}, 1, 424242, 'note')",
        (VLESS_UUID,),
    )
    conn.execute("INSERT INTO client_inbounds VALUES (1, 6, '', 0)")
    conn.execute(
        f"INSERT INTO client_traffics VALUES (1, 6, 1, 'v3user', 111, 222, "
        f"{NOW_MS + 30 * DAY_MS}, {5 * GIB}, 0)"
    )
    conn.commit()
    conn.close()


async def test_v3_client_tables_are_read(uow: UnitOfWork, tmp_path: Path) -> None:
    db = tmp_path / "x-ui-v3.db"
    _make_v3_source(db)

    data = read_source(db)
    result = probe(data)
    assert result["ok"] is True
    assert result["counts"]["clients"] == 1
    assert result["counts"]["with_telegram"] == 1

    panel = _RecordingPanel()
    service = ThreexuiImportService(panel)
    async with uow:
        summary = await service.run(uow, data, squad_uuid="sq-1")
        await uow.commit()

    assert summary["panel_users_created"] == 1
    spec = panel.specs[0]
    assert spec.telegram_id == 424242
    assert spec.short_id == "v3subid12345678"
    assert spec.traffic_limit_bytes == 5 * GIB
    assert spec.extra.get("vlessUuid") == VLESS_UUID  # protocol resolved via client_inbounds
    async with uow:
        user = await uow.users.find_one(telegram_id=424242)
        assert user is not None
        sub = await uow.subscriptions.find_one(user_id=user.id)
        assert sub is not None and sub.traffic_used_bytes == 333  # traffics joined by email

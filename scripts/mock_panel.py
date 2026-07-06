"""Mock Remnawave panel — a faithful stub of the endpoints our client uses.

Lets the whole stack (bot, cabinet, mini-app, sync jobs) run end-to-end on a box that
cannot host a real panel. Point ``REMNAWAVE__BASE_URL`` at it; a real panel is a
one-line env change later. State persists to a JSON file next to the script.

Run:  uvicorn scripts.mock_panel:app --port 3010
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="mock-remnawave")

# Public base for subscription URLs (behind a proxy set MOCK_PANEL_PUBLIC_URL).
PUBLIC_URL = os.environ.get("MOCK_PANEL_PUBLIC_URL", "").rstrip("/")

STATE_FILE = Path(__file__).with_name("mock_panel_state.json")
VERSION = "2.8.4"

SQUADS = [
    {"uuid": "11111111-1111-4111-8111-111111111111", "name": "NL-AMS", "membersCount": 0},
    {"uuid": "22222222-2222-4222-8222-222222222222", "name": "DE-FRA", "membersCount": 0},
]

NODES = [
    {
        "uuid": f"aaaaaaaa-000{i}-4000-8000-00000000000{i}",
        "name": name,
        "countryCode": cc,
        "address": f"45.90.10.1{i}",
    }
    for i, (name, cc) in enumerate(
        [("NL-AMS-1", "NL"), ("DE-FRA-1", "DE"), ("FI-HEL-1", "FI")], start=1
    )
]


def _load() -> dict[str, dict[str, Any]]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save(users: dict[str, dict[str, Any]]) -> None:
    STATE_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=1))


USERS: dict[str, dict[str, Any]] = _load()


def _base_url(request: Request) -> str:
    return PUBLIC_URL or str(request.base_url).rstrip("/")


def _user_payload(u: dict[str, Any]) -> dict[str, Any]:
    return {"response": u}


def _make_user(body: dict[str, Any], request: Request) -> dict[str, Any]:
    uid = str(uuid.uuid4())
    short = uuid.uuid4().hex[:12]
    return {
        "uuid": uid,
        "shortUuid": short,
        "username": body.get("username") or f"user_{short}",
        "status": "ACTIVE",
        "expireAt": body.get("expireAt"),
        "trafficLimitBytes": int(body.get("trafficLimitBytes") or 0),
        "userTraffic": 0,
        "hwidDeviceLimit": body.get("hwidDeviceLimit"),
        "telegramId": body.get("telegramId"),
        "activeInternalSquads": body.get("activeInternalSquads") or [],
        "externalSquadUuid": body.get("externalSquadUuid"),
        "subscriptionUrl": f"{_base_url(request)}/sub/{short}",
        "tag": body.get("tag"),
    }


@app.get("/api/system/health")
async def health() -> dict[str, Any]:
    return {"response": {"isHealthy": True, "version": VERSION}}


@app.post("/api/users")
async def create_user(request: Request) -> dict[str, Any]:
    body = await request.json()
    user = _make_user(body, request)
    USERS[user["uuid"]] = user
    _save(USERS)
    return _user_payload(user)


@app.get("/api/users/by-telegram-id/{telegram_id}")
async def by_telegram(telegram_id: int) -> dict[str, Any]:
    found = [u for u in USERS.values() if u.get("telegramId") == telegram_id]
    return {"response": found}


@app.get("/api/users/{uid}")
async def get_user(uid: str) -> dict[str, Any]:
    user = USERS.get(uid)
    if user is None:
        raise HTTPException(404, "user not found")
    return _user_payload(user)


@app.patch("/api/users/{uid}")
async def patch_user(uid: str, request: Request) -> dict[str, Any]:
    user = USERS.get(uid)
    if user is None:
        raise HTTPException(404, "user not found")
    body = await request.json()
    for key in (
        "expireAt",
        "trafficLimitBytes",
        "hwidDeviceLimit",
        "telegramId",
        "activeInternalSquads",
        "externalSquadUuid",
        "username",
        "tag",
    ):
        if key in body and body[key] is not None:
            user[key] = body[key]
    _save(USERS)
    return _user_payload(user)


@app.delete("/api/users/{uid}")
async def delete_user(uid: str) -> dict[str, Any]:
    USERS.pop(uid, None)
    _save(USERS)
    return {"response": {"ok": True}}


@app.post("/api/users/{uid}/actions/{action}")
async def user_action(uid: str, action: str, request: Request) -> dict[str, Any]:
    user = USERS.get(uid)
    if user is None:
        raise HTTPException(404, "user not found")
    if action == "enable":
        user["status"] = "ACTIVE"
    elif action == "disable":
        user["status"] = "DISABLED"
    elif action == "reset-traffic":
        user["userTraffic"] = 0
    elif action == "revoke":
        user["shortUuid"] = uuid.uuid4().hex[:12]
        user["subscriptionUrl"] = f"{_base_url(request)}/sub/{user['shortUuid']}"
    elif action == "drop-connections":
        pass
    else:
        raise HTTPException(404, f"unknown action {action}")
    _save(USERS)
    return _user_payload(user)


@app.get("/api/internal-squads")
async def internal_squads() -> dict[str, Any]:
    for squad in SQUADS:
        squad["membersCount"] = len(USERS)
    return {"response": {"internalSquads": SQUADS}}


@app.get("/api/nodes")
async def nodes() -> dict[str, Any]:
    # Live-looking metrics: stable identity, wobbling load.
    rnd = random.Random(dt.datetime.now(dt.UTC).strftime("%Y%m%d%H"))
    out = []
    for i, node in enumerate(NODES):
        out.append(
            {
                **node,
                "isConnected": True,
                "isDisabled": False,
                "usersOnline": max(0, len(USERS) * (2 + i) + rnd.randint(0, 5)),
                "trafficUsedBytes": rnd.randint(40, 900) * 1024**3,
            }
        )
    return {"response": out}


@app.get("/sub/{short_id}")
async def subscription(short_id: str) -> dict[str, Any]:
    """Fake subscription endpoint (clients would fetch configs here)."""
    found = [u for u in USERS.values() if u.get("shortUuid") == short_id]
    if not found:
        raise HTTPException(404, "unknown subscription")
    return {"ok": True, "note": "mock subscription config", "user": found[0]["username"]}

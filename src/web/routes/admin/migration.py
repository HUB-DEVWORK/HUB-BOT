"""Admin: migration from other bots/panels (upload source DB/dump -> probe -> import).

Sources: remnawave-shopbot (users.db), Bedolaga (bot.db / pg_dump .sql / backup
tar.gz / ORM json / Postgres DSN), RemnaShop (pg_dump .sql / Postgres DSN) and
3x-ui (x-ui.db — the only one that CREATES users on the Remnawave panel).

Uploaded files are stored OUTSIDE the public /uploads mount (they contain
balances and tokens), referenced only by a server-generated id, and deleted
after a successful import. Imports adopt existing panel uuids (except 3x-ui),
so subscribers keep working mid-migration.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.application.services import (
    bedolaga_import,
    jolymmiels_import,
    minishop_import,
    remnashop_import,
    shopbot_import,
    threexui_import,
)
from src.application.services.bedolaga_import import BedolagaImportService
from src.application.services.jolymmiels_import import JolymmielsImportService
from src.application.services.minishop_import import MinishopImportService
from src.application.services.remnashop_import import RemnashopImportService
from src.application.services.shopbot_import import ShopbotImportService
from src.application.services.threexui_import import ThreexuiImportService
from src.core.logging import get_logger
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit
from src.web.routes.admin.deps import AdminIdentity, require_admin

log = get_logger(__name__)

router = APIRouter(prefix="/migration")

# NOT under uploads/ — that directory is publicly served.
_INBOX = Path("migration_inbox")
_MAX_BYTES = 200 * 1024 * 1024
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_UPLOAD_EXTS = (".db", ".sqlite", ".sqlite3", ".sql", ".json", ".gz", ".tgz")

Source = Literal["bedolaga", "remnashop", "threexui", "minishop", "jolymmiels"]


def _saved_file(file_id: str, suffix: str) -> Path:
    if not _ID_RE.match(file_id):
        raise HTTPException(400, "bad file id")
    path = _INBOX / f"{file_id}{suffix}"
    if not path.is_file():
        raise HTTPException(404, "файл не найден — загрузите базу заново")
    return path


async def _save_upload(file: UploadFile, suffix: str) -> str:
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "файл больше 200 МБ")
    if not data:
        raise HTTPException(400, "пустой файл")
    _INBOX.mkdir(exist_ok=True)
    file_id = uuid.uuid4().hex
    (_INBOX / f"{file_id}{suffix}").write_bytes(data)
    return file_id


# --- remnawave-shopbot (users.db) — kept at its original paths -------------------------


@router.post("/shopbot/upload")
async def upload_db(file: UploadFile) -> dict[str, str]:
    name = (file.filename or "").lower()
    if not name.endswith((".db", ".sqlite", ".sqlite3")):
        raise HTTPException(400, "нужен файл users.db (SQLite) из remnawave-shopbot")
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "файл больше 200 МБ")
    if not data.startswith(b"SQLite format 3"):
        raise HTTPException(400, "это не SQLite-база — нужен users.db из папки бота")
    _INBOX.mkdir(exist_ok=True)
    file_id = uuid.uuid4().hex
    (_INBOX / f"{file_id}.db").write_bytes(data)
    return {"file_id": file_id}


class FileIn(BaseModel):
    file_id: str = Field(..., min_length=32, max_length=32)


@router.post("/shopbot/probe")
async def probe(
    body: FileIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    path = _saved_file(body.file_id, ".db")
    try:
        result = await asyncio.to_thread(shopbot_import.probe, path)
    except Exception as exc:
        return {"ok": False, "detail": f"не удалось прочитать базу: {exc}"}
    async with container.uow() as uow:
        await audit(uow, identity, "migration.shopbot.probe", None)
        await uow.commit()
    return result


@router.post("/shopbot/run")
async def run_import(
    body: FileIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    path = _saved_file(body.file_id, ".db")
    try:
        data = await asyncio.to_thread(shopbot_import.read_source, path)
    except Exception as exc:
        raise HTTPException(400, f"не удалось прочитать базу: {exc}") from exc
    if not data["users"]:
        raise HTTPException(400, "таблица users пуста — это точно users.db шопбота?")

    service = ShopbotImportService(container.referrals)
    async with container.uow() as uow:
        summary = await service.run(uow, data)
        await audit(
            uow,
            identity,
            "migration.shopbot.run",
            None,
            users=summary["users_created"] + summary["users_updated"],
            subscriptions=summary["subscriptions"],
        )
        await uow.commit()
    path.unlink(missing_ok=True)
    log.info("shopbot import done", **{k: v for k, v in summary.items() if k != "skipped"})
    summary["skipped"] = summary["skipped"][:50]  # keep the response bounded
    return {"ok": True, **summary}


# --- Bedolaga / RemnaShop / 3x-ui -------------------------------------------------------


@router.post("/upload")
async def upload_source(file: UploadFile) -> dict[str, str]:
    """Generic upload: SQLite db, pg_dump .sql, Bedolaga backup tar.gz or ORM json."""
    name = (file.filename or "").lower()
    if not (name.endswith(_UPLOAD_EXTS) or name.endswith(".tar.gz")):
        raise HTTPException(400, "нужен файл базы: .db/.sqlite, .sql, .json или бэкап .tar.gz")
    file_id = await _save_upload(file, ".src")
    return {"file_id": file_id}


class SourceIn(BaseModel):
    file_id: str | None = Field(None, min_length=32, max_length=32)
    dsn: str | None = Field(None, min_length=10, max_length=512)
    squad_uuid: str | None = Field(None, max_length=64)  # 3x-ui only


async def _load_source(source: Source, body: SourceIn) -> tuple[dict[str, Any], Path | None]:
    """Read the source into plain dicts from an uploaded file or a live Postgres DSN."""
    if body.dsn:
        if source == "threexui":
            raise HTTPException(400, "3x-ui импортируется только из файла x-ui.db")
        if not body.dsn.startswith(("postgres://", "postgresql://")):
            raise HTTPException(400, "dsn должен быть postgres:// URL")
        dsn_readers = {
            "bedolaga": bedolaga_import.read_source_dsn,
            "remnashop": remnashop_import.read_source_dsn,
            "minishop": minishop_import.read_source_dsn,
            "jolymmiels": jolymmiels_import.read_source_dsn,
        }
        try:
            return await dsn_readers[source](body.dsn), None
        except Exception as exc:
            raise HTTPException(400, f"не удалось прочитать базу по DSN: {exc}") from exc
    if not body.file_id:
        raise HTTPException(400, "передайте file_id или dsn")
    path = _saved_file(body.file_id, ".src")
    readers = {
        "bedolaga": bedolaga_import.read_source,
        "remnashop": remnashop_import.read_source,
        "threexui": threexui_import.read_source,
        "minishop": minishop_import.read_source,
        "jolymmiels": jolymmiels_import.read_source,
    }
    try:
        return await asyncio.to_thread(readers[source], path), path
    except Exception as exc:
        raise HTTPException(400, f"не удалось прочитать файл: {exc}") from exc


async def _panel_squads(container: AppContainer) -> list[dict[str, str]]:
    try:
        squads = await container.remnawave_client.get_internal_squads()
    except Exception as exc:  # panel offline is not a probe failure
        log.warning("migration: failed to list panel squads", error=str(exc))
        return []
    return [{"uuid": str(s.uuid), "name": s.name} for s in squads]


@router.post("/{source}/probe")
async def probe_source(
    source: Source,
    body: SourceIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    data, _ = await _load_source(source, body)
    probes = {
        "bedolaga": bedolaga_import.probe,
        "remnashop": remnashop_import.probe,
        "threexui": threexui_import.probe,
        "minishop": minishop_import.probe,
        "jolymmiels": jolymmiels_import.probe,
    }
    result = probes[source](data)
    if source == "threexui" and result.get("ok"):
        result["squads"] = await _panel_squads(container)
    async with container.uow() as uow:
        await audit(uow, identity, f"migration.{source}.probe", None)
        await uow.commit()
    return result


@router.post("/{source}/run")
async def run_source_import(
    source: Source,
    body: SourceIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    data, path = await _load_source(source, body)
    checks = {
        "bedolaga": bedolaga_import.probe,
        "remnashop": remnashop_import.probe,
        "threexui": threexui_import.probe,
        "minishop": minishop_import.probe,
        "jolymmiels": jolymmiels_import.probe,
    }
    check = checks[source](data)
    if not check.get("ok"):
        raise HTTPException(400, str(check.get("detail") or "в источнике нет данных для импорта"))
    if source == "threexui" and not body.squad_uuid:
        # Panel users created without a squad get no inbounds -> dead subscriptions.
        raise HTTPException(400, "не выбран сквад — юзеры на панели остались бы без конфигов")

    async with container.uow() as uow:
        if source == "threexui":
            xui = ThreexuiImportService(container.remnawave_client)
            summary = await xui.run(uow, data, squad_uuid=body.squad_uuid or None)
        elif source == "bedolaga":
            summary = await BedolagaImportService(container.referrals).run(uow, data)
        elif source == "minishop":
            summary = await MinishopImportService(container.referrals).run(uow, data)
        elif source == "jolymmiels":
            joly = JolymmielsImportService(container.referrals, container.remnawave_client)
            summary = await joly.run(uow, data)
        else:
            summary = await RemnashopImportService(container.referrals).run(uow, data)
        await audit(
            uow,
            identity,
            f"migration.{source}.run",
            None,
            users=summary.get("users_created", 0) + summary.get("users_updated", 0),
            subscriptions=summary.get("subscriptions", 0),
        )
        await uow.commit()
    if path is not None:
        path.unlink(missing_ok=True)
    log.info(f"{source} import done", **{k: v for k, v in summary.items() if k != "skipped"})
    summary["skipped"] = summary["skipped"][:50]  # keep the response bounded
    return {"ok": True, **summary}

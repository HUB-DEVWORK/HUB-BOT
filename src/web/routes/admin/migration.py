"""Admin: migration from remnawave-shopbot (upload users.db -> probe -> import).

The DB file is stored OUTSIDE the public /uploads mount (it contains balances and
tokens), referenced only by a server-generated id, and deleted after a successful
import. Import adopts existing panel uuids, so subscribers keep working.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.application.services import shopbot_import
from src.application.services.shopbot_import import ShopbotImportService
from src.core.logging import get_logger
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit
from src.web.routes.admin.deps import AdminIdentity, require_admin

log = get_logger(__name__)

router = APIRouter(prefix="/migration/shopbot")

# NOT under uploads/ — that directory is publicly served.
_INBOX = Path("migration_inbox")
_MAX_BYTES = 200 * 1024 * 1024
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _file_for(file_id: str) -> Path:
    if not _ID_RE.match(file_id):
        raise HTTPException(400, "bad file id")
    path = _INBOX / f"{file_id}.db"
    if not path.is_file():
        raise HTTPException(404, "файл не найден — загрузите users.db заново")
    return path


@router.post("/upload")
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


@router.post("/probe")
async def probe(
    body: FileIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    path = _file_for(body.file_id)
    try:
        result = await asyncio.to_thread(shopbot_import.probe, path)
    except Exception as exc:
        return {"ok": False, "detail": f"не удалось прочитать базу: {exc}"}
    async with container.uow() as uow:
        await audit(uow, identity, "migration.shopbot.probe", None)
        await uow.commit()
    return result


@router.post("/run")
async def run_import(
    body: FileIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    path = _file_for(body.file_id)
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

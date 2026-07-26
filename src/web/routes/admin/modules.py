"""Admin: module management (list / enable-disable / upload / delete).

The bot's module system (``src.bot.modules``) discovers built-in modules baked
into the image and *uploaded* modules living in a shared writable volume
(``EXTERNAL_MODULES_DIR``). This route is the web control plane over that:

* list every discovered module with its effective on/off state,
* flip ``MODULE_<NAME>_ENABLED`` (takes effect on the next bot restart —
  the enable gate resolves once at startup),
* upload a new module as a **zip** or as a **folder** (multi-file multipart),
* delete an uploaded module,
* trigger a bot restart via the updater sidecar (same plumbing as «Обновить»).

SECURITY: an uploaded module is arbitrary Python that runs with the bot's full
privileges once enabled + restarted. This endpoint is admin-JWT gated; it never
executes uploaded code itself — it only writes files to disk. Validation here
(name shape, zip-slip guard, no shadowing a built-in, size/count caps) is about
integrity, not sandboxing.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.bot.modules import EXTERNAL_MODULES_DIR
from src.bot.modules.discovery import discover_manifests
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import OkOut, audit
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/modules")

# A module folder/package name: lowercase identifier, 2..40 chars.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
# Never installable as an uploaded module (framework files / meta).
_RESERVED = {"api", "loader", "hooks", "discovery", "__pycache__", "__init__"}

MAX_FILES = 200
MAX_TOTAL = 8 * 1024 * 1024  # 8 MB per module
MAX_MEMBER = 4 * 1024 * 1024  # 4 MB per file


def _builtin_dir() -> Path:
    """First entry of the package path = the baked-in built-in location."""
    import src.bot.modules as _pkg

    return Path(next(iter(_pkg.__path__)))


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise HTTPException(400, f"недопустимое имя модуля: {name!r} (a-z, 0-9, _; 2–40 симв.)")
    if name in _RESERVED:
        raise HTTPException(400, f"имя {name!r} зарезервировано")
    if (_builtin_dir() / name).is_dir():
        raise HTTPException(409, f"встроенный модуль {name!r} нельзя перезаписать загрузкой")
    return name


def _safe_relpath(raw: str) -> str:
    """Normalise a member path and reject anything escaping the module root."""
    p = raw.replace("\\", "/").strip().lstrip("/")
    parts: list[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == ".." or "\x00" in seg:
            raise HTTPException(400, f"недопустимый путь в архиве: {raw!r}")
        parts.append(seg)
    if not parts:
        raise HTTPException(400, f"пустой путь в архиве: {raw!r}")
    return "/".join(parts)


def _split_top(relpaths: list[str]) -> tuple[str, dict[str, str]]:
    """Derive the single top-level dir (= module name) and strip it off each path."""
    tops = {rp.split("/", 1)[0] for rp in relpaths}
    dirs = {rp.split("/", 1)[0] for rp in relpaths if "/" in rp}
    if len(dirs) != 1:
        raise HTTPException(
            400,
            "в загрузке должна быть ровно одна корневая папка модуля "
            f"(нашёл: {sorted(tops)})",
        )
    top = next(iter(dirs))
    stripped: dict[str, str] = {}
    for rp in relpaths:
        if rp == top or not rp.startswith(top + "/"):
            # a stray file at the archive root alongside the module dir — ignore it
            continue
        stripped[rp[len(top) + 1 :]] = rp
    return top, stripped


def _install(name: str, files: dict[str, bytes]) -> None:
    """Atomically (re)write ``EXTERNAL_MODULES_DIR/<name>`` from an inner-path->bytes map."""
    if "manifest.py" not in files:
        raise HTTPException(400, "в модуле нет manifest.py в корне пакета")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"слишком много файлов (> {MAX_FILES})")
    total = sum(len(b) for b in files.values())
    if total > MAX_TOTAL:
        raise HTTPException(413, f"модуль слишком большой (> {MAX_TOTAL // 1024 // 1024} МБ)")
    # Make it a proper package even if the upload forgot __init__.py.
    files.setdefault("__init__.py", b"")

    root = Path(EXTERNAL_MODULES_DIR)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            500,
            "нет прав на запись в папку внешних модулей. "
            "Docker: `docker compose run --rm --user root web chown -R app:app /app/ext_modules`.",
        ) from exc

    staging = root / f".{name}.tmp"
    dest = root / name
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    try:
        for rel, data in files.items():
            fp = staging / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(staging, dest)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise HTTPException(500, f"не удалось записать модуль: {exc}") from exc


async def _audit_install(container: AppContainer, identity: AdminIdentity, name: str, via: str) -> None:
    async with container.uow() as uow:
        await audit(uow, identity, "module.upload", f"module:{name}", via=via)
        await uow.commit()


# -- endpoints ------------------------------------------------------------------


@router.get("")
async def list_modules(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    mods = discover_manifests()
    async with container.uow() as uow:
        state: dict[str, bool] = {}
        for m in mods:
            try:
                state[m.name] = bool(await container.bot_config.value(uow, m.enable_key))
            except Exception:  # noqa: BLE001 — fall back to manifest default
                state[m.name] = m.default_enabled
    return {
        "modules": [
            {
                "name": m.name,
                "title_ru": m.title_ru,
                "title_en": m.title_en,
                "version": m.version,
                "enabled": state[m.name],
                "external": m.external,
                "requires": list(m.requires),
                "has_config": m.has_config,
                "enable_key": m.enable_key,
                "config_keys": list(m.config_keys),
            }
            for m in mods
        ],
        "ext_dir": EXTERNAL_MODULES_DIR,
    }


class ToggleIn(BaseModel):
    enabled: bool


@router.post("/{name}/toggle")
async def toggle_module(
    name: str,
    body: ToggleIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    mods = {m.name: m for m in discover_manifests()}
    m = mods.get(name)
    if m is None:
        raise HTTPException(404, f"модуль {name!r} не найден")
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, {m.enable_key: body.enabled})
        await audit(uow, identity, "module.toggle", f"module:{name}", enabled=body.enabled)
        await uow.commit()
    return {"ok": True, "restart_required": True}


@router.post("/upload-zip")
async def upload_zip(
    file: UploadFile,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > MAX_TOTAL:
        raise HTTPException(413, f"архив слишком большой (> {MAX_TOTAL // 1024 // 1024} МБ)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, "это не корректный zip-архив") from exc

    members = [i for i in zf.infolist() if not i.is_dir()]
    if not members:
        raise HTTPException(400, "архив пуст")
    relmap = {i.filename: _safe_relpath(i.filename) for i in members}
    top, stripped = _split_top(list(relmap.values()))
    name = _validate_name(top)

    rev = {v: k for k, v in relmap.items()}  # safe-rel -> original zip name
    files: dict[str, bytes] = {}
    for inner, safe_original in stripped.items():
        info = zf.getinfo(rev[safe_original])
        if info.file_size > MAX_MEMBER:
            raise HTTPException(413, f"файл слишком большой: {inner}")
        files[inner] = zf.read(info)
    _install(name, files)
    await _audit_install(container, identity, name, "zip")
    return {"ok": True, "name": name, "via": "zip", "restart_required": True}


@router.post("/upload-folder")
async def upload_folder(
    files: list[UploadFile] = File(...),
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(400, "не передано ни одного файла")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"слишком много файлов (> {MAX_FILES})")
    by_rel = {_safe_relpath(f.filename or ""): f for f in files}
    top, stripped = _split_top(list(by_rel.keys()))
    name = _validate_name(top)

    out: dict[str, bytes] = {}
    for inner, safe_original in stripped.items():
        data = await by_rel[safe_original].read()
        if len(data) > MAX_MEMBER:
            raise HTTPException(413, f"файл слишком большой: {inner}")
        out[inner] = data
    _install(name, out)
    await _audit_install(container, identity, name, "folder")
    return {"ok": True, "name": name, "via": "folder", "restart_required": True}


@router.delete("/{name}")
async def delete_module(
    name: str,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise HTTPException(400, "недопустимое имя модуля")
    dest = Path(EXTERNAL_MODULES_DIR) / name
    if not dest.is_dir():
        raise HTTPException(404, f"загруженный модуль {name!r} не найден")
    try:
        shutil.rmtree(dest)
    except OSError as exc:
        raise HTTPException(500, f"не удалось удалить модуль: {exc}") from exc
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, {f"MODULE_{name.upper()}_ENABLED": False})
        await audit(uow, identity, "module.delete", f"module:{name}")
        await uow.commit()
    return OkOut()


@router.post("/restart-bot")
async def restart_bot(
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        await audit(uow, identity, "module.restart-bot", None)
        await uow.commit()

    from src.infrastructure.services.updater import request_restart

    if request_restart("bot"):
        return {"ok": True, "status": "started"}
    return {
        "ok": False,
        "status": "no-updater",
        "hint": "Модуль обновлений (updater) не подключён — перезапусти бота вручную: "
        "`./scripts/dc.sh restart bot`.",
    }


@router.post("/restart-web")
async def restart_web(
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        await audit(uow, identity, "module.restart-web", None)
        await uow.commit()

    from src.infrastructure.services.updater import request_restart

    if request_restart("web"):
        return {"ok": True, "status": "started"}
    return {
        "ok": False,
        "status": "no-updater",
        "hint": "Модуль обновлений (updater) не подключён — перезапусти web вручную: "
        "`./scripts/dc.sh restart web`.",
    }

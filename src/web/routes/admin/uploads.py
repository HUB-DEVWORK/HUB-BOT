"""Admin: media uploads (broadcast media, menu screen images, mini-app covers).

Files land in ``uploads/`` next to the app cwd and are served at ``/uploads/...``.
Extension whitelist + size cap; names are random uuids so paths are unguessable.
The audit trail happens where the file gets attached (broadcast / menu save).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from src.web.routes.admin.deps import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

UPLOAD_DIR = Path("uploads")
MAX_BYTES = 20 * 1024 * 1024  # 20 MB
ALLOWED = {
    ".jpg": "photo",
    ".jpeg": "photo",
    ".png": "photo",
    ".webp": "photo",
    ".gif": "gif",
    ".mp4": "video",
}


@router.post("/upload")
async def upload(file: UploadFile) -> dict[str, str]:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"unsupported file type: {ext or '?'}")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (max 20 MB)")
    UPLOAD_DIR.mkdir(exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / name).write_bytes(data)
    return {"path": f"uploads/{name}", "url": f"/uploads/{name}", "kind": ALLOWED[ext]}

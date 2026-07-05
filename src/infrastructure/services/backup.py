"""Encrypted backups (pyzipper AES). The scheduler dumps the DB, this zips it encrypted.

The pg_dump invocation itself lives in the taskiq task (needs the DB binary); this module
handles the AES-encrypted archiving + retention.
"""

from __future__ import annotations

from pathlib import Path

import pyzipper


def create_encrypted_zip(sources: list[Path], out_path: Path, password: str) -> Path:
    """Zip ``sources`` into an AES-256 encrypted archive at ``out_path``."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pyzipper.AESZipFile(
        out_path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(password.encode())
        for src in sources:
            if src.exists():
                zf.write(src, arcname=src.name)
    return out_path


def prune_old_backups(directory: Path, keep: int) -> list[Path]:
    """Delete all but the ``keep`` newest ``*.zip`` files. Returns the removed paths."""
    backups = sorted(directory.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed: list[Path] = []
    for old in backups[keep:]:
        old.unlink(missing_ok=True)
        removed.append(old)
    return removed

"""Minimal reader for plain-format ``pg_dump`` output (COPY ... FROM stdin blocks).

Both Bedolaga and RemnaShop ship a built-in backup that runs
``pg_dump --format=plain`` — the resulting ``.sql`` is the file an owner can
actually get their hands on without shell access to Postgres. We only need the
data, so we extract COPY blocks for the requested tables and ignore the DDL.

Values come back as raw strings (or None for ``\\N``); importers coerce types
themselves — the same normalization they already need for SQLite sources.
"""

from __future__ import annotations

import re

_COPY_RE = re.compile(
    r'^COPY\s+(?:[\w"]+\.)?"?(?P<table>\w+)"?\s*\((?P<cols>[^)]*)\)\s+FROM\s+stdin;\s*$'
)

# COPY text-format escapes (PostgreSQL docs, "Text Format").
_SIMPLE_ESCAPES = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
}


def _unescape(raw: str) -> str | None:
    r"""Decode one COPY field: ``\N`` is NULL, backslash escapes are literal bytes."""
    if raw == "\\N":
        return None
    if "\\" not in raw:
        return raw
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            i += 2
        elif nxt == "x" and i + 2 < n:
            hex_digits = ""
            j = i + 2
            while j < n and len(hex_digits) < 2 and raw[j] in "0123456789abcdefABCDEF":
                hex_digits += raw[j]
                j += 1
            if hex_digits:
                out.append(chr(int(hex_digits, 16)))
                i = j
            else:
                out.append(nxt)
                i += 2
        elif nxt in "01234567":
            oct_digits = ""
            j = i + 1
            while j < n and len(oct_digits) < 3 and raw[j] in "01234567":
                oct_digits += raw[j]
                j += 1
            out.append(chr(int(oct_digits, 8)))
            i = j
        else:
            out.append(nxt)
            i += 2
    return "".join(out)


def parse_copy_blocks(text: str, tables: set[str]) -> dict[str, list[dict[str, str | None]]]:
    """Extract row dicts for ``tables`` from a plain pg_dump. Missing tables -> absent key."""
    result: dict[str, list[dict[str, str | None]]] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _COPY_RE.match(lines[i])
        i += 1
        if match is None or match["table"] not in tables:
            continue
        cols = [c.strip().strip('"') for c in match["cols"].split(",") if c.strip()]
        rows = result.setdefault(match["table"], [])
        while i < len(lines):
            line = lines[i]
            i += 1
            if line == "\\.":
                break
            fields = [_unescape(f) for f in line.split("\t")]
            if len(fields) == len(cols):
                rows.append(dict(zip(cols, fields, strict=True)))
    return result


def looks_like_pgdump(text_head: str) -> bool:
    """Cheap sniff: plain pg_dump starts with SQL comments and contains COPY blocks."""
    head = text_head.lstrip()
    return head.startswith("--") or "COPY " in text_head

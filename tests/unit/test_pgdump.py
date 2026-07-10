"""pg_dump COPY-block parser: escapes, NULLs, table filtering."""

from __future__ import annotations

from src.application.services.pgdump import looks_like_pgdump, parse_copy_blocks

DUMP = """--
-- PostgreSQL database dump
--

SET statement_timeout = 0;

CREATE TABLE public.users (id integer, name text);

COPY public.users (id, name, note) FROM stdin;
1\talice\t\\N
2\tbob\\ttab\tline\\nbreak
3\t\\\\slash\toctal\\011x
\\.

COPY public.ignored (a) FROM stdin;
9
\\.

COPY "transactions" (id, amount_kopeks) FROM stdin;
7\t19900
\\.
"""


def test_parses_requested_tables_with_escapes() -> None:
    data = parse_copy_blocks(DUMP, {"users", "transactions"})
    assert set(data) == {"users", "transactions"}

    users = data["users"]
    assert users[0] == {"id": "1", "name": "alice", "note": None}
    assert users[1]["name"] == "bob\ttab"
    assert users[1]["note"] == "line\nbreak"
    assert users[2]["name"] == "\\slash"
    assert users[2]["note"] == "octal\tx"  # \011 = TAB via octal escape

    assert data["transactions"] == [{"id": "7", "amount_kopeks": "19900"}]


def test_sniff() -> None:
    assert looks_like_pgdump(DUMP[:200])
    assert not looks_like_pgdump('{"data": {}}')

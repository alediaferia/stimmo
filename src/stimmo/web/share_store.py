"""SQLite-backed content-addressed store for share-link blobs.

Design notes (from docs/share-link-store-plan.md §5.1–5.4):

- Schema: ``shares(id TEXT PK, blob BLOB, created_at INT, last_seen INT) STRICT``
  with WAL pragmas.
- ID scheme: ``base62(sha256(blob))[:N]`` where N defaults to 8.  base62 uses
  ``[0-9A-Za-z]`` — URL-safe, no padding, denser than hex.
- Idempotency: ``INSERT OR IGNORE`` then read-back-and-compare.  If the stored
  blob matches we return the id (normal case).  If it differs — a true prefix
  collision between two *different* blobs — we fall back to N+2 chars and insert
  the longer id.  This branch should essentially never fire in practice.
- ``put`` is idempotent: same blob → same id, no duplicate rows.
- ``get`` bumps ``last_seen`` on every read (GC hook per D7; throttling deferred).
- Unknown id → None.

Thread safety: SQLite WAL is safe for one writer + concurrent readers in a
single process.  The handlers that call put/get are sync ``def`` routes, so no
anyio thread offload is needed (they run in the sync thread pool already managed
by Starlette/FastAPI).

The ``ShareStore`` Protocol is the narrow public interface; ``SqliteShareStore``
is the only implementation for now.  Both are designed to allow a future swap to
Redis/Postgres without touching handler code (per the Cache protocol pattern in
the MCP package).
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# base62 helpers
# ---------------------------------------------------------------------------

_B62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
assert len(_B62_ALPHABET) == 62


def _base62_encode(data: bytes) -> str:
    """Encode *data* to a base62 string (big-endian, no padding)."""
    n = int.from_bytes(data, "big")
    if n == 0:
        return _B62_ALPHABET[0]
    chars: list[str] = []
    while n:
        n, remainder = divmod(n, 62)
        chars.append(_B62_ALPHABET[remainder])
    return "".join(reversed(chars))


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ShareStore(Protocol):
    """Narrow interface for the share-link blob store.

    ``put`` is idempotent: the same blob always yields the same id.
    ``get`` returns the blob and bumps last_seen, or None if the id is absent.
    """

    def put(self, blob: bytes) -> str: ...

    def get(self, id: str) -> bytes | None: ...


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------

_DDL = """\
CREATE TABLE IF NOT EXISTS shares (
    id          TEXT PRIMARY KEY,
    blob        BLOB NOT NULL,
    created_at  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
) STRICT;
"""

_PRAGMAS = [
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA busy_timeout=3000;",
]


class SqliteShareStore:
    """Content-addressed SQLite blob store for share links.

    Parameters
    ----------
    path:
        File path for the SQLite database, or ``":memory:"`` for an in-process
        database (useful in tests).
    id_len:
        Number of base62 characters to use as the primary ID.  Default 8 (see
        §5.2 for the birthday-bound reasoning).
    clock:
        Callable returning the current Unix epoch seconds.  Injectable for
        deterministic tests.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        id_len: int = 8,
        clock=time.time,
    ) -> None:
        self._path = str(path)
        self._id_len = id_len
        self._clock = clock
        # Open connection once; safe for single-process sync usage.
        self._conn = self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        for pragma in _PRAGMAS:
            conn.execute(pragma)
        conn.execute(_DDL)
        conn.commit()
        return conn

    def _id_for(self, blob: bytes, length: int | None = None) -> str:
        """Return the base62 id for *blob* at the given prefix length."""
        n = length if length is not None else self._id_len
        digest = hashlib.sha256(blob).digest()
        return _base62_encode(digest)[:n]

    def put(self, blob: bytes) -> str:
        """Store *blob* and return its short id.  Idempotent (same blob → same id).

        Collision guard: if ``INSERT OR IGNORE`` inserts nothing (id already
        exists), reads back the stored blob.  If the stored blob matches *blob*
        (the normal idempotent case) return the id.  If it differs (a true
        prefix collision between two distinct blobs — extremely rare), retry
        with id_len+2 chars and recurse once.
        """
        return self._put_with_len(blob, self._id_len)

    def _put_with_len(self, blob: bytes, length: int) -> str:
        share_id = self._id_for(blob, length)
        now = int(self._clock())

        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO shares (id, blob, created_at, last_seen) "
                "VALUES (?, ?, ?, ?)",
                (share_id, blob, now, now),
            )

        # Read back to detect collision.
        row = self._conn.execute("SELECT blob FROM shares WHERE id = ?", (share_id,)).fetchone()

        if row is None:
            # Should not happen (we just inserted/ignored), but be safe.
            raise RuntimeError(f"Share store: failed to read back id {share_id!r}")

        stored_blob = bytes(row[0])
        if stored_blob == blob:
            return share_id

        # True prefix collision: two different blobs share the same prefix at
        # this length.  Fall back to a longer prefix (N+2).  This branch should
        # essentially never fire in practice.
        fallback_len = length + 2
        return self._put_with_len(blob, fallback_len)

    def get(self, id: str) -> bytes | None:
        """Return the blob for *id*, bumping last_seen.  Returns None if absent."""
        row = self._conn.execute("SELECT blob FROM shares WHERE id = ?", (id,)).fetchone()

        if row is None:
            return None

        now = int(self._clock())
        with self._conn:
            self._conn.execute("UPDATE shares SET last_seen = ? WHERE id = ?", (now, id))

        return bytes(row[0])

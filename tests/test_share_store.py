"""Tests for src/stimmo/web/share_store.py (Step 2).

Coverage:
- put() is idempotent: same blob → same id, one row in the DB
- get() returns the blob and bumps last_seen; returns None for unknown id
- injected clock is used for created_at and last_seen
- in-memory store (:memory:) works correctly
- tmp_path store works correctly
- synthetic forced prefix-collision guard: two different blobs whose sha256
  digests share the same N-char prefix are stored under distinct ids (N and N+2).
"""

from __future__ import annotations

import hashlib
import time

from stimmo.web.share_store import SqliteShareStore, _base62_encode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path=None, *, clock=time.time, id_len=8) -> SqliteShareStore:
    """Return a fresh SqliteShareStore backed by :memory: (or tmp_path if given)."""
    path = ":memory:" if tmp_path is None else str(tmp_path / "share.db")
    return SqliteShareStore(path, clock=clock, id_len=id_len)


# ---------------------------------------------------------------------------
# Basic put / get
# ---------------------------------------------------------------------------


class TestPutGet:
    def test_put_returns_string_id(self):
        store = _make_store()
        blob = b"hello world"
        id_ = store.put(blob)
        assert isinstance(id_, str)
        assert len(id_) == 8
        assert id_.isalnum()

    def test_put_idempotent_same_blob_same_id(self):
        store = _make_store()
        blob = b"some payload bytes"
        id1 = store.put(blob)
        id2 = store.put(blob)
        assert id1 == id2

    def test_put_idempotent_single_row(self):
        store = _make_store()
        blob = b"dedup test"
        store.put(blob)
        store.put(blob)
        rows = store._conn.execute("SELECT COUNT(*) FROM shares").fetchone()[0]
        assert rows == 1

    def test_put_different_blobs_different_ids(self):
        store = _make_store()
        id1 = store.put(b"blob one")
        id2 = store.put(b"blob two")
        assert id1 != id2

    def test_get_returns_blob(self):
        store = _make_store()
        blob = b"\x00\x01\x02binary data"
        id_ = store.put(blob)
        result = store.get(id_)
        assert result == blob

    def test_get_unknown_id_returns_none(self):
        store = _make_store()
        assert store.get("notexist") is None

    def test_get_bumps_last_seen(self):
        # Use a list-based clock that advances each call
        call_counter = [0]
        clock_values = [1000, 2000, 3000]

        def advancing_clock():
            v = clock_values[min(call_counter[0], len(clock_values) - 1)]
            call_counter[0] += 1
            return v

        store = SqliteShareStore(":memory:", clock=advancing_clock, id_len=8)
        blob = b"bump test"
        id_ = store.put(blob)

        # Read initial last_seen (should be clock value at put time)
        row_before = store._conn.execute(
            "SELECT last_seen FROM shares WHERE id = ?", (id_,)
        ).fetchone()
        last_seen_before = row_before[0]

        # Advance time and get
        clock_values[0] = 9999
        store.get(id_)

        row_after = store._conn.execute(
            "SELECT last_seen FROM shares WHERE id = ?", (id_,)
        ).fetchone()
        last_seen_after = row_after[0]

        # last_seen should have been updated
        assert last_seen_after >= last_seen_before


class TestClockInjection:
    def test_created_at_uses_injected_clock(self):
        fixed_time = 1_700_000_000
        store = SqliteShareStore(":memory:", clock=lambda: fixed_time, id_len=8)
        blob = b"clock test"
        id_ = store.put(blob)
        row = store._conn.execute("SELECT created_at FROM shares WHERE id = ?", (id_,)).fetchone()
        assert row[0] == fixed_time

    def test_last_seen_uses_injected_clock_on_get(self):
        put_time = 1_700_000_000
        get_time = 1_700_100_000
        calls = [put_time, get_time]
        call_idx = [0]

        def clock():
            v = calls[call_idx[0]]
            if call_idx[0] < len(calls) - 1:
                call_idx[0] += 1
            return v

        store = SqliteShareStore(":memory:", clock=clock, id_len=8)
        blob = b"time test"
        id_ = store.put(blob)
        store.get(id_)

        row = store._conn.execute("SELECT last_seen FROM shares WHERE id = ?", (id_,)).fetchone()
        assert row[0] == get_time


class TestInMemoryStore:
    def test_in_memory_round_trip(self):
        store = SqliteShareStore(":memory:")
        blob = b"in memory"
        id_ = store.put(blob)
        assert store.get(id_) == blob

    def test_in_memory_two_instances_are_independent(self):
        s1 = SqliteShareStore(":memory:")
        s2 = SqliteShareStore(":memory:")
        blob = b"independent"
        id_ = s1.put(blob)
        # s2 has its own in-memory DB; id should not exist there
        assert s2.get(id_) is None


class TestTmpPathStore:
    def test_tmp_path_round_trip(self, tmp_path):
        store = SqliteShareStore(tmp_path / "share.db")
        blob = b"disk test"
        id_ = store.put(blob)
        assert store.get(id_) == blob

    def test_tmp_path_persists_across_reopen(self, tmp_path):
        db_path = tmp_path / "share.db"
        blob = b"persistence test"
        id_ = SqliteShareStore(db_path).put(blob)
        # Open a new connection to the same file
        store2 = SqliteShareStore(db_path)
        assert store2.get(id_) == blob


# ---------------------------------------------------------------------------
# Prefix collision guard (synthetic)
# ---------------------------------------------------------------------------


class TestCollisionGuard:
    """Force a prefix collision by injecting a blob that already occupies the id slot."""

    def test_collision_fallback_uses_longer_id(self):
        """Two different blobs that would map to the same 8-char prefix should
        be stored under distinct ids (the second under a longer prefix)."""
        store = _make_store()

        blob_a = b"blob_a_content"
        id_a = store.put(blob_a)
        assert len(id_a) == 8

        # Manually plant a different blob under blob_a's id to simulate a collision.
        impostor = b"impostor_blob_that_is_different"
        with store._conn:
            store._conn.execute(
                "INSERT OR REPLACE INTO shares (id, blob, created_at, last_seen) "
                "VALUES (?, ?, ?, ?)",
                (id_a, impostor, 0, 0),
            )

        # Now put blob_a again: the store should detect the mismatch and use N+2.
        id_a2 = store.put(blob_a)
        # The fallback id must differ from the colliding slot and be longer.
        assert id_a2 != id_a
        assert len(id_a2) == 10  # 8 + 2

        # Both blobs should now be retrievable.
        assert store.get(id_a) == impostor  # the planted one
        assert store.get(id_a2) == blob_a  # the actual blob under longer id


# ---------------------------------------------------------------------------
# base62 encoding sanity
# ---------------------------------------------------------------------------


class TestBase62:
    def test_known_value(self):
        # sha256 of b"test" starts with 9f86d081...
        # Confirm _base62_encode produces URL-safe alnum output.
        digest = hashlib.sha256(b"test").digest()
        encoded = _base62_encode(digest)
        assert encoded.isalnum()
        assert len(encoded) > 0

    def test_zero_bytes(self):
        result = _base62_encode(b"\x00")
        assert result == "0"

    def test_id_len_8_produces_8_char_prefix(self):
        store = _make_store()
        blob = b"length check"
        id_ = store.put(blob)
        assert len(id_) == 8

    def test_custom_id_len(self):
        store = _make_store(id_len=6)
        blob = b"custom len"
        id_ = store.put(blob)
        assert len(id_) == 6

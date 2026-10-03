"""Persistent state: last Gmail historyId, per-message status, and pending (unmatched) updates.

Two interchangeable backends: SQLite for local runs, Firestore on Cloud Run (its disk is ephemeral).
"""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,          -- done | skipped | dry_run | failed | deferred (quota)
    attempts INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pending (
    key TEXT PRIMARY KEY,          -- "<message id>:<update index>"
    payload TEXT NOT NULL,         -- JSON: the email's identity + the extracted update
    created_at REAL NOT NULL
);
"""


class State:
    """SQLite backend."""

    def __init__(self, path: Path):
        self.path = str(path)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            with conn:  # commit on success, rollback on error
                yield conn
        finally:
            conn.close()

    def get(self, key: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def message(self, msg_id: str) -> Optional[Tuple[str, int]]:
        """Return (status, attempts) or None if never seen."""
        with self._conn() as c:
            row = c.execute("SELECT status, attempts FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return (row[0], row[1]) if row else None

    def record(self, msg_id: str, status: str, detail: str = "") -> None:
        prev = self.message(msg_id)
        attempts = (prev[1] if prev else 0) + (1 if status == "failed" else 0)
        with self._conn() as c:
            c.execute(
                "INSERT INTO messages(id, status, attempts, detail, updated_at) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, attempts = excluded.attempts, "
                "detail = excluded.detail, updated_at = excluded.updated_at",
                (msg_id, status, attempts, detail[:500], time.time()),
            )

    def retryable_failures(self, max_attempts: int, backoff_seconds: int = 60, defer_seconds: int = 300) -> List[str]:
        """Messages due for another try.

        'failed': waits backoff_seconds * attempts since the last failure, gives up after max_attempts.
        'deferred' (API quota exhausted): retried every defer_seconds indefinitely; never counts as an attempt.
        """
        now = time.time()
        with self._conn() as c:
            rows = c.execute(
                "SELECT id FROM messages WHERE "
                "(status = 'failed' AND attempts < ? AND updated_at + ? * attempts <= ?) "
                "OR (status = 'deferred' AND updated_at + ? <= ?)",
                (max_attempts, backoff_seconds, now, defer_seconds, now),
            ).fetchall()
        return [r[0] for r in rows]

    # --- pending: updates that matched no sheet row yet (the order # may be added to the sheet later) ---
    def add_pending(self, key: str, payload: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO pending(key, payload, created_at) VALUES(?, ?, ?)",
                      (key, payload, time.time()))

    def list_pending(self, max_age_seconds: float) -> List[Tuple[str, str]]:
        """Pending updates younger than max_age_seconds; older ones are dropped."""
        cutoff = time.time() - max_age_seconds
        with self._conn() as c:
            c.execute("DELETE FROM pending WHERE created_at < ?", (cutoff,))
            rows = c.execute("SELECT key, payload FROM pending ORDER BY created_at").fetchall()
        return [(r[0], r[1]) for r in rows]

    def remove_pending(self, key: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM pending WHERE key = ?", (key,))


class FirestoreState:
    """Firestore backend for Cloud Run. Same interface as State. Untested until deployed."""

    def __init__(self) -> None:
        from google.cloud import firestore  # imported lazily: not needed for local runs
        self.db = firestore.Client(project=settings.gcp_project)

    def _doc(self, collection: str, key: str):
        return self.db.collection(collection).document(key)

    def get(self, key: str) -> Optional[str]:
        snap = self._doc("kv", key).get()
        return snap.to_dict()["value"] if snap.exists else None

    def set(self, key: str, value: str) -> None:
        self._doc("kv", key).set({"value": value})

    def message(self, msg_id: str) -> Optional[Tuple[str, int]]:
        snap = self._doc("messages", msg_id).get()
        if not snap.exists:
            return None
        d = snap.to_dict()
        return d["status"], d["attempts"]

    def record(self, msg_id: str, status: str, detail: str = "") -> None:
        prev = self.message(msg_id)
        attempts = (prev[1] if prev else 0) + (1 if status == "failed" else 0)
        self._doc("messages", msg_id).set(
            {"status": status, "attempts": attempts, "detail": detail[:500], "updated_at": time.time()})

    def retryable_failures(self, max_attempts: int, backoff_seconds: int = 60, defer_seconds: int = 300) -> List[str]:
        now = time.time()
        out = []
        for status in ("failed", "deferred"):  # single-field queries need no composite index
            for snap in self.db.collection("messages").where("status", "==", status).stream():
                d = snap.to_dict()
                if status == "failed" and d["attempts"] < max_attempts and \
                        d["updated_at"] + backoff_seconds * d["attempts"] <= now:
                    out.append(snap.id)
                if status == "deferred" and d["updated_at"] + defer_seconds <= now:
                    out.append(snap.id)
        return out

    def add_pending(self, key: str, payload: str) -> None:
        ref = self._doc("pending", key)
        if not ref.get().exists:
            ref.set({"payload": payload, "created_at": time.time()})

    def list_pending(self, max_age_seconds: float) -> List[Tuple[str, str]]:
        cutoff = time.time() - max_age_seconds
        out = []
        for snap in self.db.collection("pending").stream():
            d = snap.to_dict()
            if d["created_at"] < cutoff:
                snap.reference.delete()
            else:
                out.append((d["created_at"], snap.id, d["payload"]))
        return [(k, p) for _, k, p in sorted(out)]

    def remove_pending(self, key: str) -> None:
        self._doc("pending", key).delete()


def get_state():
    if settings.state_backend == "firestore":
        return FirestoreState()
    return State(settings.state_db)

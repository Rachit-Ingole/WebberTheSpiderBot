"""Local SQLite memory for people and robot events."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def load_project_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class MemoryStore:
    """SQLite-backed event store; facts remain queryable without an LLM."""

    def __init__(self, database_path=None):
        load_project_env()
        default_path = Path(__file__).resolve().parent.parent / "data" / "spidey_memory.db"
        self.path = Path(database_path or os.getenv("SPIDEY_MEMORY_DB", default_path))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self):
        with self._session() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    person_name TEXT,
                    room TEXT NOT NULL DEFAULT 'unknown',
                    confidence REAL,
                    snapshot_path TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_events_time
                    ON events(occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_person
                    ON events(person_name, occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_room
                    ON events(room, occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_type
                    ON events(event_type, occurred_at DESC);
                CREATE TABLE IF NOT EXISTS rooms (
                    name TEXT PRIMARY KEY,
                    x1 INTEGER NOT NULL,
                    y1 INTEGER NOT NULL,
                    x2 INTEGER NOT NULL,
                    y2 INTEGER NOT NULL
                );
                """
            )

    def list_rooms(self):
        with self._session() as connection:
            rows = connection.execute(
                "SELECT name, x1, y1, x2, y2 FROM rooms ORDER BY name"
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_rooms(self, rooms):
        """Replace manually drawn room regions with validated rectangles."""
        clean = []
        seen = set()
        for room in rooms or []:
            name = str(room.get("name", "")).strip()[:64]
            if not name or name.lower() in seen:
                continue
            try:
                x1, y1 = int(room["x1"]), int(room["y1"])
                x2, y2 = int(room["x2"]), int(room["y2"])
            except (KeyError, TypeError, ValueError):
                continue
            x1, x2 = sorted((max(0, min(100, x1)), max(0, min(100, x2))))
            y1, y2 = sorted((max(0, min(100, y1)), max(0, min(100, y2))))
            if x2 <= x1 or y2 <= y1:
                continue
            seen.add(name.lower())
            clean.append((name, x1, y1, x2, y2))
        with self._session() as connection:
            connection.execute("DELETE FROM rooms")
            connection.executemany(
                "INSERT INTO rooms(name, x1, y1, x2, y2) VALUES (?, ?, ?, ?, ?)",
                clean,
            )
        return self.list_rooms()

    def log_event(self, event_type, person_name=None, room="unknown",
                  confidence=None, snapshot_path=None, details=None):
        occurred_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events
                    (occurred_at, event_type, person_name, room, confidence,
                     snapshot_path, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurred_at,
                    event_type,
                    person_name,
                    room or "unknown",
                    confidence,
                    snapshot_path,
                    json.dumps(details or {}, separators=(",", ":")),
                ),
            )
            return cursor.lastrowid

    def last_seen(self, person_name, room=None):
        query = """
            SELECT * FROM events
            WHERE event_type = 'person_seen' AND lower(person_name) = lower(?)
        """
        parameters = [person_name]
        if room:
            query += " AND room = ?"
            parameters.append(room)
        query += " ORDER BY occurred_at DESC LIMIT 1"
        with self._session() as connection:
            row = connection.execute(query, parameters).fetchone()
        return dict(row) if row else None

    def recent_intruders(self, limit=20):
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE event_type = 'intruder_detected'
                ORDER BY occurred_at DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_events(self, limit=20):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY occurred_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

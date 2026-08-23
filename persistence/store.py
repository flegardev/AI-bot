from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  event_type TEXT NOT NULL,
  mode TEXT,
  symbol TEXT,
  entity_id TEXT,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, timestamp);
CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
  client_order_id TEXT PRIMARY KEY,
  response TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class TradingStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def record_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        mode: str = "",
        symbol: str = "",
        entity_id: str = "",
    ) -> int:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO events(timestamp,event_type,mode,symbol,entity_id,payload) "
                "VALUES(?,?,?,?,?,?)",
                (timestamp, event_type, mode, symbol, entity_id, json.dumps(payload, default=str)),
            )
            return int(cursor.lastrowid or 0)

    def recent_events(self, limit: int = 100, event_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM events"
        params: list[Any] = []
        if event_type:
            query += " WHERE event_type=?"
            params.append(event_type)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def set_state(self, key: str, value: Any) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO state(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, json.dumps(value, default=str), datetime.now(timezone.utc).isoformat()),
            )

    def get_state(self, key: str, default: Any = None) -> Any:
        with self.connection() as connection:
            row = connection.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def save_idempotent_response(self, client_order_id: str, response: dict[str, Any]) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO idempotency(client_order_id,response,created_at) VALUES(?,?,?)",
                (
                    client_order_id,
                    json.dumps(response, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def get_idempotent_response(self, client_order_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT response FROM idempotency WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
        return json.loads(row["response"]) if row else None

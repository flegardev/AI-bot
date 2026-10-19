from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class ResearchRegistry:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS research (id TEXT PRIMARY KEY, kind TEXT, status TEXT, created TEXT, completed TEXT, payload TEXT, result TEXT)")

    def begin(self, kind, payload):
        identifier = uuid4().hex
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO research VALUES(?,?,?,?,?,?,?)", (identifier, kind, "pending",
                datetime.now(timezone.utc).isoformat(), None, json.dumps(payload, default=str), None))
        return identifier

    def finish(self, identifier, status, result):
        if status not in {"completed", "failed", "rejected"}:
            raise ValueError("invalid terminal research status")
        with sqlite3.connect(self.path) as db:
            cursor = db.execute("UPDATE research SET status=?, completed=?, result=? WHERE id=? AND status='pending'",
                (status, datetime.now(timezone.utc).isoformat(), json.dumps(result, default=str), identifier))
            if cursor.rowcount != 1:
                raise ValueError("research result is immutable or unknown")

    def records(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM research ORDER BY created,id").fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"]),
                 "result": json.loads(row["result"]) if row["result"] else None} for row in rows]

    def export(self, path):
        Path(path).write_text("\n".join(json.dumps(row) for row in self.records()) + "\n", encoding="utf-8")

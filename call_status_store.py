# -*- coding: utf-8 -*-
"""SQLite-backed persistence for outbound call-control status."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


DEFAULT_DB_FILENAME = "call_control.sqlite3"

CALL_COLUMNS = {
    "room_name",
    "phone_number",
    "status",
    "reason",
    "sip_status_code",
    "sip_status",
    "sip_call_id",
    "participant_identity",
    "participant_status",
    "error",
    "metadata_json",
    "created_at",
    "updated_at",
    "dispatched_at",
    "dialing_at",
    "answered_at",
    "ended_at",
}


FAILURE_STATUSES = {
    "dispatch_failed",
    "failed",
    "failed_busy",
    "failed_no_answer",
    "failed_rejected",
    "failed_unreachable",
    "failed_trunk",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_path() -> Path:
    configured = os.environ.get("CALL_API_DB_PATH") or os.environ.get("CALL_STATUS_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().with_name(DEFAULT_DB_FILENAME)


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calls (
            call_id TEXT PRIMARY KEY,
            room_name TEXT NOT NULL,
            phone_number TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            sip_status_code TEXT,
            sip_status TEXT,
            sip_call_id TEXT,
            participant_identity TEXT,
            participant_status TEXT,
            error TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            dispatched_at TEXT,
            dialing_at TEXT,
            answered_at TEXT,
            ended_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS call_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            sip_status_code TEXT,
            sip_status TEXT,
            message TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(call_id) REFERENCES calls(call_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_call_events_call_id ON call_events(call_id, id)")


def _json_dumps(value: Optional[dict[str, Any]]) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _json_loads(raw_value: Any) -> dict[str, Any]:
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _timestamp_column_for_status(status: Optional[str]) -> Optional[str]:
    if not status:
        return None
    if status == "dispatched":
        return "dispatched_at"
    if status == "dialing":
        return "dialing_at"
    if status in {"answered", "active"}:
        return "answered_at"
    if status == "completed" or status in FAILURE_STATUSES or status.startswith("failed_"):
        return "ended_at"
    return None


def _insert_event(
    conn: sqlite3.Connection,
    call_id: str,
    *,
    status: str,
    reason: Optional[str] = None,
    sip_status_code: Optional[Any] = None,
    sip_status: Optional[str] = None,
    message: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO call_events (
            call_id, status, reason, sip_status_code, sip_status, message, details_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            call_id,
            status,
            reason,
            str(sip_status_code) if sip_status_code is not None else None,
            sip_status,
            message,
            _json_dumps(details),
            created_at or now_iso(),
        ),
    )


def create_call_record(record: dict[str, Any]) -> dict[str, Any]:
    """Create or replace the initial call record and add its first event."""
    call_id = str(record["call_id"])
    created_at = record.get("created_at") or now_iso()
    status = record.get("status") or "dispatching"
    metadata_json = _json_dumps(record.get("metadata"))
    values = {
        "call_id": call_id,
        "room_name": record.get("room_name") or "",
        "phone_number": record.get("phone_number") or "",
        "status": status,
        "reason": record.get("reason"),
        "sip_status_code": str(record["sip_status_code"]) if record.get("sip_status_code") is not None else None,
        "sip_status": record.get("sip_status"),
        "sip_call_id": record.get("sip_call_id"),
        "participant_identity": record.get("participant_identity"),
        "participant_status": record.get("participant_status"),
        "error": record.get("error"),
        "metadata_json": metadata_json,
        "created_at": created_at,
        "updated_at": created_at,
        "dispatched_at": record.get("dispatched_at"),
        "dialing_at": record.get("dialing_at"),
        "answered_at": record.get("answered_at"),
        "ended_at": record.get("ended_at"),
    }
    timestamp_column = _timestamp_column_for_status(status)
    if timestamp_column and not values.get(timestamp_column):
        values[timestamp_column] = created_at

    columns = ["call_id", *CALL_COLUMNS]
    placeholders = ", ".join("?" for _ in columns)
    assignments = ", ".join(f"{column}=excluded.{column}" for column in CALL_COLUMNS)

    with _connection() as conn:
        conn.execute(
            f"""
            INSERT INTO calls ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(call_id) DO UPDATE SET {assignments}
            """,
            [values.get(column) for column in columns],
        )
        _insert_event(
            conn,
            call_id,
            status=status,
            reason=values.get("reason"),
            sip_status_code=values.get("sip_status_code"),
            sip_status=values.get("sip_status"),
            message=record.get("event_message") or "Call record created",
            details=record.get("event_details") or {"room_name": values["room_name"]},
            created_at=created_at,
        )
    return get_call_record(call_id) or {}


def update_call_record(
    call_id: Optional[str],
    *,
    status: Optional[str] = None,
    reason: Optional[str] = None,
    sip_status_code: Optional[Any] = None,
    sip_status: Optional[str] = None,
    sip_call_id: Optional[str] = None,
    participant_identity: Optional[str] = None,
    participant_status: Optional[str] = None,
    error: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    event_message: Optional[str] = None,
    event_details: Optional[dict[str, Any]] = None,
) -> bool:
    """Update a persisted call record. Returns False when call_id is absent or unknown."""
    if not call_id:
        return False

    updates: dict[str, Any] = {"updated_at": now_iso()}
    if status is not None:
        updates["status"] = status
    if reason is not None:
        updates["reason"] = reason
    if sip_status_code is not None:
        updates["sip_status_code"] = str(sip_status_code)
    if sip_status is not None:
        updates["sip_status"] = sip_status
    if sip_call_id is not None:
        updates["sip_call_id"] = sip_call_id
    if participant_identity is not None:
        updates["participant_identity"] = participant_identity
    if participant_status is not None:
        updates["participant_status"] = participant_status
    if error is not None:
        updates["error"] = error
    if metadata is not None:
        updates["metadata_json"] = _json_dumps(metadata)

    timestamp_column = _timestamp_column_for_status(status)
    if timestamp_column:
        updates[timestamp_column] = updates["updated_at"]

    assignments = ", ".join(f"{column} = ?" for column in updates)
    values = [updates[column] for column in updates]

    with _connection() as conn:
        cursor = conn.execute(
            f"UPDATE calls SET {assignments} WHERE call_id = ?",
            [*values, call_id],
        )
        if cursor.rowcount == 0:
            return False
        _insert_event(
            conn,
            call_id,
            status=status or updates.get("status") or "updated",
            reason=reason,
            sip_status_code=sip_status_code,
            sip_status=sip_status,
            message=event_message,
            details=event_details,
            created_at=updates["updated_at"],
        )
    return True


def get_call_record(call_id: str) -> Optional[dict[str, Any]]:
    with _connection() as conn:
        row = conn.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
        if row is None:
            return None
        event_rows = conn.execute(
            """
            SELECT id, status, reason, sip_status_code, sip_status, message, details_json, created_at
            FROM call_events
            WHERE call_id = ?
            ORDER BY id ASC
            """,
            (call_id,),
        ).fetchall()

    record = dict(row)
    metadata_json = record.pop("metadata_json", "{}")
    record["metadata"] = _json_loads(metadata_json)
    record["events"] = [
        {
            **{key: event_row[key] for key in event_row.keys() if key != "details_json"},
            "details": _json_loads(event_row["details_json"]),
        }
        for event_row in event_rows
    ]
    return record


def list_call_records(limit: int = 100) -> list[dict[str, Any]]:
    """Return recent call records without full event histories for dashboard views."""
    bounded_limit = max(1, min(int(limit or 100), 500))
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT
                calls.*,
                (SELECT COUNT(*) FROM call_events WHERE call_events.call_id = calls.call_id) AS event_count
            FROM calls
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()

    records = []
    for row in rows:
        record = dict(row)
        metadata_json = record.pop("metadata_json", "{}")
        record["metadata"] = _json_loads(metadata_json)
        records.append(record)
    return records

# -*- coding: utf-8 -*-
"""PostgreSQL persistence for outbound call-control status."""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional


CALL_COLUMNS = (
    "room_name",
    "phone_number",
    "status",
    "reason",
    "sip_status_code",
    "sip_status",
    "sip_call_id",
    "participant_identity",
    "participant_status",
    "transcript_source",
    "transcript_text",
    "session_report_json",
    "vobiz_call_uuid",
    "vobiz_recording_id",
    "recording_source",
    "recording_url",
    "recording_duration_ms",
    "recording_format",
    "recording_type",
    "error",
    "metadata_json",
    "created_at",
    "updated_at",
    "dispatched_at",
    "dialing_at",
    "answered_at",
    "ended_at",
)

FAILURE_STATUSES = {
    "dispatch_failed",
    "failed",
    "failed_busy",
    "failed_no_answer",
    "failed_rejected",
    "failed_unreachable",
    "failed_trunk",
}

COMPLETED_STATUSES = (
    "completed",
    "failed",
    "failed_busy",
    "failed_no_answer",
    "failed_rejected",
    "failed_unreachable",
    "failed_trunk",
)

ACTIVE_STATUSES = (
    "dispatching",
    "dispatched",
    "dialing",
    "answered",
    "active",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_url() -> Optional[str]:
    """Return the configured PostgreSQL URL without logging or exposing it."""
    configured = (
        os.environ.get("CALL_API_DATABASE_URL")
        or os.environ.get("CALL_STATUS_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("POSTGRESQL_URL")
        or ""
    ).strip()
    return configured or None


def require_database_url() -> str:
    url = database_url()
    if not url:
        raise RuntimeError(
            "PostgreSQL persistence is required. Set CALL_API_DATABASE_URL "
            "or CALL_STATUS_DATABASE_URL on both the Call API and worker."
        )
    return url


def database_backend() -> str:
    return "postgres"


_schema_ensured = False


def _connect():
    global _schema_ensured
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL persistence requires the psycopg package. Run `uv sync` "
            "after adding psycopg[binary] to pyproject.toml."
        ) from exc

    timeout = int(os.environ.get("CALL_API_DB_CONNECT_TIMEOUT", "10"))
    conn = psycopg.connect(require_database_url(), row_factory=dict_row, connect_timeout=timeout)
    if not _schema_ensured:
        # ponytail: ensure schema checks are run only once per application lifecycle to avoid network latency on every connection
        _ensure_schema(conn)
        _schema_ensured = True
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _execute(conn: Any, sql: str, params: tuple[Any, ...] = ()):
    return conn.execute(sql, params)


def _ensure_schema(conn: Any) -> None:
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
            transcript_source TEXT,
            transcript_text TEXT,
            session_report_json TEXT,
            vobiz_call_uuid TEXT,
            vobiz_recording_id TEXT,
            recording_source TEXT,
            recording_url TEXT,
            recording_duration_ms TEXT,
            recording_format TEXT,
            recording_type TEXT,
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
    for column, column_type in {
        "transcript_source": "TEXT",
        "transcript_text": "TEXT",
        "session_report_json": "TEXT",
        "vobiz_call_uuid": "TEXT",
        "vobiz_recording_id": "TEXT",
        "recording_source": "TEXT",
        "recording_url": "TEXT",
        "recording_duration_ms": "TEXT",
        "recording_format": "TEXT",
        "recording_type": "TEXT",
    }.items():
        conn.execute(f"ALTER TABLE calls ADD COLUMN IF NOT EXISTS {column} {column_type}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS call_events (
            id BIGSERIAL PRIMARY KEY,
            call_id TEXT NOT NULL REFERENCES calls(call_id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            reason TEXT,
            sip_status_code TEXT,
            sip_status TEXT,
            message TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_call_events_call_id ON call_events(call_id, id)")


def _json_dumps(value: Optional[dict[str, Any]]) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _json_loads(raw_value: Any) -> dict[str, Any]:
    if not raw_value:
        return {}
    if isinstance(raw_value, dict):
        return raw_value
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


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _insert_event(
    conn: Any,
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
    _execute(
        conn,
        """
        INSERT INTO call_events (
            call_id, status, reason, sip_status_code, sip_status, message, details_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
        "transcript_source": record.get("transcript_source"),
        "transcript_text": record.get("transcript_text"),
        "session_report_json": _json_dumps(record.get("session_report")) if record.get("session_report") is not None else None,
        "vobiz_call_uuid": record.get("vobiz_call_uuid"),
        "vobiz_recording_id": record.get("vobiz_recording_id"),
        "recording_source": record.get("recording_source"),
        "recording_url": record.get("recording_url"),
        "recording_duration_ms": str(record["recording_duration_ms"]) if record.get("recording_duration_ms") is not None else None,
        "recording_format": record.get("recording_format"),
        "recording_type": record.get("recording_type"),
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

    columns = ("call_id", *CALL_COLUMNS)
    placeholders = ", ".join("%s" for _ in columns)
    assignments = ", ".join(f"{column}=excluded.{column}" for column in CALL_COLUMNS)

    with _connection() as conn:
        _execute(
            conn,
            f"""
            INSERT INTO calls ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(call_id) DO UPDATE SET {assignments}
            """,
            tuple(values.get(column) for column in columns),
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
    transcript_source: Optional[str] = None,
    transcript_text: Optional[str] = None,
    session_report: Optional[dict[str, Any]] = None,
    vobiz_call_uuid: Optional[str] = None,
    vobiz_recording_id: Optional[str] = None,
    recording_source: Optional[str] = None,
    recording_url: Optional[str] = None,
    recording_duration_ms: Optional[Any] = None,
    recording_format: Optional[str] = None,
    recording_type: Optional[str] = None,
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
    if transcript_source is not None:
        updates["transcript_source"] = transcript_source
    if transcript_text is not None:
        updates["transcript_text"] = transcript_text
    if session_report is not None:
        updates["session_report_json"] = _json_dumps(session_report)
    if vobiz_call_uuid is not None:
        updates["vobiz_call_uuid"] = vobiz_call_uuid
    if vobiz_recording_id is not None:
        updates["vobiz_recording_id"] = vobiz_recording_id
    if recording_source is not None:
        updates["recording_source"] = recording_source
    if recording_url is not None:
        updates["recording_url"] = recording_url
    if recording_duration_ms is not None:
        updates["recording_duration_ms"] = str(recording_duration_ms)
    if recording_format is not None:
        updates["recording_format"] = recording_format
    if recording_type is not None:
        updates["recording_type"] = recording_type
    if error is not None:
        updates["error"] = error
    if metadata is not None:
        updates["metadata_json"] = _json_dumps(metadata)

    timestamp_column = _timestamp_column_for_status(status)
    if timestamp_column:
        updates[timestamp_column] = updates["updated_at"]

    assignments = ", ".join(f"{column} = %s" for column in updates)
    values = tuple(updates[column] for column in updates)

    with _connection() as conn:
        cursor = _execute(
            conn,
            f"UPDATE calls SET {assignments} WHERE call_id = %s",
            (*values, call_id),
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


def _record_from_row(row: Any) -> dict[str, Any]:
    record = _row_to_dict(row)
    metadata_json = record.pop("metadata_json", "{}")
    session_report_json = record.pop("session_report_json", None)
    record["metadata"] = _json_loads(metadata_json)
    record["session_report"] = _json_loads(session_report_json)
    return record


def get_call_record(call_id: str) -> Optional[dict[str, Any]]:
    with _connection() as conn:
        row = _execute(conn, "SELECT * FROM calls WHERE call_id = %s", (call_id,)).fetchone()
        if row is None:
            return None
        event_rows = _execute(
            conn,
            """
            SELECT id, status, reason, sip_status_code, sip_status, message, details_json, created_at
            FROM call_events
            WHERE call_id = %s
            ORDER BY id ASC
            """,
            (call_id,),
        ).fetchall()

    record = _record_from_row(row)
    record["events"] = [
        {
            **{key: value for key, value in _row_to_dict(event_row).items() if key != "details_json"},
            "details": _json_loads(_row_to_dict(event_row).get("details_json")),
        }
        for event_row in event_rows
    ]
    return record


def get_call_record_by_vobiz_call_uuid(vobiz_call_uuid: str) -> Optional[dict[str, Any]]:
    with _connection() as conn:
        row = _execute(
            conn,
            "SELECT call_id FROM calls WHERE vobiz_call_uuid = %s ORDER BY created_at DESC LIMIT 1",
            (vobiz_call_uuid,),
        ).fetchone()
    if row is None:
        return None
    return get_call_record(_row_to_dict(row)["call_id"])


def list_call_records(limit: int = 100) -> list[dict[str, Any]]:
    """Return recent call records without full event histories for dashboard views."""
    bounded_limit = max(1, min(int(limit or 100), 500))
    with _connection() as conn:
        rows = _execute(
            conn,
            """
            SELECT
                calls.*,
                (SELECT COUNT(*) FROM call_events WHERE call_events.call_id = calls.call_id) AS event_count
            FROM calls
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (bounded_limit,),
        ).fetchall()

    return [_record_from_row(row) for row in rows]


def list_active_call_records() -> list[dict[str, Any]]:
    """Return active calls used by dashboard kill/start controls."""
    with _connection() as conn:
        rows = _execute(
            conn,
            """
            SELECT call_id, room_name, phone_number, status, metadata_json
            FROM calls
            WHERE status IN ('dispatching', 'dispatched', 'agent_ready', 'dialing', 'answered', 'active')
            ORDER BY created_at ASC
            """,
        ).fetchall()

    calls = []
    for row in rows:
        call = _row_to_dict(row)
        call["metadata"] = _json_loads(call.pop("metadata_json", "{}"))
        calls.append(call)
    return calls


def list_completed_call_records() -> list[dict[str, Any]]:
    """Return completed/failed calls in oldest-first order for sheet syncing."""
    with _connection() as conn:
        rows = _execute(
            conn,
            """
            SELECT *
            FROM calls
            WHERE status IN ('completed', 'dispatch_failed', 'failed', 'failed_busy', 'failed_no_answer', 'failed_rejected', 'failed_unreachable', 'failed_trunk')
            ORDER BY created_at ASC
            """,
        ).fetchall()

    return [_record_from_row(row) for row in rows]

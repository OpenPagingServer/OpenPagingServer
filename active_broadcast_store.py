#!/usr/bin/env python3

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path("/tmp/openpagingserver-runtime") if os.name != "nt" else (BASE_DIR / "runtime")
DB_PATH = RUNTIME_DIR / "active_broadcasts.sqlite3"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOCK_TIMEOUT_SECONDS = float(os.getenv("ACTIVE_BROADCAST_LOCK_TIMEOUT", "5"))
REMOVAL_DELIVERY_STATES = {"expired", "cancelled"}
MESSAGE_EXPIRATION_MESSAGE_ID_RE = re.compile(r"^\d+$")


def _message_expiration_trigger_targets(value):
    any_message = False
    message_ids = []
    seen = set()
    for token in str(value or "").strip().split("|"):
        token = token.strip()
        if not token.lower().startswith("msg="):
            continue
        for part in token[4:].replace(",", ".").split("."):
            part = part.strip()
            if not part:
                continue
            if part == "*":
                any_message = True
                continue
            if not MESSAGE_EXPIRATION_MESSAGE_ID_RE.fullmatch(part) or part in seen:
                continue
            seen.add(part)
            message_ids.append(part)
    return {"any_message": any_message, "message_ids": message_ids}


def _ensure_runtime_dir():
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Unable to create active broadcast runtime directory '{RUNTIME_DIR}': {exc}") from exc
    if not os.access(RUNTIME_DIR, os.W_OK):
        raise OSError(f"Active broadcast runtime directory is not writable: {RUNTIME_DIR}")


@contextmanager
def _connect():
    _ensure_runtime_dir()
    conn = sqlite3.connect(str(DB_PATH), timeout=max(0.1, LOCK_TIMEOUT_SECONDS))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(max(0.1, LOCK_TIMEOUT_SECONDS) * 1000)}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_broadcasts (
                id TEXT PRIMARY KEY,
                template_id TEXT,
                expires_rule TEXT,
                sender TEXT NOT NULL DEFAULT '',
                groups_value TEXT NOT NULL DEFAULT '',
                delivery TEXT NOT NULL DEFAULT 'pending',
                issued TEXT NOT NULL,
                expires TEXT,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_broadcast_controls (
                id TEXT PRIMARY KEY,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_active_broadcasts_pending "
            "ON active_broadcasts (delivery, issued)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_active_broadcasts_expires "
            "ON active_broadcasts (expires)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_active_broadcasts_template_id "
            "ON active_broadcasts (template_id)"
        )
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for pattern in (DATE_FORMAT, "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _stringify_datetime(value):
    if isinstance(value, datetime):
        return value.strftime(DATE_FORMAT)
    return value


def _normalize_record(record):
    data = dict(record or {})
    normalized = {}
    for key, value in data.items():
        normalized[str(key)] = _stringify_datetime(value)
    normalized["id"] = str(normalized.get("id") or "").strip()
    normalized["delivery"] = str(normalized.get("delivery") or "pending").strip() or "pending"
    normalized["sender"] = str(normalized.get("sender") or "").strip()
    normalized["groups"] = str(normalized.get("groups") or "").strip()
    normalized["issued"] = _stringify_datetime(normalized.get("issued")) or datetime.now().strftime(DATE_FORMAT)
    expires = _parse_datetime(normalized.get("expires"))
    normalized["expires"] = expires.strftime(DATE_FORMAT) if expires else None
    template_id = normalized.get("template_id")
    if template_id is not None and template_id != "":
        normalized["template_id"] = str(template_id)
    return normalized


def _matches_expires_rule(record, template_id):
    token = str(template_id).strip()
    if not token:
        return False
    targets = _message_expiration_trigger_targets(record.get("expires_rule"))
    return bool(targets["any_message"] or token in targets["message_ids"])


def _row_to_record(row):
    if row is None:
        return None
    payload = row["payload"] if "payload" in row.keys() else ""
    try:
        data = json.loads(payload) if payload else {}
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    # Row columns are the live authority for mutable broadcast state.
    data["id"] = row["id"]
    data["template_id"] = row["template_id"]
    data["expires_rule"] = row["expires_rule"]
    data["sender"] = row["sender"]
    data["groups"] = row["groups_value"]
    data["delivery"] = row["delivery"]
    data["issued"] = row["issued"]
    data["expires"] = row["expires"]
    return _normalize_record(data)


def _delete_ids(conn, broadcast_ids):
    ids = [str(item).strip() for item in (broadcast_ids or []) if str(item).strip()]
    if not ids:
        return
    placeholders = ", ".join(["?"] * len(ids))
    rows = conn.execute(
        f"SELECT payload FROM active_broadcasts WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    runtime_root = RUNTIME_DIR.resolve()
    for row in rows:
        payload = row["payload"] if isinstance(row, sqlite3.Row) else (row[0] if row else "")
        try:
            data = json.loads(payload or "{}")
        except (TypeError, ValueError):
            data = {}
        recording_raw = str((data or {}).get("runtime_recording") or "").strip()
        if not recording_raw:
            continue
        try:
            recording_path = Path(recording_raw).resolve()
            if recording_path.is_file() and (recording_path == runtime_root or runtime_root in recording_path.parents):
                recording_path.unlink()
        except OSError:
            pass
    placeholders = ", ".join(["?"] * len(ids))
    conn.execute(f"DELETE FROM active_broadcasts WHERE id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM active_broadcast_controls WHERE id IN ({placeholders})", ids)


def _prune_expired_records(conn):
    now_text = datetime.now().strftime(DATE_FORMAT)
    rows = conn.execute(
        "SELECT id FROM active_broadcasts "
        "WHERE expires IS NOT NULL AND expires <> '' AND expires <= ?",
        (now_text,),
    ).fetchall()
    expired_ids = [row["id"] for row in rows]
    if expired_ids:
        _delete_ids(conn, expired_ids)
    return expired_ids


def put_active_broadcast(record):
    normalized = _normalize_record(record)
    if not normalized["id"]:
        raise ValueError("Active broadcast record requires an id")
    payload = json.dumps(normalized, sort_keys=True)
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO active_broadcasts (
                id, template_id, expires_rule, sender, groups_value,
                delivery, issued, expires, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["id"],
                normalized.get("template_id"),
                normalized.get("expires_rule"),
                normalized.get("sender", ""),
                normalized.get("groups", ""),
                normalized.get("delivery", "pending"),
                normalized.get("issued"),
                normalized.get("expires"),
                payload,
            ),
        )
        conn.execute("DELETE FROM active_broadcast_controls WHERE id = ?", (normalized["id"],))
    return normalized["id"]


def fetch_active_broadcast(broadcast_id):
    wanted = str(broadcast_id or "").strip()
    if not wanted:
        return None
    with _connect() as conn:
        _prune_expired_records(conn)
        row = conn.execute(
            "SELECT id, template_id, expires_rule, sender, groups_value, delivery, issued, expires, payload "
            "FROM active_broadcasts WHERE id = ? LIMIT 1",
            (wanted,),
        ).fetchone()
    return _row_to_record(row)


def list_active_broadcasts(limit=200):
    with _connect() as conn:
        _prune_expired_records(conn)
        rows = conn.execute(
            "SELECT id, template_id, expires_rule, sender, groups_value, delivery, issued, expires, payload "
            "FROM active_broadcasts ORDER BY issued DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return [_row_to_record(row) for row in rows if row is not None]


def list_pending_active_broadcast_ids(limit=20, exclude_sender="sendmsgd"):
    with _connect() as conn:
        _prune_expired_records(conn)
        rows = conn.execute(
            "SELECT id, template_id, expires_rule, sender, groups_value, delivery, issued, expires, payload "
            "FROM active_broadcasts "
            "WHERE delivery = '' OR delivery = 'pending' "
            "ORDER BY issued ASC",
        ).fetchall()
    pending_ids = []
    excluded_sender = str(exclude_sender or "").strip()
    wanted = max(0, int(limit))
    if wanted == 0:
        return pending_ids
    for row in rows:
        record = _row_to_record(row)
        if record is None:
            continue
        if excluded_sender and str(record.get("sender") or "").strip() == excluded_sender and not bool(record.get("monitor_child")):
            continue
        pending_ids.append(record["id"])
        if wanted and len(pending_ids) >= wanted:
            break
    return pending_ids


def claim_active_broadcast_delivery(broadcast_id, stream_id):
    wanted = str(broadcast_id or "").strip()
    if not wanted:
        return False
    with _connect() as conn:
        _prune_expired_records(conn)
        cursor = conn.execute(
            "UPDATE active_broadcasts SET delivery = ? "
            "WHERE id = ? AND (delivery = '' OR delivery = 'pending')",
            (f"sending:{stream_id}", wanted),
        )
    return cursor.rowcount > 0


def mark_active_broadcast_delivery(broadcast_id, status):
    wanted = str(broadcast_id or "").strip()
    if not wanted:
        return False
    state = str(status or "").strip()
    with _connect() as conn:
        if state in REMOVAL_DELIVERY_STATES:
            cursor = conn.execute("DELETE FROM active_broadcasts WHERE id = ?", (wanted,))
            conn.execute("DELETE FROM active_broadcast_controls WHERE id = ?", (wanted,))
        else:
            cursor = conn.execute(
                "UPDATE active_broadcasts SET delivery = ? WHERE id = ?",
                (state, wanted),
            )
    return cursor.rowcount > 0


def request_active_broadcast_stop(broadcast_id):
    wanted = str(broadcast_id or "").strip()
    if not wanted:
        return False
    now_text = datetime.now().strftime(DATE_FORMAT)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO active_broadcast_controls (id, stop_requested, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET stop_requested = 1, updated_at = excluded.updated_at
            """,
            (wanted, now_text),
        )
    return True


def clear_active_broadcast_stop_request(broadcast_id):
    wanted = str(broadcast_id or "").strip()
    if not wanted:
        return False
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM active_broadcast_controls WHERE id = ?", (wanted,))
    return cursor.rowcount > 0


def active_broadcast_stop_requested(broadcast_id):
    wanted = str(broadcast_id or "").strip()
    if not wanted:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT stop_requested FROM active_broadcast_controls WHERE id = ? LIMIT 1",
            (wanted,),
        ).fetchone()
    return bool(row and int(row["stop_requested"] or 0))


def expire_active_broadcasts():
    with _connect() as conn:
        return _prune_expired_records(conn)


def expire_active_broadcasts_by_template_ids(template_ids, exclude_broadcast_ids=None):
    wanted = [str(template_id).strip() for template_id in (template_ids or []) if str(template_id).strip()]
    if not wanted:
        return []
    excluded = [str(broadcast_id).strip() for broadcast_id in (exclude_broadcast_ids or []) if str(broadcast_id).strip()]
    where_parts = [f"template_id IN ({', '.join(['?'] * len(wanted))})"]
    params = list(wanted)
    if excluded:
        where_parts.append(f"id NOT IN ({', '.join(['?'] * len(excluded))})")
        params.extend(excluded)
    where_sql = " AND ".join(where_parts)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM active_broadcasts WHERE {where_sql}",
            params,
        ).fetchall()
        expired_ids = [row["id"] for row in rows]
        if expired_ids:
            _delete_ids(conn, expired_ids)
    return expired_ids


def expire_active_broadcasts_triggered_by_template(template_id, exclude_broadcast_ids=None):
    token = str(template_id or "").strip()
    if not token:
        return []
    excluded = {str(broadcast_id).strip() for broadcast_id in (exclude_broadcast_ids or []) if str(broadcast_id).strip()}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, template_id, expires_rule, sender, groups_value, delivery, issued, expires, payload "
            "FROM active_broadcasts WHERE expires_rule LIKE '%msg=%'",
        ).fetchall()
        expired_ids = []
        for row in rows:
            record = _row_to_record(row)
            if record and record["id"] not in excluded and _matches_expires_rule(record, token):
                expired_ids.append(record["id"])
        if expired_ids:
            _delete_ids(conn, expired_ids)
    return expired_ids


def expire_active_broadcasts_any_message(exclude_broadcast_ids=None):
    excluded = {str(broadcast_id).strip() for broadcast_id in (exclude_broadcast_ids or []) if str(broadcast_id).strip()}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, template_id, expires_rule, sender, groups_value, delivery, issued, expires, payload "
            "FROM active_broadcasts WHERE expires_rule LIKE '%msg=%'",
        ).fetchall()
        expired_ids = []
        for row in rows:
            record = _row_to_record(row)
            if not record or record["id"] in excluded:
                continue
            if _message_expiration_trigger_targets(record.get("expires_rule")).get("any_message"):
                expired_ids.append(record["id"])
        if expired_ids:
            _delete_ids(conn, expired_ids)
    return expired_ids

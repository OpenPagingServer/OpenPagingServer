#!/usr/bin/env python3

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path("/tmp/openpagingserver-runtime") if os.name != "nt" else (BASE_DIR / "runtime")
DB_PATH = RUNTIME_DIR / "active_broadcasts.sqlite3"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOCK_TIMEOUT_SECONDS = float(os.getenv("ACTIVE_BROADCAST_LOCK_TIMEOUT", "5"))
REMOVAL_DELIVERY_STATES = {"expired", "cancelled"}


def _ensure_runtime_dir():
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Unable to create active broadcast runtime directory '{RUNTIME_DIR}': {exc}") from exc
    if not os.access(RUNTIME_DIR, os.W_OK):
        raise OSError(f"Active broadcast runtime directory is not writable: {RUNTIME_DIR}")


def _connect():
    _ensure_runtime_dir()
    conn = sqlite3.connect(str(DB_PATH), timeout=max(0.1, LOCK_TIMEOUT_SECONDS))
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
    return conn


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
    raw = str(record.get("expires_rule") or "").strip()
    if not raw.lower().startswith("msg="):
        return False
    token = str(template_id).strip()
    if not token:
        return False
    parts = [part.strip() for part in raw[4:].split(".") if part.strip()]
    return token in parts


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
    if not str(data.get("id") or "").strip():
        data["id"] = row["id"]
    if not str(data.get("template_id") or "").strip():
        data["template_id"] = row["template_id"]
    if not str(data.get("expires_rule") or "").strip():
        data["expires_rule"] = row["expires_rule"]
    if not str(data.get("sender") or "").strip():
        data["sender"] = row["sender"]
    if not str(data.get("groups") or "").strip():
        data["groups"] = row["groups_value"]
    if not str(data.get("delivery") or "").strip():
        data["delivery"] = row["delivery"]
    if not str(data.get("issued") or "").strip():
        data["issued"] = row["issued"]
    if not str(data.get("expires") or "").strip():
        data["expires"] = row["expires"]
    return _normalize_record(data)


def _delete_ids(conn, broadcast_ids):
    ids = [str(item).strip() for item in (broadcast_ids or []) if str(item).strip()]
    if not ids:
        return
    placeholders = ", ".join(["?"] * len(ids))
    conn.execute(f"DELETE FROM active_broadcasts WHERE id IN ({placeholders})", ids)


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


def list_pending_active_broadcast_ids(limit=20, exclude_sender="sendmsgd"):
    with _connect() as conn:
        _prune_expired_records(conn)
        rows = conn.execute(
            "SELECT id FROM active_broadcasts "
            "WHERE sender <> ? AND (delivery = '' OR delivery = 'pending') "
            "ORDER BY issued ASC LIMIT ?",
            (str(exclude_sender or ""), max(0, int(limit))),
        ).fetchall()
    return [row["id"] for row in rows]


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
        else:
            cursor = conn.execute(
                "UPDATE active_broadcasts SET delivery = ? WHERE id = ?",
                (state, wanted),
            )
    return cursor.rowcount > 0


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


def expire_active_broadcasts_triggered_by_template(template_id):
    token = str(template_id or "").strip()
    if not token:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, template_id, expires_rule, sender, groups_value, delivery, issued, expires, payload "
            "FROM active_broadcasts WHERE expires_rule LIKE 'msg=%'",
        ).fetchall()
        expired_ids = []
        for row in rows:
            record = _row_to_record(row)
            if record and _matches_expires_rule(record, token):
                expired_ids.append(record["id"])
        if expired_ids:
            _delete_ids(conn, expired_ids)
    return expired_ids

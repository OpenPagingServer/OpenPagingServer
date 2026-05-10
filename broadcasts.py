#!/usr/bin/env python3

import re
import uuid
from datetime import datetime, timedelta

from active_broadcast_store import (
    expire_active_broadcasts_by_template_ids,
    expire_active_broadcasts_triggered_by_template,
    put_active_broadcast,
)


TYPE_MAP = {
    "text": "TextMessage",
    "audio": "AudioMessage",
    "text+audio": "Text+AudioMessage",
    "liveaudio": "Page",
    "liveaudio+text": "Page",
    "Page": "Page",
    "AudioMessage": "AudioMessage",
    "TextMessage": "TextMessage",
    "Text+AudioMessage": "Text+AudioMessage",
}


def runtime_type(value):
    return TYPE_MAP.get(str(value or "").strip(), str(value or "").strip() or "TextMessage")


def legacy_type(value):
    token = str(value or "").strip()
    if token == "TextMessage":
        return "text"
    if token == "AudioMessage":
        return "audio"
    if token == "Text+AudioMessage":
        return "text+audio"
    if token == "Page":
        return "liveaudio"
    return token


def is_audio_type(value):
    return legacy_type(value) in ("audio", "text+audio", "liveaudio", "liveaudio+text")


def is_text_type(value):
    return legacy_type(value) in ("text", "text+audio", "liveaudio+text")


def table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    rows = cursor.fetchall()
    columns = set()
    for row in rows:
        if isinstance(row, dict):
            columns.add(row.get("Field"))
        else:
            columns.add(row[0])
    return {column for column in columns if column}


def row_get(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def history_update_delivery(cursor, broadcast_ids, status):
    columns = table_columns(cursor, "broadcasts")
    if "delivery" not in columns:
        return
    ids = [str(broadcast_id).strip() for broadcast_id in (broadcast_ids or []) if str(broadcast_id).strip()]
    if not ids:
        return
    placeholders = ", ".join(["%s"] * len(ids))
    cursor.execute(
        f"UPDATE broadcasts SET delivery = %s WHERE id IN ({placeholders})",
        tuple([status] + ids),
    )


def parse_expires(value, issued=None):
    raw = str(value or "").strip()
    if not raw or raw.lower() == "manual":
        return None, raw
    if raw.lower().startswith("msg="):
        return None, raw
    match = re.fullmatch(r"(\d+)\s*m", raw, re.IGNORECASE)
    if match:
        base = issued or datetime.now()
        return base + timedelta(minutes=int(match.group(1))), raw
    return None, raw


def fetch_template(cursor, message_id):
    columns = table_columns(cursor, "messages")
    wanted = [
        "messageid",
        "name",
        "shortmessage",
        "longmessage",
        "icon",
        "color",
        "type",
        "expires",
        "image",
        "audio",
        "priority",
        "vendor_specific",
    ]
    selected = [column for column in wanted if column in columns]
    cursor.execute(
        f"SELECT {', '.join(selected)} FROM messages WHERE messageid = %s LIMIT 1",
        (message_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(zip(selected, row))


def create_broadcast_from_template(cursor, template, groups, sender=""):
    issued = datetime.now()
    expires_at, expires_rule = parse_expires(template.get("expires"), issued)
    broadcast_id = uuid.uuid4().hex
    values = {
        "id": broadcast_id,
        "name": template.get("name") or "",
        "shortmessage": template.get("shortmessage") or "",
        "longmessage": template.get("longmessage") or "",
        "icon": template.get("icon") or "",
        "color": template.get("color") or "",
        "vendor_specific": template.get("vendor_specific") or "",
        "template_id": template.get("messageid"),
        "expires_rule": expires_rule,
        "type": runtime_type(template.get("type")),
        "expires": expires_at,
        "issued": issued,
        "groups": str(groups or ""),
        "image": template.get("image") or "",
        "audio": template.get("audio") or "",
        "sender": sender or "",
        "priority": template.get("priority") or "Normal",
        "delivery": "pending",
    }
    columns = table_columns(cursor, "broadcasts")
    insert_columns = [column for column in values if column in columns]
    placeholders = ", ".join(["%s"] * len(insert_columns))
    column_sql = ", ".join(f"`{column}`" for column in insert_columns)
    cursor.execute(
        f"INSERT INTO broadcasts ({column_sql}) VALUES ({placeholders})",
        tuple(values[column] for column in insert_columns),
    )
    put_active_broadcast(values)
    return broadcast_id, expires_rule


def expire_message_rule_broadcasts(cursor, expires_rule, exclude_broadcast_ids=None):
    raw = str(expires_rule or "").strip()
    if not raw.lower().startswith("msg="):
        return
    template_ids = [item.strip() for item in raw[4:].split(".") if item.strip()]
    if not template_ids:
        return
    expired_ids = expire_active_broadcasts_by_template_ids(
        template_ids,
        exclude_broadcast_ids=exclude_broadcast_ids,
    )
    history_update_delivery(cursor, expired_ids, "expired")


def expire_broadcasts_triggered_by_template(cursor, template_id):
    expired_ids = expire_active_broadcasts_triggered_by_template(template_id)
    history_update_delivery(cursor, expired_ids, "expired")

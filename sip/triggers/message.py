import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from active_broadcast_store import put_active_broadcast
from audio_utils import generate_wav
from broadcasts import (
    expand_broadcast_record_variables,
    expire_broadcasts_triggered_by_template as shared_expire_broadcasts_triggered_by_template,
    expire_message_rule_broadcasts as shared_expire_message_rule_broadcasts,
    message_expiration_trigger_targets,
    parse_message_expiration,
)

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

trigger_name = "message"

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")


def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )


def table_columns(cursor, table):
    cursor.execute(f"SHOW COLUMNS FROM `{table}`")
    return {row[0] for row in cursor.fetchall()}


def runtime_type(value):
    mapping = {
        "text": "TextMessage",
        "audio": "AudioMessage",
        "text+audio": "Text+AudioMessage",
        "liveaudio": "Page",
        "liveaudio+text": "Page",
    }
    token = str(value or "").strip()
    return mapping.get(token, token or "TextMessage")


def parse_expires(value, issued):
    return parse_message_expiration(value, issued)


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
        f"SELECT {', '.join('`' + column + '`' for column in selected)} FROM messages WHERE messageid = %s LIMIT 1",
        (message_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(selected, row))


def message_exists(cursor, message_id):
    cursor.execute(
        "SELECT 1 FROM messages WHERE messageid = %s LIMIT 1",
        (message_id,),
    )
    return cursor.fetchone() is not None


def first_existing(columns, names):
    for name in names:
        if name in columns:
            return name
    return None


def clean_groups(value):
    raw = str(value or "").strip()
    parts = []
    for part in raw.replace(",", ".").split("."):
        part = part.strip()
        if part and part not in parts:
            parts.append(part)
    return ".".join(parts)


def fetch_siptrunk_groups(cursor, message_id, sender=""):
    table = "endpoints-input-siptrunk"
    columns = table_columns(cursor, table)

    group_column = first_existing(
        columns,
        [
            "groups",
            "group",
            "groupid",
            "group_id",
            "groupids",
            "group_ids",
        ],
    )

    if not group_column:
        return ""

    lookup_values = []

    if sender:
        lookup_values.append(str(sender).strip())

    if message_id:
        lookup_values.append(str(message_id).strip())
        lookup_values.append(f"{trigger_name}:{message_id}")
        lookup_values.append(f"{trigger_name},{message_id}")
        lookup_values.append(f"{trigger_name} {message_id}")

    lookup_columns = [
        column for column in [
            "extension",
            "exten",
            "number",
            "did",
            "callerid",
            "caller_id",
            "sender",
            "trigger",
            "args",
            "arg",
            "argument",
            "messageid",
            "message_id",
            "template_id",
        ] if column in columns
    ]

    for value in lookup_values:
        if not value:
            continue
        for column in lookup_columns:
            cursor.execute(
                f"SELECT `{group_column}` FROM `{table}` WHERE `{column}` = %s LIMIT 1",
                (value,),
            )
            row = cursor.fetchone()
            if row:
                groups = clean_groups(row[0])
                if groups:
                    return groups

    return ""


def split_group_and_message_value(cursor, value):
    raw = str(value or "").strip()

    for separator in [",", ":", "|"]:
        if separator in raw:
            left, right = [part.strip() for part in raw.rsplit(separator, 1)]
            if left and right:
                return clean_groups(left), right

    if "." in raw:
        parts = [part.strip() for part in raw.split(".") if part.strip()]
        for index in range(1, len(parts)):
            groups = clean_groups(".".join(parts[:index]))
            message_id = ".".join(parts[index:])
            if groups and message_id and message_exists(cursor, message_id):
                return groups, message_id

        for index in range(len(parts) - 1, 0, -1):
            groups = clean_groups(".".join(parts[:index]))
            message_id = ".".join(parts[index:])
            if groups and message_id:
                return groups, message_id

    return "", raw


def resolve_group_and_message(cursor, raw, sender=""):
    value = str(raw or "").strip()

    groups, message_id = split_group_and_message_value(cursor, value)
    if groups and message_id:
        return groups, message_id

    message_id = value
    groups = fetch_siptrunk_groups(cursor, message_id, sender)

    if groups:
        return groups, message_id

    return "", message_id


def expire_triggered_broadcasts(cursor, template_id, groups, exclude_broadcast_ids=None):
    shared_expire_broadcasts_triggered_by_template(
        cursor,
        template_id,
        exclude_broadcast_ids=exclude_broadcast_ids,
        trigger_groups=groups,
    )


def expire_message_rule_broadcasts(cursor, expires_rule, groups, exclude_broadcast_ids=None):
    shared_expire_message_rule_broadcasts(
        cursor,
        expires_rule,
        exclude_broadcast_ids=exclude_broadcast_ids,
        trigger_groups=groups,
    )


def insert_broadcast(cursor, template, groups, sender):
    issued = datetime.now()
    expires_at, expires_rule = parse_expires(template.get("expires"), issued)
    values = {
        "id": uuid.uuid4().hex,
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
        "groups": clean_groups(groups),
        "image": template.get("image") or "",
        "audio": template.get("audio") or "",
        "sender": sender or "",
        "priority": template.get("priority") or "Normal",
        "delivery": "pending",
    }
    expand_broadcast_record_variables(cursor, values, source_values={"sender": sender or ""})
    columns = table_columns(cursor, "broadcasts")
    insert_columns = [column for column in values if column in columns]
    placeholders = ", ".join(["%s"] * len(insert_columns))
    column_sql = ", ".join(f"`{column}`" for column in insert_columns)
    cursor.execute(
        f"INSERT INTO broadcasts ({column_sql}) VALUES ({placeholders})",
        tuple(values[column] for column in insert_columns),
    )
    put_active_broadcast(values)
    return values["id"], expires_rule


def _create_broadcast(arg, sender=""):
    try:
        raw = str(arg or "").strip()
        if not raw:
            return

        conn = db()
        try:
            with conn.cursor() as cur:
                group_id, message_id = resolve_group_and_message(cur, raw, sender)
                if not message_id:
                    return
                if not group_id:
                    return

                template = fetch_template(cur, message_id)
                if not template:
                    return

                broadcast_id, expires_rule = insert_broadcast(cur, template, group_id, sender)
                expire_message_rule_broadcasts(cur, expires_rule, group_id, exclude_broadcast_ids=[broadcast_id])
                expire_triggered_broadcasts(cur, message_id, group_id, exclude_broadcast_ids=[broadcast_id])
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def handle(arg, sender=""):
    threading.Thread(target=_create_broadcast, args=(arg, sender), daemon=True).start()

    return {
        "session_class": None,
        "generator": generate_wav("./audio/sending.wav"),
        "on_start": None
    }

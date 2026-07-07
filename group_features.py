#!/usr/bin/env python3

import os
import uuid
from datetime import datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv

from active_broadcast_store import list_active_broadcasts


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

MONITOR_CATEGORY_ORDER = ("messages", "paging", "bells")
GROUP_FEATURE_COLUMNS = (
    "monitor_members",
    "monitor_categories",
    "page_pre_tone",
    "page_post_tone",
    "suspend_bells_on_emergency",
)


def db(cursorclass=pymysql.cursors.DictCursor):
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=cursorclass,
        autocommit=False,
    )


def table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    rows = cursor.fetchall()
    columns = set()
    for row in rows:
        if isinstance(row, dict):
            columns.add(str(row.get("Field") or ""))
        else:
            columns.add(str(row[0] or ""))
    return {column for column in columns if column}


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def group_member_tokens(value):
    return [token for token in str(value or "").replace(",", " ").split() if token]


def parse_monitor_categories(value):
    categories = []
    seen = set()
    for token in str(value or "").replace(";", ",").replace("|", ",").split(","):
        normalized = str(token or "").strip().lower()
        if normalized not in MONITOR_CATEGORY_ORDER or normalized in seen:
            continue
        seen.add(normalized)
        categories.append(normalized)
    return categories


def serialize_monitor_categories(values):
    wanted = {str(value or "").strip().lower() for value in (values or [])}
    return ",".join(category for category in MONITOR_CATEGORY_ORDER if category in wanted)


def ensure_group_feature_schema():
    conn = db()
    try:
        with conn.cursor() as cur:
            columns = table_columns(cur, "groups")
            statements = []
            if "monitor_members" not in columns:
                statements.append("ALTER TABLE `groups` ADD COLUMN monitor_members TEXT DEFAULT NULL")
            if "monitor_categories" not in columns:
                statements.append("ALTER TABLE `groups` ADD COLUMN monitor_categories VARCHAR(64) DEFAULT NULL")
            if "page_pre_tone" not in columns:
                statements.append("ALTER TABLE `groups` ADD COLUMN page_pre_tone TEXT DEFAULT NULL")
            if "page_post_tone" not in columns:
                statements.append("ALTER TABLE `groups` ADD COLUMN page_post_tone TEXT DEFAULT NULL")
            if "suspend_bells_on_emergency" not in columns:
                statements.append(
                    "ALTER TABLE `groups` ADD COLUMN suspend_bells_on_emergency TINYINT(1) NOT NULL DEFAULT 0"
                )
            for statement in statements:
                cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _normalize_group_row(row, columns=None):
    if isinstance(row, dict):
        source = dict(row)
    else:
        ordered_columns = list(columns or [])
        values = list(row or ())
        source = {
            ordered_columns[index]: values[index]
            for index in range(min(len(ordered_columns), len(values)))
        }
    available = set(columns or source.keys())
    return {
        "id": str(source.get("id") or "").strip(),
        "name": str(source.get("name") or "").strip(),
        "members": str(source.get("members") or "").strip(),
        "monitor_members": str(source.get("monitor_members") or "").strip() if "monitor_members" in available else "",
        "monitor_categories": parse_monitor_categories(source.get("monitor_categories") or "") if "monitor_categories" in available else [],
        "page_pre_tone": str(source.get("page_pre_tone") or "").strip() if "page_pre_tone" in available else "",
        "page_post_tone": str(source.get("page_post_tone") or "").strip() if "page_post_tone" in available else "",
        "suspend_bells_on_emergency": truthy(source.get("suspend_bells_on_emergency")) if "suspend_bells_on_emergency" in available else False,
    }


def fetch_group_rows(cursor, group_ids=None):
    columns = table_columns(cursor, "groups")
    selected = ["id", "name", "members"] + [column for column in GROUP_FEATURE_COLUMNS if column in columns]
    select_sql = ", ".join(f"`{column}`" for column in selected)
    ids = []
    if group_ids:
        seen = set()
        for group_id in group_ids:
            token = str(group_id or "").strip()
            if token and token not in seen:
                seen.add(token)
                ids.append(token)
    if ids:
        placeholders = ", ".join(["%s"] * len(ids))
        cursor.execute(
            f"SELECT {select_sql} FROM `groups` WHERE id IN ({placeholders}) ORDER BY name ASC",
            tuple(ids),
        )
    else:
        cursor.execute(f"SELECT {select_sql} FROM `groups` ORDER BY name ASC")
    return [_normalize_group_row(row, columns=selected) for row in cursor.fetchall()]


def selected_group_ids(groups_value, cursor=None):
    tokens = []
    for part in str(groups_value or "").split("."):
        token = str(part or "").strip()
        if token and token not in tokens:
            tokens.append(token)
    if "0" not in tokens:
        return tokens
    owns_connection = cursor is None
    conn = db() if owns_connection else None
    try:
        cur = conn.cursor() if owns_connection else cursor
        return [row["id"] for row in fetch_group_rows(cur) if row.get("id")]
    finally:
        if owns_connection:
            conn.close()


def selected_group_rows(cursor, groups_value):
    ids = selected_group_ids(groups_value, cursor=cursor)
    if not ids:
        return []
    rows = fetch_group_rows(cursor, ids)
    by_id = {row["id"]: row for row in rows if row.get("id")}
    ordered = [by_id[group_id] for group_id in ids if group_id in by_id]
    return ordered


def group_names_for_value(cursor, groups_value):
    rows = selected_group_rows(cursor, groups_value)
    names = []
    seen = set()
    for row in rows:
        label = row.get("name") or row.get("id") or ""
        if label and label not in seen:
            seen.add(label)
            names.append(label)
    return names


def regular_group_targets(rows):
    targets = []
    seen = set()
    for row in rows or []:
        for token in group_member_tokens(row.get("members")):
            if token not in seen:
                seen.add(token)
                targets.append(token)
    return targets


def monitor_category_enabled(row, category):
    normalized = str(category or "").strip().lower()
    return normalized in set(row.get("monitor_categories") or [])


def monitor_targets_for_rows(rows, category):
    targets = []
    seen = set()
    for row in rows or []:
        if not monitor_category_enabled(row, category):
            continue
        for token in group_member_tokens(row.get("monitor_members")):
            if token not in seen:
                seen.add(token)
                targets.append(token)
    return targets


def paging_tone_sequence(cursor, groups_value, position):
    key = "page_pre_tone" if str(position or "").strip().lower() == "pre" else "page_post_tone"
    tones = []
    seen = set()
    for row in selected_group_rows(cursor, groups_value):
        tone = str(row.get(key) or "").strip()
        if tone and tone not in seen:
            seen.add(tone)
            tones.append(tone)
    return tones


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _format_monitor_groups_label(rows):
    names = []
    seen = set()
    for row in rows or []:
        label = row.get("name") or row.get("id") or ""
        if label and label not in seen:
            seen.add(label)
            names.append(label)
    return ", ".join(names) if names else "selected groups"


def _monitor_longmessage(base_record, rows):
    sender = str((base_record or {}).get("sender") or "").strip() or "System"
    message_name = str((base_record or {}).get("name") or "").strip() or "Message"
    groups_label = _format_monitor_groups_label(rows)
    issued = _parse_datetime((base_record or {}).get("issued")) or datetime.now()
    original_parts = []
    shortmessage = str((base_record or {}).get("shortmessage") or "").strip()
    longmessage = str((base_record or {}).get("longmessage") or "").strip()
    if shortmessage:
        original_parts.append(shortmessage)
    if longmessage:
        original_parts.append(longmessage)
    quoted = "\n\n".join(original_parts)
    prefix = f"{sender} sent {message_name} to {groups_label} at {issued.strftime('%m/%d/%Y %I:%M %p')}"
    return prefix + f'\n"{quoted}"' if quoted else prefix


def build_monitor_message_child_records(cursor, base_record):
    rows = selected_group_rows(cursor, (base_record or {}).get("groups") or "")
    monitor_targets = monitor_targets_for_rows(rows, "messages")
    if not rows or not monitor_targets:
        return [], []
    sender = str((base_record or {}).get("sender") or "").strip() or "System"
    message_name = str((base_record or {}).get("name") or "").strip() or "Message"
    groups_label = _format_monitor_groups_label(rows)
    child = dict(base_record or {})
    child["id"] = uuid.uuid4().hex
    child["name"] = "Monitor"
    child["shortmessage"] = f"{sender} sent {message_name} to {groups_label}"
    child["longmessage"] = _monitor_longmessage(base_record, rows)
    child["delivery"] = "pending"
    child["explicit_targets"] = list(monitor_targets)
    child["monitor_child"] = True
    child["source_broadcast_id"] = str((base_record or {}).get("id") or "").strip()
    return monitor_targets, [child]


def record_is_livepage(record):
    return str((record or {}).get("runtime_kind") or "").strip().lower() == "livepage"


def record_is_bell(record):
    runtime_kind = str((record or {}).get("runtime_kind") or "").strip().lower()
    if runtime_kind == "bell":
        return True
    sender = str((record or {}).get("sender") or "").strip().lower()
    if sender == "belld":
        return True
    template_id = str((record or {}).get("template_id") or "").strip().lower()
    return template_id.startswith("bell-")


def record_is_immediate(record):
    tokens = [token.strip().lower() for token in str((record or {}).get("expires_rule") or "").split("|") if token.strip()]
    return "0m" in tokens


def record_is_active_emergency(record):
    if str((record or {}).get("priority") or "").strip().lower() != "emergency":
        return False
    if record_is_livepage(record) or record_is_bell(record):
        return False
    if bool((record or {}).get("monitor_child")):
        return False
    delivery = str((record or {}).get("delivery") or "").strip().lower()
    if delivery in {"expired", "cancelled", "failed"}:
        return False
    if record_is_immediate(record) and delivery in {"sent", "stopped"}:
        return False
    return True


def suspended_bell_groups(cursor):
    rows = [row for row in fetch_group_rows(cursor) if row.get("suspend_bells_on_emergency")]
    if not rows:
        return []
    eligible_ids = {row["id"] for row in rows if row.get("id")}
    affected_ids = set()
    for record in list_active_broadcasts(limit=500):
        if not record_is_active_emergency(record):
            continue
        for group_id in selected_group_ids(record.get("groups"), cursor=cursor):
            if group_id in eligible_ids:
                affected_ids.add(group_id)
    return [row for row in rows if row.get("id") in affected_ids]


def filtered_bell_group_ids(cursor, groups_value):
    suspended_ids = {row["id"] for row in suspended_bell_groups(cursor)}
    if not suspended_ids:
        return selected_group_ids(groups_value, cursor=cursor)
    return [group_id for group_id in selected_group_ids(groups_value, cursor=cursor) if group_id not in suspended_ids]

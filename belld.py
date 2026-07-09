#!/usr/bin/env python3

import os
import socket
import struct
import time
import uuid
import wave
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pymysql
from dotenv import load_dotenv
from endpoints import ULAW_TO_LINEAR_TABLE, audio_frames, connect_endpoint_ipc, mix_ulaw_frames

from active_broadcast_store import RUNTIME_DIR, mark_active_broadcast_delivery, put_active_broadcast
from group_features import (
    fetch_group_rows,
    filtered_bell_group_ids,
    monitor_targets_for_rows,
    regular_group_targets,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"

POLL_INTERVAL = max(0.2, float(os.getenv("BELL_POLL_INTERVAL", "0.5")))
BELL_OVERLAP_BRIDGE_SECONDS = max(0.0, float(os.getenv("BELL_OVERLAP_BRIDGE_SECONDS", "15")))
BELL_STARTUP_GRACE_SECONDS = max(1.0, float(os.getenv("BELL_STARTUP_GRACE_SECONDS", "90")))
BELL_PREBUILD_LEAD_SECONDS = max(5.0, float(os.getenv("BELL_PREBUILD_LEAD_SECONDS", "30")))
BELL_FRAME_RATE = 8000
BELL_FRAME_BYTES = 160
BELL_FRAME_SECONDS = BELL_FRAME_BYTES / BELL_FRAME_RATE
BELL_CLUSTER_DIR = RUNTIME_DIR / "belld-clusters"
LOG_FILE = BASE_DIR / "belld_debug.log"
last_seen_by_schedule = {}
audio_duration_cache = {}


def log(message):
    if not DEBUG:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_schema():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bell_schedules (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    enabled TINYINT(1) NOT NULL DEFAULT 1,
                    timezone VARCHAR(64) NOT NULL DEFAULT 'server',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """
            )
            try:
                cur.execute("ALTER TABLE bell_schedules ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'server'")
            except Exception:
                pass
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bell_lists (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    schedule_id INT NOT NULL DEFAULT 0,
                    name VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX schedule_id_idx (schedule_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """
            )
            try:
                cur.execute("ALTER TABLE bell_lists MODIFY schedule_id INT NOT NULL DEFAULT 0")
            except Exception:
                pass
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bell_events (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    list_id INT NOT NULL,
                    fire_time TIME NOT NULL,
                    audio TEXT NOT NULL,
                    days_of_week VARCHAR(32) NOT NULL DEFAULT '0,1,2,3,4,5,6',
                    INDEX list_id_idx (list_id),
                    INDEX fire_time_idx (fire_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """
            )
            try:
                cur.execute("ALTER TABLE bell_events ADD COLUMN days_of_week VARCHAR(32) NOT NULL DEFAULT '0,1,2,3,4,5,6'")
            except Exception:
                pass
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bell_schedule_groups (
                    schedule_id INT NOT NULL,
                    group_id VARCHAR(100) NOT NULL,
                    PRIMARY KEY (schedule_id, group_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bell_calendar (
                    schedule_id INT NOT NULL,
                    bell_date DATE NOT NULL,
                    list_id INT DEFAULT NULL,
                    PRIMARY KEY (schedule_id, bell_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bell_calendar_lists (
                    schedule_id INT NOT NULL,
                    bell_date DATE NOT NULL,
                    list_id INT NOT NULL,
                    PRIMARY KEY (schedule_id, bell_date, list_id),
                    INDEX bell_date_idx (bell_date),
                    INDEX list_id_idx (list_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """
            )
            cur.execute(
                """
                INSERT IGNORE INTO bell_calendar_lists (schedule_id, bell_date, list_id)
                SELECT schedule_id, bell_date, list_id
                FROM bell_calendar
                WHERE list_id IS NOT NULL AND list_id > 0
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bell_event_dispatches (
                    schedule_id INT NOT NULL,
                    bell_date DATE NOT NULL,
                    event_id INT NOT NULL,
                    broadcast_id VARCHAR(64) NOT NULL DEFAULT '',
                    fired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (schedule_id, bell_date, event_id),
                    INDEX bell_date_idx (bell_date),
                    INDEX fired_at_idx (fired_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    name VARCHAR(255) DEFAULT '',
                    shortmessage TEXT,
                    longmessage TEXT,
                    icon VARCHAR(255) DEFAULT '',
                    color VARCHAR(32) DEFAULT '',
                    vendor_specific TEXT,
                    type VARCHAR(64) DEFAULT 'TextMessage',
                    expires DATETIME DEFAULT NULL,
                    issued DATETIME DEFAULT NULL,
                    `groups` TEXT,
                    image VARCHAR(255) DEFAULT '',
                    audio TEXT,
                    sender VARCHAR(255) DEFAULT '',
                    priority VARCHAR(32) DEFAULT 'Normal',
                    delivery VARCHAR(32) DEFAULT 'pending',
                    template_id VARCHAR(64) DEFAULT NULL,
                    expires_rule VARCHAR(64) DEFAULT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """
            )
            cur.execute("SELECT COUNT(*) AS total FROM bell_schedules")
            if int(cur.fetchone()["total"]) == 0:
                cur.execute("INSERT INTO bell_schedules (name, enabled, timezone) VALUES ('Default Bell Schedule', 1, 'server')")
                cur.execute(
                    "INSERT INTO bell_lists (schedule_id, name) VALUES (0, 'Regular Day')"
                )
            cur.execute("SELECT COUNT(*) AS total FROM bell_lists WHERE schedule_id = 0")
            if int(cur.fetchone()["total"]) == 0:
                cur.execute("INSERT INTO bell_lists (schedule_id, name) VALUES (0, 'Regular Day')")
        conn.commit()
    finally:
        conn.close()


def table_columns(cur, table):
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    return {row["Field"] for row in cur.fetchall()}


def server_timezone_id():
    timezone_file = Path("/etc/timezone")
    if timezone_file.is_file():
        value = timezone_file.read_text(encoding="utf-8", errors="ignore").strip()
        if value:
            return value
    localtime = Path("/etc/localtime")
    try:
        if localtime.is_symlink():
            target = localtime.resolve()
            marker = "zoneinfo"
            parts = target.parts
            if marker in parts:
                idx = parts.index(marker)
                timezone_name = "/".join(parts[idx + 1:])
                if timezone_name:
                    return timezone_name
    except Exception:
        pass
    return time.tzname[0] if time.tzname else "UTC"


def timezone_for(value):
    timezone_name = str(value or "server").strip()
    if timezone_name == "server" or not timezone_name:
        timezone_name = server_timezone_id()
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        log(f"unknown timezone {timezone_name!r}; falling back to UTC")
        return ZoneInfo("UTC")


def fetch_enabled_schedules():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, timezone FROM bell_schedules WHERE enabled = 1 ORDER BY id ASC")
            return cur.fetchall()
    finally:
        conn.close()


def fetch_due_events(schedule, last_seen, now):
    day_value = now.date()
    start_seconds = bell_window_start_seconds(day_value, last_seen)
    end_seconds = bell_time_seconds(now)
    bell_date = day_value.strftime("%Y-%m-%d")
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.id AS schedule_id,
                    s.name AS schedule_name,
                    l.id AS list_id,
                    l.name AS list_name,
                    e.id AS event_id,
                    e.fire_time,
                    e.audio,
                    e.days_of_week,
                    GROUP_CONCAT(g.group_id ORDER BY g.group_id SEPARATOR '.') AS group_ids
                FROM bell_schedules s
                JOIN bell_calendar_lists c ON c.schedule_id = s.id
                JOIN bell_lists l ON l.id = c.list_id AND (l.schedule_id = 0 OR l.schedule_id = s.id)
                JOIN bell_events e ON e.list_id = l.id
                JOIN bell_schedule_groups g ON g.schedule_id = s.id
                LEFT JOIN bell_event_dispatches d
                  ON d.schedule_id = s.id
                 AND d.bell_date = c.bell_date
                 AND d.event_id = e.id
                WHERE s.enabled = 1
                  AND s.id = %s
                  AND c.bell_date = %s
                  AND d.event_id IS NULL
                  AND TIME_TO_SEC(e.fire_time) > %s
                  AND TIME_TO_SEC(e.fire_time) <= %s
                  AND FIND_IN_SET(%s, e.days_of_week)
                GROUP BY s.id, s.name, l.id, l.name, e.id, e.fire_time, e.audio, e.days_of_week
                ORDER BY e.fire_time ASC, e.id ASC
                """,
                (
                    schedule["id"],
                    bell_date,
                    start_seconds,
                    end_seconds,
                    now.strftime("%w"),
                ),
            )
            return cur.fetchall()
    finally:
        conn.close()


def fetch_schedule_events_after(schedule, day_value, after_reference, weekday):
    start_seconds = bell_window_start_seconds(day_value, after_reference)
    bell_date = day_value.strftime("%Y-%m-%d")
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.id AS schedule_id,
                    s.name AS schedule_name,
                    l.id AS list_id,
                    l.name AS list_name,
                    e.id AS event_id,
                    e.fire_time,
                    e.audio,
                    e.days_of_week,
                    GROUP_CONCAT(g.group_id ORDER BY g.group_id SEPARATOR '.') AS group_ids
                FROM bell_schedules s
                JOIN bell_calendar_lists c ON c.schedule_id = s.id
                JOIN bell_lists l ON l.id = c.list_id AND (l.schedule_id = 0 OR l.schedule_id = s.id)
                JOIN bell_events e ON e.list_id = l.id
                JOIN bell_schedule_groups g ON g.schedule_id = s.id
                LEFT JOIN bell_event_dispatches d
                  ON d.schedule_id = s.id
                 AND d.bell_date = c.bell_date
                 AND d.event_id = e.id
                WHERE s.enabled = 1
                  AND s.id = %s
                  AND c.bell_date = %s
                  AND d.event_id IS NULL
                  AND TIME_TO_SEC(e.fire_time) > %s
                  AND FIND_IN_SET(%s, e.days_of_week)
                GROUP BY s.id, s.name, l.id, l.name, e.id, e.fire_time, e.audio, e.days_of_week
                ORDER BY e.fire_time ASC, e.id ASC
                """,
                (
                    schedule["id"],
                    bell_date,
                    start_seconds,
                    weekday,
                ),
            )
            return cur.fetchall()
    finally:
        conn.close()


def bell_time_seconds(value):
    if hasattr(value, "hour") and hasattr(value, "minute") and hasattr(value, "second"):
        return (int(value.hour) * 3600) + (int(value.minute) * 60) + int(value.second)
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    text = str(value or "").strip()
    if not text:
        return 0
    parts = text.split(":")
    if len(parts) == 3:
        try:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = int(float(parts[2]))
            return (hours * 3600) + (minutes * 60) + seconds
        except ValueError:
            return 0
    return 0


def bell_event_start(day_value, event):
    return datetime.combine(day_value, datetime.min.time()) + timedelta(seconds=bell_time_seconds(event.get("fire_time")))


def bell_window_start_seconds(day_value, reference):
    if reference is None or reference.date() != day_value:
        return -1
    return bell_time_seconds(reference)


def bounded_catchup_reference(day_value, reference, now_value):
    floor = max(
        datetime.combine(day_value, datetime.min.time()) - timedelta(seconds=1),
        now_value - timedelta(seconds=BELL_STARTUP_GRACE_SECONDS),
    )
    if reference is None:
        return floor
    return max(reference, floor)


def bell_audio_duration_seconds(audio_value):
    audio_key = str(audio_value or "").strip()
    if not audio_key:
        return 0.0
    cached = audio_duration_cache.get(audio_key)
    if cached is not None:
        return cached
    frame_count = 0
    for _frame in audio_frames(audio_key):
        frame_count += 1
    duration = frame_count * BELL_FRAME_SECONDS
    audio_duration_cache[audio_key] = duration
    return duration


def bell_offset_frames(offset_seconds):
    return max(0, int(round(float(offset_seconds) / BELL_FRAME_SECONDS)))


def build_bell_cluster(schedule, events, start_index, day_value):
    first_event = events[start_index]
    cluster_start = bell_event_start(day_value, first_event)
    cluster_end = cluster_start + timedelta(seconds=bell_audio_duration_seconds(first_event.get("audio")))
    included = []
    index = start_index
    bridge = timedelta(seconds=BELL_OVERLAP_BRIDGE_SECONDS)
    while index < len(events):
        event = events[index]
        event_start = bell_event_start(day_value, event)
        if included and event_start > cluster_end + bridge:
            break
        duration = bell_audio_duration_seconds(event.get("audio"))
        event_end = event_start + timedelta(seconds=duration)
        cluster_end = max(cluster_end, event_end)
        item = dict(event)
        item["start_at"] = event_start
        item["duration_seconds"] = duration
        included.append(item)
        index += 1
    return {
        "schedule_id": int(schedule["id"]),
        "schedule_name": str(schedule.get("name") or "Bell Schedule"),
        "start_at": cluster_start,
        "end_at": cluster_end,
        "events": included,
    }, index - start_index


def write_cluster_audio_file(cluster):
    BELL_CLUSTER_DIR.mkdir(parents=True, exist_ok=True)
    event_ids = [str(int(event.get("event_id") or 0)) for event in cluster["events"]]
    first_event_id = event_ids[0] if event_ids else "0"
    last_event_id = event_ids[-1] if event_ids else "0"
    path = BELL_CLUSTER_DIR / (
        f"bell-cluster-{cluster['schedule_id']}-"
        f"{cluster['start_at'].strftime('%Y%m%d-%H%M%S')}-"
        f"{first_event_id}-{last_event_id}.wav"
    )
    # The filename is deterministic — reuse a cluster file that was pre-built
    # ahead of the fire time so dispatch is instant.
    try:
        if path.is_file() and path.stat().st_size > 44:
            return str(path)
    except OSError:
        pass
    frames_by_index = defaultdict(list)
    max_frame_index = 0
    for event in cluster["events"]:
        start_frame = bell_offset_frames((event["start_at"] - cluster["start_at"]).total_seconds())
        frame_index = start_frame
        yielded = False
        for frame in audio_frames(event.get("audio") or ""):
            frames_by_index[frame_index].append(frame)
            frame_index += 1
            yielded = True
        if yielded:
            max_frame_index = max(max_frame_index, frame_index)
        else:
            log(f"cluster event={event.get('event_id')} produced no audio")
    total_frames = max(
        1,
        max_frame_index,
        bell_offset_frames((cluster["end_at"] - cluster["start_at"]).total_seconds()),
    )
    temp_path = path.with_name(path.name + ".building")
    with wave.open(str(temp_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(BELL_FRAME_RATE)
        for frame_index in range(total_frames):
            mixed = mix_ulaw_frames(frames_by_index.get(frame_index, []))
            handle.writeframesraw(struct.pack("<160h", *[ULAW_TO_LINEAR_TABLE[byte] for byte in mixed[:BELL_FRAME_BYTES]]))
    os.replace(str(temp_path), str(path))
    log(f"cluster_audio_built path={path} frames={total_frames}")
    return str(path)


def prepare_upcoming_clusters(schedule, now):
    """Pre-build cluster audio files ahead of their fire time so dispatch at
    the scheduled moment is instant. Returns the next upcoming cluster start
    time (or None)."""
    day_value = now.date()
    events = fetch_schedule_events_after(schedule, day_value, now, now.strftime("%w"))
    lead_deadline = now + timedelta(seconds=BELL_PREBUILD_LEAD_SECONDS)
    next_start = None
    index = 0
    while index < len(events):
        event_start = bell_event_start(day_value, events[index])
        if next_start is None or event_start < next_start:
            next_start = event_start
        if event_start > lead_deadline:
            break
        cluster, consumed = build_bell_cluster(schedule, events, index, day_value)
        if len(cluster["events"]) > 1:
            try:
                write_cluster_audio_file(cluster)
            except Exception as exc:
                log(f"prebuild_error schedule={cluster['schedule_id']} error={exc}")
        index += max(1, consumed)
    return next_start


def insert_broadcast(record):
    conn = db()
    try:
        with conn.cursor() as cur:
            columns = table_columns(cur, "broadcasts")
            insert_columns = [column for column in record.keys() if column in columns]
            if not insert_columns:
                return
            placeholders = ", ".join(["%s"] * len(insert_columns))
            column_sql = ", ".join(f"`{column}`" for column in insert_columns)
            cur.execute(
                f"INSERT INTO broadcasts ({column_sql}) VALUES ({placeholders})",
                tuple(record[column] for column in insert_columns),
            )
        conn.commit()
    finally:
        conn.close()


def send_broadcast_ipc(broadcast_id):
    stream_id = uuid.uuid4().hex
    sock = None
    try:
        sock = connect_endpoint_ipc(timeout=5)
        sock.sendall(f"BROADCAST {stream_id} {broadcast_id}\n".encode("utf-8"))
        response = sock.recv(1024)
        log(f"broadcast_id={broadcast_id} stream={stream_id} response={response!r}")
        return b"DONE" in response
    except Exception as exc:
        log(f"broadcast_id={broadcast_id} ipc_error={exc}")
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def dispatch_bell_record(record):
    broadcast_id = str(record.get("id") or "").strip()
    insert_broadcast(record)
    put_active_broadcast(record)
    log(
        f"dispatch bell broadcast={broadcast_id} groups={record.get('groups')} "
        f"audio={record.get('audio')} template={record.get('template_id')}"
    )
    if not send_broadcast_ipc(broadcast_id):
        mark_active_broadcast_delivery(broadcast_id, "failed")


def mark_events_dispatched(schedule_id, day_value, event_ids, broadcast_id):
    cleaned_ids = []
    seen = set()
    for event_id in event_ids or []:
        try:
            numeric = int(event_id)
        except (TypeError, ValueError):
            continue
        if numeric <= 0 or numeric in seen:
            continue
        seen.add(numeric)
        cleaned_ids.append(numeric)
    if not cleaned_ids:
        return
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT IGNORE INTO bell_event_dispatches (schedule_id, bell_date, event_id, broadcast_id)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (
                        int(schedule_id),
                        day_value.strftime("%Y-%m-%d"),
                        event_id,
                        str(broadcast_id or ""),
                    )
                    for event_id in cleaned_ids
                ],
            )
        conn.commit()
    finally:
        conn.close()


def bell_group_targets(groups_value):
    conn = db()
    try:
        with conn.cursor() as cur:
            group_ids = filtered_bell_group_ids(cur, groups_value)
            if not group_ids:
                return "", []
            rows = fetch_group_rows(cur, group_ids)
    finally:
        conn.close()
    by_id = {row["id"]: row for row in rows if row.get("id")}
    ordered_rows = [by_id[group_id] for group_id in group_ids if group_id in by_id]
    targets = []
    seen = set()
    for token in regular_group_targets(ordered_rows) + monitor_targets_for_rows(ordered_rows, "bells"):
        if token not in seen:
            seen.add(token)
            targets.append(token)
    return ".".join(group_ids), targets


def fire_event(event, day_value):
    broadcast_id = uuid.uuid4().hex
    issued = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    schedule_name = event.get("schedule_name") or "Bell Schedule"
    list_name = event.get("list_name") or "Bell List"
    group_ids, explicit_targets = bell_group_targets(event.get("group_ids"))
    audio = str(event.get("audio") or "").strip()
    if not group_ids or not audio:
        log(f"skip event={event.get('event_id')} group_ids={group_ids!r} audio={audio!r}")
        return

    record = {
        "id": broadcast_id,
        "name": f"{schedule_name} Bell",
        "shortmessage": "Bell",
        "longmessage": f"Bell from {list_name}",
        "icon": "fa-solid fa-bell",
        "color": "#1976D2",
        "vendor_specific": "",
        "template_id": f"bell-{event.get('event_id')}",
        "expires_rule": "manual",
        "type": "AudioMessage",
        "expires": None,
        "issued": issued,
        "groups": group_ids,
        "image": "",
        "audio": audio,
        "sender": "belld",
        "priority": "Normal",
        "delivery": "pending",
        "explicit_targets": explicit_targets,
        "runtime_kind": "bell",
    }
    mark_events_dispatched(
        int(event.get("schedule_id") or 0),
        day_value,
        [event.get("event_id")],
        broadcast_id,
    )
    log(f"fire event={event.get('event_id')} broadcast={broadcast_id} groups={group_ids} audio={audio}")
    dispatch_bell_record(record)


def fire_event_cluster(cluster):
    events = cluster["events"]
    if len(events) == 1:
        fire_event(events[0], cluster["start_at"].date())
        return
    first_event = events[0]
    group_ids, explicit_targets = bell_group_targets(first_event.get("group_ids"))
    if not group_ids:
        log(f"skip cluster schedule={cluster['schedule_id']} missing groups")
        return
    audio_path = write_cluster_audio_file(cluster)
    broadcast_id = uuid.uuid4().hex
    issued = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    list_names = [str(event.get("list_name") or "Bell List") for event in events]
    unique_names = []
    for name in list_names:
        if name not in unique_names:
            unique_names.append(name)
    list_label = unique_names[0] if unique_names else "Bell List"
    if len(unique_names) > 1:
        list_label = f"{list_label} +{len(unique_names) - 1} more"
    template_id = f"bell-cluster-{cluster['schedule_id']}-{first_event.get('event_id')}-{events[-1].get('event_id')}"
    record = {
        "id": broadcast_id,
        "name": f"{cluster['schedule_name']} Bell",
        "shortmessage": "Bell",
        "longmessage": f"Bell sequence from {list_label}",
        "icon": "fa-solid fa-bell",
        "color": "#1976D2",
        "vendor_specific": "",
        "template_id": template_id,
        "expires_rule": "manual",
        "type": "AudioMessage",
        "expires": None,
        "issued": issued,
        "groups": group_ids,
        "image": "",
        "audio": audio_path,
        "sender": "belld",
        "priority": "Normal",
        "delivery": "pending",
        "explicit_targets": explicit_targets,
        "runtime_kind": "bell",
    }
    mark_events_dispatched(
        cluster["schedule_id"],
        cluster["start_at"].date(),
        [event.get("event_id") for event in events],
        broadcast_id,
    )
    log(
        f"fire cluster schedule={cluster['schedule_id']} events="
        f"{[event.get('event_id') for event in events]} groups={group_ids} audio={audio_path}"
    )
    dispatch_bell_record(record)


def main():
    ensure_schema()
    log(
        f"belld started poll_interval={POLL_INTERVAL} "
        f"overlap_bridge_seconds={BELL_OVERLAP_BRIDGE_SECONDS} "
        f"startup_grace_seconds={BELL_STARTUP_GRACE_SECONDS}"
    )
    while True:
        sleep_for = POLL_INTERVAL
        try:
            for schedule in fetch_enabled_schedules():
                try:
                    schedule_id = int(schedule["id"])
                    timezone = timezone_for(schedule.get("timezone"))
                    now = datetime.now(timezone).replace(tzinfo=None)
                    day_start = datetime.combine(now.date(), datetime.min.time()) - timedelta(seconds=1)
                    last_seen = last_seen_by_schedule.get(schedule_id)
                    if last_seen is None:
                        last_seen = max(day_start, now - timedelta(seconds=BELL_STARTUP_GRACE_SECONDS))
                    if now.date() != last_seen.date():
                        last_seen = day_start
                    last_seen = bounded_catchup_reference(now.date(), last_seen, now)
                    if last_seen > now:
                        last_seen_by_schedule[schedule_id] = last_seen
                        continue
                    due_events = fetch_due_events(schedule, last_seen, now)
                    advance_to = now
                    if due_events:
                        first_due_start = bell_event_start(now.date(), due_events[0]) - timedelta(seconds=1)
                        cluster_reference = bounded_catchup_reference(now.date(), first_due_start, now)
                        schedule_events = fetch_schedule_events_after(schedule, now.date(), cluster_reference, now.strftime("%w"))
                        index = 0
                        while index < len(schedule_events):
                            event = schedule_events[index]
                            if bell_event_start(now.date(), event) > now:
                                break
                            cluster, consumed = build_bell_cluster(schedule, schedule_events, index, now.date())
                            advance_to = max(advance_to, cluster["end_at"])
                            last_seen_by_schedule[schedule_id] = max(last_seen, advance_to)
                            fire_event_cluster(cluster)
                            index += consumed
                    last_seen_by_schedule[schedule_id] = max(last_seen, advance_to)
                    # Pre-build upcoming cluster audio and wake precisely for
                    # the next scheduled bell instead of a fixed poll delay.
                    try:
                        next_start = prepare_upcoming_clusters(schedule, now)
                    except Exception as exc:
                        next_start = None
                        log(f"prepare_error schedule_id={schedule_id} error={exc}")
                    if next_start is not None:
                        until_next = (next_start - datetime.now(timezone).replace(tzinfo=None)).total_seconds()
                        if until_next > 0:
                            sleep_for = min(sleep_for, until_next)
                        else:
                            sleep_for = min(sleep_for, 0.05)
                except Exception as exc:
                    log(f"schedule_error schedule_id={schedule.get('id')} error={exc}")
        except Exception as exc:
            log(f"loop_error={exc}")
        time.sleep(max(0.05, min(POLL_INTERVAL, sleep_for)))


if __name__ == "__main__":
    main()

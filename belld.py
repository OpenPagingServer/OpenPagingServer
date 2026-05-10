#!/usr/bin/env python3

import os
import socket
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pymysql
from dotenv import load_dotenv

from active_broadcast_store import put_active_broadcast, mark_active_broadcast_delivery

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"

IPC_HOST = "127.0.0.1"
IPC_PORT = 50000
POLL_INTERVAL = max(0.2, float(os.getenv("BELL_POLL_INTERVAL", "0.5")))
LOG_FILE = BASE_DIR / "belld_debug.log"
last_seen_by_schedule = {}


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
                WHERE s.enabled = 1
                  AND s.id = %s
                  AND c.bell_date = %s
                  AND e.fire_time > %s
                  AND e.fire_time <= %s
                  AND FIND_IN_SET(%s, e.days_of_week)
                GROUP BY s.id, s.name, l.id, l.name, e.id, e.fire_time, e.audio, e.days_of_week
                ORDER BY e.fire_time ASC, e.id ASC
                """,
                (
                    schedule["id"],
                    now.strftime("%Y-%m-%d"),
                    last_seen.strftime("%H:%M:%S"),
                    now.strftime("%H:%M:%S"),
                    now.strftime("%w"),
                ),
            )
            return cur.fetchall()
    finally:
        conn.close()


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
        sock = socket.create_connection((IPC_HOST, IPC_PORT), timeout=5)
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


def fire_event(event):
    broadcast_id = uuid.uuid4().hex
    issued = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    schedule_name = event.get("schedule_name") or "Bell Schedule"
    list_name = event.get("list_name") or "Bell List"
    group_ids = str(event.get("group_ids") or "").strip()
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
    }
    insert_broadcast(record)
    put_active_broadcast(record)
    log(f"fire event={event.get('event_id')} broadcast={broadcast_id} groups={group_ids} audio={audio}")
    if not send_broadcast_ipc(broadcast_id):
        mark_active_broadcast_delivery(broadcast_id, "failed")


def main():
    ensure_schema()
    log(f"belld started poll_interval={POLL_INTERVAL}")
    while True:
        try:
            for schedule in fetch_enabled_schedules():
                schedule_id = int(schedule["id"])
                timezone = timezone_for(schedule.get("timezone"))
                now = datetime.now(timezone).replace(tzinfo=None)
                last_seen = last_seen_by_schedule.get(schedule_id)
                if last_seen is None:
                    last_seen = now - timedelta(seconds=1)
                if now.date() != last_seen.date():
                    last_seen = datetime.combine(now.date(), datetime.min.time()) - timedelta(seconds=1)
                for event in fetch_due_events(schedule, last_seen, now):
                    fire_event(event)
                last_seen_by_schedule[schedule_id] = now
        except Exception as exc:
            log(f"loop_error={exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()


import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent.parent / ".env"
load_dotenv(ENV_PATH)

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
TRUNK_TABLE = "sip-trunks"
DIALPLAN_TABLE = "endpoints-input-siptrunk"

core = None


def init(core_obj):
    global core
    core = core_obj
    ensure_schema()


def log(msg):
    if core and hasattr(core, "log"):
        core.log(msg)
    else:
        print(msg)


def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )


def table_exists(cur, table):
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return cur.fetchone() is not None


def table_columns(cur, table):
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    return {row["Field"] for row in cur.fetchall() if row.get("Field")}


def ensure_schema():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{TRUNK_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`status` VARCHAR(255) NOT NULL DEFAULT 'Offline', "
                "`auth` VARCHAR(32) NOT NULL DEFAULT 'IP', "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`username` VARCHAR(255) DEFAULT NULL, "
                "`password` VARCHAR(255) DEFAULT NULL, "
                "`ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0', "
                "PRIMARY KEY (`id`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )

            columns = table_columns(cur, TRUNK_TABLE)
            if "id" not in columns:
                cur.execute(f"ALTER TABLE `{TRUNK_TABLE}` ADD COLUMN `id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST")
            if "status" not in columns:
                cur.execute(f"ALTER TABLE `{TRUNK_TABLE}` ADD COLUMN `status` VARCHAR(255) NOT NULL DEFAULT 'Offline'")
            if "auth" not in columns:
                cur.execute(f"ALTER TABLE `{TRUNK_TABLE}` ADD COLUMN `auth` VARCHAR(32) NOT NULL DEFAULT 'IP'")
            if "name" not in columns:
                cur.execute(f"ALTER TABLE `{TRUNK_TABLE}` ADD COLUMN `name` VARCHAR(255) NOT NULL DEFAULT ''")
            if "username" not in columns:
                cur.execute(f"ALTER TABLE `{TRUNK_TABLE}` ADD COLUMN `username` VARCHAR(255) DEFAULT NULL")
            if "password" not in columns:
                cur.execute(f"ALTER TABLE `{TRUNK_TABLE}` ADD COLUMN `password` VARCHAR(255) DEFAULT NULL")
            if "ipaddr" not in columns:
                cur.execute(f"ALTER TABLE `{TRUNK_TABLE}` ADD COLUMN `ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0'")

            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{DIALPLAN_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`extension` VARCHAR(100) NOT NULL DEFAULT '', "
                "`group` VARCHAR(255) DEFAULT NULL, "
                "`trigger` VARCHAR(100) NOT NULL DEFAULT 'page', "
                "`passcode` VARCHAR(64) DEFAULT NULL, "
                "PRIMARY KEY (`id`), KEY `extension_idx` (`extension`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )

            dialplan_columns = table_columns(cur, DIALPLAN_TABLE)
            if "id" not in dialplan_columns:
                cur.execute(f"ALTER TABLE `{DIALPLAN_TABLE}` ADD COLUMN `id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST")
            if "name" not in dialplan_columns:
                cur.execute(f"ALTER TABLE `{DIALPLAN_TABLE}` ADD COLUMN `name` VARCHAR(255) NOT NULL DEFAULT ''")
            if "extension" not in dialplan_columns:
                cur.execute(f"ALTER TABLE `{DIALPLAN_TABLE}` ADD COLUMN `extension` VARCHAR(100) NOT NULL DEFAULT ''")
            if "group" not in dialplan_columns:
                cur.execute(f"ALTER TABLE `{DIALPLAN_TABLE}` ADD COLUMN `group` VARCHAR(255) DEFAULT NULL")
            if "trigger" not in dialplan_columns:
                cur.execute(f"ALTER TABLE `{DIALPLAN_TABLE}` ADD COLUMN `trigger` VARCHAR(100) NOT NULL DEFAULT 'page'")
            if "passcode" not in dialplan_columns:
                cur.execute(f"ALTER TABLE `{DIALPLAN_TABLE}` ADD COLUMN `passcode` VARCHAR(64) DEFAULT NULL")

        conn.commit()
    finally:
        conn.close()


def status_label(row):
    raw_status = row.get("status")
    raw = str(raw_status or "").strip()
    if not raw:
        return "Offline"
    auth_type = str(row.get("auth") or "").upper()
    if auth_type == "USERPASS" and "," in raw and not raw.lower().startswith(("online", "offline")):
        ipaddr, user_agent = raw.split(",", 1)
        detail = " ".join(part for part in (ipaddr.strip(), user_agent.strip().strip("'\"")) if part)
        return f"Online ({detail})" if detail else "Online"
    if "," not in raw:
        return raw
    state, detail = raw.split(",", 1)
    state = state.strip() or "Online"
    detail = detail.strip().strip("'\"")
    return f"{state} ({detail})" if detail else state


def row_model(row):
    auth_type = str(row.get("auth") or "").upper()
    return ""


def row_type(row):
    auth_type = str(row.get("auth") or "").upper()
    return "Authenticated SIP Trunk" if auth_type == "USERPASS" else "IP SIP Trunk"


def row_name(row):
    name = str(row.get("name") or row.get("username") or row.get("ipaddr") or f"SIP Trunk {row.get('id')}")
    auth_type = str(row.get("auth") or "").upper()
    ipaddr = str(row.get("ipaddr") or "").strip()
    if auth_type == "IP" and ipaddr:
        return f"{name} ({ipaddr})"
    return name


def row_address(row):
    return ""


def dialplan_row_name(row):
    name = str(row.get("name") or row.get("extension") or f"SIP Extension {row.get('id')}")
    extension = str(row.get("extension") or "").strip()
    return f"{name} ({extension})" if extension else name


def get_endpoint_status():
    ensure_schema()
    endpoints = []
    conn = db()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    f"SELECT `id`, `name`, `auth`, `username`, `ipaddr`, `status` "
                    f"FROM `{TRUNK_TABLE}` ORDER BY `id` ASC"
                )
                rows = cur.fetchall()
            except pymysql.MySQLError as exc:
                log(f"siptrunks endpoint status error: {exc}")
                rows = []
            try:
                cur.execute(
                    f"SELECT `id`, `name`, `extension`, `group`, `trigger`, `passcode` "
                    f"FROM `{DIALPLAN_TABLE}` ORDER BY `id` ASC"
                )
                dialplan_rows = cur.fetchall()
            except pymysql.MySQLError as exc:
                log(f"siptrunks dialplan status error: {exc}")
                dialplan_rows = []
    finally:
        conn.close()

    for row in rows:
        endpoints.append(
            {
                "id": f"trunk-{row.get('id')}",
                "name": row_name(row),
                "address": row_address(row),
                "model": row_model(row),
                "status": status_label(row),
                "type": row_type(row),
                "direction": "Input",
                "output_capable": False,
                "bell_capable": False,
                "capabilities": ["management", "sip"],
            }
        )

    for row in dialplan_rows:
        endpoints.append(
            {
                "id": f"dialplan-{row.get('id')}",
                "name": dialplan_row_name(row),
                "address": "",
                "model": "",
                "status": "",
                "type": "SIP Trunk Extension",
                "direction": "Input",
                "output_capable": False,
                "bell_capable": False,
                "capabilities": ["management", "sip"],
            }
        )

    return {
        "module": "siptrunks",
        "display_name": "SIP Trunks",
        "output_capable": False,
        "endpoints": endpoints,
    }


def shutdown():
    pass
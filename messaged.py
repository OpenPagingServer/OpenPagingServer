#!/usr/bin/env python3

import os
import time
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from active_broadcast_store import expire_active_broadcasts

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
INTERVAL = float(os.getenv("MESSAGED_INTERVAL", "10"))
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"
LOG_FILE = BASE_DIR / "messaged_debug.log"


def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )


def log(message):
    if not DEBUG:
        return
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def expire_broadcasts():
    expired_ids = expire_active_broadcasts()
    if not expired_ids:
        return
    conn = db()
    try:
        with conn.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(expired_ids))
            cur.execute(
                f"UPDATE broadcasts SET delivery = %s WHERE id IN ({placeholders})",
                tuple(["expired"] + expired_ids),
            )
        conn.commit()
    finally:
        conn.close()


def main():
    while True:
        try:
            expire_broadcasts()
        except Exception as exc:
            log(f"loop error: {exc}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

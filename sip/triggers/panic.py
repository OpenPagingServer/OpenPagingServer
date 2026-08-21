import os
import threading
from pathlib import Path

import pymysql
from dotenv import load_dotenv

from broadcasts import create_custom_broadcast


load_dotenv(Path(__file__).resolve().parents[2] / ".env")

trigger_name = "panic"
immediate_cancel = True

PANIC_TITLE = "Panic Button"
PANIC_MESSAGE = "${sender:[CNAM]} (${sender:[CID]}) pressed a panic button at ${date+time}"

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


def clean_groups(value):
    groups = []
    for part in str(value or "").replace(",", ".").split("."):
        group_id = part.strip()
        if group_id and group_id not in groups:
            groups.append(group_id)
    return ".".join(groups)


def normalized_identity(sender="", sender_context=None):
    context = dict(sender_context or {})
    cid = str(context.get("cid") or sender or "").strip()
    cnam = str(context.get("cnam") or cid or "Unknown").strip()
    cid = cid or cnam or "Unknown"
    return cnam, cid


def _create_alert(groups, sender="", sender_context=None):
    group_value = clean_groups(groups)
    if not group_value:
        return

    cnam, cid = normalized_identity(sender, sender_context)
    values = {
        "name": PANIC_TITLE,
        "shortmessage": PANIC_MESSAGE,
        "longmessage": PANIC_MESSAGE,
        "type": "TextMessage",
        "priority": "Emergency",
        "cnam": cnam,
        "cid": cid,
    }

    conn = db()
    try:
        with conn.cursor() as cur:
            create_custom_broadcast(
                cur,
                values,
                groups=group_value,
                sender=f"{cnam} ({cid})",
            )
        conn.commit()
    finally:
        conn.close()


def handle(arg, group="", sender="", sender_context=None):
    groups = clean_groups(group or arg)
    threading.Thread(
        target=_create_alert,
        args=(groups, sender, sender_context),
        daemon=True,
        name="sip-panic-alert",
    ).start()
    return {"immediate_cancel": immediate_cancel}

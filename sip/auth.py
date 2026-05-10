import os
import pymysql
from dotenv import load_dotenv
from pathlib import Path
from ipaddress import ip_network, ip_address

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
TRUNK_TABLE = "sip-trunks"

def connect_db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        autocommit=True,
    )

def normalize_status(status):
    text = str(status or "").strip()
    return text or "Offline"

def ip_match(ipaddr, entry_ip):
    try:
        return ip_address(ipaddr) in ip_network(entry_ip, strict=False)
    except:
        return False

def auth_ip(ipaddr):
    conn = connect_db()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM `sip-trunks` WHERE auth='IP'")
            ip_entries = cur.fetchall()
            for entry in ip_entries:
                ip_val = entry.get('ipaddr')
                if ip_val and ip_val not in ("0.0.0.0", "0.0.0.0/0") and ip_match(ipaddr, ip_val):
                    return True
        return False
    finally:
        conn.close()

def get_password_for_user(username):
    conn = connect_db()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT password, ipaddr FROM `sip-trunks` WHERE auth='USERPASS' AND username=%s", (username,))
            entries = cur.fetchall()
            if entries:
                return entries[0].get('password'), entries[0].get('ipaddr')
        return None, None
    finally:
        conn.close()

def get_all_ip_trunks():
    conn = connect_db()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT ipaddr FROM `sip-trunks` WHERE auth='IP'")
            return [row['ipaddr'] for row in cur.fetchall() if row['ipaddr'] and '/' not in row['ipaddr'] and row['ipaddr'] != '0.0.0.0']
    finally:
        conn.close()

def update_trunk_status_by_ip(ipaddr, status):
    wanted = str(ipaddr or "").strip()
    if not wanted:
        return 0
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE `{TRUNK_TABLE}` SET status=%s WHERE auth='IP' AND ipaddr=%s",
                (normalize_status(status), wanted),
            )
            if cur.rowcount:
                return cur.rowcount
            cur.execute(f"SELECT id, ipaddr FROM `{TRUNK_TABLE}` WHERE auth='IP'")
            matches = []
            for row in cur.fetchall():
                trunk_id, trunk_ip = row[0], row[1]
                if trunk_ip and trunk_ip not in ("0.0.0.0", "0.0.0.0/0") and ip_match(wanted, trunk_ip):
                    matches.append(trunk_id)
            for trunk_id in matches:
                cur.execute(
                    f"UPDATE `{TRUNK_TABLE}` SET status=%s WHERE id=%s",
                    (normalize_status(status), trunk_id),
                )
            return len(matches)
    finally:
        conn.close()

def update_trunk_status_by_user(username, status):
    wanted = str(username or "").strip()
    if not wanted:
        return 0
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE `{TRUNK_TABLE}` SET status=%s WHERE auth='USERPASS' AND username=%s",
                (normalize_status(status), wanted),
            )
            return cur.rowcount
    finally:
        conn.close()

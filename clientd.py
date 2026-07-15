import base64
import hashlib
import hmac
import json
import os
import socket
import sqlite3
import struct
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pymysql
from dotenv import load_dotenv

from active_broadcast_store import fetch_active_broadcast, list_active_broadcasts
from tts import decode_tts_token, iter_tts_ffmpeg_chunks, split_audio_entries


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
DESKTOP_TOKEN_TTL_SECONDS = int(os.getenv("DESKTOP_TOKEN_TTL_SECONDS", "43200"))
DESKTOP_REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("DESKTOP_REFRESH_TOKEN_TTL_SECONDS", "2592000"))
DESKTOP_CLIENT_HEADER = "x-ops-desktop-client"
DESKTOP_TOKEN_PREFIX = "user/"
DESKTOP_REFRESH_TOKEN_PREFIX = "refresh/"
GUEST_MEMBER_TOKEN = "guest"
GUEST_TOKEN_TTL_SECONDS = 315360000
DESKTOP_STREAM_CODEC = str(os.getenv("DESKTOP_STREAM_CODEC", "mulaw")).strip().lower()
RUNTIME_DIR = Path("/tmp/openpagingserver-runtime") if os.name != "nt" else (BASE_DIR / "runtime")
RUNTIME_DB_PATH = RUNTIME_DIR / "desktop_runtime.sqlite3"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DESKTOP_CLIENT_STALE_SECONDS = int(os.getenv("DESKTOP_CLIENT_STALE_SECONDS", "45"))
CLIENTD_IPC_PORT = int(os.getenv("CLIENTD_IPC_PORT", "50011"))
DESKTOP_GROUP_CACHE_SECONDS = float(os.getenv("DESKTOP_GROUP_CACHE_SECONDS", "10"))
DESKTOP_TOUCH_INTERVAL_SECONDS = float(os.getenv("DESKTOP_TOUCH_INTERVAL_SECONDS", "5"))
CLIENTD_BROADCAST_LOOKUP_ATTEMPTS = max(1, int(os.getenv("CLIENTD_BROADCAST_LOOKUP_ATTEMPTS", "6")))
CLIENTD_BROADCAST_LOOKUP_DELAY_SECONDS = max(0.0, float(os.getenv("CLIENTD_BROADCAST_LOOKUP_DELAY_SECONDS", "0.05")))

connections = []
connections_lock = threading.Lock()
watcher_started = False
watcher_lock = threading.Lock()
ipc_started = False
ipc_lock = threading.Lock()
recent_broadcasts = {}
AUDIO_FRAME_PREFIX = b"A"
AUDIO_END_PREFIX = b"E"
group_cache = {}
group_cache_lock = threading.Lock()


def _ensure_runtime_dir():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _runtime_now_text():
    return datetime.now().strftime(DATE_FORMAT)


def _runtime_cutoff(seconds):
    return (datetime.now() - timedelta(seconds=max(1, int(seconds)))).strftime(DATE_FORMAT)


def _runtime_connect():
    _ensure_runtime_dir()
    conn = sqlite3.connect(str(RUNTIME_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS desktop_clients (
            connection_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_desktop_clients_user_id ON desktop_clients (user_id)")
    return conn


def _prune_runtime(conn):
    conn.execute("DELETE FROM desktop_clients WHERE last_seen < ?", (_runtime_cutoff(DESKTOP_CLIENT_STALE_SECONDS),))


def new_connection_id():
    return uuid.uuid4().hex


def mark_connected(connection_id, user_id):
    cid = str(connection_id or "").strip()
    uid = str(user_id if user_id is not None else "").strip()
    if not cid or not uid:
        return
    with _runtime_connect() as conn:
        _prune_runtime(conn)
        conn.execute(
            "INSERT OR REPLACE INTO desktop_clients (connection_id, user_id, last_seen) VALUES (?, ?, ?)",
            (cid, uid, _runtime_now_text()),
        )


def touch_connected(connection_id):
    cid = str(connection_id or "").strip()
    if not cid:
        return
    with _runtime_connect() as conn:
        _prune_runtime(conn)
        conn.execute("UPDATE desktop_clients SET last_seen = ? WHERE connection_id = ?", (_runtime_now_text(), cid))


def mark_disconnected(connection_id):
    cid = str(connection_id or "").strip()
    if not cid:
        return
    with _runtime_connect() as conn:
        conn.execute("DELETE FROM desktop_clients WHERE connection_id = ?", (cid,))


def connected_user_ids():
    with _runtime_connect() as conn:
        _prune_runtime(conn)
        rows = conn.execute("SELECT DISTINCT user_id FROM desktop_clients").fetchall()
    return {str(row["user_id"] if row["user_id"] is not None else "").strip() for row in rows if str(row["user_id"] if row["user_id"] is not None else "").strip()}


def user_has_connected_client(user_id):
    return str(user_id if user_id is not None else "").strip() in connected_user_ids()


def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
        read_timeout=10,
        write_timeout=10,
    )


def desktop_secret():
    source = os.getenv("FLASK_SECRET_KEY") or hashlib.sha256((DB_PASS or "openpagingserver").encode()).hexdigest()
    return source.encode("utf-8")


def desktop_member_token(user_id):
    return f"{DESKTOP_TOKEN_PREFIX}{str(user_id).strip()}"


def is_desktop_member_token(value):
    return str(value or "").strip().startswith(DESKTOP_TOKEN_PREFIX)


def desktop_member_user_id(value):
    token = str(value or "").strip()
    if not token.startswith(DESKTOP_TOKEN_PREFIX):
        return ""
    user_id = token[len(DESKTOP_TOKEN_PREFIX):].strip()
    return user_id if user_id.isdigit() else ""


def member_token_for_user(user_id):
    key = str(user_id if user_id is not None else "").strip()
    if key in {"", GUEST_MEMBER_TOKEN}:
        return GUEST_MEMBER_TOKEN
    return desktop_member_token(key)


def guest_receiver_active():
    try:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM systemsettings WHERE parameter='guest_receiver_enabled' LIMIT 1")
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return False
    return str((row or {}).get("value") or "0").strip() == "1"


def parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value).split(".", 1)[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def desktop_stream_settings(codec=None):
    normalized = str(codec or DESKTOP_STREAM_CODEC or "g722").strip().lower()
    if normalized == "g722":
        return {"codec": "g722", "sample_rate": 16000, "ffmpeg_format": "g722", "frame_size": 160}
    return {"codec": "mulaw", "sample_rate": 8000, "ffmpeg_format": "mulaw", "frame_size": 160}


def normalize_color(value):
    token = str(value or "").strip().lstrip("#")
    if len(token) == 3:
        token = "".join(ch * 2 for ch in token)
    if len(token) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in token):
        return "#f57c00"
    return "#" + token.lower()


def _signed_token_payload(token, expected_kind=None):
    raw = str(token or "").strip()
    if "." not in raw:
        return None
    payload_b64, signature = raw.rsplit(".", 1)
    expected = hmac.new(desktop_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode((payload_b64 + padding).encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if expected_kind is not None and str(payload.get("kind") or "") != str(expected_kind):
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload


def _token_session_id(payload):
    value = str((payload or {}).get("sid") or (payload or {}).get("session_id") or "").strip()
    return value if value and len(value) <= 128 else ""


def _session_id_for_user(user, session_id=None):
    wanted = str(session_id or (user or {}).get("desktop_session_id") or (user or {}).get("session_id") or "").strip()
    return wanted if wanted and len(wanted) <= 128 else ""


def build_desktop_token(user, ttl_seconds=None, session_id=None):
    ttl = ttl_seconds if ttl_seconds is not None else DESKTOP_TOKEN_TTL_SECONDS
    expires_at = int(time.time()) + max(60, ttl)
    sid = _session_id_for_user(user, session_id)
    payload = {
        "user_id": str(user.get("id") if user.get("id") is not None else "").strip(),
        "username": str(user.get("username") or ""),
        "role": str(user.get("role") or ""),
        "exp": expires_at,
    }
    if sid:
        payload["sid"] = sid
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    signature = hmac.new(desktop_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}", expires_at


def build_desktop_refresh_token(user, ttl_seconds=None, session_id=None):
    ttl = ttl_seconds if ttl_seconds is not None else DESKTOP_REFRESH_TOKEN_TTL_SECONDS
    expires_at = int(time.time()) + max(3600, ttl)
    sid = _session_id_for_user(user, session_id)
    payload = {
        "kind": DESKTOP_REFRESH_TOKEN_PREFIX.rstrip("/"),
        "user_id": str(user.get("id") if user.get("id") is not None else "").strip(),
        "username": str(user.get("username") or ""),
        "role": str(user.get("role") or ""),
        "exp": expires_at,
    }
    if sid:
        payload["sid"] = sid
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    signature = hmac.new(desktop_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}", expires_at


def active_session_for_token(session_id, user_id):
    sid = str(session_id or "").strip()
    uid = str(user_id if user_id is not None else "").strip()
    if not sid or not uid.isdigit():
        return None
    try:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, user_id, session_type, auth_provider, last_seen_at, last_full_activity, expires_at, revoked_at
                    FROM user_sessions
                    WHERE session_id=%s AND user_id=%s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > NOW())
                    LIMIT 1
                    """,
                    (sid, uid),
                )
                return cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return None


def user_from_desktop_payload(payload):
    if str((payload or {}).get("role") or "") == "guest":
        if not guest_receiver_active():
            return None
        return {"id": GUEST_MEMBER_TOKEN, "username": "Guest", "role": "guest"}
    user_id = str((payload or {}).get("user_id") if (payload or {}).get("user_id") is not None else "").strip()
    if not user_id.isdigit():
        return None
    session_id = _token_session_id(payload)
    if not active_session_for_token(session_id, user_id):
        return None
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, role, email, accountexpire, loginsleft, auth_provider FROM users WHERE id=%s LIMIT 1",
                (user_id,),
            )
            user = cur.fetchone()
    finally:
        conn.close()
    if not user:
        return None
    expire_text = str(user.get("accountexpire") or "").strip()[:10]
    if (
        len(expire_text) == 10
        and expire_text.count("-") == 2
        and expire_text[:4].isdigit()
        and expire_text != "0000-00-00"
        and expire_text < datetime.now().strftime("%Y-%m-%d")
    ):
        return None
    user["desktop_session_id"] = session_id
    return user


def verify_desktop_token(token):
    payload = _signed_token_payload(token)
    if not payload:
        return None
    return user_from_desktop_payload(payload)


def verify_desktop_refresh_token(token):
    payload = _signed_token_payload(token, DESKTOP_REFRESH_TOKEN_PREFIX.rstrip("/"))
    if not payload:
        return None
    return user_from_desktop_payload(payload)


def groups_for_user(user_id):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, members FROM `groups` ORDER BY name ASC")
            rows = cur.fetchall()
    finally:
        conn.close()
    wanted = member_token_for_user(user_id)
    groups = []
    for row in rows:
        members = str(row.get("members") or "").replace(",", " ").split()
        if wanted in members:
            groups.append({"id": str(row.get("id") or ""), "name": str(row.get("name") or "")})
    return groups


def cached_groups_for_user(user_id, force=False):
    user_key = str(user_id if user_id is not None else "").strip()
    if not user_key:
        return []
    now = time.time()
    with group_cache_lock:
        cached = group_cache.get(user_key)
        if not force and cached and (now - float(cached.get("loaded_at") or 0)) < DESKTOP_GROUP_CACHE_SECONDS:
            return list(cached.get("groups") or [])
    groups = groups_for_user(user_key)
    with group_cache_lock:
        group_cache[user_key] = {"groups": list(groups), "loaded_at": now}
    return groups


def cached_group_ids_for_user(user_id, force=False):
    return {str(group.get("id") or "").strip() for group in cached_groups_for_user(user_id, force=force) if str(group.get("id") or "").strip()}


def broadcast_explicit_targets(broadcast):
    explicit = (broadcast or {}).get("explicit_targets")
    targets = []
    if isinstance(explicit, (list, tuple, set)):
        source = explicit
    else:
        source = str(explicit or "").replace(",", " ").split()
    for item in source:
        token = str(item or "").strip()
        if token:
            targets.append(token)
    return targets


def user_in_broadcast(user_id, broadcast):
    explicit = broadcast_explicit_targets(broadcast)
    if explicit:
        return member_token_for_user(user_id) in explicit
    target_groups = [part.strip() for part in str((broadcast or {}).get("groups") or "").split(".") if part.strip()]
    if not target_groups:
        return False
    if "0" in target_groups:
        return True
    allowed = cached_group_ids_for_user(user_id)
    return any(group_id in allowed for group_id in target_groups)


def user_in_groups(user_id, groups_value):
    target_groups = [part.strip() for part in str(groups_value or "").split(".") if part.strip()]
    if not target_groups:
        return False
    if "0" in target_groups:
        return True
    allowed = cached_group_ids_for_user(user_id)
    return any(group_id in allowed for group_id in target_groups)


def first_audio_name(broadcast):
    for token in str((broadcast or {}).get("audio") or "").split(":"):
        token = token.strip()
        if token:
            return token
    return ""


def desktop_payload_for_broadcast(user, broadcast, server_origin):
    color = normalize_color((broadcast or {}).get("color"))
    audio_name = first_audio_name(broadcast)
    stream = desktop_stream_settings()
    return {
        "type": "broadcast",
        "broadcast_id": str(broadcast.get("id") or ""),
        "message_type": str(broadcast.get("type") or ""),
        "shortmessage": str(broadcast.get("shortmessage") or ""),
        "longmessage": str(broadcast.get("longmessage") or ""),
        "name": str(broadcast.get("name") or ""),
        "expires": str(broadcast.get("expires") or ""),
        "priority": str(broadcast.get("priority") or "Normal"),
        "color": color,
        "icon": str(broadcast.get("icon") or ""),
        "sender": str(broadcast.get("sender") or ""),
        "issued": str(broadcast.get("issued") or ""),
        "product_name": product_name(),
        "audio_url": "",
        "audio_mode": "websocket" if audio_name else "",
        "audio_codec": stream["codec"] if audio_name else "",
        "audio_sample_rate": stream["sample_rate"] if audio_name else 0,
        "has_audio": bool(audio_name),
        "groups": groups_for_user(user.get("id")),
    }


def product_name():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM systemsettings WHERE parameter='product_name' LIMIT 1")
            row = cur.fetchone()
    finally:
        conn.close()
    return str((row or {}).get("value") or "Open Paging Server")


def http_origin(headers):
    proto = str(headers.get("x-forwarded-proto") or headers.get("x-ops-forwarded-proto") or "http").split(",", 1)[0].strip() or "http"
    host = str(headers.get("x-forwarded-host") or headers.get("host") or "127.0.0.1").split(",", 1)[0].strip() or "127.0.0.1"
    return f"{proto}://{host}"


def recv_until(sock, marker, limit=65536):
    data = b""
    while marker not in data and len(data) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def websocket_accept_key(key):
    source = (key.strip() + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
    return base64.b64encode(hashlib.sha1(source).digest()).decode("ascii")


def send_ws_frame(sock, opcode, payload=b""):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length <= 0xFFFF:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    sock.sendall(bytes(header) + payload)


def send_ws_json(sock, payload):
    send_ws_frame(sock, 0x1, json.dumps(payload, separators=(",", ":")))


def read_ws_frame(sock):
    header = sock.recv(2)
    if len(header) < 2:
        return None, b""
    first, second = header
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]
    mask = sock.recv(4) if masked else b""
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            break
        payload += chunk
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def parse_ws_request(request_bytes):
    text = request_bytes.decode("utf-8", errors="ignore")
    lines = text.split("\r\n")
    request_line = lines[0].split() if lines else []
    target = request_line[1] if len(request_line) >= 2 else "/"
    parsed = urlparse(target)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return parsed.path, parse_qs(parsed.query), headers


def _remember_broadcast(broadcast):
    broadcast_id = str((broadcast or {}).get("id") or "").strip()
    if not broadcast_id:
        return
    recent_broadcasts[broadcast_id] = {"broadcast": broadcast, "seen_at": time.time()}
    cutoff = time.time() - 3600
    expired = [key for key, value in recent_broadcasts.items() if value.get("seen_at", 0) < cutoff]
    for key in expired:
        recent_broadcasts.pop(key, None)


def make_json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]
    return str(value)


def normalize_ipc_broadcast_payload(broadcast, fallback_id=""):
    if not isinstance(broadcast, dict):
        return None
    normalized = dict(broadcast)
    fallback = str(fallback_id or "").strip()
    broadcast_id = str(normalized.get("id") or "").strip() or fallback
    if not broadcast_id:
        return None
    normalized["id"] = broadcast_id
    _remember_broadcast(normalized)
    return normalized


def lookup_broadcast(broadcast_id):
    broadcast = fetch_active_broadcast(broadcast_id)
    if broadcast:
        _remember_broadcast(broadcast)
        return broadcast
    remembered = recent_broadcasts.get(str(broadcast_id or "").strip()) or {}
    return remembered.get("broadcast")


def resolve_broadcast_for_dispatch(broadcast_id, provided=None):
    resolved = normalize_ipc_broadcast_payload(provided, broadcast_id)
    if resolved:
        return resolved
    token = str(broadcast_id or "").strip()
    if not token:
        return None
    for attempt in range(CLIENTD_BROADCAST_LOOKUP_ATTEMPTS):
        resolved = lookup_broadcast(token)
        if resolved:
            return resolved
        if attempt + 1 < CLIENTD_BROADCAST_LOOKUP_ATTEMPTS and CLIENTD_BROADCAST_LOOKUP_DELAY_SECONDS > 0:
            time.sleep(CLIENTD_BROADCAST_LOOKUP_DELAY_SECONDS)
    return None


def register_connection(connection):
    with connections_lock:
        connections.append(connection)
    mark_connected(connection.connection_id, connection.user.get("id"))
    connection.last_runtime_touch = time.time()


def unregister_connection(connection):
    with connections_lock:
        if connection in connections:
            connections.remove(connection)
    mark_disconnected(connection.connection_id)


def connection_group_ids(connection, force=False):
    now = time.time()
    if (
        force
        or not getattr(connection, "group_ids_cache", None)
        or (now - float(getattr(connection, "groups_loaded_at", 0.0) or 0.0)) >= DESKTOP_GROUP_CACHE_SECONDS
    ):
        groups = cached_groups_for_user(connection.user.get("id"), force=force)
        connection.groups_cache = list(groups)
        connection.group_ids_cache = {str(group.get("id") or "").strip() for group in groups if str(group.get("id") or "").strip()}
        connection.groups_loaded_at = now
    return connection.group_ids_cache


def connection_in_groups(connection, groups_value):
    target_groups = [part.strip() for part in str(groups_value or "").split(".") if part.strip()]
    if not target_groups:
        return False
    if "0" in target_groups:
        return True
    allowed = connection_group_ids(connection)
    return any(group_id in allowed for group_id in target_groups)


def connection_in_broadcast(connection, broadcast):
    explicit = broadcast_explicit_targets(broadcast)
    if explicit:
        wanted = desktop_member_token(connection.user.get("id"))
        return wanted in explicit
    target_groups = [part.strip() for part in str((broadcast or {}).get("groups") or "").split(".") if part.strip()]
    if not target_groups:
        return False
    if "0" in target_groups:
        return True
    allowed = connection_group_ids(connection)
    return any(group_id in allowed for group_id in target_groups)


def maybe_touch_connected(connection, now=None):
    now = time.time() if now is None else now
    last_touch = float(getattr(connection, "last_runtime_touch", 0.0) or 0.0)
    if (now - last_touch) < DESKTOP_TOUCH_INTERVAL_SECONDS:
        return
    touch_connected(connection.connection_id)
    connection.last_runtime_touch = now


def send_binary_packet(sock, packet_type, broadcast_id, payload=b""):
    token = str(broadcast_id or "").encode("ascii", errors="ignore")[:32].ljust(32, b" ")
    send_ws_frame(sock, 0x2, packet_type + token + (payload or b""))


def send_rtp_stream_command(sock, broadcast_id, command, codec="mulaw", sample_rate=8000, stream_kind="broadcast"):
    send_ws_json(
        sock,
        {
            "type": "rtp_stream",
            "command": str(command or "").strip().lower(),
            "broadcast_id": str(broadcast_id or "").strip(),
            "stream_kind": str(stream_kind or "broadcast").strip().lower(),
            "audio_mode": "websocket",
            "audio_codec": str(codec or "mulaw").strip().lower(),
            "audio_sample_rate": int(sample_rate or 8000),
        },
    )


def clientd_ipc_send(payload):
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
    with socket.create_connection(("127.0.0.1", CLIENTD_IPC_PORT), timeout=2) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(encoded)
        response = sock.recv(4096)
    try:
        return json.loads(response.decode("utf-8"))
    except Exception:
        return {"ok": False, "error": "invalid clientd ipc response"}


def recv_line(sock, limit=65536):
    data = b""
    while b"\n" not in data and len(data) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    line, _, _rest = data.partition(b"\n")
    return line


def open_clientd_stream(payload):
    sock = socket.create_connection(("127.0.0.1", CLIENTD_IPC_PORT), timeout=5)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.sendall(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
    response = recv_line(sock)
    parsed = json.loads(response.decode("utf-8") or "{}")
    if not parsed.get("ok"):
        try:
            sock.close()
        except Exception:
            pass
    return sock, parsed


def send_stream_frame(sock, frame):
    payload = frame or b""
    sock.sendall(struct.pack("!H", len(payload)) + payload)


def start_desktop_broadcast_stream(broadcast_id, codec="mulaw", sample_rate=8000, broadcast=None):
    normalized_broadcast = normalize_ipc_broadcast_payload(make_json_safe(broadcast), fallback_id=broadcast_id)
    return open_clientd_stream(
        {
            "action": "stream_broadcast",
            "broadcast_id": str(broadcast_id or "").strip(),
            "codec": str(codec or "mulaw").strip().lower(),
            "sample_rate": int(sample_rate or 8000),
            "broadcast": normalized_broadcast,
        }
    )


def start_desktop_livepage(stream_id, groups_value, sender="", codec="mulaw", sample_rate=8000):
    return open_clientd_stream(
        {
            "action": "stream_livepage",
            "stream_id": str(stream_id or "").strip(),
            "groups": str(groups_value or "").strip(),
            "sender": str(sender or ""),
            "codec": str(codec or "mulaw").strip().lower(),
            "sample_rate": int(sample_rate or 8000),
        }
    )


def desktop_livepage_payload(connection, stream_id, groups_value, sender=""):
    return {
        "type": "broadcast",
        "broadcast_id": str(stream_id or ""),
        "message_type": "Page",
        "shortmessage": "",
        "longmessage": "",
        "priority": "Normal",
        "color": "#1976d2",
        "sender": str(sender or "Live Page"),
        "issued": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "product_name": product_name(),
        "audio_url": "",
        "audio_mode": "websocket",
        "audio_codec": "mulaw",
        "audio_sample_rate": 8000,
        "has_audio": True,
        "groups": groups_for_user(connection.user.get("id")),
    }


def start_livepage_for_group(stream_id, groups_value, sender=""):
    started = 0
    now = time.time()
    with connections_lock:
        current_connections = list(connections)
    for connection in current_connections:
        if connection.closed.is_set():
            continue
        if not connection_in_groups(connection, groups_value):
            continue
        try:
            payload = desktop_livepage_payload(connection, stream_id, groups_value, sender)
            payload["groups"] = list(getattr(connection, "groups_cache", []) or cached_groups_for_user(connection.user.get("id")))
            with connection.send_lock:
                send_ws_json(connection.sock, payload)
                send_rtp_stream_command(connection.sock, stream_id, "start", codec="mulaw", sample_rate=8000, stream_kind="livepage")
            connection.last_activity = now
            maybe_touch_connected(connection, now)
            started += 1
        except OSError:
            connection.closed.set()
    return started


def send_livepage_audio(stream_id, groups_value, frame):
    now = time.time()
    with connections_lock:
        current_connections = list(connections)
    for connection in current_connections:
        if connection.closed.is_set():
            continue
        if not connection_in_groups(connection, groups_value):
            continue
        try:
            with connection.send_lock:
                send_binary_packet(connection.sock, AUDIO_FRAME_PREFIX, stream_id, frame)
            connection.last_activity = now
            maybe_touch_connected(connection, now)
        except OSError:
            connection.closed.set()


def finish_livepage(stream_id, groups_value):
    now = time.time()
    with connections_lock:
        current_connections = list(connections)
    for connection in current_connections:
        if connection.closed.is_set():
            continue
        if not connection_in_groups(connection, groups_value):
            continue
        try:
            with connection.send_lock:
                send_rtp_stream_command(connection.sock, stream_id, "end", codec="mulaw", sample_rate=8000, stream_kind="livepage")
                send_binary_packet(connection.sock, AUDIO_END_PREFIX, stream_id, b"")
            connection.last_activity = now
            maybe_touch_connected(connection, now)
        except OSError:
            connection.closed.set()


def ffmpeg_stream_frames(source_args, codec):
    settings = desktop_stream_settings(codec)
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "quiet",
            *source_args,
            "-ar",
            str(settings["sample_rate"]),
            "-ac",
            "1",
            "-f",
            settings["ffmpeg_format"],
            "-flush_packets",
            "1",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        while True:
            chunk = process.stdout.read(settings["frame_size"])
            if not chunk:
                break
            yield chunk.ljust(settings["frame_size"], b"\x00")
    finally:
        if process.stdout is not None:
            process.stdout.close()
        process.wait()


def desktop_audio_frames(audio_files_str, codec=None):
    from endpoints import is_8k_ulaw, resolve_audio_file

    settings = desktop_stream_settings(codec)
    frame_size = settings["frame_size"]
    stream_codec = settings["codec"]
    for audio_file in split_audio_entries(audio_files_str):
        if not audio_file:
            continue
        if audio_file.startswith("%silence(") and audio_file.endswith(")"):
            try:
                duration = float(audio_file[9:-1])
            except ValueError:
                continue
            if stream_codec == "mulaw":
                for _ in range(int(duration * settings["sample_rate"] / frame_size)):
                    yield b"\xff" * frame_size
            else:
                yield from ffmpeg_stream_frames(
                    ["-f", "lavfi", "-i", f"anullsrc=r={settings['sample_rate']}:cl=mono", "-t", str(duration)],
                    stream_codec,
                )
            continue
        tts_payload = decode_tts_token(audio_file)
        if tts_payload:
            yield from iter_tts_ffmpeg_chunks(
                tts_payload,
                [
                    "-ar",
                    str(settings["sample_rate"]),
                    "-ac",
                    "1",
                    "-f",
                    settings["ffmpeg_format"],
                    "-flush_packets",
                    "1",
                    "pipe:1",
                ],
                chunk_size=frame_size,
                pad_byte=b"\xff" if stream_codec == "mulaw" else b"\x00",
            )
            continue
        file_path = resolve_audio_file(audio_file)
        if not file_path:
            continue
        if stream_codec == "mulaw" and is_8k_ulaw(file_path):
            with open(file_path, "rb") as handle:
                while True:
                    chunk = handle.read(frame_size)
                    if not chunk:
                        break
                    yield chunk.ljust(frame_size, b"\xff")
            continue
        yield from ffmpeg_stream_frames(["-i", file_path], stream_codec)


def dispatch_broadcast_start(broadcast_id, codec="mulaw", sample_rate=8000, broadcast=None):
    broadcast = resolve_broadcast_for_dispatch(broadcast_id, broadcast)
    if not broadcast:
        return 0
    started = 0
    now = time.time()
    with connections_lock:
        current_connections = list(connections)
    for connection in current_connections:
        if connection.closed.is_set():
            continue
        if not connection_in_broadcast(connection, broadcast):
            continue
        try:
            if str(broadcast_id or "").strip() not in connection.sent_ids:
                payload = desktop_payload_for_broadcast(connection.user, broadcast, connection.server_origin)
                payload["audio_codec"] = str(codec or "mulaw").strip().lower()
                payload["audio_sample_rate"] = int(sample_rate or 8000)
                payload["groups"] = list(getattr(connection, "groups_cache", []) or cached_groups_for_user(connection.user.get("id")))
                with connection.send_lock:
                    send_ws_json(connection.sock, payload)
                    send_rtp_stream_command(connection.sock, broadcast_id, "start", codec=codec, sample_rate=sample_rate, stream_kind="broadcast")
                connection.sent_ids.add(str(broadcast_id or "").strip())
            else:
                with connection.send_lock:
                    send_rtp_stream_command(connection.sock, broadcast_id, "start", codec=codec, sample_rate=sample_rate, stream_kind="broadcast")
            connection.last_activity = now
            maybe_touch_connected(connection, now)
            started += 1
        except OSError:
            connection.closed.set()
    return started


def dispatch_broadcast_frame(broadcast_id, frame, broadcast=None):
    broadcast = normalize_ipc_broadcast_payload(broadcast, fallback_id=broadcast_id) or lookup_broadcast(broadcast_id)
    if not broadcast:
        return 0
    sent = 0
    now = time.time()
    with connections_lock:
        current_connections = list(connections)
    for connection in current_connections:
        if connection.closed.is_set():
            continue
        if not connection_in_broadcast(connection, broadcast):
            continue
        try:
            with connection.send_lock:
                send_binary_packet(connection.sock, AUDIO_FRAME_PREFIX, broadcast_id, frame)
            connection.last_activity = now
            maybe_touch_connected(connection, now)
            sent += 1
        except OSError:
            connection.closed.set()
    return sent


def dispatch_broadcast_end(broadcast_id, broadcast=None):
    broadcast = normalize_ipc_broadcast_payload(broadcast, fallback_id=broadcast_id) or lookup_broadcast(broadcast_id)
    if not broadcast:
        return 0
    sent = 0
    now = time.time()
    with connections_lock:
        current_connections = list(connections)
    for connection in current_connections:
        if connection.closed.is_set():
            continue
        if not connection_in_broadcast(connection, broadcast):
            continue
        try:
            with connection.send_lock:
                send_rtp_stream_command(connection.sock, broadcast_id, "end", stream_kind="broadcast")
                send_binary_packet(connection.sock, AUDIO_END_PREFIX, broadcast_id, b"")
            connection.last_activity = now
            maybe_touch_connected(connection, now)
            sent += 1
        except OSError:
            connection.closed.set()
    return sent


def recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            return data
        data += chunk
    return data


def handle_streaming_ipc(conn, payload):
    action = str((payload or {}).get("action") or "").strip().lower()
    if action == "stream_broadcast":
        broadcast_id = str(payload.get("broadcast_id") or "").strip()
        codec = payload.get("codec") or "mulaw"
        sample_rate = payload.get("sample_rate") or 8000
        broadcast = resolve_broadcast_for_dispatch(broadcast_id, payload.get("broadcast"))
        response = dispatch_broadcast_start(broadcast_id, codec=codec, sample_rate=sample_rate, broadcast=broadcast)
        conn.sendall(json.dumps({"ok": True, "matched": response}, separators=(",", ":")).encode("utf-8") + b"\n")
        while True:
            header = recv_exact(conn, 2)
            if len(header) < 2:
                break
            frame_len = struct.unpack("!H", header)[0]
            frame = recv_exact(conn, frame_len)
            if len(frame) < frame_len:
                break
            dispatch_broadcast_frame(broadcast_id, frame, broadcast=broadcast)
        dispatch_broadcast_end(broadcast_id, broadcast=broadcast)
        return
    if action == "stream_livepage":
        stream_id = str(payload.get("stream_id") or "").strip()
        groups = str(payload.get("groups") or "").strip()
        sender = payload.get("sender") or "Live Page"
        matched = start_livepage_for_group(stream_id, groups, sender)
        conn.sendall(json.dumps({"ok": True, "matched": matched}, separators=(",", ":")).encode("utf-8") + b"\n")
        while True:
            header = recv_exact(conn, 2)
            if len(header) < 2:
                break
            frame_len = struct.unpack("!H", header)[0]
            frame = recv_exact(conn, frame_len)
            if len(frame) < frame_len:
                break
            send_livepage_audio(stream_id, groups, frame)
        finish_livepage(stream_id, groups)
        return
    conn.sendall(json.dumps({"ok": False, "error": f"unknown streaming action: {action}"}, separators=(",", ":")).encode("utf-8") + b"\n")


def handle_clientd_ipc_payload(payload):
    action = str((payload or {}).get("action") or "").strip().lower()
    if action == "start_broadcast":
        return {
            "ok": True,
            "matched": dispatch_broadcast_start(
                payload.get("broadcast_id"),
                payload.get("codec") or "mulaw",
                payload.get("sample_rate") or 8000,
            ),
        }
    if action == "broadcast_frame":
        frame = base64.b64decode(str(payload.get("frame_b64") or "").encode("ascii")) if payload.get("frame_b64") else b""
        return {"ok": True, "matched": dispatch_broadcast_frame(payload.get("broadcast_id"), frame)}
    if action == "end_broadcast":
        return {"ok": True, "matched": dispatch_broadcast_end(payload.get("broadcast_id"))}
    return {"ok": False, "error": f"unknown action: {action}"}


def _handle_clientd_ipc_conn(conn):
    try:
        request = recv_until(conn, b"\n", limit=1024 * 1024)
        payload = json.loads(request.decode("utf-8").strip() or "{}")
        action = str((payload or {}).get("action") or "").strip().lower()
        if action in {"stream_broadcast", "stream_livepage"}:
            handle_streaming_ipc(conn, payload)
            return
        response = handle_clientd_ipc_payload(payload)
    except Exception as exc:
        response = {"ok": False, "error": str(exc)}
    try:
        conn.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8"))
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def clientd_ipc_loop():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", CLIENTD_IPC_PORT))
    server.listen(20)
    while True:
        try:
            conn, _addr = server.accept()
        except OSError:
            break
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=_handle_clientd_ipc_conn, args=(conn,), daemon=True).start()


def broadcast_watcher_loop():
    while True:
        try:
            snapshots = list_active_broadcasts(limit=200)
            now = time.time()
            with connections_lock:
                current_connections = list(connections)
            for broadcast in snapshots:
                _remember_broadcast(broadcast)
                broadcast_id = str(broadcast.get("id") or "")
                if not broadcast_id:
                    continue
                if str(broadcast.get("runtime_kind") or "").strip().lower() == "livepage":
                    continue
                issued_at = parse_datetime(broadcast.get("issued"))
                if issued_at and (datetime.now() - issued_at) > timedelta(days=1):
                    continue
                for connection in current_connections:
                    if connection.closed.is_set():
                        continue
                    if broadcast_id in connection.sent_ids:
                        continue
                    late = bool(issued_at and issued_at < connection.connected_at)
                    if not user_in_broadcast(connection.user.get("id"), broadcast):
                        continue
                    try:
                        payload = desktop_payload_for_broadcast(connection.user, broadcast, connection.server_origin)
                        if payload.get("has_audio"):
                            if not late:
                                continue
                            payload["audio_mode"] = ""
                            payload["audio_codec"] = ""
                            payload["audio_sample_rate"] = 0
                        if late:
                            payload["late"] = True
                        with connection.send_lock:
                            send_ws_json(connection.sock, payload)
                        connection.sent_ids.add(broadcast_id)
                        connection.last_activity = now
                        maybe_touch_connected(connection, now)
                    except OSError:
                        connection.closed.set()
            time.sleep(0.15)
        except Exception:
            time.sleep(0.25)


def ensure_watcher_started():
    global watcher_started
    with watcher_lock:
        if watcher_started:
            return
        threading.Thread(target=broadcast_watcher_loop, daemon=True).start()
        watcher_started = True


def ensure_clientd_ipc_started():
    global ipc_started
    with ipc_lock:
        if ipc_started:
            return
        threading.Thread(target=clientd_ipc_loop, daemon=True).start()
        ipc_started = True


class DesktopConnection:
    def __init__(self, sock, user, server_origin):
        self.sock = sock
        self.user = user
        self.server_origin = server_origin
        self.connection_id = new_connection_id()
        self.sent_ids = set()
        self.closed = threading.Event()
        self.last_activity = time.time()
        self.last_runtime_touch = 0.0
        self.connected_at = datetime.now()
        self.send_lock = threading.Lock()
        self.groups_cache = []
        self.group_ids_cache = set()
        self.groups_loaded_at = 0.0


def handle_desktop_websocket_client(conn, addr, request=None):
    desktop_conn = None
    try:
        request = request if request is not None else recv_until(conn, b"\r\n\r\n")
        path, query, headers = parse_ws_request(request)
        if path != "/desktop/ws":
            conn.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
            return
        key = headers.get("sec-websocket-key", "")
        auth_value = headers.get("authorization", "")
        token = auth_value.split(" ", 1)[1].strip() if auth_value.lower().startswith("bearer ") else ""
        if not token:
            token = str((query.get("token") or [""])[0]).strip()
        user = verify_desktop_token(token)
        if not key or not user:
            conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n")
            return
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {websocket_accept_key(key)}\r\n\r\n"
        )
        conn.sendall(response.encode("ascii"))
        ensure_watcher_started()
        ensure_clientd_ipc_started()
        desktop_conn = DesktopConnection(conn, user, http_origin(headers))
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        register_connection(desktop_conn)
        connection_group_ids(desktop_conn, force=True)
        send_ws_json(
            conn,
            {
                "type": "ready",
                "user": {"id": user.get("id"), "username": user.get("username"), "role": user.get("role")},
                "product_name": product_name(),
                "groups": list(desktop_conn.groups_cache),
                "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        while not desktop_conn.closed.is_set():
            opcode, payload = read_ws_frame(conn)
            if opcode is None or opcode == 0x8:
                break
            desktop_conn.last_activity = time.time()
            maybe_touch_connected(desktop_conn)
            if opcode == 0x9:
                send_ws_frame(conn, 0xA, payload)
                continue
            if opcode == 0x1:
                try:
                    message = json.loads(payload.decode("utf-8"))
                except Exception:
                    message = {}
                if message.get("type") == "ping":
                    send_ws_json(conn, {"type": "pong", "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    except Exception:
        pass
    finally:
        if desktop_conn is not None:
            desktop_conn.closed.set()
            unregister_connection(desktop_conn)
        try:
            send_ws_frame(conn, 0x8, b"")
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

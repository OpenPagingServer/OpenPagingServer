import os
import base64
import hashlib
import json
import socket
import struct
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from endpoints import connect_endpoint_ipc

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

WS_HOST = os.getenv("LIVEPAGED_WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("LIVEPAGED_WS_PORT", "50010"))


def page_debug(message):
    if DEBUG:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] livepaged {message}")


def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )


def unique_tokens(values):
    tokens = []
    for value in values:
        token = str(value or "").strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def sip_extension_candidates(extension):
    token = str(extension or "").strip()
    candidates = [token]
    lowered = token.lower()
    if token.startswith("#"):
        candidates.append("%23" + token[1:])
    elif lowered.startswith("%23"):
        candidates.append("#" + token[3:])
    return unique_tokens(candidates)


def fetch_group_members(cur, group_id):
    target_list = set()
    for gid in str(group_id or "").split("."):
        gid = gid.strip()
        if not gid:
            continue
        cur.execute("SELECT members FROM groups WHERE id = %s", (gid,))
        row = cur.fetchone()
        page_debug(f"resolve_targets_group gid={gid!r} row={row}")
        if row and row[0]:
            for member in str(row[0]).replace(",", " ").split():
                if member:
                    target_list.add(member)
    return target_list


def page_group_from_sip_input(cur, extension):
    try:
        cur.execute("SHOW COLUMNS FROM `endpoints-input-siptrunk`")
        columns = [str(row[0]) for row in cur.fetchall() if row and row[0]]
    except pymysql.MySQLError as exc:
        page_debug(f"sip_input_columns_error extension={extension!r} error={exc}")
        return ""
    if not columns:
        return ""

    lowered_columns = {column.lower(): column for column in columns}
    wanted = unique_tokens(
        [
            lowered_columns.get("extension", "extension"),
            lowered_columns.get("trigger", "trigger"),
            lowered_columns.get("group"),
            lowered_columns.get("groups"),
            lowered_columns.get("groupid"),
            lowered_columns.get("group_id"),
            lowered_columns.get("page_group"),
            lowered_columns.get("paging_group"),
        ]
    )
    existing = [column for column in wanted if column in columns]
    if not existing or "extension" not in lowered_columns:
        return ""

    extension_column = lowered_columns["extension"]
    selected_sql = ", ".join(f"`{column}`" for column in existing)
    for candidate in sip_extension_candidates(extension):
        cur.execute(
            f"SELECT {selected_sql} FROM `endpoints-input-siptrunk` WHERE `{extension_column}` = %s LIMIT 1",
            (candidate,),
        )
        row = cur.fetchone()
        page_debug(f"sip_input_lookup extension={extension!r} candidate={candidate!r} row={row}")
        if not row:
            continue
        data = dict(zip(existing, row))
        for name in ("group", "groups", "groupid", "group_id", "page_group", "paging_group"):
            column = lowered_columns.get(name)
            value = str(data.get(column) or "").strip() if column else ""
            if value:
                return value
        trigger_column = lowered_columns.get("trigger")
        trigger = str(data.get(trigger_column) or "").strip() if trigger_column else ""
        if ":" in trigger:
            trigger_name, trigger_arg = trigger.split(":", 1)
            if trigger_name.strip().lower() == "page":
                return trigger_arg.strip()
    return ""


def parse_rtp_payload(packet):
    if len(packet) < 12:
        return b""
    cc = packet[0] & 0x0F
    ext = (packet[0] & 0x10) >> 4
    payload_type = packet[1] & 0x7F
    if payload_type != 0:
        return b""
    offset = 12 + cc * 4
    if ext:
        if len(packet) < offset + 4:
            return b""
        ext_len = int.from_bytes(packet[offset + 2:offset + 4], "big")
        offset += 4 + ext_len * 4
    if offset >= len(packet):
        return b""
    return packet[offset:]


def resolve_targets(group_id):
    page_debug(f"resolve_targets_start group={group_id!r}")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            target_list = set()
            if str(group_id) == "0":
                try:
                    cur.execute("SELECT `dir` FROM endpointmodulesloaded WHERE enabled = 'true' AND trusted = 'true'")
                    rows = cur.fetchall()
                except Exception:
                    rows = []
                page_debug(f"resolve_targets_all_modules rows={rows}")
                for row in rows:
                    if row and row[0]:
                        target_list.add(f"{row[0]}/all")
            else:
                target_list.update(fetch_group_members(cur, group_id))
                if not target_list:
                    stored_group = page_group_from_sip_input(cur, group_id)
                    if stored_group:
                        page_debug(f"resolve_targets_sip_input_group extension={group_id!r} stored_group={stored_group!r}")
                        target_list.update(fetch_group_members(cur, stored_group))
            targets = sorted(target_list)
            page_debug(f"resolve_targets_done group={group_id!r} targets={targets}")
            return targets
    except Exception as exc:
        page_debug(f"resolve_targets_error group={group_id!r} error={exc.__class__.__name__}: {exc}")
        raise
    finally:
        conn.close()


class LivePageSession:
    def __init__(self, remote_ip, remote_port, group_id, generator=None, on_finish=None, sender=None):
        self.remote_ip = remote_ip
        self.remote_port = remote_port
        self.group_id = str(group_id) if group_id is not None else ""
        self.generator = generator
        self.on_finish = on_finish
        self.sender = "" if sender is None else str(sender)
        self.local_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.local_sock.bind(("0.0.0.0", 0))
        self.local_sock.settimeout(0.5)
        self.local_port = self.local_sock.getsockname()[1]
        self.control_sock = None
        self.stream_id = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.thread = None
        self.targets = []
        self.cleanup_lock = threading.Lock()
        self.cleaned_up = False
        page_debug(
            f"session_init stream={self.stream_id} remote={self.remote_ip}:{self.remote_port} "
            f"group={self.group_id!r} sender={self.sender!r} local_port={self.local_port}"
        )

    def preflight(self):
        page_debug(f"preflight_start stream={self.stream_id} group={self.group_id!r}")
        self.targets = resolve_targets(self.group_id)
        if not self.targets:
            page_debug(f"preflight_no_targets stream={self.stream_id} group={self.group_id!r}")
            raise RuntimeError("503 Service Unavailable")
        self.control_sock = connect_endpoint_ipc(timeout=10)
        command = f"PREPARELIVE {self.stream_id} {self.group_id} {' '.join(self.targets)}\n"
        page_debug(
            f"preflight_connect stream={self.stream_id} command={command.strip()!r}"
        )
        self.control_sock.sendall(command.encode("utf-8"))
        response = self.control_sock.recv(1024)
        page_debug(f"preflight_response stream={self.stream_id} response={response!r}")
        if b"OK" not in response:
            raise RuntimeError("503 Service Unavailable")
        page_debug(f"preflight_ok stream={self.stream_id}")

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        packets = 0
        bytes_sent = 0
        try:
            while not self.stop_event.is_set():
                try:
                    packet, _ = self.local_sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                payload = parse_rtp_payload(packet)
                if not payload:
                    continue
                if getattr(self, "rtp_paused", False):
                    continue
                try:
                    if self.control_sock is not None:
                        self.control_sock.sendall(payload)
                        packets += 1
                        bytes_sent += len(payload)
                        if packets == 1 or packets % 50 == 0:
                            page_debug(
                                f"audio_forward stream={self.stream_id} packets={packets} "
                                f"bytes={bytes_sent} last_payload={len(payload)}"
                            )
                except OSError:
                    break
        finally:
            page_debug(f"run_end stream={self.stream_id} packets={packets} bytes={bytes_sent}")
            self.cleanup()

    def stop(self):
        self.stop_event.set()
        self.cleanup()

    def cleanup(self):
        with self.cleanup_lock:
            if self.cleaned_up:
                return
            self.cleaned_up = True
        try:
            if self.local_sock is not None:
                self.local_sock.close()
        except OSError:
            pass
        self.local_sock = None
        try:
            if self.control_sock is not None:
                self.control_sock.close()
        except OSError:
            pass
        self.control_sock = None
        if self.on_finish is not None:
            try:
                self.on_finish()
            except Exception:
                pass
        page_debug(f"cleanup stream={self.stream_id}")


class WebLivePageSession(LivePageSession):
    def start(self):
        return

    def send_payload(self, payload):
        if self.stop_event.is_set() or self.control_sock is None:
            return
        if not payload:
            return
        self.control_sock.sendall(payload)


def recv_until(sock, marker, limit=8192):
    data = b""
    while marker not in data:
        chunk = sock.recv(1024)
        if not chunk:
            break
        data += chunk
        if len(data) > limit:
            break
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
    send_ws_frame(sock, 0x1, json.dumps(payload))


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


def parse_ws_request(request):
    text = request.decode("utf-8", errors="ignore")
    lines = text.split("\r\n")
    if not lines:
        return "", {}, {}
    request_line = lines[0].split()
    path = request_line[1] if len(request_line) >= 2 else "/"
    parsed = urllib.parse.urlparse(path)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return parsed.path, urllib.parse.parse_qs(parsed.query), headers


def clean_group_id(value):
    value = str(value or "").strip()
    if value == "0":
        return value
    parts = []
    for part in value.split("."):
        part = part.strip()
        if part.isdigit():
            parts.append(part)
    return ".".join(parts)


def handle_websocket_client(conn, addr, request=None):
    session = None
    try:
        request = request if request is not None else recv_until(conn, b"\r\n\r\n")
        path, query, headers = parse_ws_request(request)
        key = headers.get("sec-websocket-key", "")
        group_id = clean_group_id((query.get("groups") or [""])[0])
        sender = str((query.get("sender") or ["Web Page"])[0]).strip()[:100]
        if path != "/live" or not key or not group_id:
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            return
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {websocket_accept_key(key)}\r\n\r\n"
        )
        conn.sendall(response.encode("ascii"))
        session = WebLivePageSession(addr[0], 0, group_id=group_id, sender=sender)
        try:
            session.preflight()
        except Exception as exc:
            page_debug(f"websocket_preflight_error addr={addr} group={group_id!r} error={exc}")
            send_ws_json(conn, {"type": "error", "message": "Unable to start live page."})
            return
        send_ws_json(conn, {"type": "ready", "stream_id": session.stream_id})
        while True:
            opcode, payload = read_ws_frame(conn)
            if opcode is None or opcode == 0x8:
                break
            if opcode == 0x9:
                send_ws_frame(conn, 0xA, payload)
                continue
            if opcode == 0x2:
                session.send_payload(payload)
    except Exception as exc:
        page_debug(f"websocket_client_error addr={addr} error={exc.__class__.__name__}: {exc}")
    finally:
        if session is not None:
            session.stop()
        try:
            send_ws_frame(conn, 0x8, b"")
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def serve_websocket():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((WS_HOST, WS_PORT))
    server.listen(25)
    page_debug(f"websocket_server_start host={WS_HOST} port={WS_PORT}")
    print(f"livepaged websocket server listening on {WS_HOST}:{WS_PORT}", flush=True)
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_websocket_client, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    serve_websocket()

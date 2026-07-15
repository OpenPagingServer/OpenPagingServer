import os
import sys
import os
import random
import socket
import ssl
import struct
import threading
import time
import uuid
import re
import urllib.parse
import importlib.util
import traceback
import base64
import hashlib
import select
import queue
import tempfile
import urllib.error
import urllib.request
import ipaddress
import json
from pathlib import Path

import pymysql
from dotenv import load_dotenv

try:
    import bcrypt
except:
    bcrypt = None

try:
    import dns.resolver
except Exception:
    dns = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth
from audio_utils import generate_wav, chain_generators
from rtp import RTPSession

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"
OPENPAGINGSERVER_IPADDR_URL = "https://analytics.openpagingserver.org/ipaddr"
SIP_SECURITY_FALSE_VALUES = {"0", "false", "off", "disable", "disabled", "no"}
SIP_SCANNER_SIGNATURES = tuple(
    token.strip().lower()
    for token in (
        "friendly-scanner,sipvicious,sipv,sipcli,sip-scan,sipsak,sundayddr,iWar,CSipSimple,SIVuS,Gulp,"
        "smap,svmap,siparmyknife,friendly-request,Test Agent,VaxIPUserAgent,VaxSIPUserAgent,Mr.SIP,"
        "SIPVicious,Sippts,Nmap NSE,SIP Scanner,SIP Scanner v1.0,SIP Scanner v2.0,SIPVicious v1.0,"
        "SIPVicious v1.1,SIPVicious v1.2,Python SIP Scanner,SIP Auditor,SIP Enumerator,SIP Cracker,"
        "SVWAR,SVMAP,SVCRACK"
    ).split(",")
    if token.strip()
)
SIP_INTRUSION_ATTEMPT_LIMIT = 5
SIP_INTRUSION_WINDOW_SECONDS = 5 * 60
SIP_INTRUSION_BLOCK_SECONDS = 48 * 60 * 60
SIP_INTRUSION_STORAGE_PATH = "/var/spool/openpagingserver/.sipbannedips"


def sip_abuse_override_enabled():
    return str(os.getenv("ALLOW_SIP_ABUSE", "") or "").strip().lower() == "true"

def sip_debug(message):
    if DEBUG:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] DEBUG sip {message}", flush=True)

def sip_sockname(sock):
    try:
        host, port = sock.getsockname()[:2]
        return f"{host}:{port}"
    except Exception:
        return "unknown"

loaded_triggers = {}
triggers_dir = os.path.join(os.path.dirname(__file__), "triggers")
if os.path.isdir(triggers_dir):
    for filename in os.listdir(triggers_dir):
        if filename.endswith(".py") and filename != "__init__.py":
            module_name = filename[:-3]
            filepath = os.path.join(triggers_dir, filename)
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "trigger_name") and hasattr(module, "handle"):
                loaded_triggers[module.trigger_name] = module.handle

class SipCongestionError(Exception):
    pass

class PasscodeRTPSession(RTPSession):
    def __init__(self, target_ip, target_port, passcode, server, call_id, on_success=None, on_failure=None, on_finish=None, rtp_socket=None):
        super().__init__(target_ip, target_port, payload_generator=None, on_finish=on_finish, rtp_socket=rtp_socket)
        self.passcode = str(passcode).strip()
        self.server = server
        self.call_id = call_id
        self.on_success = on_success
        self.on_failure = on_failure
        self.last_digit_count = 0
        self._sequence = int(time.time() * 1000) & 0xFFFF
        self._timestamp = int(time.time() * 8000) & 0xFFFFFFFF
        self._ssrc = int(time.time() * 1000000) & 0xFFFFFFFF

    def _read_digits(self):
        try:
            return self.get_digits()
        except:
            return ""

    def _clear_digits_safe(self):
        try:
            self.clear_digits()
        except:
            pass
        self.last_digit_count = 0

    def _prime_media_path(self, frames=6):
        try:
            frames = max(0, int(frames))
        except:
            frames = 0
        next_send = time.monotonic()
        for _ in range(frames):
            if self.stop_event.is_set():
                break
            packet = self.build_packet(self._sequence, self._timestamp, self._ssrc, b"\xff" * 160)
            try:
                self.socket.sendto(packet, (self.target_ip, self.target_port))
            except:
                break
            self._sequence = (self._sequence + 1) & 0xFFFF
            self._timestamp = (self._timestamp + 160) & 0xFFFFFFFF
            next_send += 0.02
            while not self.stop_event.is_set():
                wait = next_send - time.monotonic()
                if wait <= 0:
                    break
                try:
                    r, _, _ = select.select([self.socket], [], [], min(wait, 0.02))
                    if r:
                        data, addr = self.socket.recvfrom(4096)
                        self._learn_packet_source(addr, data)
                        pt, payload = self._parse_rtp(data)
                        if pt is not None:
                            self._handle_dtmf(pt, payload)
                except:
                    break

    def _code_ok(self, value):
        value = str(value)
        stored = str(self.passcode).strip()
        if not stored:
            return True
        if stored.lower().startswith("bcrypt"):
            if bcrypt is None:
                return False
            hashed = stored[6:].strip()
            if hashed.startswith(":"):
                hashed = hashed[1:].strip()
            try:
                return bcrypt.checkpw(value.encode("utf-8"), hashed.encode("utf-8"))
            except:
                return False
        return value == stored

    def _send_prompt(self, gen, interruptible=True):
        next_send = time.monotonic()
        for payload in gen:
            if self.stop_event.is_set():
                break
            digits = self._read_digits()
            if interruptible and len(digits) > self.last_digit_count:
                self.last_digit_count = len(digits)
                break
            packet = self.build_packet(self._sequence, self._timestamp, self._ssrc, payload)
            try:
                self.socket.sendto(packet, (self.target_ip, self.target_port))
            except:
                break
            self._sequence = (self._sequence + 1) & 0xFFFF
            self._timestamp = (self._timestamp + 160) & 0xFFFFFFFF
            next_send += 0.02
            while not self.stop_event.is_set():
                now = time.monotonic()
                wait = next_send - now
                if wait <= 0:
                    break
                try:
                    r, _, _ = select.select([self.socket], [], [], min(wait, 0.02))
                    if r:
                        data, addr = self.socket.recvfrom(4096)
                        self._learn_packet_source(addr, data)
                        pt, rtp_payload = self._parse_rtp(data)
                        if pt is not None:
                            self._handle_dtmf(pt, rtp_payload)
                            digits = self._read_digits()
                            if interruptible and len(digits) > self.last_digit_count:
                                self.last_digit_count = len(digits)
                                return
                except:
                    break

    def _wait_for_code(self):
        wait_started = time.time()
        first_digit_time = None
        last_digit_time = None
        while not self.stop_event.is_set():
            digits = self._read_digits()
            if digits:
                if first_digit_time is None:
                    first_digit_time = time.time()
                last_digit_time = time.time()
                self.last_digit_count = len(digits)
                if "#" in digits:
                    return digits.split("#", 1)[0]
            if first_digit_time is not None and time.time() - last_digit_time >= 30:
                return digits
            if first_digit_time is None and time.time() - wait_started >= 30:
                return None
            try:
                r, _, _ = select.select([self.socket], [], [], 0.05)
                if r:
                    data, addr = self.socket.recvfrom(4096)
                    self._learn_packet_source(addr, data)
                    pt, payload = self._parse_rtp(data)
                    if pt is not None:
                        self._handle_dtmf(pt, payload)
            except:
                break
        return None

    def _run_trigger_audio(self):
        next_send = time.monotonic()
        while not self.stop_event.is_set():
            gen = self.payload_generator
            if gen is None:
                time.sleep(0.05)
                continue
            try:
                payload = next(gen)
            except StopIteration:
                break
            packet = self.build_packet(self._sequence, self._timestamp, self._ssrc, payload)
            try:
                self.socket.sendto(packet, (self.target_ip, self.target_port))
            except:
                break
            self._sequence = (self._sequence + 1) & 0xFFFF
            self._timestamp = (self._timestamp + 160) & 0xFFFFFFFF
            next_send += 0.02
            sleep_for = next_send - time.monotonic()
            if sleep_for > 0:
                try:
                    r, _, _ = select.select([self.socket], [], [], sleep_for)
                    if r:
                        data, addr = self.socket.recvfrom(4096)
                        self._learn_packet_source(addr, data)
                        pt, payload = self._parse_rtp(data)
                        if pt is not None:
                            self._handle_dtmf(pt, payload)
                except:
                    pass

    def run(self):
        ok = False
        try:
            self._prime_media_path(getattr(self, "initial_silence_frames", 6) or 6)
            self.initial_silence_frames = 0
            for attempt in range(3):
                if self.stop_event.is_set():
                    break
                self._clear_digits_safe()
                self._send_prompt(generate_wav("./audio/enter-password.wav"), interruptible=True)
                entered = self._wait_for_code()
                if entered is None:
                    break
                if self._code_ok(entered):
                    ok = True
                    break
                if attempt < 2:
                    self._clear_digits_safe()
                    self._send_prompt(generate_wav("./audio/bad-password-reenter.wav"), interruptible=True)
            if ok:
                if self.on_success:
                    delegated_session = self.on_success()
                    if delegated_session is False:
                        self.stop_event.set()
                    elif delegated_session is not None and hasattr(delegated_session, "run"):
                        self.stop_event.set()
                        delegated_session.run()
                    elif delegated_session is not None and hasattr(delegated_session, "_run"):
                        self.stop_event.set()
                        delegated_session._run()
                    else:
                        self._run_trigger_audio()
                else:
                    self._run_trigger_audio()
            else:
                self._clear_digits_safe()
                self._send_prompt(generate_wav("./audio/bad-password-goodbye.wav"), interruptible=False)
                if self.on_failure:
                    self.on_failure()
        finally:
            try:
                self.socket.close()
            except:
                pass
            if self.on_finish is not None and not self.stop_event.is_set():
                try:
                    self.on_finish()
                except:
                    pass


def safe_int(value, default):
    try:
        return int(str(value).strip())
    except Exception:
        return default


def safe_float(value, default):
    try:
        return float(str(value).strip())
    except Exception:
        return default


def truthy_setting(value, default=False):
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def clean_nat_mode(value, default="auto"):
    token = str(value if value is not None else "").strip().lower()
    if token in {"auto", "automatic", ""}:
        return "auto"
    if token in {"yes", "force", "forced", "always"}:
        return "yes"
    if token in {"no", "off", "disable", "disabled", "0", "false"}:
        return "no"
    if token in {"1", "true", "on", "enable", "enabled"}:
        return "auto"
    return default


OUTBOUND_TRUNK_REGISTER_INTERVAL = 30
OUTBOUND_TRUNK_AUTH_RETRY_STEP = 300
OUTBOUND_TRUNK_AUTH_RETRY_MAX = 3600
SIP_RTP_DEFAULT_PORT_START = 40000
SIP_RTP_DEFAULT_PORT_END = 50000

SIP_COMPACT_HEADERS = {
    "call-id": "i",
    "contact": "m",
    "content-encoding": "e",
    "content-length": "l",
    "content-type": "c",
    "event": "o",
    "from": "f",
    "refer-to": "r",
    "referred-by": "b",
    "subject": "s",
    "supported": "k",
    "to": "t",
    "via": "v",
}


def sip_header_lookup_names(name):
    lowered = str(name or "").strip().lower()
    names = {lowered}
    for full, compact in SIP_COMPACT_HEADERS.items():
        if lowered in {full, compact}:
            names.add(full)
            names.add(compact)
            break
    return names


def sip_bytes(raw):
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, bytearray):
        return bytes(raw)
    return str(raw or "").encode("utf-8", errors="ignore")


def sip_header_end(data):
    best = None
    best_sep_len = 0
    for sep in (b"\r\n\r\n", b"\n\n", b"\r\r"):
        idx = data.find(sep)
        if idx != -1 and (best is None or idx < best):
            best = idx
            best_sep_len = len(sep)
    if best is None:
        return -1, 0
    return best, best_sep_len


def sip_header_lines(head):
    text = head.decode("utf-8", errors="ignore") if isinstance(head, bytes) else str(head or "")
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def sip_header_tuples_from_lines(lines):
    headers = []
    for line in lines[1:]:
        if not line:
            continue
        if line[:1] in " \t" and headers:
            name, value = headers[-1]
            headers[-1] = (name, value + " " + line.strip())
            continue
        if ":" in line:
            name, value = line.split(":", 1)
            headers.append((name.strip(), value.strip()))
    return headers


def sip_content_length(value):
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def sip_head_content_length(head):
    found = False
    content_length = 0
    for name, value in sip_header_tuples_from_lines(sip_header_lines(head)):
        if name.strip().lower() in sip_header_lookup_names("Content-Length"):
            found = True
            content_length = sip_content_length(value)
            break
    return found, content_length


def sip_split_message(raw):
    data = sip_bytes(raw)
    header_end, sep_len = sip_header_end(data)
    if header_end == -1:
        return data, b""
    return data[:header_end], data[header_end + sep_len:]


def sip_latchable_rtp_packet(packet):
    if len(packet) < 12:
        return False
    if ((packet[:1] or b"\x00")[0] >> 6) != 2:
        return False
    packet_type = packet[1] if len(packet) > 1 else 0
    return not (192 <= packet_type <= 223)


def parse_authenticate_header(value):
    raw = str(value or "").strip()
    if not raw:
        return "", {}
    scheme, _, rest = raw.partition(" ")
    pairs = {}
    for match in re.finditer(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,]+))', rest):
        pairs[match.group(1).lower()] = match.group(2) if match.group(2) is not None else match.group(3).strip()
    return scheme.strip(), pairs


def digest_response(username, password, realm, nonce, method, uri, qop=None, nc=None, cnonce=None):
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode("utf-8")).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode("utf-8")).hexdigest()
    if qop:
        return hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode("utf-8")).hexdigest()
    return hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode("utf-8")).hexdigest()


class SipClientTransaction:
    def __init__(self):
        self.responses = queue.Queue()

    def put(self, item):
        self.responses.put(item)

    def get(self, timeout=None):
        return self.responses.get(timeout=timeout)


class OutboundSipCall:
    def __init__(self, server, call_id, trunk_id, number, transport, rtp_socket, local_media_ip, advertised_media_ip, advertised_media_port, local_sip_ip, local_sip_port):
        self.server = server
        self.call_id = call_id
        self.trunk_id = trunk_id
        self.number = number
        self.transport = transport
        self.rtp_socket = rtp_socket
        self.local_media_ip = local_media_ip
        self.advertised_media_ip = advertised_media_ip
        self.advertised_media_port = safe_int(advertised_media_port, rtp_socket.getsockname()[1])
        self.local_sip_ip = local_sip_ip
        self.local_sip_port = local_sip_port
        self.local_rtp_port = rtp_socket.getsockname()[1]
        self.remote_media_ip = ""
        self.remote_media_port = 0
        self.remote_sip_ip = ""
        self.remote_sip_port = 0
        self.remote_contact = ""
        self.record_routes = []
        self.from_h = ""
        self.to_h = ""
        self.request_uri = ""
        self.conn = None
        self.cseq = 1
        self.local_tag = uuid.uuid4().hex[:10]
        self.answered = False
        self.failed = False
        self.failure_reason = ""
        self.answered_event = threading.Event()
        self.finished_event = threading.Event()
        self.disconnected_event = threading.Event()
        self.lock = threading.Lock()
        self.released = False
        self.media_path_primed = False
        self.acked_invite_cseqs = set()
        self.pracked_rseqs = set()
        self.invite_route = {}
        self.invite_route_headers = []
        self.invite_provisional = False
        self.hangup_reason = ""
        self.next_local_cseq = 2
        self.rtp_sequence = random.randrange(0, 65536)
        self.rtp_timestamp = random.randrange(0, 4294967296)
        self.rtp_ssrc = random.randrange(0, 4294967296)
        self.rtp_packets_sent = 0
        self.rtp_packets_received = 0

    def wait_answer(self, timeout=None):
        return self.answered_event.wait(timeout)

    def mark_answered(self):
        self.answered = True
        self.answered_event.set()

    def mark_failed(self, reason):
        self.failed = True
        self.failure_reason = str(reason or "")
        self.finished_event.set()
        self.answered_event.set()

    def mark_disconnected(self):
        self.disconnected_event.set()
        self.finished_event.set()

    def hangup(self, reason_text=""):
        reason_text = str(reason_text or "").strip()
        if reason_text:
            self.hangup_reason = reason_text
        self.server.finish_outbound_call(self.call_id)

class SipServer:
    def __init__(self):
        self.lock = threading.Lock()
        self.outbound_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.udp_sock = None
        self.tcp_sock = None
        self.active_port = None
        self.enabled = None
        self.manager_thread = None
        self.pinger_thread = None
        self.calls = {}
        self.pending_pings = {}
        self.registrations = {}
        self.client_transactions = {}
        self.outbound_calls = {}
        self.outbound_trunks = {}
        self.tcp_buffers = {}
        self.rtp_port_cursor = None
        self.public_ip_cache = {"value": "", "loaded_at": 0.0}
        self.sip_intrusion_attempts = {}
        self.sip_intrusion_blocks = {}
        self.sip_intrusion_storage_path = SIP_INTRUSION_STORAGE_PATH
        self.sip_intrusion_storage_mtime = 0.0
        self.load_sip_intrusion_storage(force=True)

    def connect_db(self):
        return pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            autocommit=True,
        )

    def table_columns(self, table):
        conn = self.connect_db()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SHOW COLUMNS FROM `{table}`")
                return {str(row[0] or "") for row in cur.fetchall() if row and row[0]}
        finally:
            conn.close()

    def sip_trunk_select_columns(self):
        existing = self.table_columns("sip-trunks")
        defaults = {
            "id": "0",
            "name": "''",
            "auth": "''",
            "trunk_type": "''",
            "username": "''",
            "password": "''",
            "ipaddr": "'0.0.0.0'",
            "status": "'Offline'",
            "callerid_number": "''",
            "callerid_name": "''",
            "servers_json": "''",
            "outbound_nat": "'auto'",
            "connected_server": "''",
            "connected_transport": "''",
        }
        columns = []
        for name, default_sql in defaults.items():
            if name in existing:
                columns.append(f"`{name}`")
            else:
                columns.append(f"{default_sql} AS `{name}`")
        return ", ".join(columns)

    def read_setting(self, key):
        candidates = [
            ("parameter", "value"),
            ("parameters", "value"),
            ("name", "value"),
            ("setting", "value"),
            ("key", "value"),
            ("parameter", "setting_value"),
            ("parameters", "setting_value"),
            ("name", "setting_value"),
            ("setting", "setting_value"),
            ("key", "setting_value"),
        ]
        conn = self.connect_db()
        try:
            with conn.cursor() as cur:
                for key_col, value_col in candidates:
                    try:
                        cur.execute(
                            f"SELECT `{value_col}` FROM `systemsettings` WHERE `{key_col}` = %s LIMIT 1",
                            (key,),
                        )
                        row = cur.fetchone()
                        if row is not None:
                            return row[0]
                    except pymysql.MySQLError:
                        continue
        finally:
            conn.close()
        return None

    def get_settings(self):
        enabled_raw = self.read_setting("enable_insecure_sip")
        port_raw = self.read_setting("insecure_sip_port")
        try:
            enabled = int(enabled_raw) == 1
        except:
            enabled = False
        try:
            port = int(port_raw)
        except:
            port = 5060
        if port < 1 or port > 65535:
            port = 5060
        return enabled, port

    def sip_security_setting_enabled(self, key, default=True):
        if not sip_abuse_override_enabled():
            return True
        raw = self.read_setting(key)
        token = str(raw if raw is not None else "").strip().lower()
        if not token:
            return bool(default)
        return token not in SIP_SECURITY_FALSE_VALUES

    def scanner_blocking_enabled(self):
        return self.sip_security_setting_enabled("sip_block_scanners", default=True)

    def intrusion_prevention_enabled(self):
        return self.sip_security_setting_enabled("sip_intrusion_prevention", default=True)

    def drop_transport_connection(self, conn):
        if conn is None:
            return
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    def scanner_header_matches(self, headers):
        candidates = []
        for header_name in ("User-Agent", "Server"):
            value = self.get_first(headers, header_name)
            if value:
                candidates.append(str(value).strip().lower())
        for value in candidates:
            for signature in SIP_SCANNER_SIGNATURES:
                if signature in value:
                    return True
        return False

    def valid_sip_intrusion_ip(self, value):
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return str(ipaddress.ip_address(text))
        except Exception:
            return ""

    def load_sip_intrusion_storage(self, force=False):
        path = Path(self.sip_intrusion_storage_path)
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            mtime = 0.0
        except Exception:
            return False
        if not force and mtime == self.sip_intrusion_storage_mtime:
            return False
        now = time.time()
        blocks = {}
        attempts = {}
        if mtime > 0:
            try:
                for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    parts = raw_line.strip().split()
                    if not parts:
                        continue
                    kind = parts[0].lower()
                    if kind == "block" and len(parts) >= 3:
                        ip_text = self.valid_sip_intrusion_ip(parts[1])
                        if not ip_text:
                            continue
                        blocked_until = safe_float(parts[2], 0.0)
                        if blocked_until > now:
                            blocks[ip_text] = blocked_until
                    elif kind == "attempt" and len(parts) >= 3:
                        ip_text = self.valid_sip_intrusion_ip(parts[1])
                        if not ip_text:
                            continue
                        stamps = [safe_float(item, 0.0) for item in parts[2:]]
                        stamps = [stamp for stamp in stamps if stamp >= now - SIP_INTRUSION_WINDOW_SECONDS and stamp <= now + 60]
                        if stamps:
                            attempts[ip_text] = stamps
                    elif len(parts) >= 1:
                        ip_text = self.valid_sip_intrusion_ip(parts[0])
                        if ip_text:
                            blocks[ip_text] = now + SIP_INTRUSION_BLOCK_SECONDS
            except Exception:
                return False
        with self.lock:
            self.sip_intrusion_blocks = blocks
            self.sip_intrusion_attempts = attempts
            self.sip_intrusion_storage_mtime = mtime
        return True

    def save_sip_intrusion_storage(self):
        path = Path(self.sip_intrusion_storage_path)
        now = time.time()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = []
            with self.lock:
                blocks = dict(self.sip_intrusion_blocks)
                attempts = {key: list(value) for key, value in self.sip_intrusion_attempts.items()}
            for ip_text in sorted(blocks):
                blocked_until = safe_float(blocks.get(ip_text), 0.0)
                if blocked_until > now:
                    lines.append(f"block {ip_text} {blocked_until:.3f}")
            cutoff = now - SIP_INTRUSION_WINDOW_SECONDS
            for ip_text in sorted(attempts):
                stamps = [safe_float(item, 0.0) for item in attempts.get(ip_text, []) if safe_float(item, 0.0) >= cutoff]
                if stamps:
                    lines.append("attempt " + ip_text + " " + " ".join(f"{stamp:.3f}" for stamp in stamps))
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
            os.replace(temporary, path)
            try:
                self.sip_intrusion_storage_mtime = path.stat().st_mtime
            except Exception:
                pass
            return True
        except Exception:
            return False

    def sip_intrusion_cleanup(self, now=None):
        current = float(time.time() if now is None else now)
        cutoff = current - SIP_INTRUSION_WINDOW_SECONDS
        changed = False
        with self.lock:
            for ip_text, blocked_until in list(self.sip_intrusion_blocks.items()):
                if safe_float(blocked_until, 0.0) <= current:
                    self.sip_intrusion_blocks.pop(ip_text, None)
                    changed = True
            for ip_text, stamps in list(self.sip_intrusion_attempts.items()):
                kept = [stamp for stamp in stamps if safe_float(stamp, 0.0) >= cutoff]
                if kept:
                    if kept != stamps:
                        self.sip_intrusion_attempts[ip_text] = kept
                        changed = True
                else:
                    self.sip_intrusion_attempts.pop(ip_text, None)
                    changed = True
        if changed:
            self.save_sip_intrusion_storage()
        return changed

    def intrusion_ip_blocked(self, ipaddr, now=None):
        if not self.intrusion_prevention_enabled():
            return False
        self.load_sip_intrusion_storage()
        current = float(time.time() if now is None else now)
        ip_text = self.valid_sip_intrusion_ip(ipaddr) or str(ipaddr or "").strip()
        expired = False
        with self.lock:
            blocked_until = float(self.sip_intrusion_blocks.get(ip_text, 0) or 0)
            if blocked_until > current:
                return True
            if ip_text in self.sip_intrusion_blocks:
                self.sip_intrusion_blocks.pop(ip_text, None)
                expired = True
        if expired:
            self.save_sip_intrusion_storage()
        return False

    def note_unauthorized_attempt(self, ipaddr, method, conn=None):
        if str(method or "").strip().upper() not in {"REGISTER", "INVITE"}:
            return False
        if not self.intrusion_prevention_enabled():
            return False
        self.load_sip_intrusion_storage()
        ip_text = self.valid_sip_intrusion_ip(ipaddr) or str(ipaddr or "").strip()
        now = time.time()
        save_needed = False
        with self.lock:
            blocked_until = float(self.sip_intrusion_blocks.get(ip_text, 0) or 0)
            if blocked_until > now:
                blocked = True
            else:
                cutoff = now - SIP_INTRUSION_WINDOW_SECONDS
                attempts = [stamp for stamp in self.sip_intrusion_attempts.get(ip_text, []) if stamp >= cutoff]
                attempts.append(now)
                if len(attempts) >= SIP_INTRUSION_ATTEMPT_LIMIT:
                    self.sip_intrusion_blocks[ip_text] = now + SIP_INTRUSION_BLOCK_SECONDS
                    self.sip_intrusion_attempts.pop(ip_text, None)
                    blocked = True
                else:
                    self.sip_intrusion_attempts[ip_text] = attempts
                    blocked = False
                save_needed = True
        if save_needed:
            self.save_sip_intrusion_storage()
        if blocked:
            self.drop_transport_connection(conn)
        return blocked

    def should_drop_silently(self, headers, ipaddr, conn=None):
        if self.scanner_blocking_enabled() and self.scanner_header_matches(headers):
            self.drop_transport_connection(conn)
            return True
        if self.intrusion_ip_blocked(ipaddr):
            self.drop_transport_connection(conn)
            return True
        return False

    def sip_nat_enabled(self):
        return self.sip_nat_mode() != "no"

    def sip_nat_mode(self):
        return clean_nat_mode(self.read_setting("sip_nat_support"), "auto")

    def effective_nat_mode(self, nat_mode=None):
        mode = clean_nat_mode(nat_mode if nat_mode is not None else self.sip_nat_mode(), "auto")
        if nat_mode is not None and mode == "auto":
            global_mode = self.sip_nat_mode()
            if global_mode in {"yes", "no"}:
                return global_mode
        return mode

    def sip_external_ipv4_mode(self):
        mode = str(self.read_setting("sip_external_ipv4_mode") or "auto").strip().lower()
        return mode if mode in {"auto", "manual"} else "auto"

    def sip_manual_external_ipv4(self):
        value = str(self.read_setting("sip_external_ipv4") or "").strip()
        try:
            ipaddress.IPv4Address(value)
            return value
        except Exception:
            return ""

    def ensure_sip_rtp_port_range_settings(self):
        defaults = [
            ("sip_rtp_port_start", str(SIP_RTP_DEFAULT_PORT_START), "SIP RTP port range start"),
            ("sip_rtp_port_end", str(SIP_RTP_DEFAULT_PORT_END), "SIP RTP port range end"),
        ]
        conn = self.connect_db()
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO systemsettings (`parameter`, `value`, `description`)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `value` = IF(TRIM(COALESCE(`value`, '')) = '', VALUES(`value`), `value`),
                        `description` = VALUES(`description`)
                    """,
                    defaults,
                )
        finally:
            conn.close()

    def sip_rtp_port_range(self):
        start_raw = self.read_setting("sip_rtp_port_start")
        end_raw = self.read_setting("sip_rtp_port_end")
        if start_raw in (None, "") or end_raw in (None, ""):
            self.ensure_sip_rtp_port_range_settings()
            if start_raw in (None, ""):
                start_raw = SIP_RTP_DEFAULT_PORT_START
            if end_raw in (None, ""):
                end_raw = SIP_RTP_DEFAULT_PORT_END
        start = safe_int(start_raw, SIP_RTP_DEFAULT_PORT_START)
        end = safe_int(end_raw, SIP_RTP_DEFAULT_PORT_END)
        if start < 1024:
            start = 1024
        if end > 65535:
            end = 65535
        if start > end:
            start, end = end, start
        if start % 2:
            start += 1
        if end % 2:
            end -= 1
        return start, end

    def sip_external_ipv4(self, force=False):
        if not force and self.sip_nat_mode() == "no":
            return ""
        if self.sip_external_ipv4_mode() == "manual":
            return self.sip_manual_external_ipv4()
        now = time.time()
        cached_value = str(self.public_ip_cache.get("value") or "").strip()
        cached_at = float(self.public_ip_cache.get("loaded_at") or 0.0)
        if cached_value and not force and (now - cached_at) < 300:
            return cached_value
        try:
            request_obj = urllib.request.Request(OPENPAGINGSERVER_IPADDR_URL, headers={"User-Agent": "OpenPagingServer"})
            with urllib.request.urlopen(request_obj, timeout=3) as response:
                payload = response.read().decode("utf-8", errors="ignore").strip()
            ipaddress.IPv4Address(payload)
            self.public_ip_cache = {"value": payload, "loaded_at": now}
            return payload
        except Exception:
            if cached_value:
                return cached_value
            return ""

    def is_private_address(self, value):
        try:
            return ipaddress.ip_address(str(value or "").strip()).is_private
        except Exception:
            return False

    def normalize_sdp_media_ip(self, media_ip, fallback_ip="", nat_mode=None):
        candidate = str(media_ip or "").strip()
        fallback = str(fallback_ip or "").strip()
        if not candidate:
            return fallback
        try:
            candidate_ip = ipaddress.ip_address(candidate)
        except Exception:
            return candidate
        if (
            candidate_ip.is_unspecified
            or candidate_ip.is_loopback
            or candidate_ip.is_multicast
            or candidate_ip.is_link_local
        ):
            return fallback or candidate
        if fallback:
            try:
                fallback_ipaddr = ipaddress.ip_address(fallback)
            except Exception:
                fallback_ipaddr = None
            if (
                self.effective_nat_mode(nat_mode) != "no"
                and fallback_ipaddr is not None
                and candidate_ip.is_private
                and not fallback_ipaddr.is_private
            ):
                return fallback
        return candidate

    def target_prefers_private_media(self, remote_ip=None):
        target = str(remote_ip or "").strip()
        if not target:
            return False
        if self.is_private_address(target):
            return True
        resolved = self.resolve_host_ips(target)
        return bool(resolved) and all(self.is_private_address(item) for item in resolved)

    def sip_nat_should_advertise(self, remote_ip=None, local_ip=None, nat_mode=None, force_external=False):
        mode = self.effective_nat_mode(nat_mode)
        if force_external:
            mode = "yes"
        if mode == "no":
            return False
        if mode == "yes":
            return True
        if self.target_prefers_private_media(remote_ip):
            return False
        if self.sip_external_ipv4_mode() == "manual" and self.sip_manual_external_ipv4():
            return True
        local_ip = str(local_ip or "").strip() or self.local_ip_for(remote_ip or "127.0.0.1")
        return self.is_private_address(local_ip)

    def advertised_ip_for(self, remote_ip=None, force_external=False, nat_mode=None):
        local_ip = self.local_ip_for(remote_ip or "127.0.0.1")
        mode = self.effective_nat_mode(nat_mode)
        if self.sip_nat_should_advertise(remote_ip, local_ip, nat_mode=mode, force_external=force_external):
            external = self.sip_external_ipv4(force=force_external or mode == "yes")
            if external:
                return external
        return local_ip

    def bind_rtp_socket(self):
        start, end = self.sip_rtp_port_range()
        if start > end:
            raise SipCongestionError(f"No available even SIP RTP UDP ports in configured range {start}-{end}")
        count = ((end - start) // 2) + 1
        base = self.rtp_port_cursor if self.rtp_port_cursor is not None and start <= self.rtp_port_cursor <= end else start
        if base % 2:
            base += 1
        if base > end:
            base = start
        base_index = (base - start) // 2
        for offset in range(count):
            port = start + (((base_index + offset) % count) * 2)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind(("0.0.0.0", port))
                sock.settimeout(0.25)
                self.rtp_port_cursor = port + 2 if port + 2 <= end else start
                sip_debug(f"rtp bind local={sip_sockname(sock)} range={start}-{end}")
                return sock
            except OSError:
                try:
                    sock.close()
                except Exception:
                    pass
        raise SipCongestionError(f"No available SIP RTP UDP ports in configured range {start}-{end}")

    def attach_session_media_socket(self, session, new_socket):
        closed = set()
        for attr in ("socket", "local_sock", "sock"):
            try:
                current = getattr(session, attr, None)
            except Exception:
                current = None
            if current is not None and current is not new_socket and id(current) not in closed:
                closed.add(id(current))
                try:
                    current.close()
                except Exception:
                    pass
        if hasattr(session, "socket"):
            session.socket = new_socket
        if hasattr(session, "local_sock"):
            session.local_sock = new_socket
        if hasattr(session, "sock"):
            session.sock = new_socket
        session.local_port = new_socket.getsockname()[1]
        sip_debug(f"session media bind session={session.__class__.__name__} local={sip_sockname(new_socket)}")
        return session.local_port

    def bind_session_media(self, session):
        return self.attach_session_media_socket(session, self.bind_rtp_socket())

    def session_media_socket(self, session):
        for attr in ("socket", "local_sock", "sock"):
            sock = getattr(session, attr, None)
            if sock is not None:
                return sock
        return None

    def advertised_rtp_address_for_socket(self, sock, remote_ip, nat_mode=None, preferred_public_ip=None):
        local_ip = self.local_ip_for(remote_ip or "127.0.0.1")
        advertised_ip = self.advertised_ip_for(remote_ip, nat_mode=nat_mode)
        preferred_public_ip = str(preferred_public_ip or "").strip()
        if preferred_public_ip and self.sip_nat_should_advertise(remote_ip, local_ip, nat_mode=nat_mode):
            try:
                preferred_addr = ipaddress.ip_address(preferred_public_ip)
            except Exception:
                preferred_addr = None
            if preferred_addr is not None and not (
                preferred_addr.is_unspecified
                or preferred_addr.is_loopback
                or preferred_addr.is_multicast
                or preferred_addr.is_link_local
                or preferred_addr.is_private
            ):
                advertised_ip = preferred_public_ip
        if sock is None:
            return advertised_ip, 0
        try:
            local_port = safe_int(sock.getsockname()[1], 0)
        except Exception:
            local_port = 0
        advertised_port = local_port
        sip_debug(
            f"rtp advertise local={local_ip}:{local_port} advertised={advertised_ip}:{advertised_port} "
            f"nat={'yes' if advertised_ip != local_ip else 'no'} remote={remote_ip}"
        )
        return advertised_ip, advertised_port

    def outbound_media_address_for_route(self, sock, route, local_media_ip, local_sip_ip, nat_mode=None):
        route = route or {}
        remote_ip = route.get("host")
        advertised_media_ip, advertised_media_port = self.advertised_rtp_address_for_socket(
            sock,
            remote_ip,
            nat_mode=nat_mode,
            preferred_public_ip=local_sip_ip,
        )
        transport = str(route.get("transport") or "udp").strip().lower()
        local_media_ip = str(local_media_ip or "").strip()
        if (
            transport in {"tcp", "tls"}
            and local_media_ip
            and self.sip_nat_should_advertise(remote_ip, local_media_ip, nat_mode=nat_mode)
            and self.is_private_address(local_media_ip)
        ):
            advertised_media_ip = local_media_ip
        return advertised_media_ip, advertised_media_port

    def inbound_media_address_for_source(self, sock, remote_ip, local_media_ip, local_sip_ip, transport="udp", nat_mode=None):
        advertised_media_ip, advertised_media_port = self.advertised_rtp_address_for_socket(
            sock,
            remote_ip,
            nat_mode=nat_mode,
            preferred_public_ip=local_sip_ip,
        )
        transport = str(transport or "udp").strip().lower()
        local_media_ip = str(local_media_ip or "").strip()
        if (
            transport in {"tcp", "tls"}
            and local_media_ip
            and self.sip_nat_should_advertise(remote_ip, local_media_ip, nat_mode=nat_mode)
            and self.is_private_address(local_media_ip)
        ):
            advertised_media_ip = local_media_ip
        return advertised_media_ip, advertised_media_port

    def parse_servers_json(self, raw):
        if raw in (None, ""):
            return []
        if isinstance(raw, list):
            return list(raw)
        candidate = raw
        for _ in range(3):
            if isinstance(candidate, list):
                return list(candidate)
            if isinstance(candidate, dict):
                if isinstance(candidate.get("servers"), list):
                    candidate = candidate.get("servers")
                    continue
                if any(key in candidate for key in ("server", "host", "address")):
                    return [candidate]
                return []
            try:
                candidate = json.loads(candidate)
            except Exception:
                return []
        return []

    def clean_trunk_server(self, item):
        if not isinstance(item, dict):
            return None
        server = str(item.get("server") or item.get("host") or item.get("address") or "").strip()
        if not server:
            return None
        transport = str(item.get("transport") or "udp").strip().lower()
        if transport not in {"dns", "udp", "tcp", "tls"}:
            transport = "udp"
        default_port = 5061 if transport == "tls" else 5060
        port = safe_int(item.get("port"), default_port)
        if port < 1 or port > 65535:
            port = default_port
        expires = safe_int(item.get("expires"), 300)
        if expires < 60:
            expires = 60
        if expires > 86400:
            expires = 86400
        return {
            "server": server,
            "outbound_proxy": str(item.get("outbound_proxy") or item.get("proxy") or "").strip(),
            "transport": transport,
            "port": port,
            "expires": expires,
        }

    def is_outbound_trunk_row(self, row):
        auth_type = str(row.get("auth") or "").upper()
        trunk_type = str(row.get("trunk_type") or "").upper()
        if auth_type == "OUTBOUND" or trunk_type == "OUTBOUND_AUTH":
            return True
        return any(self.clean_trunk_server(item) for item in self.parse_servers_json(row.get("servers_json"))[:8])

    def fetch_outbound_trunks(self):
        conn = self.connect_db()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                try:
                    select_columns = self.sip_trunk_select_columns()
                    cur.execute(
                        f"SELECT {select_columns} FROM `sip-trunks` "
                        f"WHERE COALESCE(`servers_json`,'')<>'' OR `auth`='OUTBOUND' OR `trunk_type`='OUTBOUND_AUTH' "
                        f"ORDER BY `id` ASC"
                    )
                    rows = cur.fetchall()
                except Exception:
                    traceback.print_exc()
                    return []
        finally:
            conn.close()
        results = []
        for row in rows:
            if not self.is_outbound_trunk_row(row):
                continue
            servers = []
            for item in self.parse_servers_json(row.get("servers_json"))[:8]:
                clean = self.clean_trunk_server(item)
                if clean:
                    servers.append(clean)
            if not servers:
                continue
            row["servers"] = servers
            row["outbound_nat"] = str(row.get("outbound_nat") or "auto").strip().lower()
            results.append(row)
        return results

    def outbound_trunk_config_signature(self, row):
        return json.dumps(
            {
                "username": str(row.get("username") or "").strip(),
                "password": str(row.get("password") or ""),
                "outbound_nat": str(row.get("outbound_nat") or "auto").strip().lower(),
                "servers": row.get("servers") or [],
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def fetch_trunk_row(self, trunk_id):
        conn = self.connect_db()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                select_columns = self.sip_trunk_select_columns()
                cur.execute(
                    f"SELECT {select_columns} FROM `sip-trunks` WHERE `id`=%s LIMIT 1",
                    (trunk_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row and self.is_outbound_trunk_row(row):
            row["servers"] = [clean for clean in (self.clean_trunk_server(item) for item in self.parse_servers_json(row.get("servers_json"))[:8]) if clean]
        return row

    def dns_service_candidates(self, domain):
        if dns is None:
            return []
        resolver = dns.resolver.Resolver()
        candidates = []
        try:
            naptr_answers = resolver.resolve(domain, "NAPTR")
        except Exception:
            naptr_answers = []
        for answer in naptr_answers:
            service = str(getattr(answer, "service", "")).upper()
            replacement = str(getattr(answer, "replacement", "")).rstrip(".")
            order = int(getattr(answer, "order", 0))
            preference = int(getattr(answer, "preference", 0))
            if service == "SIPS+D2T":
                candidates.append((order, preference, "_sips._tcp", "tls"))
            elif service == "SIP+D2T":
                candidates.append((order, preference, "_sip._tcp", "tcp"))
            elif service == "SIP+D2U":
                candidates.append((order, preference, "_sip._udp", "udp"))
            elif replacement:
                candidates.append((order, preference, replacement, "udp"))
        if not candidates:
            candidates = [
                (0, 0, "_sip._udp", "udp"),
                (1, 0, "_sip._tcp", "tcp"),
                (2, 0, "_sips._tcp", "tls"),
            ]
        routes = []
        for _, _, service_name, transport in sorted(candidates):
            query_name = service_name if service_name.startswith("_") else service_name
            target_name = f"{query_name}.{domain}" if query_name.startswith("_") else query_name
            try:
                answers = resolver.resolve(target_name, "SRV")
            except Exception:
                continue
            for answer in sorted(answers, key=lambda item: (int(getattr(item, "priority", 0)), -int(getattr(item, "weight", 0)))):
                routes.append(
                    {
                        "host": str(getattr(answer, "target", "")).rstrip("."),
                        "port": int(getattr(answer, "port", 5061 if transport == "tls" else 5060)),
                        "transport": transport,
                    }
                )
            if routes:
                break
        return routes

    def resolve_trunk_server_routes(self, server_row):
        transport = str(server_row.get("transport") or "udp").strip().lower()
        domain = str(server_row.get("server") or "").strip()
        host = str(server_row.get("outbound_proxy") or domain).strip()
        port = safe_int(server_row.get("port"), 5061 if transport == "tls" else 5060)
        uses_outbound_proxy = bool(str(server_row.get("outbound_proxy") or "").strip())
        if not host:
            return []
        if transport != "dns":
            return [{
                "host": host,
                "port": port,
                "transport": transport,
                "domain": domain or host,
                "is_outbound_proxy": uses_outbound_proxy,
            }]
        routes = self.dns_service_candidates(host)
        if routes:
            for route in routes:
                route["domain"] = domain or host
                route["is_outbound_proxy"] = uses_outbound_proxy
            return routes
        return [{
            "host": host,
            "port": 5060,
            "transport": "udp",
            "domain": domain or host,
            "is_outbound_proxy": uses_outbound_proxy,
        }]

    def route_domain(self, route):
        return str(route.get("domain") or route.get("host") or "").strip()

    def local_signaling_address(self, route, state=None, force_external=False, nat_mode=None):
        domain = self.route_domain(route)
        transport = str(route.get("transport") or "udp").strip().lower()
        mode = self.effective_nat_mode(nat_mode)
        default_ip = self.advertised_ip_for(domain or route.get("host"), force_external=force_external, nat_mode=nat_mode)
        default_port = self.active_port or 5060
        if transport in {"tcp", "tls"}:
            if state is not None:
                conn = state.get("conn")
                if conn is not None:
                    try:
                        conn_ip, conn_port = conn.getsockname()[:2]
                        if conn_ip:
                            return str(conn_ip), safe_int(conn_port, default_port)
                    except Exception:
                        pass
                public_ip = str(state.get("public_signaling_ip") or "").strip()
                public_port = safe_int(state.get("public_signaling_port"), 0)
                if mode != "no" and public_ip and public_port > 0:
                    return public_ip, public_port
            return default_ip, default_port
        if transport == "udp" and state is not None:
            public_ip = str(state.get("public_signaling_ip") or "").strip()
            public_port = safe_int(state.get("public_signaling_port"), 0)
            if mode != "no" and public_ip and public_port > 0:
                return public_ip, public_port
        return (default_ip, default_port)

    def outbound_state_for_connection(self, conn):
        if conn is None:
            return None
        with self.outbound_lock:
            states = list(self.outbound_trunks.values())
        for state in states:
            if state.get("conn") is conn:
                return state
        return None

    def inbound_dialog_addresses(self, remote_ip, transport="udp", conn=None, nat_mode=None):
        mode = clean_nat_mode(nat_mode if nat_mode is not None else self.sip_nat_mode(), "auto")
        local_media_ip = self.advertised_ip_for(remote_ip, nat_mode=mode)
        local_sip_ip = local_media_ip
        local_sip_port = self.active_port or 5060
        if str(transport or "").strip().lower() in {"tcp", "tls"} and conn is not None:
            actual_port = local_sip_port
            conn_ip = ""
            try:
                conn_ip, conn_port = conn.getsockname()[:2]
                actual_port = safe_int(conn_port, local_sip_port)
            except Exception:
                pass
            if conn_ip:
                return str(conn_ip), actual_port, local_media_ip
        return local_sip_ip, local_sip_port, local_media_ip

    def parse_via_public_mapping(self, via_value):
        raw = str(via_value or "").strip()
        if not raw:
            return "", 0
        received_match = re.search(r"(?:^|[;,\s])received=([^;,\s]+)", raw, re.IGNORECASE)
        rport_match = re.search(r"(?:^|[;,\s])rport=(\d+)", raw, re.IGNORECASE)
        public_ip = str(received_match.group(1) if received_match else "").strip()
        public_port = safe_int(rport_match.group(1) if rport_match else 0, 0)
        return public_ip, public_port

    def learn_signaling_mapping_from_response(self, response, state=None, call=None):
        headers = (response or {}).get("headers") or {}
        public_ip, public_port = self.parse_via_public_mapping(headers.get("via", ""))
        if not public_ip or public_port <= 0:
            return "", 0
        if state is not None:
            state["public_signaling_ip"] = public_ip
            state["public_signaling_port"] = public_port
        if call is not None:
            transport = str(getattr(call, "transport", "") or "").strip().lower()
            if transport not in {"tcp", "tls"}:
                call.local_sip_ip = public_ip
                call.local_sip_port = public_port
        return public_ip, public_port

    def extract_basic_credentials(self, headers):
        for header_name in ("Authorization", "Proxy-Authorization"):
            value = self.get_first(headers, header_name)
            if not value:
                continue
            parts = value.split(None, 1)
            if len(parts) != 2:
                continue
            scheme = parts[0].strip().lower()
            if scheme != "basic":
                continue
            try:
                decoded = base64.b64decode(parts[1].strip()).decode("utf-8", errors="ignore")
                if ":" in decoded:
                    username, password = decoded.split(":", 1)
                    return username, password
            except:
                pass
        return None, None

    def extract_username_from_auth(self, headers):
        auth_header = self.get_first(headers, "Authorization")
        if not auth_header:
            return None
        if auth_header.lower().startswith("digest"):
            auth_str = auth_header[6:].strip()
            matches = re.findall(r'username\s*=\s*(?:"([^"]+)"|([^,]+))', auth_str, re.IGNORECASE)
            if matches:
                return matches[0][0] if matches[0][0] else matches[0][1]
        else:
            user, _ = self.extract_basic_credentials(headers)
            return user
        return None

    def trunk_user_agent(self, headers):
        return self.get_first(headers, "User-Agent") or self.get_first(headers, "Server") or "Unknown"

    def mark_authorized_trunk_seen(self, method, ipaddr, headers):
        user_agent = self.trunk_user_agent(headers)
        if auth.auth_ip(ipaddr):
            auth.update_trunk_status_by_ip(ipaddr, f"Online, '{user_agent}'")
            return
        username = self.extract_username_from_auth(headers)
        if username and self.ip_allowed(method, ipaddr, headers=headers):
            auth.update_trunk_status_by_user(username, f"{ipaddr}, '{user_agent}'")

    def sip_trunk_hold_behavior(self, ipaddr, headers):
        behavior_columns = ("holdbehabior", "hold-behavipr", "holdbehavior", "holdbehaviour", "hold-behavior")
        username = self.extract_username_from_auth(headers) if headers else None
        try:
            conn = self.connect_db()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cur:
                    existing = set()
                    try:
                        cur.execute("SHOW COLUMNS FROM `sip-trunks`")
                        existing = {str(row.get("Field") or row.get("field") or "") for row in cur.fetchall()}
                    except Exception:
                        return "passrtp"
                    behavior_column = next((col for col in behavior_columns if col in existing), None)
                    if not behavior_column:
                        return "passrtp"
                    cur.execute(f"SELECT `auth`, `username`, `ipaddr`, `{behavior_column}` AS hold_behavior FROM `sip-trunks`")
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception:
            return "passrtp"

        selected = None
        if username:
            for row in rows:
                if str(row.get("auth") or "").upper() == "USERPASS" and str(row.get("username") or "") == username:
                    selected = row
                    break
        if selected is None:
            for row in rows:
                if str(row.get("auth") or "").upper() == "IP" and auth.ip_match(ipaddr, str(row.get("ipaddr") or "")):
                    selected = row
                    break

        behavior = str((selected or {}).get("hold_behavior") or "passrtp").strip().lower()
        if behavior not in ("passrtp", "pausertp", "endcall"):
            return "passrtp"
        return behavior

    def ip_allowed(self, method, ipaddr, headers=None):
        if auth.auth_ip(ipaddr):
            return True

        if headers:
            auth_header = self.get_first(headers, "Authorization")
            if not auth_header:
                return False

            if not auth_header.lower().startswith("digest"):
                username, password = self.extract_basic_credentials(headers)
                if username and password:
                    db_pass, allowed_ip = auth.get_password_for_user(username)
                    if db_pass == password:
                        if not allowed_ip or allowed_ip in ("0.0.0.0", "0.0.0.0/0", "") or auth.ip_match(ipaddr, allowed_ip):
                            return True
                return False

            auth_str = auth_header[6:].strip()
            creds = {}
            matches = re.findall(r'(\w+)\s*=\s*(?:"([^"]+)"|([^,]+))', auth_str)
            for match in matches:
                key = match[0].lower()
                val = match[1] if match[1] else match[2]
                creds[key] = val

            username = creds.get('username')
            realm = creds.get('realm')
            nonce = creds.get('nonce')
            uri = creds.get('uri')
            response = creds.get('response')
            qop = creds.get('qop')
            nc = creds.get('nc')
            cnonce = creds.get('cnonce')

            if not all([username, realm, nonce, uri, response]):
                return False

            db_password, allowed_ip = auth.get_password_for_user(username)
            if db_password is None:
                return False

            if allowed_ip and allowed_ip not in ("0.0.0.0", "0.0.0.0/0", ""):
                if not auth.ip_match(ipaddr, allowed_ip):
                    return False

            ha1 = hashlib.md5(f"{username}:{realm}:{db_password}".encode('utf-8')).hexdigest()
            ha2 = hashlib.md5(f"{method}:{uri}".encode('utf-8')).hexdigest()

            if qop and qop.lower() in ('auth', 'auth-int'):
                if not all([nc, cnonce]):
                    return False
                expected_response = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode('utf-8')).hexdigest()
            else:
                expected_response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode('utf-8')).hexdigest()

            if response == expected_response:
                return True

        return False

    def get_endpoint_trigger(self, extension):
        try:
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    passcode_expr = "NULL"
                    for col in ("passcode", "passocde"):
                        try:
                            cur.execute("SHOW COLUMNS FROM `endpoints-input-siptrunk` LIKE %s", (col,))
                            if cur.fetchone() is not None:
                                passcode_expr = f"`{col}`"
                                break
                        except:
                            pass

                    cur.execute(f"SELECT `trigger`, {passcode_expr} FROM `endpoints-input-siptrunk` WHERE `extension` = %s LIMIT 1", (extension,))
                    row = cur.fetchone()
                    if row is not None:
                        return "found", row[0], row[1]
                    
                    if "#" in extension:
                        cur.execute(f"SELECT `trigger`, {passcode_expr} FROM `endpoints-input-siptrunk` WHERE `extension` = %s LIMIT 1", (extension.replace("#", "%23"),))
                        row2 = cur.fetchone()
                        if row2 is not None:
                            return "found", row2[0], row2[1]
                    elif "%23" in extension:
                        cur.execute(f"SELECT `trigger`, {passcode_expr} FROM `endpoints-input-siptrunk` WHERE `extension` = %s LIMIT 1", (extension.replace("%23", "#"),))
                        row3 = cur.fetchone()
                        if row3 is not None:
                            return "found", row3[0], row3[1]
                            
                    return "not_found", None, None
            finally:
                conn.close()
        except Exception:
            return "error", None, None

    def log_packet(self, direction, addr, data, transport):
        try:
            text = data if isinstance(data, str) else data.decode("utf-8", errors="ignore")
            if not text.strip():
                return
            timestamp = time.strftime("%H:%M:%S")
            print(f"\n[{timestamp}] {direction} {transport.upper()} {addr[0]}:{addr[1]}")
            print("-" * 60)
            print(text.strip())
            print("-" * 60)
        except Exception:
            pass

    def parse_request(self, raw):
        head_bytes, body_bytes = sip_split_message(raw)
        lines = sip_header_lines(head_bytes)
        if not lines:
            return None, None, None, [], ""
        req_line = lines[0].split(" ", 2)
        if len(req_line) < 2:
            return None, None, None, [], ""
        method = req_line[0]
        uri = req_line[1]
        version = req_line[2] if len(req_line) > 2 else ""
        headers = sip_header_tuples_from_lines(lines)
        content_length_found = False
        content_length = 0
        for name, value in headers:
            if name.lower() in sip_header_lookup_names("Content-Length"):
                content_length_found = True
                content_length = sip_content_length(value)
                break
        if content_length_found and content_length > 0:
            body_bytes = body_bytes[:content_length]
        elif content_length_found and content_length == 0 and not body_bytes.lstrip().startswith(b"v="):
            body_bytes = b""
        body = body_bytes.decode("utf-8", errors="ignore")
        return method.upper(), uri, version, headers, body

    def get_first(self, headers, name):
        lookup_names = sip_header_lookup_names(name)
        for k, v in headers:
            if str(k or "").strip().lower() in lookup_names:
                return v
        return ""

    def get_all(self, headers, name):
        lookup_names = sip_header_lookup_names(name)
        return [v for k, v in headers if str(k or "").strip().lower() in lookup_names]

    def local_ip_for(self, remote_ip):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((remote_ip, 9))
            return sock.getsockname()[0]
        except:
            return "127.0.0.1"
        finally:
            sock.close()

    def sip_uri_from_header(self, value):
        raw = str(value or "").strip()
        match = re.search(r'<([^>]+)>', raw)
        if match:
            return match.group(1).strip()
        if "," in raw:
            raw = raw.split(",", 1)[0].strip()
        return raw.split(";", 1)[0].strip() if raw.lower().startswith("sip:") else raw

    def request_user_from_uri(self, uri):
        raw_uri = self.sip_uri_from_header(uri)
        if not raw_uri:
            return ""
        target = re.sub(r'^sips?:', '', raw_uri, flags=re.IGNORECASE)
        user_part = target.rsplit("@", 1)[0] if "@" in target else target
        user_part = user_part.split(";", 1)[0].strip()
        return urllib.parse.unquote(user_part)

    def parse_sip_target(self, value, fallback_ip=None, fallback_port=5060, fallback_transport="udp"):
        uri = self.sip_uri_from_header(value)
        if not uri:
            return fallback_ip, fallback_port, fallback_transport
        target = re.sub(r'^sips?:', '', uri, flags=re.IGNORECASE)
        address_part, *param_parts = target.split(";")
        params = ";".join(param_parts)
        if "@" in address_part:
            address_part = address_part.rsplit("@", 1)[1]
        host = fallback_ip
        port = fallback_port
        if address_part.startswith("[") and "]" in address_part:
            host = address_part[1:address_part.index("]")]
            rest = address_part[address_part.index("]") + 1:]
            if rest.startswith(":"):
                try:
                    port = int(rest[1:])
                except ValueError:
                    port = fallback_port
        elif ":" in address_part:
            host_part, port_part = address_part.rsplit(":", 1)
            host = host_part or fallback_ip
            try:
                port = int(port_part)
            except ValueError:
                port = fallback_port
        elif address_part:
            host = address_part
        transport = fallback_transport
        match = re.search(r'(?:^|;)transport=([^;]+)', params, re.IGNORECASE)
        if match:
            transport = match.group(1).lower()
        return host, port, transport

    def parse_via_sent_by(self, via_value):
        raw = str(via_value or "").strip()
        parts = raw.split(None, 2)
        if len(parts) < 2:
            return "", 0
        sent_by = parts[1].split(";", 1)[0].split(",", 1)[0].strip()
        host = sent_by
        port = 5060
        if sent_by.startswith("[") and "]" in sent_by:
            host = sent_by[1:sent_by.index("]")]
            rest = sent_by[sent_by.index("]") + 1:]
            if rest.startswith(":"):
                port = safe_int(rest[1:], 5060)
        elif ":" in sent_by:
            host_part, port_part = sent_by.rsplit(":", 1)
            host = host_part.strip() or host
            port = safe_int(port_part, 5060)
        return host, port

    def set_via_param(self, via_value, name, value):
        first, sep, rest = str(via_value or "").partition(",")
        pattern = re.compile(rf"(;{re.escape(name)})(?:=[^;,\s]*)?", re.IGNORECASE)
        replacement = rf"\1={value}"
        if pattern.search(first):
            first = pattern.sub(replacement, first, count=1)
        else:
            first = f"{first};{name}={value}"
        return first + sep + rest

    def nat_annotate_request_headers(self, headers, source_ip, source_port, transport="udp"):
        if str(transport or "").strip().lower() != "udp":
            return headers
        source_ip = str(source_ip or "").strip()
        source_port = safe_int(source_port, 0)
        if not source_ip or source_port <= 0:
            return headers
        annotated = []
        top_via_done = False
        for name, value in headers:
            if not top_via_done and str(name or "").strip().lower() in sip_header_lookup_names("Via"):
                via = str(value or "")
                sent_host, _sent_port = self.parse_via_sent_by(via)
                nat_detected = bool(sent_host and sent_host != source_ip)
                if sent_host and sent_host != source_ip:
                    via = self.set_via_param(via, "received", source_ip)
                try:
                    mode = self.sip_nat_mode()
                except Exception:
                    mode = "auto"
                force_rport = mode == "yes" or (mode == "auto" and nat_detected)
                if force_rport or re.search(r"(?:^|;)\s*rport(?:[;=,\s]|$)", via, re.IGNORECASE):
                    via = self.set_via_param(via, "rport", source_port)
                annotated.append((name, via))
                top_via_done = True
            else:
                annotated.append((name, value))
        return annotated

    def normalize_dialog_contact(self, contact, source_ip, source_port, transport="udp", nat_mode=None):
        fallback_transport = str(transport or "udp").strip().lower() or "udp"
        fallback_port = safe_int(source_port, 5060)
        source_ip = str(source_ip or "").strip()
        raw_contact = str(contact or "").strip()
        if not raw_contact:
            return f"<sip:{source_ip}:{fallback_port};transport={fallback_transport}>"
        uri = self.sip_uri_from_header(raw_contact)
        if not uri:
            return f"<sip:{source_ip}:{fallback_port};transport={fallback_transport}>"
        host, port, uri_transport = self.parse_sip_target(
            uri,
            fallback_ip=source_ip,
            fallback_port=fallback_port,
            fallback_transport=fallback_transport,
        )
        host = str(host or "").strip()
        port = safe_int(port, fallback_port)
        normalized_host = self.normalize_sdp_media_ip(host, source_ip, nat_mode=nat_mode)
        if normalized_host == host:
            return raw_contact
        if fallback_transport == "udp":
            port = fallback_port
        target = re.sub(r'^sips?:', '', uri, flags=re.IGNORECASE)
        address_part, *param_parts = target.split(";")
        param_suffix = ";" + ";".join(param_parts) if param_parts else ""
        user_prefix = ""
        if "@" in address_part:
            user_prefix = address_part.rsplit("@", 1)[0] + "@"
        scheme = "sips" if uri.lower().startswith("sips:") else "sip"
        final_transport = str(uri_transport or fallback_transport or "udp").strip().lower()
        if "transport=" not in param_suffix.lower():
            param_suffix += f";transport={final_transport}"
        return f"<{scheme}:{user_prefix}{normalized_host}:{port}{param_suffix}>"

    def bye_network_target(self, call):
        route_set = list(call.get("record_routes") or [])
        target_source = route_set[0] if route_set else call.get("contact")
        host, port, transport = self.parse_sip_target(
            target_source,
            fallback_ip=call.get("remote_sip_ip"),
            fallback_port=call.get("remote_sip_port") or 5060,
            fallback_transport=call.get("transport") or "udp",
        )
        fallback_ip = str(call.get("remote_sip_ip") or "").strip()
        host = str(host or "").strip()
        nat_mode = call.get("nat_mode")
        if host:
            normalized_host = self.normalize_sdp_media_ip(host, fallback_ip, nat_mode=nat_mode)
            if normalized_host:
                if normalized_host != host and (transport or "").lower() == "udp":
                    port = call.get("remote_sip_port") or port
                host = normalized_host
        return host or call.get("remote_sip_ip"), int(port or 5060), (transport or call.get("transport") or "udp").lower()

    def parse_sdp_offer(self, body, fallback_ip, nat_mode=None):
        session_ip = str(fallback_ip or "").strip()
        audio_ip = ""
        media_port = None
        has_video = False
        current_media = ""
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("c=IN IP4 "):
                if current_media == "audio":
                    audio_ip = line[9:].strip()
                elif not current_media:
                    session_ip = line[9:].strip()
            elif line.startswith("m=audio "):
                current_media = "audio"
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        media_port = int(parts[1])
                    except:
                        media_port = None
            elif line.startswith("m=video "):
                current_media = "video"
                has_video = True
            elif line.startswith("m="):
                current_media = "other"
        media_ip = audio_ip or session_ip or str(fallback_ip or "").strip()
        return self.normalize_sdp_media_ip(media_ip, fallback_ip, nat_mode=nat_mode), media_port, has_video

    def sdp_audio_connection_ip(self, body):
        session_ip = ""
        audio_ip = ""
        current_media = ""
        for raw_line in str(body or "").splitlines():
            line = raw_line.strip()
            if line.startswith("c=IN IP4 "):
                if current_media == "audio":
                    audio_ip = line[9:].strip()
                elif not current_media:
                    session_ip = line[9:].strip()
            elif line.startswith("m=audio "):
                current_media = "audio"
            elif line.startswith("m="):
                current_media = "other"
        return audio_ip or session_ip

    def sdp_media_needs_latching(self, body, fallback_ip, nat_mode=None):
        if self.effective_nat_mode(nat_mode) == "no":
            return False
        if not self.target_prefers_private_media(fallback_ip):
            return True
        media_ip = self.sdp_audio_connection_ip(body)
        if not media_ip:
            return False
        try:
            media_addr = ipaddress.ip_address(str(media_ip).strip())
        except Exception:
            return False
        if not (
            media_addr.is_private
            or media_addr.is_unspecified
            or media_addr.is_loopback
            or media_addr.is_link_local
        ):
            return False
        fallback = str(fallback_ip or "").strip()
        if not fallback:
            return True
        try:
            fallback_addr = ipaddress.ip_address(fallback)
        except Exception:
            return True
        return not fallback_addr.is_private

    def sdp_offer_is_hold(self, body):
        audio_section = False
        session_connection = ""
        audio_connection = ""
        audio_port = None
        audio_direction = ""
        for raw_line in body.splitlines():
            line = raw_line.strip().lower()
            if line.startswith("c=in ip4 "):
                if audio_section:
                    audio_connection = line[9:].strip()
                else:
                    session_connection = line[9:].strip()
            elif line.startswith("m="):
                audio_section = line.startswith("m=audio ")
                if audio_section:
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            audio_port = int(parts[1])
                        except:
                            audio_port = None
            elif audio_section and line in ("a=sendonly", "a=inactive"):
                audio_direction = line[2:]
            elif not audio_section and line in ("a=sendonly", "a=inactive") and not audio_direction:
                audio_direction = line[2:]
        connection = audio_connection or session_connection
        return audio_port == 0 or connection == "0.0.0.0" or audio_direction in ("sendonly", "inactive")

    def set_session_media_target(self, session, remote_media_ip, remote_media_port):
        if hasattr(session, "target_ip"):
            session.target_ip = remote_media_ip
        if hasattr(session, "target_port"):
            session.target_port = remote_media_port
        if hasattr(session, "remote_ip"):
            session.remote_ip = remote_media_ip
        if hasattr(session, "remote_port"):
            session.remote_port = remote_media_port

    def set_session_rtp_paused(self, session, paused):
        try:
            session.rtp_paused = bool(paused)
        except Exception:
            pass

    def set_session_rtp_latching(self, session, enabled):
        try:
            session.rtp_latching_enabled = bool(enabled)
        except Exception:
            pass

    def prepare_inbound_session(self, session):
        if session is None:
            return
        try:
            if hasattr(session, "initial_silence_frames"):
                session.initial_silence_frames = max(int(getattr(session, "initial_silence_frames", 0) or 0), 6)
        except Exception:
            pass

    def prime_inbound_media_path(self, call, frames=6):
        if not call or call.get("media_path_primed"):
            return 0
        session = call.get("session")
        sock = self.session_media_socket(session)
        if sock is None:
            return 0
        target_ip = (
            getattr(session, "target_ip", "")
            or getattr(session, "remote_ip", "")
            or call.get("remote_media_ip")
        )
        target_port = safe_int(
            getattr(session, "target_port", 0)
            or getattr(session, "remote_port", 0)
            or call.get("remote_media_port"),
            0,
        )
        if not target_ip or target_port <= 0:
            return 0
        try:
            frames = max(0, int(frames))
        except Exception:
            frames = 0
        sequence = random.randrange(0, 65536)
        timestamp = random.randrange(0, 4294967296)
        ssrc = random.randrange(0, 4294967296)
        payload = b"\xff" * 160
        sent = 0
        for _ in range(frames):
            packet = struct.pack("!BBHII", 0x80, 0x00, sequence, timestamp, ssrc) + payload
            try:
                sock.sendto(packet, (target_ip, target_port))
            except Exception as exc:
                sip_debug(
                    f"inbound media prime send failed call={call.get('call_id', '')} "
                    f"local={sip_sockname(sock)} remote={target_ip}:{target_port} "
                    f"error={exc.__class__.__name__}: {exc}"
                )
                break
            sent += 1
            sequence = (sequence + 1) & 0xFFFF
            timestamp = (timestamp + 160) & 0xFFFFFFFF
        if sent:
            call["media_path_primed"] = True
            sip_debug(
                f"inbound media prime call={call.get('call_id', '')} packets={sent} "
                f"local={sip_sockname(sock)} remote={target_ip}:{target_port}"
            )
        return sent

    def activate_call_media(self, call, run_on_start=True):
        if not call:
            return
        session = call.get("session")
        if session is None:
            return
        self.prepare_inbound_session(session)
        if not call.get("started"):
            call["started"] = True
            sip_debug(
                f"inbound media start call={call.get('call_id', '')} session={session.__class__.__name__} "
                f"local={sip_sockname(self.session_media_socket(session))} "
                f"remote={getattr(session, 'remote_ip', getattr(session, 'target_ip', ''))}:"
                f"{getattr(session, 'remote_port', getattr(session, 'target_port', ''))} "
                f"latching={bool(getattr(session, 'rtp_latching_enabled', False))}"
            )
            session.start()
        if run_on_start and call.get("on_start") and not call.get("on_start_ran"):
            try:
                call["on_start"]()
            except Exception as e:
                print(f"Error in trigger on_start: {e}")
            call["on_start_ran"] = True

    def schedule_finish_call(self, call_id, delay=0.2):
        def worker():
            time.sleep(delay)
            self.finish_call(call_id)
        threading.Thread(target=worker, daemon=True).start()

    def build_response(self, headers, status, body="", content_type="application/sdp", local_ip=None, local_port=None, transport="udp", call_tag=None, extra_headers=None, is_register=False):
        vias = self.get_all(headers, "Via")
        from_h = self.get_first(headers, "From")
        to_h = self.get_first(headers, "To")
        call_id = self.get_first(headers, "Call-ID")
        cseq = self.get_first(headers, "CSeq")
        record_routes = self.get_all(headers, "Record-Route")

        if to_h and "tag=" not in to_h.lower():
            tag = call_tag if call_tag else uuid.uuid4().hex[:10]
            to_h = to_h + ";tag=" + tag
        lines = ["SIP/2.0 " + status]
        for via in vias:
            lines.append("Via: " + via)
        if from_h:
            lines.append("From: " + from_h)
        if to_h:
            lines.append("To: " + to_h)
        if call_id:
            lines.append("Call-ID: " + call_id)
        if cseq:
            lines.append("CSeq: " + cseq)
        for rr in record_routes:
            lines.append("Record-Route: " + rr)
        contact_port = safe_int(local_port, self.active_port or 5060)
        if local_ip and not is_register:
            lines.append(f"Contact: <sip:OpenPagingServer@{local_ip}:{contact_port};{self.sip_contact_transport_param(transport)}>")
        if extra_headers:
            for header in extra_headers:
                lines.append(header)
        lines.append("Server: OpenPagingServer")
        lines.append("Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REGISTER, INFO")
        if body:
            lines.append("Content-Type: " + content_type)
            lines.append("Content-Length: " + str(len(body.encode("utf-8"))))
            return "\r\n".join(lines) + "\r\n\r\n" + body
        lines.append("Content-Length: 0")
        return "\r\n".join(lines) + "\r\n\r\n"

    def sip_contact_transport_param(self, transport):
        token = str(transport or "udp").strip().lower()
        if token in {"tcp", "tls"}:
            return f"transport={token};alias"
        return f"transport={token}"

    def sip_via_params(self, transport):
        token = str(transport or "udp").strip().lower()
        if token in {"tcp", "tls"}:
            return ";rport;alias"
        return ";rport"

    def build_503(self, headers, local_ip=None, local_port=None, transport="udp", call_tag=None, reason_text="SIP congestion"):
        return self.build_response(
            headers,
            "503 Service Unavailable",
            local_ip=local_ip,
            local_port=local_port,
            transport=transport,
            call_tag=call_tag,
            extra_headers=[f'Reason: SIP ;cause=503 ;text="{reason_text}"']
        )

    def play_trigger_failure_tone(self, session, exc):
        tone = "busy" if "busy" in str(exc).lower() else "reorder"
        player = getattr(session, "play_progress_tone", None)
        if callable(player):
            player(tone=tone, duration=4.0)

    def run_trigger_preflight(self, session, play_failure_tone=False):
        preflight = getattr(session, "preflight", None)
        if callable(preflight):
            try:
                preflight()
                if getattr(session, "setup_failed", False):
                    raise SipCongestionError("SIP congestion")
            except Exception as exc:
                if play_failure_tone:
                    self.play_trigger_failure_tone(session, exc)
                raise

    def caller_id_from_headers(self, headers):
        from_header = self.get_first(headers, "From") or self.get_first(headers, "f")
        if not from_header:
            return ""
        match = re.search(r'sip:([^@;>]+)', from_header, re.IGNORECASE)
        if match:
            return urllib.parse.unquote(match.group(1))
        match = re.search(r'"([^"]+)"', from_header)
        if match:
            return match.group(1)
        return from_header.split(";", 1)[0].strip()

    def build_invite_sdp(self, local_ip, local_port, disable_video=False, direction="sendrecv"):
        sdp = (
            "v=0\r\n"
            + f"o=OpenPagingServer 1 1 IN IP4 {local_ip}\r\n"
            + "s=OpenPagingServer\r\n"
            + f"c=IN IP4 {local_ip}\r\n"
            + "t=0 0\r\n"
            + f"m=audio {local_port} RTP/AVP 0 101\r\n"
            + "a=rtpmap:0 PCMU/8000\r\n"
            + "a=rtpmap:101 telephone-event/8000\r\n"
            + "a=fmtp:101 0-15\r\n"
        )
        if direction:
            sdp += f"a={direction}\r\n"
        if disable_video:
            sdp += "m=video 0 RTP/AVP 31\r\n"
        return sdp

    def client_transaction_key(self, call_id, cseq):
        return str(call_id or "").strip(), str(cseq or "").strip()

    def start_client_transaction(self, call_id, cseq):
        txn = SipClientTransaction()
        with self.outbound_lock:
            self.client_transactions[self.client_transaction_key(call_id, cseq)] = txn
        return txn

    def finish_client_transaction(self, call_id, cseq):
        with self.outbound_lock:
            self.client_transactions.pop(self.client_transaction_key(call_id, cseq), None)

    def queue_client_response(self, call_id, cseq, response):
        with self.outbound_lock:
            txn = self.client_transactions.get(self.client_transaction_key(call_id, cseq))
        if txn is not None:
            txn.put(response)
            return True
        return False

    def wait_for_final_response(self, txn, timeout):
        deadline = time.time() + max(0.1, float(timeout))
        responses = []
        while time.time() < deadline and not self.stop_event.is_set():
            remaining = max(0.05, deadline - time.time())
            try:
                response = txn.get(timeout=remaining)
            except queue.Empty:
                continue
            responses.append(response)
            status_code = safe_int(response.get("status_code"), 0)
            if status_code >= 200:
                return response, responses
        return None, responses

    def update_outbound_trunk_row(self, trunk_id, status, connected_server="", connected_transport=""):
        try:
            existing = self.table_columns("sip-trunks")
            assignments = []
            params = []
            if "status" in existing:
                assignments.append("`status`=%s")
                params.append(str(status or "").strip() or "Offline")
            if "connected_server" in existing:
                assignments.append("`connected_server`=%s")
                params.append(str(connected_server or "").strip())
            if "connected_transport" in existing:
                assignments.append("`connected_transport`=%s")
                params.append(str(connected_transport or "").strip())
            if not assignments:
                return
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE `sip-trunks` SET {', '.join(assignments)} WHERE `id`=%s",
                        tuple(params + [trunk_id]),
                    )
            finally:
                conn.close()
        except Exception:
            pass

    def mark_all_outbound_trunks_status(self, status):
        try:
            existing = self.table_columns("sip-trunks")
            if "status" not in existing:
                return
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE `sip-trunks` SET `status`=%s "
                        "WHERE COALESCE(`servers_json`,'')<>'' OR `auth`='OUTBOUND' OR `trunk_type`='OUTBOUND_AUTH'",
                        (str(status or "").strip() or "Offline",),
                    )
            finally:
                conn.close()
        except Exception:
            pass

    def resolve_host_ips(self, host):
        wanted = str(host or "").strip()
        if not wanted:
            return set()
        results = set()
        try:
            for item in socket.getaddrinfo(wanted, None, socket.AF_INET, socket.SOCK_STREAM):
                if item and len(item) >= 5 and item[4]:
                    results.add(str(item[4][0]))
        except Exception:
            pass
        return results

    def close_outbound_state_connection(self, state):
        conn = state.get("conn")
        state["conn"] = None
        if conn is not None:
            self.tcp_buffers.pop(id(conn), None)
            try:
                conn.close()
            except Exception:
                pass

    def outbound_connection_reader(self, trunk_id, conn, remote_host, remote_port):
        try:
            while not self.stop_event.is_set():
                raw = self.tcp_message(conn)
                if raw is None:
                    continue
                if not raw:
                    break
                try:
                    peer_addr = (remote_host, remote_port)
                    try:
                        peer_host, peer_port = conn.getpeername()[:2]
                        peer_addr = (peer_host, peer_port)
                    except Exception:
                        pass
                    response = self.handle_packet(raw, peer_addr, None, True, conn=conn, client_trunk_id=trunk_id)
                    if response is not None:
                        conn.sendall(response)
                except Exception:
                    traceback.print_exc()
        finally:
            with self.outbound_lock:
                state = self.outbound_trunks.get(str(trunk_id))
                if state and state.get("conn") is conn:
                    state["conn"] = None
                    state["registered_until"] = 0.0
                    state["next_register_at"] = 0.0
                    state["active_route"] = None
                    state["trusted_ips"] = set()
                    state["route_aliases"] = set()
            self.tcp_buffers.pop(id(conn), None)
            try:
                conn.close()
            except Exception:
                pass

    def open_outbound_state_connection(self, state, route):
        transport = str(route.get("transport") or "udp").lower()
        host = str(route.get("host") or "").strip()
        port = safe_int(route.get("port"), 5061 if transport == "tls" else 5060)
        if transport not in {"tcp", "tls"}:
            return None
        raw_sock = socket.create_connection((host, port), timeout=4)
        raw_sock.settimeout(2.0)
        raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if transport == "tls":
            context = ssl.create_default_context()
            conn = context.wrap_socket(raw_sock, server_hostname=host)
            conn.settimeout(2.0)
        else:
            conn = raw_sock
        state["conn"] = conn
        threading.Thread(
            target=self.outbound_connection_reader,
            args=(state["id"], conn, host, port),
            daemon=True,
        ).start()
        return conn

    def send_trunk_payload(self, state, route, payload):
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        transport = str(route.get("transport") or "udp").lower()
        host = str(route.get("host") or "").strip()
        port = safe_int(route.get("port"), 5061 if transport == "tls" else 5060)
        if transport in {"tcp", "tls"}:
            conn = state.get("conn")
            if conn is None:
                conn = self.open_outbound_state_connection(state, route)
            with state["send_lock"]:
                conn.sendall(data)
            return
        if self.udp_sock is None:
            tmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                tmp.sendto(data, (host, port))
            finally:
                tmp.close()
            return
        self.udp_sock.sendto(data, (host, port))

    def route_header_uri(self, route):
        host = str(route.get("host") or "").strip()
        if not host:
            return ""
        transport = str(route.get("transport") or "udp").strip().lower()
        scheme = "sips" if transport == "tls" else "sip"
        default_port = 5061 if transport == "tls" else 5060
        port = safe_int(route.get("port"), default_port)
        transport_param = ""
        if transport in {"udp", "tcp"}:
            transport_param = f";transport={transport}"
        if transport == "tls":
            transport_param = ";transport=tls"
        return f"<{scheme}:{host}:{port};lr{transport_param}>"

    def initial_route_headers(self, route):
        if not route or not truthy_setting(route.get("is_outbound_proxy"), False):
            return []
        header_uri = self.route_header_uri(route)
        return [header_uri] if header_uri else []

    def build_register_request(self, trunk, route, cseq, expires, auth_header=None, state=None):
        username = str(trunk.get("username") or "").strip()
        domain = self.route_domain(route) or str(trunk.get("ipaddr") or "").strip()
        nat_mode = clean_nat_mode(trunk.get("outbound_nat"), "auto")
        local_ip, local_port = self.local_signaling_address(route, state=state, nat_mode=nat_mode)
        transport = str(route.get("transport") or "udp").lower()
        branch = uuid.uuid4().hex[:10]
        call_id = str(trunk.get("register_call_id") or uuid.uuid4().hex)
        trunk["register_call_id"] = call_id
        lines = [
            f"REGISTER sip:{domain} SIP/2.0",
            f"Via: SIP/2.0/{transport.upper()} {local_ip}:{local_port};branch=z9hG4bK{branch}{self.sip_via_params(transport)}",
            f"From: <sip:{username}@{domain}>;tag={trunk.get('register_tag') or uuid.uuid4().hex[:10]}",
            f"To: <sip:{username}@{domain}>",
            f"Call-ID: {call_id}",
            f"CSeq: {int(cseq)} REGISTER",
            "Max-Forwards: 70",
            f"Contact: <sip:{username}@{local_ip}:{local_port};{self.sip_contact_transport_param(transport)}>",
            f"Expires: {int(expires)}",
            "User-Agent: OpenPagingServer",
        ]
        for route_header in self.initial_route_headers(route):
            lines.append(f"Route: {route_header}")
        if auth_header:
            lines.append(auth_header)
        lines.append("Content-Length: 0")
        return "\r\n".join(lines) + "\r\n\r\n", call_id, f"{int(cseq)} REGISTER"

    def build_digest_header(self, challenge_header, username, password, method, uri, proxy=False):
        scheme, attrs = parse_authenticate_header(challenge_header)
        if scheme.lower() != "digest":
            return None
        realm = attrs.get("realm") or ""
        nonce = attrs.get("nonce") or ""
        qop_options = attrs.get("qop") or ""
        qop = ""
        if qop_options:
            options = [item.strip() for item in qop_options.split(",") if item.strip()]
            qop = "auth" if "auth" in options else options[0]
        nc = "00000001"
        cnonce = uuid.uuid4().hex[:16]
        response = digest_response(username, password, realm, nonce, method, uri, qop=qop or None, nc=nc if qop else None, cnonce=cnonce if qop else None)
        parts = [
            f'username="{username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
            f'response="{response}"',
            "algorithm=MD5",
        ]
        if attrs.get("opaque"):
            parts.append(f'opaque="{attrs.get("opaque")}"')
        if qop:
            parts.extend([f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"'])
        header_name = "Proxy-Authorization" if proxy else "Authorization"
        return f"{header_name}: Digest " + ", ".join(parts)

    def outbound_status_text(self, state, detail=""):
        prefix = str(state or "").strip() or "Offline"
        detail = str(detail or "").strip()
        return f"{prefix} ({detail})" if detail else prefix

    def outbound_response_detail(self, response, default="Registration failed"):
        status_text = str((response or {}).get("status_text") or "").strip()
        if status_text:
            parts = status_text.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                status_text = parts[1].strip()
            if status_text:
                return status_text
        return str(default or "Registration failed").strip()

    def outbound_exception_status(self, exc):
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return self.outbound_status_text("Offline", "Connection timeout")
        detail = str(exc or "").strip()
        lowered = detail.lower()
        if isinstance(exc, ConnectionRefusedError) or "connection refused" in lowered or "actively refused" in lowered or "10061" in lowered:
            return self.outbound_status_text("Offline", "Connection refused")
        if "timed out" in lowered or "timeout" in lowered:
            return self.outbound_status_text("Offline", "Connection timeout")
        if "service unavailable" in lowered:
            return self.outbound_status_text("Error", "Service Unavailable")
        if detail:
            return self.outbound_status_text("Offline", detail)
        return self.outbound_status_text("Offline")

    def response_server_agent(self, response):
        headers = (response or {}).get("headers") or {}
        if isinstance(headers, dict):
            agent = str(headers.get("server") or headers.get("user-agent") or "").strip()
        else:
            agent = self.get_first(headers, "Server") or self.get_first(headers, "User-Agent") or ""
        agent = " ".join(str(agent or "").split()).strip().strip("'\"")
        return agent if agent and agent.lower() != "unknown" else ""

    def outbound_connected_server_label(self, index, route, response=None):
        label = f"Server {index}: {route.get('host')}:{route.get('port')}"
        agent = self.response_server_agent(response)
        if agent:
            label = f'{label} "{agent}"'
        return label[:255]

    def perform_register_attempt(self, state, route, expires):
        trunk = state["trunk"]
        state["register_cseq"] = safe_int(state.get("register_cseq"), 0) + 1
        request, call_id, cseq = self.build_register_request(trunk, route, state["register_cseq"], expires, state=state)
        txn = self.start_client_transaction(call_id, cseq)
        try:
            self.send_trunk_payload(state, route, request)
            response, _responses = self.wait_for_final_response(txn, 8.0)
        finally:
            self.finish_client_transaction(call_id, cseq)
        if response is None:
            return None, self.outbound_status_text("Offline", "Connection timeout"), "network"
        self.learn_signaling_mapping_from_response(response, state=state)
        status_code = safe_int(response.get("status_code"), 0)
        if status_code in {401, 407}:
            header_name = "Proxy-Authenticate" if status_code == 407 else "WWW-Authenticate"
            challenge = response.get("headers", {}).get(header_name.lower()) or ""
            auth_header = self.build_digest_header(
                challenge,
                str(trunk.get("username") or "").strip(),
                str(trunk.get("password") or "").strip(),
                "REGISTER",
                f"sip:{route.get('host')}",
                proxy=status_code == 407,
            )
            if not auth_header:
                return None, self.outbound_status_text("Error", "Unauthorized"), "auth"
            state["register_cseq"] += 1
            request, call_id, cseq = self.build_register_request(trunk, route, state["register_cseq"], expires, auth_header=auth_header, state=state)
            txn = self.start_client_transaction(call_id, cseq)
            try:
                self.send_trunk_payload(state, route, request)
                response, _responses = self.wait_for_final_response(txn, 8.0)
            finally:
                self.finish_client_transaction(call_id, cseq)
            if response is None:
                return None, self.outbound_status_text("Offline", "Connection timeout"), "network"
            self.learn_signaling_mapping_from_response(response, state=state)
            status_code = safe_int(response.get("status_code"), 0)
        if status_code == 200:
            return response, "", ""
        if status_code in {401, 403, 407}:
            return None, self.outbound_status_text("Error", "Unauthorized"), "auth"
        if status_code == 404:
            return None, self.outbound_status_text("Error", "Account not found"), "account"
        if status_code == 503:
            return None, self.outbound_status_text("Error", "Service Unavailable"), "service"
        return None, self.outbound_status_text("Error", self.outbound_response_detail(response, f"Registration failed ({status_code or 'no response'})")), "other"

    def deregister_outbound_state(self, state, deleting=False):
        route = state.get("active_route")
        if route:
            try:
                self.perform_register_attempt(state, route, 0)
            except Exception:
                pass
        self.close_outbound_state_connection(state)
        if not deleting:
            self.update_outbound_trunk_row(state["id"], "Offline", "", "")

    def maintain_outbound_trunks(self):
        rows = self.fetch_outbound_trunks()
        wanted = {str(row.get("id")): row for row in rows}
        removed_states = []
        reset_states = []
        with self.outbound_lock:
            for trunk_id in list(self.outbound_trunks.keys()):
                if trunk_id not in wanted:
                    removed_states.append(self.outbound_trunks.pop(trunk_id))
            for trunk_id, row in wanted.items():
                state = self.outbound_trunks.get(trunk_id)
                config_sig = self.outbound_trunk_config_signature(row)
                if state is None:
                    state = {
                        "id": row.get("id"),
                        "trunk": row,
                        "config_sig": config_sig,
                        "register_cseq": 0,
                        "registered_until": 0.0,
                        "next_register_at": 0.0,
                        "auth_failure_count": 0,
                        "active_route": None,
                        "trusted_ips": set(),
                        "route_aliases": set(),
                        "conn": None,
                        "send_lock": threading.Lock(),
                        "public_signaling_ip": "",
                        "public_signaling_port": 0,
                    }
                    self.outbound_trunks[trunk_id] = state
                else:
                    state["trunk"] = row
                    if state.get("config_sig") != config_sig:
                        state["pending_config_sig"] = config_sig
                        reset_states.append(state)
        for state in removed_states:
            self.deregister_outbound_state(state, deleting=True)
        for state in reset_states:
            self.deregister_outbound_state(state, deleting=False)
            with self.outbound_lock:
                current = self.outbound_trunks.get(str(state.get("id")))
                if current is state:
                    state["config_sig"] = state.pop("pending_config_sig", state.get("config_sig"))
                    state["registered_until"] = 0.0
                    state["next_register_at"] = 0.0
                    state["auth_failure_count"] = 0
                    state["active_route"] = None
                    state["trusted_ips"] = set()
                    state["route_aliases"] = set()
        now = time.time()
        for state in list(self.outbound_trunks.values()):
            if state.get("next_register_at", 0.0) > now:
                continue
            if not (state.get("trunk") or {}).get("servers"):
                state["registered_until"] = 0.0
                state["next_register_at"] = time.time() + OUTBOUND_TRUNK_REGISTER_INTERVAL
                state["active_route"] = None
                state["trusted_ips"] = set()
                state["route_aliases"] = set()
                self.update_outbound_trunk_row(state["id"], self.outbound_status_text("Error", "No SIP server configured"), "", "")
                continue
            error_text = self.outbound_status_text("Offline")
            auth_failed = False
            connected = False
            for index, server_row in enumerate(state["trunk"].get("servers") or [], start=1):
                for route in self.resolve_trunk_server_routes(server_row):
                    try:
                        if route.get("transport") in {"tcp", "tls"} and state.get("conn") is None:
                            self.open_outbound_state_connection(state, route)
                        response, error_text, failure_type = self.perform_register_attempt(state, route, server_row.get("expires") or 300)
                        if response is None:
                            if failure_type == "auth":
                                auth_failed = True
                            self.close_outbound_state_connection(state)
                            continue
                        state["registered_until"] = time.time() + max(60, safe_int(server_row.get("expires"), 300))
                        state["next_register_at"] = time.time() + OUTBOUND_TRUNK_REGISTER_INTERVAL
                        state["auth_failure_count"] = 0
                        state["active_route"] = dict(route)
                        state["server_index"] = index
                        alias_values = {
                            str(route.get("host") or "").strip(),
                            str(route.get("domain") or "").strip(),
                            str(server_row.get("server") or "").strip(),
                            str(server_row.get("outbound_proxy") or "").strip(),
                        }
                        route_ips = self.resolve_host_ips(route.get("host"))
                        domain_ips = self.resolve_host_ips(server_row.get("server"))
                        proxy_ips = self.resolve_host_ips(server_row.get("outbound_proxy"))
                        state["trusted_ips"] = set(route_ips) | set(domain_ips) | set(proxy_ips)
                        state["route_aliases"] = {value for value in alias_values if value} | state["trusted_ips"]
                        self.remember_outbound_state_targets(
                            state,
                            response.get("source_ip"),
                            [response.get("headers", {}).get("contact", "")] + list(response.get("record_routes") or []),
                        )
                        self.update_outbound_trunk_row(
                            state["id"],
                            "Online",
                            self.outbound_connected_server_label(index, route, response=response),
                            str(route.get("transport") or "udp").upper(),
                        )
                        connected = True
                        break
                    except Exception as exc:
                        self.close_outbound_state_connection(state)
                        error_text = self.outbound_exception_status(exc)
                        continue
                if connected:
                    break
            if not connected:
                state["registered_until"] = 0.0
                state["active_route"] = None
                state["trusted_ips"] = set()
                state["route_aliases"] = set()
                backoff_failures = safe_int(state.get("auth_failure_count"), 0)
                if auth_failed or backoff_failures > 0:
                    state["auth_failure_count"] = min(backoff_failures + 1, OUTBOUND_TRUNK_AUTH_RETRY_MAX // OUTBOUND_TRUNK_AUTH_RETRY_STEP)
                    state["next_register_at"] = time.time() + min(
                        OUTBOUND_TRUNK_AUTH_RETRY_MAX,
                        state["auth_failure_count"] * OUTBOUND_TRUNK_AUTH_RETRY_STEP,
                    )
                else:
                    state["auth_failure_count"] = 0
                    state["next_register_at"] = time.time() + OUTBOUND_TRUNK_REGISTER_INTERVAL
                self.update_outbound_trunk_row(state["id"], error_text or self.outbound_status_text("Offline"), "", "")

    def outbound_source_is_trusted(self, ipaddr):
        wanted = str(ipaddr or "").strip()
        if not wanted:
            return False
        with self.outbound_lock:
            states = list(self.outbound_trunks.values())
        for state in states:
            if wanted in (state.get("trusted_ips") or set()):
                return True
        return False

    def shutdown_outbound_trunks(self):
        with self.outbound_lock:
            states = list(self.outbound_trunks.values())
            self.outbound_trunks.clear()
        for state in states:
            try:
                self.deregister_outbound_state(state, deleting=False)
            except Exception:
                pass

    def target_uri_for_number(self, number, route):
        raw = str(number or "").strip()
        if raw.lower().startswith("sip:"):
            return raw
        host = self.route_domain(route)
        if "@" in raw:
            return f"sip:{raw}"
        return f"sip:{raw}@{host}" if host else f"sip:{raw}"

    def caller_display_name(self, value):
        text = str(value or "").strip().replace('"', "")
        return text[:100]

    def build_outbound_invite(self, call, trunk, route, auth_header=None, extra_headers=None):
        number = str(call.number or "").strip()
        route_transport = str(route.get("transport") or "udp").lower()
        route_host = self.route_domain(route)
        local_ip = call.local_sip_ip
        target_uri = self.target_uri_for_number(number, route)
        auth_username = str(trunk.get("username") or "").strip()
        from_user = str(trunk.get("caller_id_number") or trunk.get("callerid_number") or auth_username or "openpagingserver").strip()
        display_name = self.caller_display_name(trunk.get("caller_id_name") or trunk.get("callerid_name"))
        from_uri = f"sip:{from_user}@{route_host}" if route_host else f"sip:{from_user}"
        from_h = f'"{display_name}" <{from_uri}>' if display_name else f"<{from_uri}>"
        to_h = f"<{target_uri}>"
        contact_user = auth_username or "openpagingserver"
        contact = f"<sip:{contact_user}@{local_ip}:{call.local_sip_port};{self.sip_contact_transport_param(route_transport)}>"
        call.request_uri = target_uri
        call.from_h = from_h + f";tag={call.local_tag}"
        call.to_h = to_h
        call.invite_branch = uuid.uuid4().hex[:10]
        call.invite_route_headers = self.initial_route_headers(route)
        sdp = self.build_invite_sdp(call.advertised_media_ip, call.advertised_media_port)
        lines = [
            f"INVITE {target_uri} SIP/2.0",
            f"Via: SIP/2.0/{route_transport.upper()} {local_ip}:{call.local_sip_port};branch=z9hG4bK{call.invite_branch}{self.sip_via_params(route_transport)}",
            "Max-Forwards: 70",
            f"From: {call.from_h}",
            f"To: {call.to_h}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} INVITE",
            f"Contact: {contact}",
            "User-Agent: OpenPagingServer",
            "Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REGISTER, INFO, PRACK, UPDATE",
            "Supported: 100rel, timer",
            "Session-Expires: 1800",
            "Min-SE: 90",
        ]
        for route_header in call.invite_route_headers:
            lines.append(f"Route: {route_header}")
        for header in extra_headers or []:
            if ":" in header and "\n" not in header and "\r" not in header:
                lines.append(header)
        if auth_header:
            lines.append(auth_header)
        lines.extend(
            [
                "Content-Type: application/sdp",
                f"Content-Length: {len(sdp.encode('utf-8'))}",
                "",
                sdp,
            ]
        )
        return "\r\n".join(lines)

    def outbound_call_finished(self, call):
        if call is None or bool(getattr(call, "released", False)):
            return True
        for attr in ("finished_event", "disconnected_event"):
            event = getattr(call, attr, None)
            if event is not None:
                try:
                    if event.is_set():
                        return True
                except Exception:
                    pass
        return False

    def send_outbound_rtp_frame(self, call, payload=None):
        if self.outbound_call_finished(call):
            return False
        self.learn_outbound_media_source(call)
        if not getattr(call, "remote_media_ip", "") or not safe_int(getattr(call, "remote_media_port", 0), 0):
            return False
        frame = bytes(payload or (b"\xff" * 160))
        packet = struct.pack(
            "!BBHII",
            0x80,
            0x00,
            int(call.rtp_sequence) & 0xFFFF,
            int(call.rtp_timestamp) & 0xFFFFFFFF,
            int(call.rtp_ssrc) & 0xFFFFFFFF,
        ) + frame
        try:
            call.rtp_socket.sendto(packet, (call.remote_media_ip, int(call.remote_media_port)))
        except OSError as exc:
            sip_debug(
                f"outbound rtp send failed call={getattr(call, 'call_id', '')} "
                f"local={sip_sockname(getattr(call, 'rtp_socket', None))} "
                f"remote={call.remote_media_ip}:{int(call.remote_media_port)} error={exc}"
            )
            return False
        call.rtp_packets_sent = int(getattr(call, "rtp_packets_sent", 0) or 0) + 1
        if call.rtp_packets_sent <= 3 or call.rtp_packets_sent % 50 == 0:
            sip_debug(
                f"outbound rtp sent call={getattr(call, 'call_id', '')} "
                f"packet={call.rtp_packets_sent} local={sip_sockname(call.rtp_socket)} "
                f"remote={call.remote_media_ip}:{int(call.remote_media_port)} bytes={len(packet)}"
            )
        call.rtp_sequence = (int(call.rtp_sequence) + 1) & 0xFFFF
        call.rtp_timestamp = (int(call.rtp_timestamp) + 160) & 0xFFFFFFFF
        return True

    def apply_outbound_invite_response(self, call, response, state=None):
        headers = (response or {}).get("headers") or {}
        body = (response or {}).get("body") or ""
        self.learn_signaling_mapping_from_response(response, state=state, call=call)
        call.to_h = (response or {}).get("to") or call.to_h
        contact = str(headers.get("contact") or "").strip()
        if contact:
            call.remote_contact = contact
        record_routes = (response or {}).get("record_routes") or []
        if record_routes:
            call.record_routes = record_routes
        source_ip = str((response or {}).get("source_ip") or call.remote_sip_ip or "").strip()
        source_port = safe_int((response or {}).get("source_port"), call.remote_sip_port or 5060)
        if source_ip:
            call.remote_sip_ip = source_ip
        if source_port > 0:
            call.remote_sip_port = source_port
        if body:
            remote_media_ip, remote_media_port, _ = self.parse_sdp_offer(body, call.remote_sip_ip)
            if remote_media_port:
                call.remote_media_ip = remote_media_ip
                call.remote_media_port = remote_media_port
                call.rtp_latching_enabled = True
                sip_debug(
                    f"outbound rtp target call={getattr(call, 'call_id', '')} "
                    f"remote={remote_media_ip}:{remote_media_port} local={sip_sockname(getattr(call, 'rtp_socket', None))}"
                )
        self.remember_outbound_state_targets(
            state,
            source_ip,
            [call.remote_contact] + list(call.record_routes or []),
        )
        return bool(getattr(call, "remote_media_ip", "") and safe_int(getattr(call, "remote_media_port", 0), 0))

    def handle_outbound_invite_progress(self, call, response, state=None):
        progress_ready = self.apply_outbound_invite_response(call, response, state=state)
        self.maybe_send_outbound_prack(call, response, state=state)
        if not progress_ready:
            return False
        if not getattr(call, "media_path_primed", False):
            self.prime_outbound_media_path(call)
            call.media_path_primed = True
        return True

    def allocate_outbound_dialog_cseq(self, call):
        next_value = max(
            safe_int(getattr(call, "next_local_cseq", 0), 0),
            safe_int(getattr(call, "cseq", 0), 0) + 1,
        )
        if next_value <= 0:
            next_value = 1
        call.next_local_cseq = next_value + 1
        return next_value

    def response_requires_prack(self, response):
        status_code = safe_int((response or {}).get("status_code"), 0)
        if status_code < 101 or status_code >= 200:
            return False
        headers = (response or {}).get("headers") or {}
        require_value = str(headers.get("require") or "").lower()
        if "100rel" in require_value:
            return True
        return bool(str(headers.get("rseq") or "").strip())

    def maybe_send_outbound_prack(self, call, response, state=None):
        if not self.response_requires_prack(response):
            return False
        headers = (response or {}).get("headers") or {}
        rseq = str(headers.get("rseq") or "").strip()
        if not rseq or rseq in getattr(call, "pracked_rseqs", set()):
            return False
        route = {"host": call.remote_sip_ip, "port": call.remote_sip_port or 5060, "transport": call.transport or "udp"}
        trunk = self.fetch_trunk_row(call.trunk_id)
        selected_route = None
        selected_state = state
        if trunk:
            selected_route, selected_state = self.choose_outbound_route(trunk)
            route = self.outbound_dialog_route(call, fallback_route=selected_route or route)
        else:
            route = self.outbound_dialog_route(call, fallback_route=route)
        prack = self.build_outbound_prack(call, route, rseq)
        if self.outbound_state_matches_route(selected_state, route):
            self.send_trunk_payload(selected_state, route, prack)
        else:
            self.send_trunk_payload({"id": call.trunk_id, "conn": None, "send_lock": threading.Lock()}, route, prack)
        call.pracked_rseqs.add(rseq)
        return True

    def learn_outbound_media_source(self, call, max_packets=4):
        if not getattr(call, "rtp_latching_enabled", False):
            return False
        sock = getattr(call, "rtp_socket", None)
        if sock is None:
            return False
        learned = False
        for _ in range(max(1, safe_int(max_packets, 4))):
            try:
                ready, _, _ = select.select([sock], [], [], 0)
            except Exception:
                return learned
            if not ready:
                break
            try:
                packet, addr = sock.recvfrom(4096)
            except (BlockingIOError, socket.timeout):
                break
            except OSError:
                break
            if not addr or len(addr) < 2:
                continue
            source_ip = str(addr[0] or "").strip()
            source_port = safe_int(addr[1], 0)
            current_port = safe_int(getattr(call, "remote_media_port", 0), 0)
            if (
                not source_ip
                or source_port <= 0
                or not sip_latchable_rtp_packet(packet)
                or (current_port > 0 and current_port % 2 == 0 and source_port == current_port + 1 and source_port % 2 == 1)
            ):
                continue
            old_ip = str(getattr(call, "remote_media_ip", "") or "")
            old_port = safe_int(getattr(call, "remote_media_port", 0), 0)
            call.remote_media_ip = source_ip
            call.remote_media_port = source_port
            call.rtp_packets_received = int(getattr(call, "rtp_packets_received", 0) or 0) + 1
            if (old_ip, old_port) != (source_ip, source_port) or call.rtp_packets_received <= 3 or call.rtp_packets_received % 50 == 0:
                sip_debug(
                    f"outbound rtp learned call={getattr(call, 'call_id', '')} "
                    f"packet={call.rtp_packets_received} old={old_ip}:{old_port} "
                    f"new={source_ip}:{source_port} local={sip_sockname(sock)} bytes={len(packet)}"
                )
            learned = True
        return learned

    def prime_outbound_media_path(self, call, frame_count=6):
        for _ in range(max(1, int(frame_count or 1))):
            if not self.send_outbound_rtp_frame(call):
                break
            self.learn_outbound_media_source(call)
            time.sleep(0.02)

    def outbound_dialog_request_uri(self, call):
        uri = self.sip_uri_from_header(getattr(call, "remote_contact", ""))
        return uri or getattr(call, "request_uri", "")

    def outbound_dialog_route_headers(self, call):
        return list(reversed(list(getattr(call, "record_routes", []) or [])))

    def sip_uri_has_lr(self, value):
        uri = self.sip_uri_from_header(value)
        return bool(re.search(r"(?:^|;)lr(?:=|;|$)", uri, re.IGNORECASE))

    def outbound_dialog_request_parts(self, call):
        remote_target = self.outbound_dialog_request_uri(call)
        route_set = self.outbound_dialog_route_headers(call)
        if not route_set:
            return remote_target, []
        first_route = route_set[0]
        if self.sip_uri_has_lr(first_route):
            return remote_target, route_set
        strict_request_uri = self.sip_uri_from_header(first_route) or remote_target
        return strict_request_uri, list(route_set[1:]) + [f"<{remote_target}>"]

    def outbound_dialog_route(self, call, fallback_route=None):
        route_set = self.outbound_dialog_route_headers(call)
        target_source = route_set[0] if route_set else getattr(call, "remote_contact", "")
        fallback_route = fallback_route or {}
        fallback_transport = str(
            getattr(call, "transport", "")
            or fallback_route.get("transport")
            or "udp"
        ).lower()
        fallback_ip = str(
            getattr(call, "remote_sip_ip", "")
            or fallback_route.get("host")
            or ""
        ).strip()
        fallback_port = safe_int(
            getattr(call, "remote_sip_port", 0)
            or fallback_route.get("port")
            or 5060,
            5060,
        )
        host, port, transport = self.parse_sip_target(
            target_source,
            fallback_ip=fallback_ip,
            fallback_port=fallback_port,
            fallback_transport=fallback_transport,
        )
        return {
            "host": host or fallback_ip,
            "port": safe_int(port, fallback_port),
            "transport": (transport or fallback_transport).lower(),
        }

    def outbound_invite_route(self, call, fallback_route=None):
        route = dict(getattr(call, "invite_route", {}) or {})
        if route:
            return route
        fallback_route = fallback_route or {}
        return {
            "host": str(
                fallback_route.get("host")
                or getattr(call, "remote_sip_ip", "")
                or ""
            ).strip(),
            "port": safe_int(
                fallback_route.get("port")
                or getattr(call, "remote_sip_port", 0)
                or 5060,
                5060,
            ),
            "transport": str(
                fallback_route.get("transport")
                or getattr(call, "transport", "")
                or "udp"
            ).strip().lower(),
        }

    def outbound_state_aliases(self, state):
        aliases = set()
        for value in state.get("route_aliases") or set():
            text = str(value or "").strip().lower()
            if text:
                aliases.add(text)
        for value in state.get("trusted_ips") or set():
            text = str(value or "").strip().lower()
            if text:
                aliases.add(text)
        return aliases

    def remember_outbound_state_targets(self, state, source_ip="", header_values=None):
        if state is None:
            return
        trusted_ips = set(state.get("trusted_ips") or set())
        route_aliases = set(state.get("route_aliases") or set())
        source_ip = str(source_ip or "").strip()
        if source_ip:
            trusted_ips.add(source_ip)
            route_aliases.add(source_ip)
        for value in header_values or []:
            host, _port, _transport = self.parse_sip_target(value)
            host = str(host or "").strip()
            if not host:
                continue
            route_aliases.add(host)
            resolved = self.resolve_host_ips(host)
            if resolved:
                trusted_ips |= set(resolved)
            elif re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", host):
                trusted_ips.add(host)
        state["trusted_ips"] = trusted_ips
        state["route_aliases"] = route_aliases | trusted_ips

    def outbound_state_matches_route(self, state, route):
        if state is None:
            return False
        active_route = state.get("active_route") or {}
        wanted_transport = str(route.get("transport") or "udp").strip().lower()
        active_transport = str(active_route.get("transport") or "udp").strip().lower()
        if wanted_transport in {"tcp", "tls"} and active_transport == wanted_transport and state.get("conn") is not None:
            return True
        candidate_host = str(route.get("host") or "").strip().lower()
        aliases = self.outbound_state_aliases(state)
        return (
            (
                str(active_route.get("host") or "").strip().lower() == candidate_host
                or (candidate_host and candidate_host in aliases)
            )
            and safe_int(active_route.get("port"), 0) == safe_int(route.get("port"), 0)
            and active_transport == wanted_transport
        )

    def send_outbound_ack(self, call, response=None):
        invite_cseq = safe_int(getattr(call, "cseq", 0), 0)
        if invite_cseq in getattr(call, "acked_invite_cseqs", set()):
            return
        status_code = safe_int((response or {}).get("status_code"), 0)
        use_dialog_route = 200 <= status_code < 300
        route = {"host": call.remote_sip_ip, "port": call.remote_sip_port or 5060, "transport": call.transport or "udp"}
        state = None
        trunk = self.fetch_trunk_row(call.trunk_id)
        if trunk:
            selected_route, state = self.choose_outbound_route(trunk)
            if use_dialog_route:
                route = self.outbound_dialog_route(call, fallback_route=selected_route or route)
            else:
                route = self.outbound_invite_route(call, fallback_route=selected_route or route)
        ack = self.build_outbound_ack(call, route, dialog=use_dialog_route)
        if self.outbound_state_matches_route(state, route):
            self.send_trunk_payload(state, route, ack)
        else:
            self.send_trunk_payload({"id": call.trunk_id, "conn": None, "send_lock": threading.Lock()}, route, ack)
        call.acked_invite_cseqs.add(invite_cseq)

    def build_outbound_ack(self, call, route, dialog=True):
        transport = str(route.get("transport") or "udp").lower()
        local_ip = call.local_sip_ip
        if dialog:
            request_uri, route_headers = self.outbound_dialog_request_parts(call)
            branch = f"z9hG4bK{uuid.uuid4().hex[:10]}"
        else:
            request_uri = getattr(call, "request_uri", "") or self.target_uri_for_number(call.number, route)
            route_headers = list(getattr(call, "invite_route_headers", []) or [])
            branch = f"z9hG4bK{getattr(call, 'invite_branch', uuid.uuid4().hex[:10])}"
        lines = [
            f"ACK {request_uri} SIP/2.0",
            f"Via: SIP/2.0/{transport.upper()} {local_ip}:{call.local_sip_port};branch={branch}{self.sip_via_params(transport)}",
            f"From: {call.from_h}",
            f"To: {call.to_h}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} ACK",
            "Max-Forwards: 70",
            "Content-Length: 0",
        ]
        for rr in route_headers:
            lines.append(f"Route: {rr}")
        return "\r\n".join(lines) + "\r\n\r\n"

    def build_outbound_cancel(self, call, route):
        transport = str(route.get("transport") or "udp").lower()
        local_ip = call.local_sip_ip
        lines = [
            f"CANCEL {call.request_uri} SIP/2.0",
            f"Via: SIP/2.0/{transport.upper()} {local_ip}:{call.local_sip_port};branch=z9hG4bK{call.invite_branch}{self.sip_via_params(transport)}",
            f"From: {call.from_h}",
            f"To: {call.to_h}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} CANCEL",
            "Max-Forwards: 70",
            "Content-Length: 0",
        ]
        for route_header in list(getattr(call, "invite_route_headers", []) or []):
            lines.insert(-1, f"Route: {route_header}")
        return "\r\n".join(lines) + "\r\n\r\n"

    def build_outbound_prack(self, call, route, rseq):
        transport = str(route.get("transport") or call.transport or "udp").lower()
        local_ip = call.local_sip_ip
        request_uri, route_headers = self.outbound_dialog_request_parts(call)
        cseq = self.allocate_outbound_dialog_cseq(call)
        lines = [
            f"PRACK {request_uri} SIP/2.0",
            f"Via: SIP/2.0/{transport.upper()} {local_ip}:{call.local_sip_port};branch=z9hG4bK{uuid.uuid4().hex[:10]}{self.sip_via_params(transport)}",
            f"From: {call.from_h}",
            f"To: {call.to_h}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {cseq} PRACK",
            "Max-Forwards: 70",
            f"RAck: {rseq} {call.cseq} INVITE",
            f"Contact: <sip:openpagingserver@{local_ip}:{call.local_sip_port};{self.sip_contact_transport_param(transport)}>",
            "User-Agent: OpenPagingServer",
            "Content-Length: 0",
        ]
        for rr in route_headers:
            lines.append(f"Route: {rr}")
        return "\r\n".join(lines) + "\r\n\r\n"

    def build_outbound_bye(self, call, route):
        transport = str(route.get("transport") or call.transport or "udp").lower()
        local_ip = call.local_sip_ip
        cseq = getattr(call, "bye_cseq", None)
        if cseq is None:
            cseq = self.allocate_outbound_dialog_cseq(call)
            call.bye_cseq = cseq
        request_uri, route_headers = self.outbound_dialog_request_parts(call)
        lines = [
            f"BYE {request_uri} SIP/2.0",
            f"Via: SIP/2.0/{transport.upper()} {local_ip}:{call.local_sip_port};branch=z9hG4bK{uuid.uuid4().hex[:10]}{self.sip_via_params(transport)}",
            f"From: {call.from_h}",
            f"To: {call.to_h}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {cseq} BYE",
            "Max-Forwards: 70",
            f"Contact: <sip:openpagingserver@{local_ip}:{call.local_sip_port};{self.sip_contact_transport_param(transport)}>",
        ]
        for rr in route_headers:
            lines.append(f"Route: {rr}")
        reason_text = str(getattr(call, "hangup_reason", "") or "").replace("\\", "\\\\").replace('"', "'").replace("\r", " ").replace("\n", " ").strip()
        if reason_text:
            lines.append(f'Reason: SIP;cause=200;text="{reason_text[:120]}"')
        lines.append("Content-Length: 0")
        return "\r\n".join(lines) + "\r\n\r\n"

    def response_reason_text(self, status_code):
        status_code = safe_int(status_code, 0)
        mapping = {
            401: "Authentication failed",
            403: "Authentication failed",
            404: "Number not found",
            408: "Request timed out",
            480: "Temporarily unavailable",
            486: "Busy",
            487: "Cancelled",
            488: "Not acceptable here",
            500: "Server error",
            503: "Service unavailable",
        }
        return mapping.get(status_code, f"Call failed ({status_code})" if status_code else "Call failed")

    def wait_for_outbound_invite_final_response(self, txn, call, state, timeout):
        deadline = time.time() + max(0.1, float(timeout))
        responses = []
        while time.time() < deadline and not self.stop_event.is_set():
            remaining = max(0.05, deadline - time.time())
            try:
                response = txn.get(timeout=remaining)
            except queue.Empty:
                continue
            responses.append(response)
            status_code = safe_int(response.get("status_code"), 0)
            if 100 <= status_code < 200:
                call.invite_provisional = True
                try:
                    self.handle_outbound_invite_progress(call, response, state=state)
                except Exception:
                    traceback.print_exc()
                continue
            return response, responses
        return None, responses

    def choose_outbound_route(self, trunk):
        auth_type = str(trunk.get("auth") or "").upper()
        trunk_type = str(trunk.get("trunk_type") or "").upper()
        if not self.is_outbound_trunk_row(trunk) and (auth_type == "IP" or trunk_type == "IP"):
            host = str(trunk.get("ipaddr") or "").strip()
            if not host:
                return None, None
            return {"host": host, "port": 5060, "transport": "udp"}, None
        with self.outbound_lock:
            state = self.outbound_trunks.get(str(trunk.get("id")))
        if state and state.get("active_route"):
            return dict(state["active_route"]), state
        for server_row in trunk.get("servers") or []:
            routes = self.resolve_trunk_server_routes(server_row)
            if routes:
                return dict(routes[0]), state
        return None, state

    def place_outbound_call(self, trunk_id, number, caller_id_number="", caller_id_name="", alert_info_value="", custom_headers=None, answer_timeout=45):
        trunk = self.fetch_trunk_row(trunk_id)
        if not trunk:
            raise RuntimeError("SIP trunk not found")
        route, state = self.choose_outbound_route(trunk)
        if not route:
            raise RuntimeError("No reachable SIP server is configured for this trunk")
        local_media_socket = self.bind_rtp_socket()
        local_media_ip = self.local_ip_for(route.get("host"))
        nat_mode = clean_nat_mode(trunk.get("outbound_nat"), "auto")
        if state is not None and route.get("transport") in {"tcp", "tls"} and state.get("conn") is None:
            self.open_outbound_state_connection(state, route)
        local_sip_ip, local_sip_port = self.local_signaling_address(route, state=state, nat_mode=nat_mode)
        advertised_media_ip, advertised_media_port = self.outbound_media_address_for_route(
            local_media_socket,
            route,
            local_media_ip,
            local_sip_ip,
            nat_mode=nat_mode,
        )
        call_id = uuid.uuid4().hex
        call = OutboundSipCall(
            self,
            call_id,
            trunk.get("id"),
            number,
            str(route.get("transport") or "udp").lower(),
            local_media_socket,
            local_media_ip,
            advertised_media_ip,
            advertised_media_port,
            local_sip_ip,
            local_sip_port,
        )
        call.invite_route = dict(route)
        sip_debug(
            f"outbound rtp offer call={call.call_id} local={sip_sockname(local_media_socket)} "
            f"advertised={advertised_media_ip}:{advertised_media_port} route={route.get('host')}:{route.get('port')}"
        )
        if caller_id_number:
            trunk["caller_id_number"] = caller_id_number
        if caller_id_name:
            trunk["caller_id_name"] = caller_id_name
        extra_headers = []
        alert_value = str(alert_info_value or "").strip()
        if alert_value:
            extra_headers.append(f"Alert-Info: {alert_value}")
        for item in custom_headers or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if not name or ":" in name or "\r" in name or "\n" in name or "\r" in value or "\n" in value:
                continue
            extra_headers.append(f"{name}: {value}")
        timeout_value = max(0, safe_int(answer_timeout, 45))
        with self.outbound_lock:
            self.outbound_calls[call.call_id] = call
        try:
            challenge_header = None
            for attempt in range(2):
                auth_header = None
                if challenge_header:
                    proxy = challenge_header[0] == 407
                    auth_header = self.build_digest_header(
                        challenge_header[1],
                        str(trunk.get("username") or "").strip(),
                        str(trunk.get("password") or "").strip(),
                        "INVITE",
                        self.target_uri_for_number(number, route),
                        proxy=proxy,
                    )
                request = self.build_outbound_invite(call, trunk, route, auth_header=auth_header, extra_headers=extra_headers)
                cseq = f"{call.cseq} INVITE"
                txn = self.start_client_transaction(call.call_id, cseq)
                try:
                    if state is not None:
                        self.send_trunk_payload(state, route, request)
                    else:
                        self.send_trunk_payload({"id": trunk.get("id"), "conn": None, "send_lock": threading.Lock()}, route, request)
                    response, _responses = self.wait_for_outbound_invite_final_response(
                        txn,
                        call,
                        state,
                        timeout_value if timeout_value > 0 else 3600,
                    )
                finally:
                    self.finish_client_transaction(call.call_id, cseq)
                if response is None:
                    if timeout_value != 0 and call.invite_provisional:
                        cancel = self.build_outbound_cancel(call, route)
                        if state is not None:
                            self.send_trunk_payload(state, route, cancel)
                        else:
                            self.send_trunk_payload({"id": trunk.get("id"), "conn": None, "send_lock": threading.Lock()}, route, cancel)
                    return self.fail_outbound_call(call, "Answer timed out")
                status_code = safe_int(response.get("status_code"), 0)
                self.apply_outbound_invite_response(call, response, state=state)
                if status_code >= 200:
                    self.send_outbound_ack(call, response=response)
                if status_code in {401, 407} and attempt == 0:
                    header_name = "Proxy-Authenticate" if status_code == 407 else "WWW-Authenticate"
                    challenge_value = response.get("headers", {}).get(header_name.lower()) or ""
                    if not challenge_value:
                        return self.fail_outbound_call(call, "Authentication failed")
                    challenge_header = (status_code, challenge_value)
                    call.cseq += 1
                    continue
                if status_code != 200:
                    return self.fail_outbound_call(call, self.response_reason_text(status_code))
                if not call.remote_media_port:
                    return self.fail_outbound_call(call, "Remote endpoint did not offer audio")
                if not call.media_path_primed:
                    self.prime_outbound_media_path(call)
                    call.media_path_primed = True
                call.mark_answered()
                return call
            return self.fail_outbound_call(call, "Authentication failed")
        except Exception:
            return self.fail_outbound_call(call, "Call failed")

    def finish_outbound_call(self, call_id):
        with self.outbound_lock:
            call = self.outbound_calls.pop(str(call_id), None)
        if call is None:
            return
        call.released = True
        call.mark_disconnected()
        route = {"host": call.remote_sip_ip, "port": call.remote_sip_port or 5060, "transport": call.transport or "udp"}
        try:
            trunk = self.fetch_trunk_row(call.trunk_id)
            if trunk:
                selected_route, state = self.choose_outbound_route(trunk)
                if call.answered:
                    route = self.outbound_dialog_route(call, fallback_route=selected_route or route)
                    bye = self.build_outbound_bye(call, route)
                    if self.outbound_state_matches_route(state, route):
                        self.send_trunk_payload(state, route, bye)
                    else:
                        self.send_trunk_payload({"id": trunk.get("id"), "conn": None, "send_lock": threading.Lock()}, route, bye)
                elif call.invite_provisional:
                    route = self.outbound_invite_route(call, fallback_route=selected_route or route)
                    cancel = self.build_outbound_cancel(call, route)
                    if self.outbound_state_matches_route(state, route):
                        self.send_trunk_payload(state, route, cancel)
                    else:
                        self.send_trunk_payload({"id": trunk.get("id"), "conn": None, "send_lock": threading.Lock()}, route, cancel)
        except Exception:
            pass
        try:
            call.rtp_socket.close()
        except Exception:
            pass

    def fail_outbound_call(self, call, reason):
        with self.outbound_lock:
            self.outbound_calls.pop(str(call.call_id), None)
        call.released = True
        call.mark_failed(reason)
        try:
            call.rtp_socket.close()
        except Exception:
            pass
        return call

    def build_bye(self, call):
        from_h = call["to_h"]
        to_h = call["from_h"]
        call_id = call["call_id"]
        uri = self.sip_uri_from_header(call.get("contact"))
        if not uri:
            uri = f"sip:{call['remote_sip_ip']}:{call['remote_sip_port']}"
        target_host, target_port, target_transport = self.bye_network_target(call)
        call["bye_target_ip"] = target_host
        call["bye_target_port"] = target_port
        call["bye_target_transport"] = target_transport
        local_sip_ip = call.get("local_sip_ip") or call["local_ip"]
        local_port = self.active_port or call.get("local_sip_port") or 5060
        local_cseq = int(call.get("local_cseq", 2) or 2)
            
        lines = [
            f"BYE {uri} SIP/2.0",
            f"Via: SIP/2.0/{target_transport.upper()} {local_sip_ip}:{local_port};branch=z9hG4bK{uuid.uuid4().hex[:10]}{self.sip_via_params(target_transport)}",
            f"From: {from_h}",
            f"To: {to_h}",
            f"Call-ID: {call_id}",
            f"CSeq: {local_cseq} BYE",
            "Max-Forwards: 70",
            f"Contact: <sip:OpenPagingServer@{local_sip_ip}:{local_port};{self.sip_contact_transport_param(target_transport)}>",
        ]
        
        for rr in call.get("record_routes", []):
            lines.append(f"Route: {rr}")
            
        lines.extend([
            "User-Agent: OpenPagingServer",
            "Content-Length: 0"
        ])
        return "\r\n".join(lines) + "\r\n\r\n"

    def send_packet_to_call(self, call, packet):
        data = packet.encode("utf-8")
        is_bye = packet.startswith("BYE ")
        transport = call.get("bye_target_transport") if is_bye else call.get("transport")
        addr = (
            call.get("bye_target_ip") if is_bye and call.get("bye_target_ip") else call["remote_sip_ip"],
            call.get("bye_target_port") if is_bye and call.get("bye_target_port") else call["remote_sip_port"],
        )
        if transport in {"tcp", "tls"} and call["conn"]:
            try:
                call["conn"].sendall(data)
            except:
                pass
        elif transport in {"tcp", "tls"}:
            try:
                with socket.create_connection(addr, timeout=2) as tcp_sock:
                    tcp_sock.sendall(data)
            except:
                pass
        elif self.udp_sock:
            try:
                self.udp_sock.sendto(data, addr)
            except:
                pass

    def finish_call(self, call_id):
        with self.lock:
            call = self.calls.pop(call_id, None)
        if call is not None:
            try:
                bye_packet = self.build_bye(call)
                self.send_packet_to_call(call, bye_packet)
            except:
                pass
            try:
                call["session"].stop()
            except:
                pass

    def finish_early_media(self, call_id):
        with self.lock:
            call = self.calls.pop(call_id, None)
        if call is not None:
            try:
                status = call.get("early_media_status", "404 Not Found")
                response = self.build_response(
                    call["headers"],
                    status,
                    local_ip=call.get("local_sip_ip") or call["local_ip"],
                    local_port=call.get("local_sip_port"),
                    transport=call["transport"],
                    call_tag=call["local_tag"],
                )
                self.send_packet_to_call(call, response)
            except:
                pass

    def start_tone(self, call_id):
        with self.lock:
            call = self.calls.get(call_id)
            if call is None:
                return
            self.activate_call_media(call, run_on_start=True)

    def stop_call(self, call_id):
        with self.lock:
            call = self.calls.pop(call_id, None)
        if call is not None:
            call["session"].stop()

    def build_trigger_result(self, trigger, user, headers):
        generator = None
        early_media_status = None
        session_class = None
        on_start = None

        trigger_str = str(trigger).strip() if trigger is not None else ""
        parsed_trigger = trigger_str
        arg = None
        if ":" in trigger_str:
            parts = trigger_str.split(":", 1)
            parsed_trigger = parts[0].strip().lower()
            arg = parts[1].strip()
        else:
            parsed_trigger = trigger_str.lower()
            
        if parsed_trigger.startswith("%23"):
            parsed_trigger = "#" + parsed_trigger[3:]
            
        if parsed_trigger in loaded_triggers:
            sender = self.caller_id_from_headers(headers)
            if parsed_trigger == "page":
                page_group = arg if arg else user
                res = loaded_triggers[parsed_trigger](arg, group=page_group, sender=sender)
            else:
                try:
                    res = loaded_triggers[parsed_trigger](arg, sender=sender)
                except TypeError:
                    res = loaded_triggers[parsed_trigger](arg)
            session_class = res.get("session_class")
            generator = res.get("generator")
            on_start = res.get("on_start")
            early_media_status = res.get("early_media_status")
        else:
            early_media_status = "503 Service Unavailable"
            generator = chain_generators(generate_wav("./audio/503.wav"), generate_wav("./audio/unrecognized_trigger.wav"))

        if session_class is None:
            session_class = RTPSession

        return session_class, generator, on_start, early_media_status

    def make_media_session(self, session_class, remote_media_ip, remote_media_port, generator, call_id, early_media_status, media_socket=None):
        session = None
        media_bound = False
        allocated_socket = None
        finish_callback = lambda cid=call_id: self.finish_early_media(cid) if early_media_status else self.finish_call(cid)
        if session_class.__name__ == "RTPSession":
            rtp_socket = media_socket
            if rtp_socket is None:
                rtp_socket = self.bind_rtp_socket()
                allocated_socket = rtp_socket
            try:
                session = session_class(
                    remote_media_ip,
                    remote_media_port,
                    payload_generator=generator,
                    on_finish=finish_callback,
                    rtp_socket=rtp_socket,
                )
                media_bound = True
                allocated_socket = None
            except TypeError:
                if allocated_socket is not None:
                    try:
                        allocated_socket.close()
                    except Exception:
                        pass
                    allocated_socket = None
                session = session_class(
                    remote_media_ip,
                    remote_media_port,
                    generator,
                    on_finish=finish_callback,
                )
                if media_socket is not None:
                    self.attach_session_media_socket(session, media_socket)
                    media_bound = True
        else:
            try:
                session = session_class(
                    remote_media_ip,
                    remote_media_port,
                    generator=generator,
                    on_finish=finish_callback,
                )
            except TypeError:
                session = session_class(
                    remote_media_ip,
                    remote_media_port,
                    generator,
                    on_finish=finish_callback,
                )
        if session is not None and not media_bound:
            if media_socket is not None:
                self.attach_session_media_socket(session, media_socket)
            else:
                self.bind_session_media(session)
        if allocated_socket is not None:
            try:
                allocated_socket.close()
            except Exception:
                pass
        return session

    def password_success(self, call_id):
        with self.lock:
            call = self.calls.get(call_id)
        if call is None:
            return None
        try:
            session_class, generator, on_start, early_media_status = self.build_trigger_result(call["trigger"], call["user"], call["headers"])
            old_session = call["session"]
            old_socket = self.session_media_socket(old_session)
            rtp_latching_enabled = bool(call.get("rtp_latching_enabled", False))
            delegated_session = self.make_media_session(
                session_class,
                call["remote_media_ip"],
                call["remote_media_port"],
                generator,
                call_id,
                early_media_status,
                media_socket=old_socket,
            )
            self.set_session_rtp_latching(delegated_session, rtp_latching_enabled)
            try:
                self.run_trigger_preflight(delegated_session, play_failure_tone=True)
            except Exception:
                traceback.print_exc()
                old_session.stop_event.set()
                self.finish_call(call_id)
                return False
            old_session.stop_event.set()
            with self.lock:
                call = self.calls.get(call_id)
                if call is None:
                    try:
                        delegated_session.stop()
                    except Exception:
                        pass
                    return False
                call["session"] = delegated_session
                call["on_start"] = on_start
                call["early_media_status"] = early_media_status
                call["started"] = False
                call["on_start_ran"] = False
                call["local_cseq"] = call.get("local_cseq", 2)
            self.activate_call_media(call, run_on_start=True)
            return delegated_session
        except Exception:
            traceback.print_exc()
            with self.lock:
                call = self.calls.get(call_id)
            if call:
                self.finish_call(call_id)
            return None

    def password_failure(self, call_id):
        self.finish_call(call_id)

    def handle_reinvite(self, method, headers, body, source_ip, source_port, transport="udp", conn=None, trusted=False):
        call_id = self.get_first(headers, "Call-ID")
        if not call_id:
            return self.build_response(headers, "400 Bad Request")
        if not trusted and not self.ip_allowed(method, source_ip, headers=headers):
            if self.note_unauthorized_attempt(source_ip, method, conn=conn):
                return None
            nonce = uuid.uuid4().hex
            return self.build_response(headers, "401 Unauthorized", extra_headers=[f'WWW-Authenticate: Digest realm="OpenPagingServer", nonce="{nonce}", algorithm=MD5, qop="auth"'])

        with self.lock:
            call = self.calls.get(call_id)
            if call is None:
                return self.build_response(headers, "481 Call/Transaction Does Not Exist")
            session = call["session"]
            nat_mode = call.get("nat_mode")

        hold = self.sdp_offer_is_hold(body) if body else False
        hold_behavior = self.sip_trunk_hold_behavior(source_ip, headers) if hold else "passrtp"
        remote_media_ip, remote_media_port, has_video = self.parse_sdp_offer(body, source_ip, nat_mode=nat_mode) if body else (None, None, False)
        rtp_latching_enabled = self.sdp_media_needs_latching(body, source_ip, nat_mode=nat_mode) if body else False

        with self.lock:
            call = self.calls.get(call_id)
            if call is None:
                return self.build_response(headers, "481 Call/Transaction Does Not Exist")
            session = call["session"]
            call["remote_sip_ip"] = source_ip
            call["remote_sip_port"] = source_port
            call["transport"] = transport
            call["conn"] = conn
            call["cseq"] = self.get_first(headers, "CSeq")
            contact = self.get_first(headers, "Contact")
            if contact:
                call["contact"] = self.normalize_dialog_contact(
                    contact,
                    source_ip,
                    source_port,
                    transport=transport,
                    nat_mode=call.get("nat_mode"),
                )
            record_routes = self.get_all(headers, "Record-Route")
            if record_routes:
                call["record_routes"] = record_routes
            if remote_media_ip and remote_media_port and remote_media_port > 0:
                call["remote_media_ip"] = remote_media_ip
                call["remote_media_port"] = remote_media_port
                call["has_video"] = has_video
                call["rtp_latching_enabled"] = rtp_latching_enabled
                self.set_session_media_target(session, remote_media_ip, remote_media_port)
                self.set_session_rtp_latching(session, rtp_latching_enabled)
            elif body and has_video:
                call["has_video"] = True

            if hold:
                call["held"] = True
                call["hold_behavior"] = hold_behavior
                if hold_behavior == "pausertp":
                    self.set_session_rtp_paused(session, True)
                    answer_direction = "recvonly"
                elif hold_behavior == "endcall":
                    self.set_session_rtp_paused(session, True)
                    answer_direction = "inactive"
                else:
                    self.set_session_rtp_paused(session, False)
                    answer_direction = "sendrecv"
            else:
                call["held"] = False
                call["hold_behavior"] = "passrtp"
                self.set_session_rtp_paused(session, False)
                answer_direction = "sendrecv"

            local_ip = call["local_ip"]
            local_port = call["local_port"]
            disable_video = call.get("has_video", False)
            local_tag = call.get("local_tag")

        sdp = self.build_invite_sdp(local_ip, local_port, disable_video=disable_video, direction=answer_direction)
        response = self.build_response(
            headers,
            "200 OK",
            body=sdp,
            local_ip=call.get("local_sip_ip") or local_ip,
            local_port=call.get("local_sip_port"),
            transport=transport,
            call_tag=local_tag,
        )
        if hold and hold_behavior == "endcall":
            self.schedule_finish_call(call_id)
        return response

    def handle_invite(self, method, uri, headers, body, source_ip, source_port, transport="udp", conn=None, trusted=False):
        call_id = self.get_first(headers, "Call-ID")
        if not call_id:
            return self.build_response(headers, "400 Bad Request")
        with self.lock:
            existing_call = call_id in self.calls
        if existing_call:
            response = self.handle_reinvite(method, headers, body, source_ip, source_port, transport=transport, conn=conn, trusted=trusted)
            return response
        if not trusted and not self.ip_allowed(method, source_ip, headers=headers):
            if self.note_unauthorized_attempt(source_ip, method, conn=conn):
                return None
            nonce = uuid.uuid4().hex
            return self.build_response(headers, "401 Unauthorized", extra_headers=[f'WWW-Authenticate: Digest realm="OpenPagingServer", nonce="{nonce}", algorithm=MD5, qop="auth"'])
        if not trusted:
            self.mark_authorized_trunk_seen(method, source_ip, headers)
        nat_mode = self.sip_nat_mode()
        user = self.request_user_from_uri(uri)
        
        db_status, trigger, passcode = self.get_endpoint_trigger(user)

        generator = None
        early_media_status = None
        session_class = None
        on_start = None

        if db_status == "not_found":
            early_media_status = "404 Not Found"
            generator = generate_wav("./audio/404.wav")
        elif db_status == "error":
            early_media_status = "503 Service Unavailable"
            generator = chain_generators(generate_wav("./audio/503.wav"), generate_wav("./audio/database_error.wav"))
        else:
            passcode_str = str(passcode).strip() if passcode is not None else ""
            if passcode_str:
                remote_media_ip, remote_media_port, has_video = self.parse_sdp_offer(body, source_ip, nat_mode=nat_mode)
                rtp_latching_enabled = self.sdp_media_needs_latching(body, source_ip, nat_mode=nat_mode)
                if not remote_media_port:
                    return self.build_response(headers, "400 Bad Request")
                local_sip_ip, local_sip_port, local_media_ip = self.inbound_dialog_addresses(remote_media_ip, transport=transport, conn=conn, nat_mode=nat_mode)
                from_h = self.get_first(headers, "From")
                to_h = self.get_first(headers, "To")
                local_tag = uuid.uuid4().hex[:10]
                if "tag=" not in to_h.lower():
                    to_h = to_h + ";tag=" + local_tag
                contact = self.normalize_dialog_contact(
                    self.get_first(headers, "Contact"),
                    source_ip,
                    source_port,
                    transport=transport,
                    nat_mode=nat_mode,
                )
                try:
                    session = PasscodeRTPSession(
                        remote_media_ip,
                        remote_media_port,
                        passcode_str,
                        self,
                        call_id,
                        on_success=lambda cid=call_id: self.password_success(cid),
                        on_failure=lambda cid=call_id: self.password_failure(cid),
                        on_finish=lambda cid=call_id: self.finish_call(cid),
                        rtp_socket=self.bind_rtp_socket(),
                    )
                except SipCongestionError as exc:
                    return self.build_503(headers, local_ip=local_sip_ip, local_port=local_sip_port, transport=transport, call_tag=local_tag, reason_text=str(exc))
                self.set_session_rtp_latching(session, rtp_latching_enabled)
                local_media_ip, local_media_port = self.inbound_media_address_for_source(
                    self.session_media_socket(session),
                    remote_media_ip,
                    local_media_ip,
                    local_sip_ip,
                    transport=transport,
                    nat_mode=nat_mode,
                )
                with self.lock:
                    call = {
                        "session": session,
                        "started": False,
                        "headers": headers,
                        "remote_sip_ip": source_ip,
                        "remote_media_ip": remote_media_ip,
                        "remote_media_port": remote_media_port,
                        "remote_sip_port": source_port,
                        "local_ip": local_media_ip,
                        "local_port": local_media_port,
                        "local_sip_ip": local_sip_ip,
                        "local_sip_port": local_sip_port,
                        "from_h": from_h,
                        "to_h": to_h,
                        "local_tag": local_tag,
                        "call_id": call_id,
                        "cseq": self.get_first(headers, "CSeq"),
                        "local_cseq": 2,
                        "contact": contact,
                        "transport": transport,
                        "has_video": has_video,
                        "conn": conn,
                        "on_start": None,
                        "on_start_ran": True,
                        "early_media_status": None,
                        "rtp_latching_enabled": rtp_latching_enabled,
                        "record_routes": self.get_all(headers, "Record-Route"),
                        "trigger": trigger,
                        "user": user,
                        "nat_mode": nat_mode,
                    }
                    self.calls[call_id] = call
                self.prime_inbound_media_path(call)
                sdp = self.build_invite_sdp(local_media_ip, local_media_port, disable_video=has_video)
                return self.build_response(headers, "200 OK", body=sdp, local_ip=local_sip_ip, local_port=local_sip_port, transport=transport, call_tag=local_tag)

            session_class, generator, on_start, early_media_status = self.build_trigger_result(trigger, user, headers)

        if session_class is None:
            session_class = RTPSession

        remote_media_ip, remote_media_port, has_video = self.parse_sdp_offer(body, source_ip, nat_mode=nat_mode)
        rtp_latching_enabled = self.sdp_media_needs_latching(body, source_ip, nat_mode=nat_mode)
        if not remote_media_port:
            return self.build_response(headers, "400 Bad Request")
        
        local_sip_ip, local_sip_port, local_media_ip = self.inbound_dialog_addresses(remote_media_ip, transport=transport, conn=conn, nat_mode=nat_mode)
        
        try:
            session = self.make_media_session(session_class, remote_media_ip, remote_media_port, generator, call_id, early_media_status)
        except SipCongestionError as exc:
            return self.build_503(headers, local_ip=local_sip_ip, local_port=local_sip_port, transport=transport, reason_text=str(exc))
        self.set_session_rtp_latching(session, rtp_latching_enabled)
        local_media_ip, local_media_port = self.inbound_media_address_for_source(
            self.session_media_socket(session),
            remote_media_ip,
            local_media_ip,
            local_sip_ip,
            transport=transport,
            nat_mode=nat_mode,
        )

        try:
            self.run_trigger_preflight(session)
        except Exception:
            traceback.print_exc()
            return self.build_503(headers, local_ip=local_sip_ip, local_port=local_sip_port, transport=transport)
        
        prime_call = None
        with self.lock:
            call = self.calls.get(call_id)
            if call is None:
                from_h = self.get_first(headers, "From")
                to_h = self.get_first(headers, "To")
                local_tag = uuid.uuid4().hex[:10]
                if "tag=" not in to_h.lower():
                    to_h = to_h + ";tag=" + local_tag
                contact = self.normalize_dialog_contact(
                    self.get_first(headers, "Contact"),
                    source_ip,
                    source_port,
                    transport=transport,
                    nat_mode=nat_mode,
                )
                
                call = {
                    "session": session,
                    "started": False,
                    "headers": headers,
                    "remote_sip_ip": source_ip,
                    "remote_media_ip": remote_media_ip,
                    "remote_media_port": remote_media_port,
                    "remote_sip_port": source_port,
                    "local_ip": local_media_ip,
                    "local_port": local_media_port,
                    "local_sip_ip": local_sip_ip,
                    "local_sip_port": local_sip_port,
                    "from_h": from_h,
                    "to_h": to_h,
                    "local_tag": local_tag,
                    "call_id": call_id,
                    "cseq": self.get_first(headers, "CSeq"),
                    "local_cseq": 2,
                    "contact": contact,
                    "transport": transport,
                    "has_video": has_video,
                    "conn": conn,
                    "on_start": on_start,
                    "on_start_ran": False,
                    "early_media_status": early_media_status,
                    "rtp_latching_enabled": rtp_latching_enabled,
                    "record_routes": self.get_all(headers, "Record-Route"),
                    "trigger": trigger,
                    "user": user,
                    "nat_mode": nat_mode,
                }
                self.calls[call_id] = call

            if early_media_status:
                self.activate_call_media(call, run_on_start=True)
            else:
                prime_call = call

        if prime_call is not None:
            self.prime_inbound_media_path(prime_call)

        sdp = self.build_invite_sdp(call["local_ip"], call["local_port"], disable_video=call["has_video"])
        status = "183 Session Progress" if call["early_media_status"] else "200 OK"
        return self.build_response(
            headers,
            status,
            body=sdp,
            local_ip=call.get("local_sip_ip") or call["local_ip"],
            local_port=call.get("local_sip_port"),
            transport=transport,
            call_tag=call["local_tag"],
        )

    def handle_packet(self, data, addr, sock, tcp, conn=None, client_trunk_id=None):
        if not data.strip():
            return None
        transport = "tcp" if tcp else "udp"
        method, uri, version, headers, body = self.parse_request(data)
        
        if not method:
            return None

        if method != "SIP/2.0" and self.should_drop_silently(headers, addr[0], conn=conn):
            return None

        if method != "SIP/2.0":
            headers = self.nat_annotate_request_headers(headers, addr[0], addr[1], transport)
            
        if method == "SIP/2.0":
            status_code = uri
            call_id = self.get_first(headers, "Call-ID")
            cseq = self.get_first(headers, "CSeq")
            lower_headers = {str(name).lower(): value for name, value in headers if name}
            response = {
                "status_code": safe_int(status_code, 0),
                "status_text": str(uri or ""),
                "headers": lower_headers,
                "body": body,
                "to": self.get_first(headers, "To"),
                "record_routes": self.get_all(headers, "Record-Route"),
                "source_ip": addr[0],
                "source_port": addr[1],
            }
            outbound_call = None
            is_invite_response = call_id and "INVITE" in str(cseq or "").upper()
            if is_invite_response:
                with self.outbound_lock:
                    outbound_call = self.outbound_calls.get(str(call_id))
            if call_id and cseq and self.queue_client_response(call_id, cseq, response):
                return None
            if outbound_call is not None:
                with self.outbound_lock:
                    state = self.outbound_trunks.get(str(outbound_call.trunk_id))
                try:
                    self.handle_outbound_invite_progress(outbound_call, response, state=state)
                except Exception:
                    traceback.print_exc()
                if safe_int(status_code, 0) >= 200:
                    try:
                        self.send_outbound_ack(outbound_call, response=response)
                    except Exception:
                        pass
                return None
            if call_id:
                with self.lock:
                    ping_info = self.pending_pings.pop(call_id, None)
                if ping_info:
                    if status_code == "200":
                        user_agent = self.get_first(headers, "User-Agent") or self.get_first(headers, "Server") or "Unknown"
                        auth.update_trunk_status_by_ip(ping_info['ip'], f"Online, '{user_agent}'")
                    else:
                        auth.update_trunk_status_by_ip(ping_info['ip'], "Offline")
            return None
            
        if method == "OPTIONS":
            self.mark_authorized_trunk_seen("OPTIONS", addr[0], headers)
            response = self.build_response(headers, "200 OK")
        elif method == "REGISTER":
            if self.ip_allowed("REGISTER", addr[0], headers=headers):
                contact = self.get_first(headers, "Contact")
                expires = self.get_first(headers, "Expires")
                if not expires and contact and "expires=" in contact.lower():
                    matches = re.search(r'expires=(\d+)', contact, re.IGNORECASE)
                    if matches:
                        expires = matches.group(1)
                try:
                    expires_int = int(expires) if expires else 3600
                except:
                    expires_int = 3600
                
                extra = []
                if contact:
                    extra.append(f"Contact: {contact}")
                extra.append(f"Expires: {expires_int}")
                
                response = self.build_response(headers, "200 OK", extra_headers=extra, is_register=True)
                
                username = self.extract_username_from_auth(headers)
                if username:
                    user_agent = self.get_first(headers, "User-Agent") or "Unknown"
                    if expires_int > 0:
                        with self.lock:
                            self.registrations[username] = time.time() + expires_int
                        auth.update_trunk_status_by_user(username, f"{addr[0]}, '{user_agent}'")
                    else:
                        with self.lock:
                            self.registrations.pop(username, None)
                        auth.update_trunk_status_by_user(username, "Offline")
            else:
                if self.note_unauthorized_attempt(addr[0], "REGISTER", conn=conn):
                    response = None
                else:
                    nonce = uuid.uuid4().hex
                    response = self.build_response(headers, "401 Unauthorized", extra_headers=[f'WWW-Authenticate: Digest realm="OpenPagingServer", nonce="{nonce}", algorithm=MD5, qop="auth"'])
        elif method == "INVITE":
            call_id = self.get_first(headers, "Call-ID")
            with self.outbound_lock:
                outbound_call = self.outbound_calls.get(str(call_id))
            if outbound_call is not None:
                outbound_call.remote_sip_ip = str(addr[0] or outbound_call.remote_sip_ip or "")
                outbound_call.remote_sip_port = safe_int(addr[1], outbound_call.remote_sip_port or 5060)
                outbound_call.transport = transport or outbound_call.transport
                outbound_call.conn = conn
                contact = self.get_first(headers, "Contact")
                if contact:
                    outbound_call.remote_contact = contact
                record_routes = self.get_all(headers, "Record-Route")
                if record_routes:
                    outbound_call.record_routes = record_routes
                if body:
                    remote_media_ip, remote_media_port, _ = self.parse_sdp_offer(body, outbound_call.remote_sip_ip)
                    if remote_media_port:
                        outbound_call.remote_media_ip = remote_media_ip
                        outbound_call.remote_media_port = remote_media_port
                        outbound_call.rtp_latching_enabled = True
                        sip_debug(
                            f"outbound rtp target call={getattr(outbound_call, 'call_id', '')} "
                            f"remote={remote_media_ip}:{remote_media_port} local={sip_sockname(outbound_call.rtp_socket)}"
                        )
                sdp = self.build_invite_sdp(outbound_call.advertised_media_ip, outbound_call.advertised_media_port)
                response = self.build_response(
                    headers,
                    "200 OK",
                    body=sdp,
                    local_ip=outbound_call.local_sip_ip,
                    local_port=outbound_call.local_sip_port,
                    transport=transport,
                    call_tag=outbound_call.local_tag,
                )
                encoded = response.encode("utf-8")
                if tcp:
                    return encoded
                sock.sendto(encoded, addr)
                return None
            trusted = client_trunk_id is not None or self.outbound_source_is_trusted(addr[0])
            trying = self.build_response(headers, "100 Trying")
            try:
                encoded_trying = trying.encode("utf-8")
                if tcp:
                    if conn is not None:
                        conn.sendall(encoded_trying)
                else:
                    sock.sendto(encoded_trying, addr)
            except Exception:
                pass
            response = self.handle_invite(method, uri, headers, body, addr[0], addr[1], transport=transport, conn=conn, trusted=trusted)
        elif method == "ACK":
            call_id = self.get_first(headers, "Call-ID")
            if call_id:
                with self.lock:
                    call = self.calls.get(call_id)
                    if call is not None:
                        call["remote_sip_ip"] = addr[0]
                        call["remote_sip_port"] = addr[1]
                        call["transport"] = transport
                        call["conn"] = conn
                self.start_tone(call_id)
            response = None
        elif method == "BYE":
            call_id = self.get_first(headers, "Call-ID")
            with self.outbound_lock:
                outbound_call = self.outbound_calls.pop(str(call_id), None)
            if outbound_call is not None:
                try:
                    outbound_call.released = True
                    outbound_call.mark_disconnected()
                finally:
                    try:
                        outbound_call.rtp_socket.close()
                    except Exception:
                        pass
                response = self.build_response(headers, "200 OK")
            else:
                local_tag = None
                if call_id:
                    with self.lock:
                        call = self.calls.get(call_id)
                        if call:
                            local_tag = call.get("local_tag")
                    self.stop_call(call_id)
                response = self.build_response(headers, "200 OK", call_tag=local_tag)
        elif method == "INFO":
            call_id = self.get_first(headers, "Call-ID")
            if call_id:
                with self.lock:
                    call = self.calls.get(call_id)
                    if call and hasattr(call["session"], "_append_digit"):
                        body_lower = body.lower()
                        signal_match = re.search(r'signal\s*=\s*([0-9abcd\*#]+|%23)', body_lower, re.IGNORECASE)
                        if signal_match:
                            sig = signal_match.group(1)
                            if sig == "%23":
                                sig = "#"
                            sig = sig.upper()
                            if sig in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "#", "A", "B", "C", "D"):
                                call["session"]._append_digit(sig)
                    elif call and hasattr(call["session"], "dtmf_event_flag"):
                        body_lower = body.lower()
                        if "signal=11" in body_lower or "signal=#" in body_lower or "signal=%23" in body_lower:
                            call["session"].dtmf_event_flag = True
            response = self.build_response(headers, "200 OK")
        else:
            response = self.build_response(headers, "501 Not Implemented")
            
        if response is None:
            return None
            
        encoded = response.encode("utf-8")
        if tcp:
            return encoded
            
        sock.sendto(encoded, addr)
        return None

    def udp_loop(self, sock):
        while not self.stop_event.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except OSError:
                break
            except:
                continue
            if self.intrusion_ip_blocked(addr[0]):
                continue
            try:
                self.handle_packet(data, addr, sock, False)
            except Exception as e:
                print(f"[ERROR] Exception in handle_packet: {e}")
                traceback.print_exc()

    def tcp_message(self, conn):
        conn.settimeout(2.0)
        buffer_key = id(conn)
        data = self.tcp_buffers.get(buffer_key, b"")
        while len(data) < 65535:
            data = data.lstrip(b"\r\n")
            header_end, sep_len = sip_header_end(data)
            if header_end != -1:
                _found, content_length = sip_head_content_length(data[:header_end])
                total_length = header_end + sep_len + max(0, content_length)
                if len(data) >= total_length:
                    message = data[:total_length]
                    remainder = data[total_length:]
                    if remainder:
                        self.tcp_buffers[buffer_key] = remainder
                    else:
                        self.tcp_buffers.pop(buffer_key, None)
                    return message
            try:
                chunk = conn.recv(4096)
                if not chunk:
                    self.tcp_buffers.pop(buffer_key, None)
                    return b""
                data += chunk
                self.tcp_buffers[buffer_key] = data
            except socket.timeout:
                self.tcp_buffers[buffer_key] = data
                if not data:
                    return None
                continue
            except Exception:
                self.tcp_buffers.pop(buffer_key, None)
                return b""
        self.tcp_buffers[buffer_key] = data
        return None

    def tcp_client(self, conn, addr):
        if self.intrusion_ip_blocked(addr[0]):
            self.drop_transport_connection(conn)
            return
        try:
            while not self.stop_event.is_set():
                raw = self.tcp_message(conn)
                if raw is None:
                    continue
                if not raw:
                    break
                try:
                    response = self.handle_packet(raw, addr, None, True, conn=conn)
                    if response is not None:
                        conn.sendall(response)
                except Exception as e:
                    print(f"[ERROR] Exception in TCP handle_packet: {e}")
                    traceback.print_exc()
        except:
            pass
        finally:
            self.tcp_buffers.pop(id(conn), None)
            try:
                conn.close()
            except:
                pass

    def tcp_loop(self, sock):
        while not self.stop_event.is_set():
            try:
                conn, addr = sock.accept()
            except OSError:
                break
            except:
                continue
            if self.intrusion_ip_blocked(addr[0]):
                self.drop_transport_connection(conn)
                continue
            threading.Thread(target=self.tcp_client, args=(conn, addr), daemon=True).start()

    def close_sockets(self):
        with self.lock:
            if self.udp_sock is not None:
                try:
                    self.udp_sock.close()
                except:
                    pass
                self.udp_sock = None
            if self.tcp_sock is not None:
                try:
                    self.tcp_sock.close()
                except:
                    pass
                self.tcp_sock = None
            self.active_port = None

    def try_bind_pair(self, port):
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp_sock.bind(("0.0.0.0", port))
            tcp_sock.bind(("0.0.0.0", port))
            tcp_sock.listen(50)
            return udp_sock, tcp_sock
        except:
            try:
                udp_sock.close()
            except:
                pass
            try:
                tcp_sock.close()
            except:
                pass
            return None, None

    def bind_listeners(self, desired_port):
        candidates = [desired_port]
        if desired_port != 5060:
            candidates.append(5060)
        for port in candidates:
            udp_sock, tcp_sock = self.try_bind_pair(port)
            if udp_sock is not None and tcp_sock is not None:
                with self.lock:
                    self.udp_sock = udp_sock
                    self.tcp_sock = tcp_sock
                    self.active_port = port
                threading.Thread(target=self.udp_loop, args=(udp_sock,), daemon=True).start()
                threading.Thread(target=self.tcp_loop, args=(tcp_sock,), daemon=True).start()
                return True
        return False

    def manager(self):
        while not self.stop_event.is_set():
            try:
                enabled, port = self.get_settings()
                if not enabled:
                    if self.enabled is not False or self.udp_sock is not None or self.tcp_sock is not None:
                        self.close_sockets()
                    self.enabled = False
                else:
                    self.enabled = True
                    if self.active_port != port or self.udp_sock is None or self.tcp_sock is None:
                        self.close_sockets()
                        self.bind_listeners(port)
                self.sip_intrusion_cleanup()
                self.maintain_outbound_trunks()
            except Exception:
                traceback.print_exc()
            time.sleep(2)

    def pinger_loop(self):
        while not self.stop_event.is_set():
            now = time.time()
            expired_users = []
            with self.lock:
                for user, exp_time in list(self.registrations.items()):
                    if now > exp_time:
                        expired_users.append(user)
                        del self.registrations[user]
            for user in expired_users:
                auth.update_trunk_status_by_user(user, "Offline")

            timed_out_pings = []
            with self.lock:
                for cid, info in list(self.pending_pings.items()):
                    if now - info['time'] > 5:
                        timed_out_pings.append(info['ip'])
                        del self.pending_pings[cid]
            for ip in timed_out_pings:
                auth.update_trunk_status_by_ip(ip, "Offline")

            if self.enabled and self.udp_sock:
                ips_to_ping = auth.get_all_ip_trunks()
                for ip in ips_to_ping:
                    call_id = uuid.uuid4().hex
                    branch = uuid.uuid4().hex[:10]
                    tag = uuid.uuid4().hex[:10]
                    local_ip = self.local_ip_for(ip)
                    
                    req = (
                        f"OPTIONS sip:{ip} SIP/2.0\r\n"
                        f"Via: SIP/2.0/UDP {local_ip}:{self.active_port};branch=z9hG4bK{branch}\r\n"
                        f"From: <sip:ping@{local_ip}>;tag={tag}\r\n"
                        f"To: <sip:{ip}>\r\n"
                        f"Call-ID: {call_id}\r\n"
                        f"CSeq: 1 OPTIONS\r\n"
                        f"Max-Forwards: 70\r\n"
                        f"User-Agent: OpenPagingServer\r\n"
                        f"Content-Length: 0\r\n\r\n"
                    )
                    with self.lock:
                        self.pending_pings[call_id] = {'ip': ip, 'time': time.time()}
                    try:
                        self.udp_sock.sendto(req.encode('utf-8'), (ip, 5060))
                    except:
                        auth.update_trunk_status_by_ip(ip, "Offline")
            
            time.sleep(30)

    def start(self):
        self.mark_all_outbound_trunks_status("Offline")
        if self.manager_thread is None or not self.manager_thread.is_alive():
            self.stop_event.clear()
            self.manager_thread = threading.Thread(target=self.manager, daemon=True)
            self.manager_thread.start()
        if self.pinger_thread is None or not self.pinger_thread.is_alive():
            self.pinger_thread = threading.Thread(target=self.pinger_loop, daemon=True)
            self.pinger_thread.start()
        try:
            self.maintain_outbound_trunks()
        except Exception:
            traceback.print_exc()

    def shutdown(self):
        self.stop_event.set()
        self.shutdown_outbound_trunks()
        self.close_sockets()
        with self.lock:
            for call_id in list(self.calls.keys()):
                call = self.calls.pop(call_id, None)
                if call is not None:
                    call["session"].stop()
        with self.outbound_lock:
            outbound_ids = list(self.outbound_calls.keys())
        for call_id in outbound_ids:
            try:
                self.finish_outbound_call(call_id)
            except Exception:
                pass

sip_server = SipServer()

def start():
    sip_server.start()

def shutdown():
    sip_server.shutdown()

if __name__ == "__main__":
    start()
    while True:
        time.sleep(3600)

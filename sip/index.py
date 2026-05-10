import os
import sys
import os
import socket
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
from pathlib import Path

import pymysql
from dotenv import load_dotenv

try:
    import bcrypt
except:
    bcrypt = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth
from audio_utils import generate_wav, chain_generators
from rtp import RTPSession

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

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
    def __init__(self, target_ip, target_port, passcode, server, call_id, on_success=None, on_failure=None, on_finish=None):
        super().__init__(target_ip, target_port, payload_generator=None, on_finish=on_finish)
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
                        data, _ = self.socket.recvfrom(4096)
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
                    data, _ = self.socket.recvfrom(4096)
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
                        data, _ = self.socket.recvfrom(4096)
                        pt, payload = self._parse_rtp(data)
                        if pt is not None:
                            self._handle_dtmf(pt, payload)
                except:
                    pass

    def run(self):
        ok = False
        try:
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

class SipServer:
    def __init__(self):
        self.lock = threading.Lock()
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

    def connect_db(self):
        return pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            autocommit=True,
        )

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
        text = raw.decode("utf-8", errors="ignore")
        parts = text.split("\r\n\r\n", 1)
        head = parts[0]
        body = parts[1] if len(parts) > 1 else ""
        lines = head.split("\r\n")
        if not lines:
            return None, None, None, [], ""
        req_line = lines[0].split(" ", 2)
        if len(req_line) < 2:
            return None, None, None, [], ""
        method = req_line[0]
        uri = req_line[1]
        version = req_line[2] if len(req_line) > 2 else ""
        headers = []
        content_length = 0
        for line in lines[1:]:
            if not line:
                continue
            if line[:1] in " \t" and headers:
                name, value = headers[-1]
                headers[-1] = (name, value + " " + line.strip())
                continue
            if ":" in line:
                name, value = line.split(":", 1)
                name = name.strip()
                value = value.strip()
                headers.append((name, value))
                if name.lower() == "content-length":
                    try:
                        content_length = int(value)
                    except:
                        content_length = 0
        if content_length > 0:
            body = body[:content_length]
        return method.upper(), uri, version, headers, body

    def get_first(self, headers, name):
        lname = name.lower()
        for k, v in headers:
            if k.lower() == lname:
                return v
        return ""

    def get_all(self, headers, name):
        lname = name.lower()
        return [v for k, v in headers if k.lower() == lname]

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

    def bye_network_target(self, call):
        route_set = list(call.get("record_routes") or [])
        target_source = route_set[-1] if route_set else call.get("contact")
        host, port, transport = self.parse_sip_target(
            target_source,
            fallback_ip=call.get("remote_sip_ip"),
            fallback_port=call.get("remote_sip_port") or 5060,
            fallback_transport=call.get("transport") or "udp",
        )
        return host or call.get("remote_sip_ip"), int(port or 5060), (transport or call.get("transport") or "udp").lower()

    def parse_sdp_offer(self, body, fallback_ip):
        media_ip = fallback_ip
        media_port = None
        has_video = False
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("c=IN IP4 "):
                media_ip = line[9:].strip()
            elif line.startswith("m=audio "):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        media_port = int(parts[1])
                    except:
                        media_port = None
            elif line.startswith("m=video "):
                has_video = True
        return media_ip, media_port, has_video

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

    def schedule_finish_call(self, call_id, delay=0.2):
        def worker():
            time.sleep(delay)
            self.finish_call(call_id)
        threading.Thread(target=worker, daemon=True).start()

    def build_response(self, headers, status, body="", content_type="application/sdp", local_ip=None, transport="udp", call_tag=None, extra_headers=None, is_register=False):
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
        if local_ip and not is_register:
            lines.append(f"Contact: <sip:OpenPagingServer@{local_ip}:{self.active_port};transport={transport}>")
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

    def build_503(self, headers, local_ip=None, transport="udp", call_tag=None, reason_text="SIP congestion"):
        return self.build_response(
            headers,
            "503 Service Unavailable",
            local_ip=local_ip,
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
        local_port = self.active_port or call.get("local_sip_port") or 5060
        local_cseq = int(call.get("local_cseq", 2) or 2)
            
        lines = [
            f"BYE {uri} SIP/2.0",
            f"Via: SIP/2.0/{target_transport.upper()} {call['local_ip']}:{local_port};branch=z9hG4bK{uuid.uuid4().hex[:10]};rport",
            f"From: {from_h}",
            f"To: {to_h}",
            f"Call-ID: {call_id}",
            f"CSeq: {local_cseq} BYE",
            "Max-Forwards: 70",
            f"Contact: <sip:OpenPagingServer@{call['local_ip']}:{local_port};transport={target_transport}>",
        ]
        
        for rr in reversed(call.get("record_routes", [])):
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
        if transport == "tcp" and call["conn"] and addr[0] == call.get("remote_sip_ip"):
            try:
                call["conn"].sendall(data)
            except:
                pass
        elif transport == "tcp":
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
                response = self.build_response(call["headers"], status, local_ip=call["local_ip"], transport=call["transport"], call_tag=call["local_tag"])
                self.send_packet_to_call(call, response)
            except:
                pass

    def start_tone(self, call_id):
        with self.lock:
            call = self.calls.get(call_id)
            if call is None:
                return
            session = call["session"]
            if not call["started"]:
                call["started"] = True
                session.start()
                if call.get("on_start"):
                    try:
                        call["on_start"]()
                    except Exception as e:
                        print(f"Error in trigger on_start: {e}")

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

    def make_media_session(self, session_class, remote_media_ip, remote_media_port, generator, call_id, early_media_status):
        if session_class.__name__ == "RTPSession":
            try:
                return session_class(
                    remote_media_ip,
                    remote_media_port,
                    payload_generator=generator,
                    on_finish=lambda cid=call_id: self.finish_early_media(cid) if early_media_status else self.finish_call(cid),
                )
            except TypeError:
                return session_class(
                    remote_media_ip,
                    remote_media_port,
                    generator,
                    on_finish=lambda cid=call_id: self.finish_early_media(cid) if early_media_status else self.finish_call(cid),
                )
        else:
            try:
                return session_class(
                    remote_media_ip,
                    remote_media_port,
                    generator=generator,
                    on_finish=lambda cid=call_id: self.finish_early_media(cid) if early_media_status else self.finish_call(cid),
                )
            except TypeError:
                return session_class(
                    remote_media_ip,
                    remote_media_port,
                    generator,
                    on_finish=lambda cid=call_id: self.finish_early_media(cid) if early_media_status else self.finish_call(cid),
                )

    def password_success(self, call_id):
        with self.lock:
            call = self.calls.get(call_id)
        if call is None:
            return None
        try:
            session_class, generator, on_start, early_media_status = self.build_trigger_result(call["trigger"], call["user"], call["headers"])
            old_session = call["session"]
            delegated_session = self.make_media_session(
                session_class,
                call["remote_media_ip"],
                call["remote_media_port"],
                generator,
                call_id,
                early_media_status,
            )
            old_socket = old_session.socket
            try:
                replacement_socket = (
                    getattr(delegated_session, "socket", None)
                    or getattr(delegated_session, "local_sock", None)
                    or getattr(delegated_session, "sock", None)
                )
                if replacement_socket is not None:
                    replacement_socket.close()
            except Exception:
                pass
            if hasattr(delegated_session, "socket"):
                delegated_session.socket = old_socket
            if hasattr(delegated_session, "local_sock"):
                delegated_session.local_sock = old_socket
            if hasattr(delegated_session, "sock"):
                delegated_session.sock = old_socket
            delegated_session.local_port = old_session.local_port
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
                call["started"] = True
                call["local_cseq"] = call.get("local_cseq", 2)
            if on_start:
                try:
                    on_start()
                except Exception as e:
                    print(f"Error in trigger on_start: {e}")
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

    def handle_reinvite(self, method, headers, body, source_ip, source_port, transport="udp", conn=None):
        call_id = self.get_first(headers, "Call-ID")
        if not call_id:
            return self.build_response(headers, "400 Bad Request")
        if not self.ip_allowed(method, source_ip, headers=headers):
            nonce = uuid.uuid4().hex
            return self.build_response(headers, "401 Unauthorized", extra_headers=[f'WWW-Authenticate: Digest realm="OpenPagingServer", nonce="{nonce}", algorithm=MD5, qop="auth"'])

        with self.lock:
            call = self.calls.get(call_id)
            if call is None:
                return self.build_response(headers, "481 Call/Transaction Does Not Exist")
            session = call["session"]

        hold = self.sdp_offer_is_hold(body) if body else False
        hold_behavior = self.sip_trunk_hold_behavior(source_ip, headers) if hold else "passrtp"
        remote_media_ip, remote_media_port, has_video = self.parse_sdp_offer(body, source_ip) if body else (None, None, False)

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
                call["contact"] = contact
            record_routes = self.get_all(headers, "Record-Route")
            if record_routes:
                call["record_routes"] = record_routes
            if remote_media_ip and remote_media_port and remote_media_port > 0:
                call["remote_media_ip"] = remote_media_ip
                call["remote_media_port"] = remote_media_port
                call["has_video"] = has_video
                self.set_session_media_target(session, remote_media_ip, remote_media_port)
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
        response = self.build_response(headers, "200 OK", body=sdp, local_ip=local_ip, transport=transport, call_tag=local_tag)
        if hold and hold_behavior == "endcall":
            self.schedule_finish_call(call_id)
        return response

    def handle_invite(self, method, uri, headers, body, source_ip, source_port, transport="udp", conn=None):
        call_id = self.get_first(headers, "Call-ID")
        if not call_id:
            return self.build_response(headers, "400 Bad Request")
        with self.lock:
            existing_call = call_id in self.calls
        if existing_call:
            response = self.handle_reinvite(method, headers, body, source_ip, source_port, transport=transport, conn=conn)
            if response is not None:
                return response
        if not self.ip_allowed(method, source_ip, headers=headers):
            nonce = uuid.uuid4().hex
            return self.build_response(headers, "401 Unauthorized", extra_headers=[f'WWW-Authenticate: Digest realm="OpenPagingServer", nonce="{nonce}", algorithm=MD5, qop="auth"'])
        self.mark_authorized_trunk_seen(method, source_ip, headers)
        user = uri
        if ":" in user:
            user = user.split(":", 1)[1]
        if "@" in user:
            user = user.split("@", 1)[0]

        user = urllib.parse.unquote(user)
        
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
                remote_media_ip, remote_media_port, has_video = self.parse_sdp_offer(body, source_ip)
                if not remote_media_port:
                    return self.build_response(headers, "400 Bad Request")
                local_ip = self.local_ip_for(remote_media_ip)
                from_h = self.get_first(headers, "From")
                to_h = self.get_first(headers, "To")
                local_tag = uuid.uuid4().hex[:10]
                if "tag=" not in to_h.lower():
                    to_h = to_h + ";tag=" + local_tag
                contact = self.get_first(headers, "Contact")
                if not contact:
                    contact = f"sip:{source_ip}:{source_port}"
                session = PasscodeRTPSession(
                    remote_media_ip,
                    remote_media_port,
                    passcode_str,
                    self,
                    call_id,
                    on_success=lambda cid=call_id: self.password_success(cid),
                    on_failure=lambda cid=call_id: self.password_failure(cid),
                    on_finish=lambda cid=call_id: self.finish_call(cid),
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
                        "local_ip": local_ip,
                        "local_port": session.local_port,
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
                        "early_media_status": None,
                        "record_routes": self.get_all(headers, "Record-Route"),
                        "trigger": trigger,
                        "user": user,
                    }
                    self.calls[call_id] = call
                sdp = self.build_invite_sdp(local_ip, session.local_port, disable_video=has_video)
                return self.build_response(headers, "200 OK", body=sdp, local_ip=local_ip, transport=transport, call_tag=local_tag)

            session_class, generator, on_start, early_media_status = self.build_trigger_result(trigger, user, headers)

        if session_class is None:
            session_class = RTPSession

        remote_media_ip, remote_media_port, has_video = self.parse_sdp_offer(body, source_ip)
        if not remote_media_port:
            return self.build_response(headers, "400 Bad Request")
        
        local_ip = self.local_ip_for(remote_media_ip)
        
        session = self.make_media_session(session_class, remote_media_ip, remote_media_port, generator, call_id, early_media_status)

        try:
            self.run_trigger_preflight(session)
        except Exception:
            traceback.print_exc()
            return self.build_503(headers, local_ip=local_ip, transport=transport)
        
        with self.lock:
            call = self.calls.get(call_id)
            if call is None:
                from_h = self.get_first(headers, "From")
                to_h = self.get_first(headers, "To")
                local_tag = uuid.uuid4().hex[:10]
                if "tag=" not in to_h.lower():
                    to_h = to_h + ";tag=" + local_tag
                contact = self.get_first(headers, "Contact")
                if not contact:
                    contact = f"sip:{source_ip}:{source_port}"
                
                call = {
                    "session": session,
                    "started": bool(early_media_status),
                    "headers": headers,
                    "remote_sip_ip": source_ip,
                    "remote_media_ip": remote_media_ip,
                    "remote_media_port": remote_media_port,
                    "remote_sip_port": source_port,
                    "local_ip": local_ip,
                    "local_port": session.local_port,
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
                    "early_media_status": early_media_status,
                    "record_routes": self.get_all(headers, "Record-Route"),
                    "trigger": trigger,
                    "user": user,
                }
                self.calls[call_id] = call

            if early_media_status:
                session.start()

        sdp = self.build_invite_sdp(call["local_ip"], call["local_port"], disable_video=call["has_video"])
        status = "183 Session Progress" if call["early_media_status"] else "200 OK"
        return self.build_response(headers, status, body=sdp, local_ip=call["local_ip"], transport=transport, call_tag=call["local_tag"])

    def handle_packet(self, data, addr, sock, tcp, conn=None):
        if not data.strip():
            return None
        transport = "tcp" if tcp else "udp"
        method, uri, version, headers, body = self.parse_request(data)
        
        if not method:
            return None
            
        if method == "SIP/2.0":
            status_code = uri
            call_id = self.get_first(headers, "Call-ID")
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
                nonce = uuid.uuid4().hex
                response = self.build_response(headers, "401 Unauthorized", extra_headers=[f'WWW-Authenticate: Digest realm="OpenPagingServer", nonce="{nonce}", algorithm=MD5, qop="auth"'])
        elif method == "INVITE":
            response = self.handle_invite(method, uri, headers, body, addr[0], addr[1], transport=transport, conn=conn)
        elif method == "ACK":
            call_id = self.get_first(headers, "Call-ID")
            if call_id:
                self.start_tone(call_id)
            response = None
        elif method == "BYE":
            call_id = self.get_first(headers, "Call-ID")
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
            try:
                self.handle_packet(data, addr, sock, False)
            except Exception as e:
                print(f"[ERROR] Exception in handle_packet: {e}")
                traceback.print_exc()

    def tcp_message(self, conn):
        conn.settimeout(2.0)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65535:
            try:
                chunk = conn.recv(4096)
                if not chunk:
                    return b""
                data += chunk
            except socket.timeout:
                if not data:
                    return None
                continue
            except:
                return b""
                
        if not data:
            return b""
            
        header_end = data.find(b"\r\n\r\n")
        if header_end == -1:
            return data
            
        head = data[:header_end].decode("utf-8", errors="ignore")
        content_length = 0
        for line in head.split("\r\n")[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                if name.strip().lower() == "content-length":
                    try:
                        content_length = int(value.strip())
                    except:
                        content_length = 0
                    break
                    
        body = data[header_end + 4:]
        while len(body) < content_length:
            try:
                chunk = conn.recv(4096)
                if not chunk:
                    return b""
                body += chunk
            except socket.timeout:
                continue
            except:
                return b""
                
        return data[:header_end + 4] + body[:content_length]

    def tcp_client(self, conn, addr):
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
        while not self.stop_event.is_set():
            for port in candidates:
                udp_sock, tcp_sock = self.try_bind_pair(port)
                if udp_sock is not None and tcp_sock is not None:
                    with self.lock:
                        self.udp_sock = udp_sock
                        self.tcp_sock = tcp_sock
                        self.active_port = port
                    threading.Thread(target=self.udp_loop, args=(udp_sock,), daemon=True).start()
                    threading.Thread(target=self.tcp_loop, args=(tcp_sock,), daemon=True).start()
                    return
            time.sleep(2)

    def manager(self):
        while not self.stop_event.is_set():
            enabled, port = self.get_settings()
            if not enabled:
                if self.enabled is not False:
                    self.close_sockets()
                self.enabled = False
                time.sleep(2)
                continue
            self.enabled = True
            if self.active_port != port or self.udp_sock is None or self.tcp_sock is None:
                self.close_sockets()
                self.bind_listeners(port)
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
        if self.manager_thread is None or not self.manager_thread.is_alive():
            self.stop_event.clear()
            self.manager_thread = threading.Thread(target=self.manager, daemon=True)
            self.manager_thread.start()
        if self.pinger_thread is None or not self.pinger_thread.is_alive():
            self.pinger_thread = threading.Thread(target=self.pinger_loop, daemon=True)
            self.pinger_thread.start()

    def shutdown(self):
        self.stop_event.set()
        self.close_sockets()
        with self.lock:
            for call_id in list(self.calls.keys()):
                call = self.calls.pop(call_id, None)
                if call is not None:
                    call["session"].stop()

sip_server = SipServer()

def start():
    sip_server.start()

def shutdown():
    sip_server.shutdown()

if __name__ == "__main__":
    start()
    while True:
        time.sleep(3600)

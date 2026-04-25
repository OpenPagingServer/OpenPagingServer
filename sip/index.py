import os
import sys
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
from pathlib import Path

import pymysql
from dotenv import load_dotenv

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
                    cur.execute("SELECT `trigger` FROM `endpoints-input-siptrunk` WHERE `extension` = %s LIMIT 1", (extension,))
                    row = cur.fetchone()
                    if row is not None:
                        return "found", row[0]
                    
                    if "#" in extension:
                        cur.execute("SELECT `trigger` FROM `endpoints-input-siptrunk` WHERE `extension` = %s LIMIT 1", (extension.replace("#", "%23"),))
                        row2 = cur.fetchone()
                        if row2 is not None:
                            return "found", row2[0]
                    elif "%23" in extension:
                        cur.execute("SELECT `trigger` FROM `endpoints-input-siptrunk` WHERE `extension` = %s LIMIT 1", (extension.replace("%23", "#"),))
                        row3 = cur.fetchone()
                        if row3 is not None:
                            return "found", row3[0]
                            
                    return "not_found", None
            finally:
                conn.close()
        except Exception:
            return "error", None

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

    def run_trigger_preflight(self, session):
        preflight = getattr(session, "preflight", None)
        if callable(preflight):
            preflight()
            if getattr(session, "setup_failed", False):
                raise SipCongestionError("SIP congestion")

    def build_invite_sdp(self, local_ip, local_port, disable_video=False):
        sdp = (
            "v=0\r\n"
            + f"o=OpenPagingServer 1 1 IN IP4 {local_ip}\r\n"
            + "s=OpenPagingServer\r\n"
            + f"c=IN IP4 {local_ip}\r\n"
            + "t=0 0\r\n"
            + f"m=audio {local_port} RTP/AVP 0\r\n"
            + "a=rtpmap:0 PCMU/8000\r\n"
        )
        if disable_video:
            sdp += "m=video 0 RTP/AVP 31\r\n"
        return sdp

    def build_bye(self, call):
        from_h = call["to_h"]
        to_h = call["from_h"]
        call_id = call["call_id"]
        uri = call["contact"]
        
        match = re.search(r'<([^>]+)>', uri)
        if match:
            uri = match.group(1)
        else:
            uri = uri.strip()
            
        lines = [
            f"BYE {uri} SIP/2.0",
            f"Via: SIP/2.0/{call['transport'].upper()} {call['local_ip']}:{self.active_port};branch=z9hG4bK{uuid.uuid4().hex[:10]}",
            f"From: {from_h}",
            f"To: {to_h}",
            f"Call-ID: {call_id}",
            "CSeq: 1 BYE",
            "Max-Forwards: 70",
            f"Contact: <sip:OpenPagingServer@{call['local_ip']}:{self.active_port};transport={call['transport']}>",
        ]
        
        for rr in reversed(call.get("record_routes", [])):
            lines.append(f"Route: {rr}")
            
        lines.extend([
            "User-Agent: OpenPagingServer",
            "Content-Length: 0",
            "\r\n",
        ])
        return "\r\n".join(lines)

    def finish_call(self, call_id):
        with self.lock:
            call = self.calls.pop(call_id, None)
        if call is not None:
            try:
                bye_packet = self.build_bye(call)
                data = bye_packet.encode("utf-8")
                addr = (call["remote_sip_ip"], call["remote_sip_port"])
                if call["transport"] == "tcp" and call["conn"]:
                    try:
                        call["conn"].sendall(data)
                    except:
                        pass
                elif call["transport"] == "udp" and self.udp_sock:
                    self.udp_sock.sendto(data, addr)
            except:
                pass

    def finish_early_media(self, call_id):
        with self.lock:
            call = self.calls.pop(call_id, None)
        if call is not None:
            try:
                status = call.get("early_media_status", "404 Not Found")
                response = self.build_response(call["headers"], status, local_ip=call["local_ip"], transport=call["transport"], call_tag=call["local_tag"])
                data = response.encode("utf-8")
                addr = (call["remote_sip_ip"], call["remote_sip_port"])
                if call["transport"] == "tcp" and call["conn"]:
                    try:
                        call["conn"].sendall(data)
                    except:
                        pass
                elif call["transport"] == "udp" and self.udp_sock:
                    self.udp_sock.sendto(data, addr)
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

    def handle_invite(self, method, uri, headers, body, source_ip, source_port, transport="udp", conn=None):
        call_id = self.get_first(headers, "Call-ID")
        if not call_id:
            return self.build_response(headers, "400 Bad Request")
        if not self.ip_allowed(method, source_ip, headers=headers):
            nonce = uuid.uuid4().hex
            return self.build_response(headers, "401 Unauthorized", extra_headers=[f'WWW-Authenticate: Digest realm="OpenPagingServer", nonce="{nonce}", algorithm=MD5, qop="auth"'])
        user = uri
        if ":" in user:
            user = user.split(":", 1)[1]
        if "@" in user:
            user = user.split("@", 1)[0]

        user = urllib.parse.unquote(user)
        
        db_status, trigger = self.get_endpoint_trigger(user)

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

        remote_media_ip, remote_media_port, has_video = self.parse_sdp_offer(body, source_ip)
        if not remote_media_port:
            return self.build_response(headers, "400 Bad Request")
        
        local_ip = self.local_ip_for(remote_media_ip)
        
        if session_class.__name__ == "RTPSession":
            try:
                session = session_class(
                    remote_media_ip,
                    remote_media_port,
                    payload_generator=generator,
                    on_finish=lambda cid=call_id: self.finish_early_media(cid) if early_media_status else self.finish_call(cid),
                )
            except TypeError:
                session = session_class(
                    remote_media_ip,
                    remote_media_port,
                    generator,
                    on_finish=lambda cid=call_id: self.finish_early_media(cid) if early_media_status else self.finish_call(cid),
                )
        else:
            try:
                session = session_class(
                    remote_media_ip,
                    remote_media_port,
                    generator=generator,
                    on_finish=lambda cid=call_id: self.finish_early_media(cid) if early_media_status else self.finish_call(cid),
                )
            except TypeError:
                session = session_class(
                    remote_media_ip,
                    remote_media_port,
                    generator,
                    on_finish=lambda cid=call_id: self.finish_early_media(cid) if early_media_status else self.finish_call(cid),
                )

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
                    "contact": contact,
                    "transport": transport,
                    "has_video": has_video,
                    "conn": conn,
                    "on_start": on_start,
                    "early_media_status": early_media_status,
                    "record_routes": self.get_all(headers, "Record-Route"),
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
                    if call and hasattr(call["session"], "dtmf_event_flag"):
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

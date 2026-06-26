import ipaddress
import json
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pymysql
from dotenv import load_dotenv
from waitress.server import create_server

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
CHARSET = "utf8mb4"
WEB_ERROR_DIR = BASE_DIR / "srv" / "web" / "errors"
MULTICAST_GATEWAY_PROVISION_PATH = "/.well-known/openpagingserver/multicast-gateway-provision"
MULTICAST_GATEWAY_FAILED_WINDOW_SECONDS = 24 * 60 * 60
MULTICAST_GATEWAY_BAN_SECONDS = 48 * 60 * 60
MULTICAST_GATEWAY_FAILED_LIMIT = 5
MAX_SPECIAL_JSON_BODY = 65536


def connect(cursorclass=pymysql.cursors.DictCursor):
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset=CHARSET,
        cursorclass=cursorclass,
        autocommit=False,
    )


pdo = connect


def db():
    return connect()


class MulticastGatewayProvisionBanStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.failures = {}
        self.banned_until = {}

    def _prune_failures_locked(self, now_value):
        cutoff = now_value - MULTICAST_GATEWAY_FAILED_WINDOW_SECONDS
        expired = []
        for ip, attempts in self.failures.items():
            kept = [attempt for attempt in attempts if attempt >= cutoff]
            if kept:
                self.failures[ip] = kept
            else:
                expired.append(ip)
        for ip in expired:
            self.failures.pop(ip, None)

    def is_banned(self, ip):
        now_value = time.time()
        with self.lock:
            until = self.banned_until.get(str(ip or ""))
            if until is None:
                return False
            if until > now_value:
                return True
            self.banned_until.pop(str(ip or ""), None)
            return False

    def note_success(self, ip):
        with self.lock:
            self.failures.pop(str(ip or ""), None)

    def note_failure(self, ip):
        now_value = time.time()
        banned = False
        banned_until = None
        with self.lock:
            self._prune_failures_locked(now_value)
            key = str(ip or "")
            attempts = list(self.failures.get(key) or [])
            attempts.append(now_value)
            self.failures[key] = attempts
            if len(attempts) >= MULTICAST_GATEWAY_FAILED_LIMIT:
                banned = True
                banned_until = now_value + MULTICAST_GATEWAY_BAN_SECONDS
                self.banned_until[key] = banned_until
                self.failures.pop(key, None)
        return banned, banned_until


multicast_gateway_ban_store = MulticastGatewayProvisionBanStore()


def read_web_settings():
    defaults = {"webserver_enable": "1", "webserver_http_port": "80", "api_http_enable": "0", "api_http_port": "8088"}
    if not all([DB_HOST, DB_USER, DB_NAME]):
        return defaults
    try:
        conn = db()
    except Exception as exc:
        print(f"webd database connection failed, using defaults: {exc}", flush=True)
        return defaults
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT parameter, value FROM systemsettings WHERE parameter IN ('webserver_enable','webserver_http_port','api_http_enable','api_http_port')")
            for row in cur.fetchall():
                defaults[str(row["parameter"])] = str(row["value"])
    finally:
        conn.close()
    return defaults


def enabled(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def port_value(value):
    try:
        port = int(str(value or "").strip())
    except ValueError:
        return 80
    if not 1 <= port <= 65535:
        return 80
    return port


def ports_to_try(configured_port):
    configured = port_value(configured_port)
    ports = [configured]
    if configured != 80:
        ports.append(80)
    return ports


def parse_proxy_allowlist(raw_value):
    tokens = []
    for part in str(raw_value or "").split(","):
        token = part.strip().strip("'").strip('"')
        if token:
            tokens.append(token)
    allowlist = []
    for token in tokens:
        try:
            if "/" in token:
                allowlist.append(ipaddress.ip_network(token, strict=False))
            else:
                allowlist.append(ipaddress.ip_address(token))
        except ValueError:
            print(f"webd ignoring invalid reverse proxy allowlist entry: {token}", flush=True)
    return tuple(allowlist)


def proxy_is_trusted(remote_addr, allowlist):
    try:
        remote_ip = ipaddress.ip_address(str(remote_addr or "").strip())
    except ValueError:
        return False
    for entry in allowlist:
        if isinstance(entry, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            if remote_ip == entry:
                return True
        elif remote_ip in entry:
            return True
    return False


def forwarded_for_client_ip(value):
    for part in str(value or "").split(","):
        token = part.strip()
        if not token:
            continue
        try:
            return str(ipaddress.ip_address(token))
        except ValueError:
            continue
    return ""


def request_client_ip(remote_addr, headers, allowlist):
    if using_reverse_proxy_headers(headers) and proxy_is_trusted(remote_addr, allowlist):
        forwarded = forwarded_for_client_ip((headers or {}).get("x-forwarded-for"))
        if forwarded:
            return forwarded
    return str(remote_addr or "").strip()


class ReverseProxyTrustMiddleware:
    def __init__(self, app, allowlist, denied_html):
        self.app = app
        self.allowlist = allowlist
        self.denied_html = denied_html

    def __call__(self, environ, start_response):
        using_reverse_proxy = any(
            environ.get(name)
            for name in (
                "HTTP_FORWARDED",
                "HTTP_VIA",
                "HTTP_X_FORWARDED_FOR",
                "HTTP_X_FORWARDED_PROTO",
                "HTTP_X_FORWARDED_HOST",
                "HTTP_X_FORWARDED_PORT",
                "HTTP_X_FORWARDED_SERVER",
                "HTTP_X_REAL_IP",
                "HTTP_X_PROXYUSER_IP",
                "HTTP_TRUE_CLIENT_IP",
                "HTTP_CF_CONNECTING_IP",
                "HTTP_CF_RAY",
            )
        )
        remote_addr = environ.get("HTTP_X_OPS_REMOTE_ADDR") or environ.get("REMOTE_ADDR", "")
        if remote_addr:
            environ["REMOTE_ADDR"] = remote_addr
        if using_reverse_proxy:
            if not proxy_is_trusted(remote_addr, self.allowlist):
                body = self.denied_html
                start_response(
                    "403 Forbidden",
                    [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(body))),
                    ],
                )
                return [body]
            forwarded_proto = str(environ.get("HTTP_X_FORWARDED_PROTO") or "").split(",", 1)[0].strip()
            forwarded_host = str(environ.get("HTTP_X_FORWARDED_HOST") or "").split(",", 1)[0].strip()
            forwarded_port = str(environ.get("HTTP_X_FORWARDED_PORT") or "").split(",", 1)[0].strip()
            if forwarded_proto:
                environ["wsgi.url_scheme"] = forwarded_proto
                environ["HTTPS"] = "on" if forwarded_proto == "https" else "off"
            if forwarded_host:
                environ["HTTP_HOST"] = forwarded_host
                if ":" in forwarded_host:
                    host_name, host_port = forwarded_host.rsplit(":", 1)
                    environ["SERVER_NAME"] = host_name
                    if host_port.isdigit():
                        environ["SERVER_PORT"] = host_port
                else:
                    environ["SERVER_NAME"] = forwarded_host
            elif forwarded_port and forwarded_port.isdigit():
                environ["SERVER_PORT"] = forwarded_port
            forwarded_for = forwarded_for_client_ip(environ.get("HTTP_X_FORWARDED_FOR"))
            if forwarded_for:
                environ["REMOTE_ADDR"] = forwarded_for
        return self.app(environ, start_response)


class StripServerHeaderMiddleware:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        def filtered_start_response(status, headers, exc_info=None):
            filtered = [(name, value) for name, value in headers if name.lower() != "server"]
            return start_response(status, filtered, exc_info)

        return self.app(environ, filtered_start_response)


def load_reverse_proxy_denied_html():
    path = WEB_ERROR_DIR / "503-RP.html"
    if path.is_file():
        return path.read_bytes()
    return b"<html><body><h1>403 Forbidden</h1><p>This request came through an untrusted reverse proxy.</p></body></html>"


def create_waitress_server(app, port, trusted_proxy_allowlist=(), denied_html=None):
    denied_html = denied_html if denied_html is not None else load_reverse_proxy_denied_html()
    wrapped = ReverseProxyTrustMiddleware(app, trusted_proxy_allowlist, denied_html)
    wrapped = StripServerHeaderMiddleware(wrapped)
    return create_server(wrapped, host="127.0.0.1", port=port, ident="")


def recv_until(sock, marker, limit=65536):
    data = b""
    while marker not in data and len(data) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def split_head_body(data):
    head, sep, body = bytes(data or b"").partition(b"\r\n\r\n")
    if not sep:
        return bytes(data or b""), b""
    return head + sep, body


def parse_request_head(head_bytes):
    text = head_bytes.decode("iso-8859-1", errors="ignore")
    head, _, _ = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    request_line = lines[0] if lines else ""
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    parts = request_line.split()
    target = parts[1] if len(parts) >= 2 else "/"
    parsed = urlsplit(target)
    return request_line, parsed.path or "/", headers


def request_method(request_line):
    parts = str(request_line or "").split()
    return parts[0].upper() if parts else "GET"


def content_length_value(headers):
    raw = str((headers or {}).get("content-length") or "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return -1
    return value if value >= 0 else -1


def recv_request_body(sock, initial_body, content_length, limit=MAX_SPECIAL_JSON_BODY):
    if content_length < 0 or content_length > limit:
        raise ValueError("Request body is too large.")
    data = bytearray(initial_body or b"")
    while len(data) < content_length:
        chunk = sock.recv(min(65536, content_length - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data[:content_length])


def status_text(status_code):
    return {
        200: "OK",
        400: "Bad Request",
        401: "Unauthorized",
        405: "Method Not Allowed",
        413: "Payload Too Large",
        500: "Internal Server Error",
    }.get(int(status_code), "OK")


def send_json_response(client, status_code, payload, head_only=False):
    body = json.dumps(payload).encode("utf-8")
    header = (
        f"HTTP/1.1 {int(status_code)} {status_text(status_code)}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("iso-8859-1")
    client.sendall(header if head_only else (header + body))


def normalized_request_host(headers):
    raw = str((headers or {}).get("x-forwarded-host") or (headers or {}).get("host") or "").split(",", 1)[0].strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlsplit(raw)
        return str(parsed.hostname or "").strip()
    if raw.startswith("[") and "]" in raw:
        return raw[1:].split("]", 1)[0].strip()
    if raw.count(":") == 1:
        return raw.split(":", 1)[0].strip()
    return raw


def query_one(sql, params=None):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()
    finally:
        conn.close()


def execute(sql, params=None):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()


def load_multicast_gateway_provision_handler():
    from multicastgatewayd import provision_gateway_peer

    return provision_gateway_peer


def using_reverse_proxy_headers(headers):
    return any(
        headers.get(name)
        for name in (
            "forwarded",
            "via",
            "x-forwarded-for",
            "x-forwarded-proto",
            "x-forwarded-host",
            "x-forwarded-port",
            "x-forwarded-server",
            "x-real-ip",
            "x-proxyuser-ip",
            "true-client-ip",
            "cf-connecting-ip",
            "cf-ray",
        )
    )


def rewrite_request_head(head_bytes, extra_headers):
    text = head_bytes.decode("iso-8859-1", errors="ignore")
    head, sep, tail = text.partition("\r\n\r\n")
    if not sep:
        return head_bytes
    lines = head.split("\r\n")
    present = {line.split(":", 1)[0].strip().lower() for line in lines[1:] if ":" in line}
    for name, value in extra_headers.items():
        if name.lower() not in present:
            lines.append(f"{name}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n" + tail).encode("iso-8859-1", errors="ignore")


def relay_stream(source, target):
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            target.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            target.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def reject_forbidden(client, denied_html):
    body = denied_html
    client.sendall(
        b"HTTP/1.1 403 Forbidden\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )


class FrontServer:
    def __init__(self, app, port):
        self.allowlist = parse_proxy_allowlist(os.getenv("WEB_REVERSE_PROXY_ALLOWED"))
        self.denied_html = load_reverse_proxy_denied_html()
        self.internal_server = create_waitress_server(app, 0, self.allowlist, self.denied_html)
        self.internal_port = self.internal_server.effective_port
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("0.0.0.0", port))
        self.listener.listen(100)
        self.effective_port = self.listener.getsockname()[1]
        self._closed = threading.Event()
        self._internal_thread = None

    def close(self):
        self._closed.set()
        try:
            self.listener.close()
        except OSError:
            pass
        try:
            self.internal_server.close()
        except OSError:
            pass

    def run(self):
        self._internal_thread = threading.Thread(target=self.internal_server.run, daemon=True)
        self._internal_thread.start()
        while not self._closed.is_set():
            try:
                client, addr = self.listener.accept()
            except OSError:
                break
            threading.Thread(target=self.handle_client, args=(client, addr), daemon=True).start()

    def handle_client(self, client, addr):
        upstream = None
        try:
            remote_addr = addr[0]
            if multicast_gateway_ban_store.is_banned(remote_addr):
                client.close()
                return
            head = recv_until(client, b"\r\n\r\n")
            if not head:
                client.close()
                return
            request_line, path, headers = parse_request_head(head)
            if using_reverse_proxy_headers(headers) and not proxy_is_trusted(remote_addr, self.allowlist):
                reject_forbidden(client, self.denied_html)
                client.close()
                return
            client_ip = request_client_ip(remote_addr, headers, self.allowlist)
            if client_ip != remote_addr and multicast_gateway_ban_store.is_banned(client_ip):
                client.close()
                return
            if path == MULTICAST_GATEWAY_PROVISION_PATH:
                self.handle_multicast_gateway_provision(client, client_ip, request_line, headers, head)
                return
            if path == "/live" and "websocket" in str(headers.get("upgrade") or "").lower():
                from livepaged import handle_websocket_client

                handle_websocket_client(client, addr, head)
                return
            if path == "/desktop/ws" and "websocket" in str(headers.get("upgrade") or "").lower():
                from clientd import handle_desktop_websocket_client

                handle_desktop_websocket_client(client, addr, head)
                return
            upstream = socket.create_connection(("127.0.0.1", self.internal_port), timeout=10)
            upstream.sendall(rewrite_request_head(head, {"X-Ops-Remote-Addr": remote_addr}))
            t1 = threading.Thread(target=relay_stream, args=(client, upstream), daemon=True)
            t2 = threading.Thread(target=relay_stream, args=(upstream, client), daemon=True)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        except OSError:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass

    def handle_multicast_gateway_provision(self, client, client_ip, request_line, headers, head):
        method = request_method(request_line)
        if method in {"GET", "HEAD"}:
            send_json_response(
                client,
                200,
                {
                    "status": "success",
                    "service": "multicast-gateway-provision",
                    "path": MULTICAST_GATEWAY_PROVISION_PATH,
                },
                head_only=(method == "HEAD"),
            )
            return
        if method != "POST":
            send_json_response(client, 405, {"status": "error", "message": "Method not allowed."})
            return
        content_length = content_length_value(headers)
        if content_length < 0:
            send_json_response(client, 400, {"status": "error", "message": "Invalid content length."})
            return
        if content_length > MAX_SPECIAL_JSON_BODY:
            send_json_response(client, 413, {"status": "error", "message": "Request body is too large."})
            return
        _head_only, initial_body = split_head_body(head)
        try:
            body = recv_request_body(client, initial_body, content_length)
            payload = json.loads(body.decode("utf-8") or "{}") if content_length else {}
            if not isinstance(payload, dict):
                raise ValueError("JSON payload must be an object.")
        except Exception as exc:
            send_json_response(client, 400, {"status": "error", "message": str(exc)})
            return
        try:
            provision_gateway_peer = load_multicast_gateway_provision_handler()
            status_code, response_payload = provision_gateway_peer(
                query_one,
                execute,
                payload,
                client_ip,
                normalized_request_host(headers),
            )
        except Exception as exc:
            send_json_response(client, 500, {"status": "error", "message": str(exc)})
            return
        if int(status_code) == 401:
            banned, _banned_until = multicast_gateway_ban_store.note_failure(client_ip)
            if banned:
                print(f"webd banned {client_ip} after repeated multicast gateway provisioning failures", flush=True)
        elif int(status_code) < 400:
            multicast_gateway_ban_store.note_success(client_ip)
        send_json_response(client, status_code, response_payload)


def create_front_server(app, port):
    return FrontServer(app, port)


def server_ports(label, configured_port):
    if label == "web":
        return ports_to_try(configured_port)
    return [port_value(configured_port)]


def build_servers(settings):
    servers = []
    if enabled(settings.get("webserver_enable")):
        from srv.web.app import app as web_app

        servers.append(("web", "Open Paging Server", web_app, server_ports("web", settings.get("webserver_http_port"))))
    if enabled(settings.get("api_http_enable")):
        from srv.api.app import app as api_app

        servers.append(("api", "Open Paging Server API", api_app, server_ports("api", settings.get("api_http_port"))))
    return servers


def main():
    settings = read_web_settings()
    specs = build_servers(settings)
    if not specs:
        print("webd disabled because both web and API listeners are off", flush=True)
        return 0

    last_errors = {}
    while True:
        running = []
        failed = False
        try:
            for label, title, app, ports in specs:
                server = None
                for port in ports:
                    try:
                        server = create_front_server(app, port)
                        print(f"webd serving {title} on http://0.0.0.0:{server.effective_port}", flush=True)
                        break
                    except (OSError, socket.error) as exc:
                        last_errors[label] = exc
                        print(f"webd {label} port {port} unavailable: {exc}", flush=True)
                if server is None:
                    failed = True
                    break
                thread = threading.Thread(target=server.run, daemon=True)
                thread.start()
                running.append((server, thread))
            if not failed:
                for _server, thread in running:
                    thread.join()
                return 0
        finally:
            for server, _thread in running:
                server.close()
        wait_bits = []
        for label, _title, _app, ports in specs:
            last_error = last_errors.get(label)
            wait_bits.append(f"{label} ports {', '.join(map(str, ports))}: {last_error}")
        print("webd waiting for ports to become available; " + "; ".join(wait_bits), flush=True)
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())

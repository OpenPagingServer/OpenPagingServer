#!/usr/bin/env python3

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
import sqlite3
import struct
import tempfile
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DEFAULT_PORT = int(os.getenv("OPS_MULTICAST_GATEWAY_PORT", "8710"))
HELLO_INTERVAL_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_HELLO_INTERVAL", "10"))
ONLINE_WINDOW_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_ONLINE_WINDOW", "30"))
DEDUP_WINDOW_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_DEDUP_WINDOW", "3"))
PRESENCE_UPDATE_INTERVAL_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_PRESENCE_INTERVAL", "5"))
PEER_ADDRESS_CACHE_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_ADDRESS_CACHE", "30"))
MAX_LABEL_LENGTH = 255
MAX_HOST_LENGTH = 255
KEY_TEXT_LENGTH = 128
MULTICAST_GATEWAY_PROVISION_PATH = "/.well-known/openpagingserver/multicast-gateway-provision"
CODE_HELLO = b"01"
CODE_MEDIA = b"02"
CODE_LOCAL_SOURCE = b"03"
NODE_KIND_OPS = 1
NODE_KIND_GATEWAY = 2


def default_ops_key_path():
    if os.name == "nt":
        return PROJECT_ROOT / "runtime" / ".mg.key"
    return Path("/etc/openpagingserver/.mg.key")


def default_gateway_state_dir(base_dir=None):
    root = Path(base_dir) if base_dir else BASE_DIR
    if os.name == "nt":
        return root
    return root


def default_gateway_key_path(base_dir=None):
    return default_gateway_state_dir(base_dir) / ".mg.key"


def default_gateway_db_path(base_dir=None):
    return default_gateway_state_dir(base_dir) / "multicastgateway.sqlite3"


def default_gateway_lock_path(base_dir=None):
    return default_gateway_state_dir(base_dir) / "multicastgateway.lock"


def ensure_parent(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _b64_encode(value):
    return base64.urlsafe_b64encode(bytes(value)).decode("ascii").rstrip("=")


def _b64_decode(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("Public key is required.")
    padding = "=" * ((4 - (len(text) % 4)) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def normalize_label(value):
    return str(value or "").strip()[:MAX_LABEL_LENGTH]


def normalize_host(value):
    return str(value or "").strip()[:MAX_HOST_LENGTH]


def normalize_public_key(value):
    raw = _b64_decode(value)
    if len(raw) != 32:
        raise ValueError("Public key is invalid.")
    return _b64_encode(raw)


def parse_peer_target(value, default_port=DEFAULT_PORT):
    text = normalize_host(value)
    if not text:
        raise ValueError("Server address is required.")
    if text.startswith("[") and "]" in text:
        host, _, rest = text[1:].partition("]")
        port = default_port
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
        return host.strip(), port
    if text.count(":") == 1 and "." in text.split(":", 1)[0]:
        host, port_text = text.split(":", 1)
        return host.strip(), int(port_text)
    return text, default_port


def peer_status_from_timestamp(value, now=None, online_window=ONLINE_WINDOW_SECONDS):
    if value is None:
        return "Offline"
    now_value = float(now if now is not None else time.time())
    if (now_value - float(value)) <= float(online_window):
        return "Online"
    return "Offline"


def ensure_private_key_file(path):
    path = Path(path)
    ensure_parent(path)
    if path.exists():
        return path
    private_key = x25519.X25519PrivateKey.generate()
    raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(_b64_encode(raw) + "\n", encoding="utf-8")
    try:
        os.chmod(temp_path, 0o600)
    except OSError:
        pass
    temp_path.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_identity(path):
    path = ensure_private_key_file(path)
    raw = _b64_decode(path.read_text(encoding="utf-8").strip())
    if len(raw) != 32:
        raise ValueError(f"Invalid multicast gateway private key at {path}")
    private_key = x25519.X25519PrivateKey.from_private_bytes(raw)
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "key_path": Path(path),
        "private_key": private_key,
        "public_key_bytes": public_bytes,
        "public_key": _b64_encode(public_bytes),
        "_crypto_cache": {},
        "_crypto_lock": threading.Lock(),
    }


def public_key_text_from_bytes(value):
    raw = bytes(value or b"")
    if len(raw) != 32:
        raise ValueError("Public key is invalid.")
    return _b64_encode(raw)


def public_key_bytes_from_text(value):
    raw = _b64_decode(value)
    if len(raw) != 32:
        raise ValueError("Public key is invalid.")
    return raw


def public_key_preview(value, prefix=10):
    text = normalize_public_key(value)
    if len(text) <= prefix + 3:
        return text
    return text[:prefix] + "..."


def _shared_key(local_private_key, local_public_bytes, peer_public_bytes):
    shared_secret = local_private_key.exchange(x25519.X25519PublicKey.from_public_bytes(peer_public_bytes))
    ordered = sorted([bytes(local_public_bytes), bytes(peer_public_bytes)])
    salt = hashlib.sha256(ordered[0] + ordered[1]).digest()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"openpagingserver-multicastgateway-v1",
    ).derive(shared_secret)


def _peer_crypto_context(local_identity, peer_public_key=None, peer_public_bytes=None):
    if peer_public_bytes is None:
        peer_public_bytes = public_key_bytes_from_text(peer_public_key)
    if peer_public_key is None:
        peer_public_key = public_key_text_from_bytes(peer_public_bytes)
    cache_key = str(peer_public_key)
    cache = local_identity.setdefault("_crypto_cache", {})
    lock = local_identity.setdefault("_crypto_lock", threading.Lock())
    with lock:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        local_public_bytes = local_identity["public_key_bytes"]
        shared_key = _shared_key(local_identity["private_key"], local_public_bytes, peer_public_bytes)
        context = {
            "peer_public_key": cache_key,
            "peer_public_bytes": peer_public_bytes,
            "aead": AESGCM(shared_key),
            "encode_aad_suffix": local_public_bytes + peer_public_bytes,
            "decode_aad_suffix": peer_public_bytes + local_public_bytes,
        }
        cache[cache_key] = context
        return context


def encode_secure_packet(code, local_identity, peer_public_key, body):
    code = bytes(code or b"")
    if len(code) != 2:
        raise ValueError("Packet code must be 2 bytes.")
    context = _peer_crypto_context(local_identity, peer_public_key=peer_public_key)
    sender_public_bytes = local_identity["public_key_bytes"]
    nonce = secrets.token_bytes(12)
    aad = code + context["encode_aad_suffix"]
    ciphertext = context["aead"].encrypt(nonce, bytes(body or b""), aad)
    return code + sender_public_bytes + nonce + ciphertext


def decode_secure_packet(packet, local_identity, trusted_peers):
    data = bytes(packet or b"")
    if len(data) < 46:
        raise ValueError("Packet is too short.")
    code = data[:2]
    sender_public_bytes = data[2:34]
    sender_public_key = public_key_text_from_bytes(sender_public_bytes)
    peer = trusted_peers.get(sender_public_key)
    if not peer:
        raise KeyError("Unknown peer.")
    nonce = data[34:46]
    ciphertext = data[46:]
    context = _peer_crypto_context(local_identity, peer_public_key=sender_public_key, peer_public_bytes=sender_public_bytes)
    aad = code + context["decode_aad_suffix"]
    body = context["aead"].decrypt(nonce, ciphertext, aad)
    return code, sender_public_key, body, peer


def encode_hello_body(node_kind, label):
    label_bytes = normalize_label(label).encode("utf-8")
    if len(label_bytes) > 255:
        label_bytes = label_bytes[:255]
    return struct.pack("!BB", int(node_kind), len(label_bytes)) + label_bytes


def decode_hello_body(body):
    data = bytes(body or b"")
    if len(data) < 2:
        raise ValueError("Hello payload is invalid.")
    node_kind = data[0]
    label_length = data[1]
    label = data[2:2 + label_length].decode("utf-8", errors="ignore")
    return {"node_kind": node_kind, "label": label}


def normalize_multicast_destination(address, port, family=None, ttl=None):
    host = str(address or "").split("%", 1)[0].strip()
    if not host:
        raise ValueError("Multicast address is required.")
    ip = ipaddress.ip_address(host)
    if not ip.is_multicast:
        raise ValueError("Multicast address is invalid.")
    port_value = int(port or 0)
    if port_value < 1 or port_value > 65535:
        raise ValueError("Multicast port is invalid.")
    family_value = 6 if (family == socket.AF_INET6 or ip.version == 6) else 4
    ttl_value = 1
    if ttl not in (None, ""):
        ttl_value = max(0, min(255, int(ttl)))
    return {"address": str(ip), "port": port_value, "family": family_value, "ttl": ttl_value}


def encode_media_body(address, port, payload, family=None, ttl=None):
    normalized = normalize_multicast_destination(address, port, family=family, ttl=ttl)
    ip_obj = ipaddress.ip_address(normalized["address"])
    addr_bytes = ip_obj.packed
    return (
        struct.pack("!BBHB", normalized["family"], normalized["ttl"], normalized["port"], len(addr_bytes))
        + addr_bytes
        + bytes(payload or b"")
    )


def decode_media_body(body):
    data = bytes(body or b"")
    if len(data) < 5:
        raise ValueError("Media payload is invalid.")
    family, ttl, port, addr_len = struct.unpack("!BBHB", data[:5])
    if addr_len not in {4, 16}:
        raise ValueError("Media address is invalid.")
    if len(data) < 5 + addr_len:
        raise ValueError("Media payload is truncated.")
    addr_bytes = data[5:5 + addr_len]
    payload = data[5 + addr_len:]
    address = str(ipaddress.ip_address(addr_bytes))
    return {"family": family, "ttl": ttl, "port": port, "address": address, "payload": payload}


def encode_local_source_packet(address, port, payload, family=None, ttl=None):
    return CODE_LOCAL_SOURCE + encode_media_body(address, port, payload, family=family, ttl=ttl)


def decode_local_source_packet(packet):
    data = bytes(packet or b"")
    if not data.startswith(CODE_LOCAL_SOURCE):
        raise ValueError("Local source packet is invalid.")
    return decode_media_body(data[2:])


def default_ipv4_multicast_interface():
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 53))
        address = str(probe.getsockname()[0] or "").strip()
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    finally:
        if probe is not None:
            probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM):
            address = str(info[4][0] or "").strip()
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    return None


_rebroadcast_sockets = {}
_rebroadcast_lock = threading.Lock()


def _rebroadcast_socket_for_family(family):
    with _rebroadcast_lock:
        sock = _rebroadcast_sockets.get(family)
        if sock is not None:
            return sock
        if family == 6:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            interface = default_ipv4_multicast_interface()
            if interface:
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
                except OSError:
                    pass
        _rebroadcast_sockets[family] = sock
        return sock


def rebroadcast_multicast_packet(address, port, payload, family=None, ttl=None):
    normalized = normalize_multicast_destination(address, port, family=family, ttl=ttl)
    payload_bytes = bytes(payload or b"")
    if not payload_bytes:
        return
    if normalized["family"] == 6:
        sock = _rebroadcast_socket_for_family(6)
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, normalized["ttl"])
        except OSError:
            pass
        sock.sendto(payload_bytes, (normalized["address"], normalized["port"], 0, 0))
        return
    sock = _rebroadcast_socket_for_family(4)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, normalized["ttl"])
    except OSError:
        pass
    sock.sendto(payload_bytes, (normalized["address"], normalized["port"]))


class DuplicatePacketFilter:
    def __init__(self, window_seconds=DEDUP_WINDOW_SECONDS):
        self.window_seconds = float(window_seconds)
        self.lock = threading.Lock()
        self.entries = {}

    def _prune(self, now_value):
        expired = [key for key, seen_at in self.entries.items() if now_value - seen_at > self.window_seconds]
        for key in expired:
            self.entries.pop(key, None)

    def seen(self, address, port, payload):
        digest = hashlib.sha256(
            str(address).encode("utf-8") + b"|" + str(int(port or 0)).encode("ascii") + b"|" + bytes(payload or b"")
        ).digest()[:16]
        now_value = time.monotonic()
        with self.lock:
            self._prune(now_value)
            previous = self.entries.get(digest)
            self.entries[digest] = now_value
        return previous is not None


class FileInstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        ensure_parent(self.path)
        self.handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.close()
            return False
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()).encode("ascii"))
        self.handle.flush()
        return True

    def close(self):
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self.handle.close()
        except OSError:
            pass
        self.handle = None


class GatewayPeerStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)

    def _connect(self):
        ensure_parent(self.db_path)
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS peers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL DEFAULT '',
                host TEXT NOT NULL DEFAULT '',
                port INTEGER NOT NULL DEFAULT 8710,
                public_key TEXT NOT NULL UNIQUE,
                peer_type TEXT NOT NULL DEFAULT 'gateway',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_seen REAL DEFAULT NULL,
                last_ip TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
                updated_at REAL NOT NULL DEFAULT (strftime('%s','now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                parameter TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
            """
        )
        return conn

    def list_peers(self):
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, label, host, port, public_key, peer_type, enabled, last_seen, last_ip, created_at, updated_at
                FROM peers
                ORDER BY LOWER(label), id
                """
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def peer_map(self):
        rows = self.list_peers()
        mapping = {}
        for row in rows:
            if int(row.get("enabled") or 0) != 1:
                continue
            key = normalize_public_key(row.get("public_key"))
            row["public_key"] = key
            mapping[key] = row
        return mapping

    def upsert_peer(self, label, host, port, public_key, peer_type="gateway", enabled=1):
        label_value = normalize_label(label)
        host_value = normalize_host(host)
        port_value = int(port or DEFAULT_PORT)
        key_value = normalize_public_key(public_key)
        peer_type_value = str(peer_type or "gateway").strip().lower() or "gateway"
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO peers (label, host, port, public_key, peer_type, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(public_key) DO UPDATE SET
                    label=excluded.label,
                    host=excluded.host,
                    port=excluded.port,
                    peer_type=excluded.peer_type,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (label_value, host_value, port_value, key_value, peer_type_value, int(enabled), time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_peer(self, peer_id):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM peers WHERE id = ?", (int(peer_id),))
            conn.commit()
        finally:
            conn.close()

    def update_presence(self, public_key, last_ip, seen_at=None):
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE peers SET last_seen = ?, last_ip = ?, updated_at = ? WHERE public_key = ?",
                (float(seen_at if seen_at is not None else time.time()), normalize_host(last_ip), time.time(), normalize_public_key(public_key)),
            )
            conn.commit()
        finally:
            conn.close()

    def get_setting(self, parameter, default=""):
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM settings WHERE parameter = ?", (str(parameter or ""),)).fetchone()
            if row is None:
                return default
            return str(row["value"] if "value" in row.keys() else row[0])
        finally:
            conn.close()

    def set_setting(self, parameter, value):
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO settings (parameter, value)
                VALUES (?, ?)
                ON CONFLICT(parameter) DO UPDATE SET value = excluded.value
                """,
                (str(parameter or ""), str(value or "")),
            )
            conn.commit()
        finally:
            conn.close()


def host_port_for_display(host, port):
    host_value = normalize_host(host)
    port_value = int(port or DEFAULT_PORT)
    if ":" in host_value and not host_value.startswith("["):
        return f"[{host_value}]:{port_value}"
    return f"{host_value}:{port_value}"


def secure_compare(left, right):
    return hmac.compare_digest(str(left or ""), str(right or ""))


def monotonic_now():
    return time.monotonic()

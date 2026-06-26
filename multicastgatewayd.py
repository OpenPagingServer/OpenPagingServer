#!/usr/bin/env python3

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import signal
import socket
import struct
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import pymysql
except Exception:
    pymysql = None

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import x25519
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except Exception:
    hashes = None
    serialization = None
    x25519 = None
    AESGCM = None
    HKDF = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = int(os.getenv("OPS_MULTICAST_GATEWAY_PORT", "8710"))
HELLO_INTERVAL_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_HELLO_INTERVAL", "10"))
ONLINE_WINDOW_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_ONLINE_WINDOW", "30"))
DEDUP_WINDOW_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_DEDUP_WINDOW", "3"))
PRESENCE_UPDATE_INTERVAL_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_PRESENCE_INTERVAL", "5"))
PEER_ADDRESS_CACHE_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_ADDRESS_CACHE", "30"))
MAX_LABEL_LENGTH = 255
MAX_HOST_LENGTH = 255
MULTICAST_GATEWAY_PROVISION_PATH = "/.well-known/openpagingserver/multicast-gateway-provision"
CODE_HELLO = b"01"
CODE_MEDIA = b"02"
CODE_LOCAL_SOURCE = b"03"
NODE_KIND_OPS = 1
NODE_KIND_GATEWAY = 2

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
DEFAULT_LOCK_PATH = Path(
    os.getenv(
        "OPS_MULTICAST_GATEWAY_LOCK_PATH",
        str((BASE_DIR / "runtime" / "multicastgateway.lock") if os.name == "nt" else (Path(tempfile.gettempdir()) / "openpagingserver-multicastgateway.lock")),
    )
)
TABLE_NAME = "multicast_gateway_servers"


def require_crypto():
    if all(value is not None for value in (hashes, serialization, x25519, AESGCM, HKDF)):
        return
    raise RuntimeError("Multicast Gateway requires the Python 'cryptography' package.")


def default_ops_key_path():
    if os.name == "nt":
        return BASE_DIR / "runtime" / ".mg.key"
    return Path("/etc/openpagingserver/.mg.key")


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
    require_crypto()
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
    require_crypto()
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
    require_crypto()
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
    require_crypto()
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
    require_crypto()
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
    require_crypto()
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


def host_port_for_display(host, port):
    host_value = normalize_host(host)
    port_value = int(port or DEFAULT_PORT)
    if ":" in host_value and not host_value.startswith("["):
        return f"[{host_value}]:{port_value}"
    return f"{host_value}:{port_value}"


def secure_compare(left, right):
    return hmac.compare_digest(str(left or ""), str(right or ""))


def listener_bind_specs(host, port):
    value = str(host or "").strip()
    if value in {"", "*", "0.0.0.0"}:
        specs = [(socket.AF_INET, ("0.0.0.0", port))]
        if socket.has_ipv6:
            specs.append((socket.AF_INET6, ("::", port, 0, 0)))
        return specs
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    if ip.version == 6:
        return [(socket.AF_INET6, (value, port, 0, 0))]
    return [(socket.AF_INET, (value, port))]


def create_bound_socket(family, bind_addr):
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6 and hasattr(socket, "IPPROTO_IPV6") and hasattr(socket, "IPV6_V6ONLY"):
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except OSError:
            pass
    sock.bind(bind_addr)
    sock.settimeout(1.0)
    return sock


def format_bind_addr(address):
    if isinstance(address, tuple) and len(address) >= 4:
        return f"[{address[0]}]:{address[1]}"
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return str(address)


def resolve_peer_addresses(host, port):
    targets = []
    try:
        for family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_DGRAM):
            if family == socket.AF_INET:
                targets.append((family, (sockaddr[0], sockaddr[1])))
            elif family == socket.AF_INET6:
                targets.append((family, (sockaddr[0], sockaddr[1], 0, 0)))
    except OSError:
        return []
    seen = set()
    unique = []
    for item in targets:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def monotonic_now():
    return time.monotonic()


def parse_ipv4_multicast_udp_frame(frame):
    data = bytes(frame or b"")
    if len(data) < 42:
        return None
    ether_type = struct.unpack("!H", data[12:14])[0]
    if ether_type != 0x0800:
        return None
    version_ihl = data[14]
    if version_ihl >> 4 != 4:
        return None
    ihl = (version_ihl & 0x0F) * 4
    if ihl < 20 or len(data) < 14 + ihl + 8:
        return None
    protocol = data[23]
    if protocol != 17:
        return None
    ttl = data[22]
    destination = str(ipaddress.ip_address(data[30:34]))
    if not ipaddress.ip_address(destination).is_multicast:
        return None
    udp_offset = 14 + ihl
    destination_port = struct.unpack("!H", data[udp_offset + 2:udp_offset + 4])[0]
    udp_length = struct.unpack("!H", data[udp_offset + 4:udp_offset + 6])[0]
    if udp_length < 8 or len(data) < udp_offset + udp_length:
        return None
    payload = data[udp_offset + 8:udp_offset + udp_length]
    return {"address": destination, "port": destination_port, "ttl": ttl, "family": 4, "payload": payload}


class GatewayRuntime:
    def __init__(
        self,
        identity,
        peer_store,
        node_kind,
        local_label,
        bind_host="0.0.0.0",
        port=DEFAULT_PORT,
        enable_capture=False,
        enable_rebroadcast=True,
        allow_local_source=False,
        log=None,
    ):
        self.identity = identity
        self.peer_store = peer_store
        self.node_kind = int(node_kind or NODE_KIND_GATEWAY)
        self.local_label = str(local_label or "").strip()
        self.bind_host = str(bind_host or "0.0.0.0").strip() or "0.0.0.0"
        self.port = int(port or DEFAULT_PORT)
        self.enable_capture = bool(enable_capture)
        self.enable_rebroadcast = bool(enable_rebroadcast)
        self.allow_local_source = bool(allow_local_source)
        self.log = log or (lambda msg: None)
        self.stop_event = threading.Event()
        self.duplicate_filter = DuplicatePacketFilter()
        self.server_sockets = {}
        self.peer_cache = {}
        self.peer_cache_at = 0.0
        self.peer_cache_lock = threading.Lock()
        self.peer_address_cache = {}
        self.peer_address_cache_lock = threading.Lock()
        self.presence_cache = {}
        self.presence_cache_lock = threading.Lock()

    def refresh_peers(self, force=False):
        now_value = monotonic_now()
        with self.peer_cache_lock:
            if not force and (now_value - self.peer_cache_at) < 2.0:
                return self.peer_cache
            self.peer_cache = self.peer_store.peer_map()
            self.peer_cache_at = now_value
            return self.peer_cache

    def _resolve_peer_addresses(self, host, port, force=False):
        host_value = str(host or "").strip()
        port_value = int(port or self.port)
        if not host_value:
            return []
        cache_key = (host_value, port_value)
        now_value = monotonic_now()
        with self.peer_address_cache_lock:
            cached = self.peer_address_cache.get(cache_key)
            if cached is not None and not force and (now_value - cached[0]) < PEER_ADDRESS_CACHE_SECONDS:
                return cached[1]
        targets = resolve_peer_addresses(host_value, port_value)
        with self.peer_address_cache_lock:
            self.peer_address_cache[cache_key] = (now_value, targets)
        return targets

    def _note_peer_presence(self, public_key, source_ip):
        key_value = str(public_key or "").strip()
        ip_value = str(source_ip or "").split("%", 1)[0].strip()
        if not key_value or not ip_value:
            return
        now_value = monotonic_now()
        with self.presence_cache_lock:
            cached = self.presence_cache.get(key_value)
            if cached is not None and cached[0] == ip_value and (now_value - cached[1]) < PRESENCE_UPDATE_INTERVAL_SECONDS:
                return
            self.presence_cache[key_value] = (ip_value, now_value)
        try:
            self.peer_store.update_presence(key_value, ip_value)
        except Exception:
            pass

    def _socket_for_family(self, family):
        return self.server_sockets.get(family)

    def _send_datagram(self, family, target, payload):
        sock = self._socket_for_family(family)
        if sock is None:
            return False
        try:
            sock.sendto(payload, target)
            return True
        except OSError:
            return False

    def _send_to_peer(self, peer, payload):
        targets = self._resolve_peer_addresses(peer.get("host"), int(peer.get("port") or self.port))
        if not targets:
            targets = self._resolve_peer_addresses(peer.get("host"), int(peer.get("port") or self.port), force=True)
        if not targets:
            return False
        delivered = False
        for family, address in targets:
            delivered = self._send_datagram(family, address, payload) or delivered
        return delivered

    def send_hello_to_peers(self):
        body = encode_hello_body(self.node_kind, self.local_label)
        peers = self.refresh_peers(force=False)
        for peer in peers.values():
            try:
                payload = encode_secure_packet(CODE_HELLO, self.identity, peer.get("public_key"), body)
            except Exception:
                continue
            self._send_to_peer(peer, payload)

    def _forward_media_to_peers(self, media, exclude_public_key=None):
        payload_body = encode_media_body(media.get("address"), media.get("port"), media.get("payload"), family=media.get("family"), ttl=media.get("ttl"))
        peers = self.refresh_peers(force=False)
        for peer in peers.values():
            if exclude_public_key and str(peer.get("public_key")) == str(exclude_public_key):
                continue
            try:
                payload = encode_secure_packet(CODE_MEDIA, self.identity, peer.get("public_key"), payload_body)
            except Exception:
                continue
            self._send_to_peer(peer, payload)

    def _handle_secure_packet(self, packet, source_addr):
        peers = self.refresh_peers(force=False)
        try:
            code, sender_public_key, body, _peer = decode_secure_packet(packet, self.identity, peers)
        except KeyError:
            try:
                code, sender_public_key, body, _peer = decode_secure_packet(packet, self.identity, self.refresh_peers(force=True))
            except Exception:
                return
        except Exception:
            return
        source_ip = str(source_addr[0] or "").split("%", 1)[0].strip()
        self._note_peer_presence(sender_public_key, source_ip)
        if code == CODE_HELLO:
            try:
                decode_hello_body(body)
            except Exception:
                pass
            return
        if code != CODE_MEDIA:
            return
        try:
            media = decode_media_body(body)
        except Exception:
            return
        if self.duplicate_filter.seen(media.get("address"), media.get("port"), media.get("payload")):
            return
        if self.enable_rebroadcast:
            try:
                rebroadcast_multicast_packet(
                    media.get("address"),
                    media.get("port"),
                    media.get("payload"),
                    family=socket.AF_INET6 if int(media.get("family") or 4) == 6 else socket.AF_INET,
                    ttl=media.get("ttl"),
                )
            except Exception:
                pass
        self._forward_media_to_peers(media, exclude_public_key=sender_public_key)

    def _handle_local_source_packet(self, packet, source_addr):
        if not self.allow_local_source:
            return
        source_ip = str(source_addr[0] or "").split("%", 1)[0].strip()
        try:
            ip_obj = ipaddress.ip_address(source_ip)
        except ValueError:
            return
        if not ip_obj.is_loopback:
            return
        try:
            media = decode_local_source_packet(packet)
        except Exception:
            return
        self.duplicate_filter.seen(media.get("address"), media.get("port"), media.get("payload"))
        self._forward_media_to_peers(media)

    def _socket_loop(self, sock):
        while not self.stop_event.is_set():
            try:
                packet, source_addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if packet.startswith(CODE_LOCAL_SOURCE):
                self._handle_local_source_packet(packet, source_addr)
                continue
            if packet[:2] not in {CODE_HELLO, CODE_MEDIA}:
                continue
            self._handle_secure_packet(packet, source_addr)

    def _hello_loop(self):
        while not self.stop_event.is_set():
            try:
                self.send_hello_to_peers()
            except Exception as exc:
                self.log(f"multicast gateway hello error: {exc}")
            self.stop_event.wait(HELLO_INTERVAL_SECONDS)

    def _capture_loop(self):
        if not self.enable_capture or os.name == "nt" or not hasattr(socket, "AF_PACKET"):
            return
        raw_sock = None
        try:
            raw_sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0800))
            raw_sock.settimeout(1.0)
        except OSError as exc:
            self.log(f"multicast gateway local capture disabled: {exc}")
            if raw_sock is not None:
                try:
                    raw_sock.close()
                except OSError:
                    pass
            return
        self.log("multicast gateway local multicast capture enabled")
        try:
            while not self.stop_event.is_set():
                try:
                    frame = raw_sock.recv(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                media = parse_ipv4_multicast_udp_frame(frame)
                if not media:
                    continue
                if self.duplicate_filter.seen(media.get("address"), media.get("port"), media.get("payload")):
                    continue
                self._forward_media_to_peers(media)
        finally:
            try:
                raw_sock.close()
            except OSError:
                pass

    def serve(self):
        bind_addrs = []
        sockets = {}
        try:
            for family, bind_addr in listener_bind_specs(self.bind_host, self.port):
                sock = create_bound_socket(family, bind_addr)
                sockets[family] = sock
                bind_addrs.append(format_bind_addr(bind_addr))
        except OSError:
            for sock in sockets.values():
                try:
                    sock.close()
                except OSError:
                    pass
            raise
        self.server_sockets = sockets
        self.log(f"multicast gateway listening on {', '.join(bind_addrs)} udp")
        threads = []
        for sock in self.server_sockets.values():
            thread = threading.Thread(target=self._socket_loop, args=(sock,), daemon=True)
            thread.start()
            threads.append(thread)
        hello_thread = threading.Thread(target=self._hello_loop, daemon=True)
        hello_thread.start()
        threads.append(hello_thread)
        if self.enable_capture:
            capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            capture_thread.start()
            threads.append(capture_thread)
        try:
            while not self.stop_event.is_set():
                time.sleep(0.25)
        finally:
            for sock in self.server_sockets.values():
                try:
                    sock.close()
                except OSError:
                    pass
            self.server_sockets = {}

    def stop(self):
        self.stop_event.set()


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def db_connection():
    if pymysql is None:
        raise RuntimeError("PyMySQL is not installed.")
    return pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME, autocommit=True)


def settings_map():
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT parameter, value FROM systemsettings")
            rows = cur.fetchall()
    finally:
        conn.close()
    result = {}
    for parameter, value in rows:
        result[str(parameter)] = "" if value is None else str(value)
    return result


def gateway_enabled():
    try:
        data = settings_map()
    except Exception as exc:
        log(f"multicast gateway settings read error: {exc}")
        return False
    return str(data.get("allow_multicast_gateway", "0")).strip().lower() in {"1", "true", "yes", "on"}


def product_name():
    try:
        return settings_map().get("product_name") or "Open Paging Server"
    except Exception:
        return "Open Paging Server"


def ensure_peer_table():
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    label VARCHAR(255) NOT NULL DEFAULT '',
                    host VARCHAR(255) NOT NULL DEFAULT '',
                    port INT NOT NULL DEFAULT {DEFAULT_PORT},
                    public_key VARCHAR(128) NOT NULL,
                    peer_type VARCHAR(32) NOT NULL DEFAULT 'gateway',
                    enabled TINYINT(1) NOT NULL DEFAULT 1,
                    last_seen DOUBLE NULL DEFAULT NULL,
                    last_ip VARCHAR(64) NOT NULL DEFAULT '',
                    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_multicast_gateway_public_key (public_key)
                )
                """
            )
    finally:
        conn.close()


def ensure_ops_identity():
    return load_identity(default_ops_key_path())


def list_ops_peers(query_all_fn):
    return query_all_fn(
        f"""
        SELECT id, label, host, port, public_key, peer_type, enabled, last_seen, last_ip, created_at, updated_at
        FROM {TABLE_NAME}
        ORDER BY LOWER(label), id
        """
    )


def upsert_ops_peer(execute_fn, label, host, port, public_key, peer_type="gateway", enabled=1):
    execute_fn(
        f"""
        INSERT INTO {TABLE_NAME} (label, host, port, public_key, peer_type, enabled, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            label = VALUES(label),
            host = VALUES(host),
            port = VALUES(port),
            peer_type = VALUES(peer_type),
            enabled = VALUES(enabled),
            updated_at = NOW()
        """,
        (
            normalize_label(label),
            normalize_host(host),
            int(port or DEFAULT_PORT),
            normalize_public_key(public_key),
            str(peer_type or "gateway").strip().lower() or "gateway",
            int(enabled),
        ),
    )


def update_ops_peer_presence(execute_fn, public_key, last_ip, last_seen=None):
    execute_fn(
        f"UPDATE {TABLE_NAME} SET last_seen=%s, last_ip=%s, updated_at=NOW() WHERE public_key=%s",
        (
            float(last_seen if last_seen is not None else time.time()),
            normalize_host(last_ip),
            normalize_public_key(public_key),
        ),
    )


def delete_ops_peer(execute_fn, peer_id):
    execute_fn(f"DELETE FROM {TABLE_NAME} WHERE id = %s", (int(peer_id),))


def authenticate_admin_user(query_one_fn, username, password):
    user = query_one_fn(
        "SELECT id, username, role, password, salt FROM users WHERE username=%s OR email=%s LIMIT 1",
        (str(username or "").strip(), str(username or "").strip()),
    )
    if not user:
        return None
    expected_hash = hashlib.sha256((str(password or "") + str(user.get("salt") or "")).encode()).hexdigest()
    if not secure_compare(expected_hash, str(user.get("password") or "")):
        return None
    if str(user.get("role") or "") not in {"admin", "tempadmin"}:
        return None
    return user


def system_product_name(query_one_fn):
    row = query_one_fn("SELECT value FROM systemsettings WHERE parameter=%s LIMIT 1", ("product_name",))
    if not row:
        return "Open Paging Server"
    if isinstance(row, dict):
        return str(row.get("value") or "Open Paging Server")
    return str(row[0] or "Open Paging Server")


def provision_gateway_peer(query_one_fn, execute_fn, payload, remote_addr, request_host):
    user = authenticate_admin_user(query_one_fn, payload.get("username"), payload.get("password"))
    if not user:
        return 401, {"status": "error", "message": "Invalid username or password."}
    ensure_peer_table()
    identity = ensure_ops_identity()
    product = system_product_name(query_one_fn)
    gateway_label = str(payload.get("gateway_label") or "").strip()
    remote_ip = str(remote_addr or "").split("%", 1)[0].strip()
    upsert_ops_peer(execute_fn, gateway_label or remote_ip, "", DEFAULT_PORT, payload.get("public_key"), peer_type="gateway", enabled=1)
    if remote_ip:
        update_ops_peer_presence(execute_fn, payload.get("public_key"), remote_ip, last_seen=time.time())
    return 200, {
        "status": "success",
        "product_name": product,
        "public_key": identity["public_key"],
        "udp_host": str(request_host or "").strip() or str(remote_addr or "").split("%", 1)[0].strip(),
        "udp_port": DEFAULT_PORT,
    }


class OpsPeerStore:
    def peer_map(self):
        ensure_peer_table()
        conn = db_connection()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT id, label, host, port, public_key, peer_type, enabled, last_seen, last_ip
                    FROM {TABLE_NAME}
                    WHERE enabled = 1
                    ORDER BY label ASC, id ASC
                    """
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        mapping = {}
        for row in rows:
            key = str(row.get("public_key") or "").strip()
            if not key:
                continue
            row["host"] = str(row.get("host") or "").strip() or str(row.get("last_ip") or "").strip()
            row["port"] = int(row.get("port") or DEFAULT_PORT)
            mapping[key] = row
        return mapping

    def update_presence(self, public_key, last_ip):
        ensure_peer_table()
        conn = db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {TABLE_NAME} SET last_seen=%s, last_ip=%s WHERE public_key=%s",
                    (float(datetime.now().timestamp()), str(last_ip or "").strip(), str(public_key or "").strip()),
                )
        finally:
            conn.close()


runtime = None
instance_lock = None


def shutdown(_sig=None, _frame=None):
    global runtime, instance_lock
    if runtime is not None:
        runtime.stop()
    if instance_lock is not None:
        instance_lock.close()


def main():
    global runtime, instance_lock
    if not gateway_enabled():
        return
    ensure_peer_table()
    instance_lock = FileInstanceLock(DEFAULT_LOCK_PATH)
    if not instance_lock.acquire():
        print("Multicast Gateway is already running", flush=True)
        raise SystemExit(0)
    identity = ensure_ops_identity()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    runtime = GatewayRuntime(
        identity=identity,
        peer_store=OpsPeerStore(),
        node_kind=NODE_KIND_OPS,
        local_label=product_name(),
        bind_host=os.getenv("OPS_MULTICAST_GATEWAY_BIND", "0.0.0.0"),
        port=DEFAULT_PORT,
        enable_capture=False,
        enable_rebroadcast=True,
        allow_local_source=True,
        log=log,
    )
    try:
        runtime.serve()
    finally:
        shutdown()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        shutdown()
        sys.exit(0)

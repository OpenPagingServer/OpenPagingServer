import base64
import hashlib
import html
import importlib.util
import ipaddress
import io
import itertools
import json
import math
import os
import random
import re
import select
import shutil
import socket
import subprocess
import struct
import sys
import tarfile
import tempfile
import threading
import time
import subprocess
import urllib.error
import urllib.request
import uuid
import wave
import xml.etree.ElementTree as ET
import ast
from collections import deque
from datetime import datetime, timezone, timedelta as _timedelta
from pathlib import Path, PurePosixPath

try:
    import pymysql
except Exception:
    pymysql = None

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*_args, **_kwargs):
        return False
from active_broadcast_store import (
    RUNTIME_DIR,
    active_broadcast_stop_requested,
    clear_active_broadcast_stop_request,
    claim_active_broadcast_delivery,
    expire_active_broadcasts_by_template_ids,
    expire_active_broadcasts_triggered_by_template,
    fetch_active_broadcast,
    list_pending_active_broadcast_ids,
    mark_active_broadcast_delivery,
    put_active_broadcast,
    request_active_broadcast_stop,
)
from group_features import fetch_group_rows, record_is_bell, record_is_immediate, regular_group_targets
from tts import decode_tts_token, iter_tts_ffmpeg_chunks, split_audio_entries
from multicastgatewayd import encode_local_source_packet
try:
    from sip.codec import (
        SIP_CODEC_PAYLOADS as SIP_EDGE_CODEC_PAYLOADS,
        encode_sip_rtp_payload as encode_edge_rtp_payload,
        decode_sip_rtp_payload as decode_edge_rtp_payload,
    )
except ImportError:
    SIP_EDGE_CODEC_PAYLOADS = {
        "PCMU": {"payload_type": 0, "samples_per_frame": 160},
        "PCMA": {"payload_type": 8, "samples_per_frame": 160},
        "G722": {"payload_type": 9, "samples_per_frame": 160, "sample_rate_wire": 16000},
        "OPUS": {"payload_type": 111, "samples_per_frame": 960},
        "G7221C": {"payload_type": 121, "samples_per_frame": 640, "sample_rate_wire": 32000},
        "G726-32": {"payload_type": 2, "samples_per_frame": 160},
    }

    def encode_edge_rtp_payload(codec_name, payload, encoder_state=None):
        frame = bytes(payload or b"")[:160].ljust(160, b"\xff")
        if str(codec_name or "").upper() == "PCMA":
            return frame.translate(ULAW_TO_ALAW_TABLE), encoder_state
        return frame, encoder_state

    def decode_edge_rtp_payload(codec_name, payload, decoder_state=None):
        # Fallback decoder used only when sip.codec is unavailable. Normalizes
        # inbound RTP payloads to u-law bytes so AMD analysis works on the
        # codecs we can decode without native libraries (PCMU/PCMA); other
        # codecs return empty so AMD simply skips those frames.
        data = bytes(payload or b"")
        codec = str(codec_name or "").upper()
        if codec == "PCMA":
            return data.translate(ALAW_TO_ULAW_TABLE), decoder_state
        if codec in ("", "PCMU"):
            return data, decoder_state
        return b"", decoder_state

try:
    from broadcasts import expand_message_variables, is_audio_type, message_expiration_is_immediate
except Exception:
    def expand_message_variables(text, _cursor, sender="", sender_context=None, now=None, api_cache=None, product_name=""):
        return str(text or "")
    def is_audio_type(value):
        return str(value or "").strip() in ("audio", "text+audio", "liveaudio", "liveaudio+text", "AudioMessage", "Text+AudioMessage", "Page")
    def message_expiration_is_immediate(value):
        return str(value or "").strip().lower() == "0m"

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"

def read_ops_version():
    pyproject_path = BASE_DIR / "pyproject.toml"
    try:
        text = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return "1.0.0"
    in_project_section = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_project_section = line == "[project]"
            continue
        if in_project_section:
            match = re.fullmatch(r"version\s*=\s*[\"']([^\"']+)[\"']", line)
            if match:
                return match.group(1)
    match = re.search(r"(?m)^\s*version\s*=\s*[\"']([^\"']+)[\"']", text)
    return match.group(1) if match else "1.0.0"


OPS_VERSION = read_ops_version()
MODULE_STORE_DIR = Path(os.getenv("ENDPOINT_MODULES_PATH", "/var/lib/openpagingserver/endpointmodules"))
MODULE_CACHE_DIR = Path(os.getenv("ENDPOINT_MODULE_CACHE_PATH", str(MODULE_STORE_DIR / ".cache")))
TRUSTED_CA_DIR = Path(os.getenv("OPS_TRUSTED_CA_DIR", "/etc/openpagingserver/trustedca"))
MODULE_LOG_DIR = Path(os.getenv("OPS_ENDPOINT_MODULE_LOG_DIR", "/var/log/openpagingserver/endpointmodules"))
ENDPOINT_IPC_SOCKET_PATH = Path("/run/openpagingserver/endpointmodules.sock")
LOG_FILE = MODULE_LOG_DIR / "endpoint_dispatch.log"
VALID_MESSAGE_PRIORITIES = {"Low", "Normal", "High", "Emergency"}
LIST_ENDPOINTS_STATUS_TIMEOUT = max(0.25, float(os.getenv("OPS_LIST_ENDPOINTS_STATUS_TIMEOUT", "1.5")))
MODULE_STATUS_CACHE_TTL = max(0.0, float(os.getenv("OPS_MODULE_STATUS_CACHE_TTL", "5")))
MODULE_STATUS_TASK_STALE_SECONDS = max(
    LIST_ENDPOINTS_STATUS_TIMEOUT,
    float(os.getenv("OPS_MODULE_STATUS_TASK_STALE_SECONDS", "30")),
)

loaded_modules = {}
module_load_errors = {}
loaded_modules_lock = threading.Lock()
module_status_lock = threading.Lock()
module_status_cache = {}
module_status_tasks = {}
endpoint_package_cache_lock = threading.Lock()
endpoint_package_info_cache = {}
endpoint_package_list_cache = {}
stream_states = {}
stream_states_lock = threading.Lock()
message_vendor_schema_ready = False
input_rate_limit_buckets = {}
input_rate_limit_lock = threading.Lock()
broadcast_watcher_stop = threading.Event()
broadcast_delivery_ids = set()
broadcast_delivery_lock = threading.Lock()
core = None
server_socket = None
thirdparty_warning_keys = set()
siptrunks_runtime = None
multicast_rtp_runtime = None
http_request_runtime = None
service_monitor_runtime = None
multicast_gateway_source_sock = None
multicast_gateway_source_lock = threading.Lock()
multicast_gateway_source_next_retry = 0.0
multicast_socket_sendto_patched = False


class StreamState:
    def __init__(self, stream_id, target_map):
        self.stream_id = stream_id
        self.target_map = target_map
        self.pending_modules = {name for name, targets in target_map.items() if targets}
        self.ready_modules = set()
        self.failed_modules = set()
        self.ready_event = threading.Event()

    def mark_ready(self, module_name):
        if module_name in self.pending_modules:
            self.ready_modules.add(module_name)
        if (self.ready_modules | self.failed_modules) >= self.pending_modules:
            self.ready_event.set()

    def mark_failed(self, module_name):
        if module_name in self.pending_modules:
            self.failed_modules.add(module_name)
        if (self.ready_modules | self.failed_modules) >= self.pending_modules:
            self.ready_event.set()


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after):
        super().__init__(f"input module send rate limit exceeded; retry after {max(1, int(retry_after or 1))} seconds")
        self.retry_after = max(1, int(retry_after or 1))


def init(core_obj):
    global core
    core = core_obj
    install_multicast_gateway_sendto_patch()
    try:
        ensure_siptrunks_schema()
    except Exception as exc:
        log(f"siptrunks schema init error: {exc}")
    try:
        ensure_multicast_rtp_schema()
    except Exception as exc:
        log(f"multicast rtp schema init error: {exc}")
    try:
        ensure_httprequest_schema()
    except Exception as exc:
        log(f"http request schema init error: {exc}")
    try:
        ensure_servicemonitor_schema()
    except Exception as exc:
        log(f"service monitor schema init error: {exc}")
    ensure_builtin_modules_loaded()
    threading.Thread(target=start_ipc_server, daemon=True).start()
    threading.Thread(target=broadcast_watcher_loop, daemon=True).start()


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if DEBUG:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as handle:
                handle.write(f"[{timestamp}] {msg}\n")
        except OSError:
            print(msg)
    if core is not None and hasattr(core, "log"):
        core.log(msg)
    elif DEBUG:
        print(msg)


def page_debug(msg):
    if DEBUG:
        log(f"DEBUG {msg}")


def configured_thirdparty_user():
    return str(os.getenv("THIRDPARTY_USER", "") or "").strip()


def log_thirdparty_warning(key, msg):
    if key in thirdparty_warning_keys:
        return
    thirdparty_warning_keys.add(key)
    log(msg)


def safe_module_name(value):
    return re.fullmatch(r"^[A-Za-z0-9_-]+$", str(value or "")) is not None


def package_module_name(value):
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-_")
    return normalized or "module"


def supports_unix_sockets():
    return hasattr(socket, "AF_UNIX") and os.name != "nt"


def current_process_uid():
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None:
        return geteuid()
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return getuid()
    return -1


def resolve_thirdparty_user_record(context="endpoint module"):
    user = configured_thirdparty_user()
    if not user:
        return None
    if os.name == "nt":
        log_thirdparty_warning(
            "thirdparty-windows",
            "THIRDPARTY_USER is set, but Windows process switching requires credentials; using the main OPS user",
        )
        return None
    try:
        import pwd
    except ImportError:
        log_thirdparty_warning(
            "thirdparty-pwd-unavailable",
            f"THIRDPARTY_USER={user!r} is set, but this platform cannot look up Unix users; using the main OPS user",
        )
        return None
    try:
        return pwd.getpwnam(user)
    except KeyError:
        log_thirdparty_warning(
            f"thirdparty-missing-{user}",
            f"THIRDPARTY_USER={user!r} was not found for {context}; using the main OPS user",
        )
        return None


def apply_endpoint_ipc_socket_permissions(sock_path):
    record = resolve_thirdparty_user_record("endpoint module IPC socket")
    if record is not None:
        try:
            uid = current_process_uid()
            os.chown(sock_path.parent, uid, record.pw_gid)
            os.chmod(sock_path.parent, 0o750)
            os.chown(sock_path, uid, record.pw_gid)
            os.chmod(sock_path, 0o660)
            log(f"endpoint IPC socket allows THIRDPARTY_USER={record.pw_name!r}")
            return
        except OSError as exc:
            log_thirdparty_warning(
                f"thirdparty-socket-perms-{record.pw_name}",
                f"Unable to grant THIRDPARTY_USER={record.pw_name!r} access to endpoint IPC socket: {exc}; using owner-only permissions",
            )
    try:
        os.chmod(sock_path, 0o600)
    except OSError as exc:
        log(f"unable to set endpoint IPC socket permissions: {exc}")


def tune_ipc_stream_socket(sock):
    try:
        family = getattr(sock, "family", None)
        if family in (socket.AF_INET, socket.AF_INET6):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    return sock


def connect_endpoint_ipc(timeout=2):
    if supports_unix_sockets() and ENDPOINT_IPC_SOCKET_PATH.exists():
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(ENDPOINT_IPC_SOCKET_PATH))
            return sock
        except Exception:
            sock.close()
            raise
    return tune_ipc_stream_socket(socket.create_connection(("127.0.0.1", 50000), timeout=timeout))


def create_endpoint_ipc_server_socket():
    if supports_unix_sockets():
        ENDPOINT_IPC_SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            if ENDPOINT_IPC_SOCKET_PATH.exists() or ENDPOINT_IPC_SOCKET_PATH.is_socket():
                ENDPOINT_IPC_SOCKET_PATH.unlink()
        except OSError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(ENDPOINT_IPC_SOCKET_PATH))
        apply_endpoint_ipc_socket_permissions(ENDPOINT_IPC_SOCKET_PATH)
        return sock, f"unix:{ENDPOINT_IPC_SOCKET_PATH}"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 50000))
    return sock, "tcp:127.0.0.1:50000"


def clean_tar_name(name):
    clean = str(name or "").replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]
    return clean.lstrip("/")


def validate_tar_member(member):
    name = clean_tar_name(member.name)
    if not name:
        raise ValueError("tar member has an empty name")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe tar member path: {member.name}")
    if member.issym() or member.islnk() or member.isdev():
        raise ValueError(f"unsupported tar member type: {member.name}")
    return name


def read_tar_file(tar, wanted_name):
    wanted_name = clean_tar_name(wanted_name)
    for member in tar.getmembers():
        name = validate_tar_member(member)
        if name == wanted_name and member.isfile():
            extracted = tar.extractfile(member)
            return extracted.read() if extracted is not None else b""
    return None


def safe_extract_tar_bytes(data, target_dir):
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = validate_tar_member(member)
            destination = (root / name).resolve()
            if root not in destination.parents and destination != root:
                raise ValueError(f"unsafe tar extraction target: {name}")
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, open(destination, "wb") as handle:
                shutil.copyfileobj(source, handle)


def bundle_regular_files(bundle_path):
    files = []
    with tarfile.open(bundle_path, "r:gz") as tar:
        for member in tar.getmembers():
            name = validate_tar_member(member)
            if not member.isfile() or name.startswith(".signature/"):
                continue
            extracted = tar.extractfile(member)
            files.append((name, extracted.read() if extracted is not None else b""))
    return files


def canonical_signature_payload(bundle_path):
    lines = [b"OPSEPM-SIGNATURE-V1\n"]
    for name, data in sorted(bundle_regular_files(bundle_path), key=lambda item: item[0]):
        digest = hashlib.sha256(data).hexdigest()
        lines.append(
            name.encode("utf-8")
            + b"\0"
            + str(len(data)).encode("ascii")
            + b"\0"
            + digest.encode("ascii")
            + b"\n"
        )
    return b"".join(lines)


def bundle_signature_digest(bundle_path):
    return hashlib.sha256(canonical_signature_payload(bundle_path)).digest()


def bundle_hash(bundle_path):
    digest = hashlib.sha256()
    with open(bundle_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(bundle_path):
    with tarfile.open(bundle_path, "r:gz") as tar:
        raw = read_tar_file(tar, "manifest.json")
    if raw is None:
        raise ValueError("manifest.json is missing")
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("manifest.json is empty")
    manifest = json.loads(text)
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain an object")
    module = package_module_name(manifest.get("module") or Path(bundle_path).stem)
    if not safe_module_name(module):
        raise ValueError("manifest module name is invalid")
    manifest["module"] = module
    manifest.setdefault("name", module)
    manifest.setdefault("version", "")
    manifest.setdefault("description", "")
    manifest.setdefault("developer", manifest.get("author", ""))
    manifest.setdefault("input_type", manifest.get("type", "Output") or "Output")
    manifest.setdefault("minimum_ops_version", OPS_VERSION)
    manifest.setdefault("requirements", [])
    return manifest


def load_crypto():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, ed448, padding, rsa
    except Exception as exc:
        raise RuntimeError("cryptography is required for endpoint module signatures") from exc
    return {
        "x509": x509,
        "hashes": hashes,
        "ec": ec,
        "ed25519": ed25519,
        "ed448": ed448,
        "padding": padding,
        "rsa": rsa,
    }


def pem_blocks(data, marker=b"CERTIFICATE"):
    begin = b"-----BEGIN " + marker + b"-----"
    end = b"-----END " + marker + b"-----"
    blocks = []
    offset = 0
    while True:
        start = data.find(begin, offset)
        if start < 0:
            break
        finish = data.find(end, start)
        if finish < 0:
            break
        finish += len(end)
        blocks.append(data[start:finish] + b"\n")
        offset = finish
    return blocks


def load_pem_certs(data):
    crypto = load_crypto()
    return [crypto["x509"].load_pem_x509_certificate(block) for block in pem_blocks(data)]


def trusted_ca_certs():
    certs = []
    if not TRUSTED_CA_DIR.is_dir():
        return certs
    for path in sorted(TRUSTED_CA_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".pem", ".crt", ".cer"}:
            continue
        try:
            certs.extend(load_pem_certs(path.read_bytes()))
        except Exception:
            continue
    return certs


def cert_fingerprint(cert):
    crypto = load_crypto()
    return cert.fingerprint(crypto["hashes"].SHA256())


def cert_valid_now(cert):
    now = datetime.now(timezone.utc)
    not_before = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before.replace(tzinfo=timezone.utc)
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
    return not_before <= now <= not_after


def cert_is_ca(cert):
    crypto = load_crypto()
    try:
        return bool(cert.extensions.get_extension_for_class(crypto["x509"].BasicConstraints).value.ca)
    except Exception:
        return True


def verify_cert_signature(child, issuer):
    crypto = load_crypto()
    public_key = issuer.public_key()
    signature_algorithm = child.signature_hash_algorithm
    if isinstance(public_key, crypto["rsa"].RSAPublicKey):
        public_key.verify(child.signature, child.tbs_certificate_bytes, crypto["padding"].PKCS1v15(), signature_algorithm)
    elif isinstance(public_key, crypto["ec"].EllipticCurvePublicKey):
        public_key.verify(child.signature, child.tbs_certificate_bytes, crypto["ec"].ECDSA(signature_algorithm))
    elif isinstance(public_key, (crypto["ed25519"].Ed25519PublicKey, crypto["ed448"].Ed448PublicKey)):
        public_key.verify(child.signature, child.tbs_certificate_bytes)
    else:
        public_key.verify(child.signature, child.tbs_certificate_bytes)


def certificate_chain_trusted(leaf, presented_certs, ca_certs):
    ca_certs = [cert for cert in ca_certs if cert_valid_now(cert)]
    if not ca_certs or not cert_valid_now(leaf):
        return False
    trusted_fingerprints = {cert_fingerprint(cert) for cert in ca_certs}
    if cert_fingerprint(leaf) in trusted_fingerprints:
        return True
    intermediates = [cert for cert in presented_certs[1:] if cert_valid_now(cert)]
    current = leaf
    seen = {cert_fingerprint(current)}
    for _ in range(len(intermediates) + len(ca_certs) + 1):
        issuer = None
        for candidate in intermediates + ca_certs:
            if current.issuer != candidate.subject or not cert_is_ca(candidate):
                continue
            try:
                verify_cert_signature(current, candidate)
                issuer = candidate
                break
            except Exception:
                continue
        if issuer is None:
            return False
        issuer_fingerprint = cert_fingerprint(issuer)
        if issuer_fingerprint in trusted_fingerprints:
            return True
        if issuer_fingerprint in seen:
            return False
        seen.add(issuer_fingerprint)
        current = issuer
    return False


def cert_subject_value(cert, attribute_name):
    crypto = load_crypto()
    oid = {
        "organization": crypto["x509"].NameOID.ORGANIZATION_NAME,
        "common_name": crypto["x509"].NameOID.COMMON_NAME,
    }[attribute_name]
    values = cert.subject.get_attributes_for_oid(oid)
    return values[0].value if values else ""


def certificate_organization(cert):
    return cert_subject_value(cert, "organization") or cert_subject_value(cert, "common_name") or "Unknown Organization"


def verify_payload_signature(leaf_cert, signature, digest):
    crypto = load_crypto()
    public_key = leaf_cert.public_key()
    if isinstance(public_key, crypto["rsa"].RSAPublicKey):
        public_key.verify(signature, digest, crypto["padding"].PKCS1v15(), crypto["hashes"].SHA256())
    elif isinstance(public_key, crypto["ec"].EllipticCurvePublicKey):
        public_key.verify(signature, digest, crypto["ec"].ECDSA(crypto["hashes"].SHA256()))
    elif isinstance(public_key, (crypto["ed25519"].Ed25519PublicKey, crypto["ed448"].Ed448PublicKey)):
        public_key.verify(signature, digest)
    else:
        public_key.verify(signature, digest)


def verify_bundle_signature(bundle_path):
    try:
        with tarfile.open(bundle_path, "r:gz") as tar:
            cert_pem = read_tar_file(tar, ".signature/cert.pem")
            signature = read_tar_file(tar, ".signature/signature.sig")
            legacy_cert_pem = read_tar_file(tar, "signature/cert.pem")
            legacy_signature = read_tar_file(tar, "signature/signature.sig")
        if not cert_pem or not signature:
            detail = ""
            if legacy_cert_pem or legacy_signature:
                detail = "found legacy signature/ folder; expected .signature/"
            return {"trusted": False, "signature_state": "unsigned", "error": "This module is unsigned and cannot be verified", "detail": detail}
        certs = load_pem_certs(cert_pem)
        if not certs:
            return {"trusted": False, "signature_state": "unsigned", "error": "This module is unsigned and cannot be verified"}
        leaf = certs[0]
        organization = certificate_organization(leaf)
        if not certificate_chain_trusted(leaf, certs, trusted_ca_certs()):
            return {
                "trusted": False,
                "signature_state": "untrusted",
                "error": "This module does not have a trusted CA. Refer to the developer for information.",
                "organization": organization,
            }
        verify_payload_signature(leaf, signature, bundle_signature_digest(bundle_path))
        return {
            "trusted": True,
            "signature_state": "trusted",
            "organization": organization,
            "signature_label": f"Signed by {organization}",
        }
    except Exception as exc:
        return {"trusted": False, "signature_state": "unsigned", "error": "This module is unsigned and cannot be verified", "detail": str(exc)}


def module_load_error_text(package):
    error = str(package.get("load_error") or "").strip()
    verification = package.get("verification") or {}
    if not error:
        error = str(verification.get("error") or "This module is unsigned and cannot be verified").strip()
    detail = str(verification.get("detail") or "").strip()
    if detail:
        return f"{error} ({detail})"
    return error


def extract_bundle_root_files(bundle_path, root_target):
    root_target = Path(root_target)
    root_target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path, "r:gz") as tar:
        for member in tar.getmembers():
            name = validate_tar_member(member)
            if "/" in name or name in {"payload", "web"} or name.startswith(".signature/"):
                continue
            if not member.isfile():
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, open(root_target / name, "wb") as handle:
                shutil.copyfileobj(source, handle)


def ensure_bundle_extracted(bundle_path, manifest=None):
    bundle_path = Path(bundle_path)
    manifest = manifest or read_manifest(bundle_path)
    module = manifest["module"]
    target = MODULE_CACHE_DIR / module / bundle_hash(bundle_path)
    marker = target / ".extracted"
    if marker.is_file():
        return {
            "cache_path": target,
            "payload_path": target / "payload",
            "web_path": target / "web",
            "root_path": target / "root",
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".extract-", dir=str(target.parent)))
    try:
        with tarfile.open(bundle_path, "r:gz") as tar:
            payload_bytes = read_tar_file(tar, "payload")
            web_bytes = read_tar_file(tar, "web")
        if payload_bytes is None:
            raise ValueError("payload archive is missing")
        safe_extract_tar_bytes(payload_bytes, temp_dir / "payload")
        if web_bytes is not None:
            safe_extract_tar_bytes(web_bytes, temp_dir / "web")
        else:
            (temp_dir / "web").mkdir(parents=True, exist_ok=True)
        extract_bundle_root_files(bundle_path, temp_dir / "root")
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        install_sql = temp_dir / "root" / "install.sql"
        if install_sql.is_file() and not (temp_dir / "payload" / "install.sql").exists():
            shutil.copy2(install_sql, temp_dir / "payload" / "install.sql")
        if target.exists():
            shutil.rmtree(target)
        temp_dir.rename(target)
        (target / ".extracted").write_text("ok\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return {
        "cache_path": target,
        "payload_path": target / "payload",
        "web_path": target / "web",
        "root_path": target / "root",
    }


def module_tables_from_install_sql(sql_text):
    tables = []
    for match in re.finditer(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+`?([^`\s(]+)`?", sql_text, re.IGNORECASE):
        table = match.group(1)
        if table.startswith("endpoints-") and table not in tables:
            tables.append(table)
    return tables


def endpoint_package_stat_key(bundle_path):
    stat = Path(bundle_path).stat()
    return (str(Path(bundle_path).resolve()), stat.st_size, getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))


def endpoint_package_directory_signature():
    if not MODULE_STORE_DIR.is_dir():
        return ()
    signature = []
    for path in sorted(MODULE_STORE_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".opsepm":
            continue
        stat = path.stat()
        signature.append(
            (
                path.name,
                stat.st_size,
                getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            )
        )
    return tuple(signature)


def endpoint_package_info(bundle_path, extract_if_trusted=False):
    bundle_path = Path(bundle_path)
    cache_key = endpoint_package_stat_key(bundle_path)
    with endpoint_package_cache_lock:
        cached = endpoint_package_info_cache.get(cache_key)
        if cached is not None and (not extract_if_trusted or cached.get("extract_ready")):
            return dict(cached["info"])
    manifest = read_manifest(bundle_path)
    verification = verify_bundle_signature(bundle_path)
    info = {
        "module": manifest["module"],
        "bundle_path": bundle_path,
        "manifest": manifest,
        "verification": verification,
        "trusted": bool(verification.get("trusted")),
        "load_error": "" if verification.get("trusted") else "",
    }
    if not info["trusted"]:
        info["load_error"] = module_load_error_text(info)
    if extract_if_trusted and info["trusted"]:
        info.update(ensure_bundle_extracted(bundle_path, manifest))
    cache_entry = {
        "info": dict(info),
        "extract_ready": bool(info.get("trusted")) and "payload_path" in info and "web_path" in info,
    }
    with endpoint_package_cache_lock:
        endpoint_package_info_cache[cache_key] = cache_entry
    return dict(info)


def discover_endpoint_packages(extract_if_trusted=False):
    directory_signature = endpoint_package_directory_signature()
    cache_key = (extract_if_trusted, directory_signature)
    with endpoint_package_cache_lock:
        cached = endpoint_package_list_cache.get(cache_key)
        if cached is not None:
            return {name: dict(info) for name, info in cached.items()}
    modules = {}
    if not MODULE_STORE_DIR.is_dir():
        return modules
    for path in sorted(MODULE_STORE_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".opsepm":
            continue
        try:
            info = endpoint_package_info(path, extract_if_trusted=extract_if_trusted)
        except Exception as exc:
            module = package_module_name(path.stem)
            info = {
                "module": module,
                "bundle_path": path,
                "manifest": {
                    "module": module,
                    "name": module,
                    "description": "",
                    "version": "",
                    "developer": "",
                    "input_type": "Output",
                    "minimum_ops_version": OPS_VERSION,
                    "requirements": [],
                },
                "verification": {"trusted": False, "signature_state": "unsigned", "error": "This module is unsigned and cannot be verified", "detail": str(exc)},
                "trusted": False,
                "load_error": f"This module is unsigned and cannot be verified ({exc})",
            }
        modules[info["module"]] = info
    cached_modules = {name: dict(info) for name, info in modules.items()}
    with endpoint_package_cache_lock:
        endpoint_package_list_cache[cache_key] = cached_modules
    return modules


def get_db_connection():
    if pymysql is None:
        raise RuntimeError("PyMySQL is not installed")
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )


SIP_TRUNK_TABLE = "sip-trunks"
SIP_DIALPLAN_TABLE = "endpoints-input-siptrunk"
SIP_OUTPUT_TABLE = "endpoints-output-siptrunk"
SIP_TRUNK_TYPE_IP = "IP"
SIP_TRUNK_TYPE_INBOUND_AUTH = "INBOUND_AUTH"
SIP_TRUNK_TYPE_OUTBOUND_AUTH = "OUTBOUND_AUTH"
SIP_OUTPUT_MODE_PAGE = "page"
SIP_OUTPUT_MODE_TELEPHONE = "telephone"
SIP_ALERT_INFO_PRESETS = {
    "ring-answer": "ring-answer",
    "intercom": "intercom",
    "answer-after-0": "answer-after=0",
    "alert-autoanswer": "info=alert-autoanswer",
    "auto-answer": "auto answer",
    "intercom-true": "intercom=true",
}
SIP_OUTPUT_AMD_ACTIONS = {"hangup", "redial"}
SIP_OUTPUT_NAT_MODES = {"auto", "yes", "no"}
SIP_OUTPUT_FRAME_BYTES = 160
SIP_OUTPUT_SILENCE_FRAME = b"\xff" * SIP_OUTPUT_FRAME_BYTES
SIP_AMD_FRAME_MS = 20
SIP_AMD_LISTEN_SECONDS = 4.5
SIP_AMD_TIMEOUT_STEP_MS = 250
SIP_AMD_MIN_VOICE_AVERAGE = 500.0
SIP_AMD_NOISE_MULTIPLIER = 2.5
SIP_AMD_MACHINE_CONTINUOUS_MS = 1800
SIP_AMD_MACHINE_TOTAL_MS = 2400
SIP_AMD_MACHINE_AFTER_PAUSE_MS = 1400
SIP_AMD_HUMAN_MIN_GREETING_MS = 200
SIP_AMD_HUMAN_MAX_GREETING_MS = 1200
SIP_AMD_HUMAN_TOTAL_MS = 1400
SIP_AMD_HUMAN_PAUSE_MS = 700
SIP_AMD_BEEP_FREQUENCIES = (900, 1000, 1100, 1400)
SIP_AMD_BEEP_POWER_RATIO = 18.0
SIP_AMD_BEEP_MIN_ENERGY = 700.0
SIP_AMD_BEEP_MS = 100
MULTICAST_RTP_MODULE = "multicastrtp"
MULTICAST_RTP_TABLE = "endpoints-output-multicastrtp"
MULTICAST_RTP_NAME = "Multicast RTP"
MULTICAST_RTP_DESCRIPTION = "Send a plain old multicast RTP stream. The vast majority of SME VoIP phones and speakers can subscribe to and accept to these."
MULTICAST_RTP_WARNING = "Open Paging Server is unable to guarantee the delivery of audio to endpoints. Ensure that every single device subscribed to a multicast stream is able to reliably receive audio before beginning production use. Multicast packets are not transmitted over WAN and most VPN tunnels. In such a case you will need a multicast gateway."
MULTICAST_RTP_CODECS = {"PCMU": 0, "PCMA": 8, "G722": 9, "OPUS": 111, "G7221C": 121, "G726-32": 2}
MULTICAST_RTP_DEFAULT_PACKET_MS = 20
MULTICAST_RTP_MIN_PACKET_MS = 20
MULTICAST_RTP_MAX_PACKET_MS = 200
MULTICAST_RTP_FRAME_MS = 20
MULTICAST_RTP_FRAME_SIZE = 160
# When the codec changes on a live channel, stop the stream for this long
# before rebuilding the RTP sender so listeners cleanly re-sync.
MULTICAST_RTP_CODEC_RESTART_SECONDS = 5.0
# Number of preroll frames the multicast channel streams before the source is
# reported ready. Matches the original 60 ms priming (3 * 20 ms) so calls are
# answered immediately; tunable via env for sites whose devices need more
# settling time.
try:
    MULTICAST_RTP_READY_SILENCE_FRAMES = max(1, int(os.getenv("OPS_MULTICAST_PREROLL_FRAMES", "3")))
except (TypeError, ValueError):
    MULTICAST_RTP_READY_SILENCE_FRAMES = 3
MULTICAST_RTP_IDLE_SECONDS = 1.0
# Standby (background audio) configuration for a multicast stream. Controls what
# the stream emits while no message/livepage is in effect.
MULTICAST_RTP_STANDBY_MODES = ("stop", "rebroadcast", "silence")
MULTICAST_RTP_STANDBY_MSG_ACTIONS = ("keep", "stop", "silence", "emergency")
MULTICAST_RTP_MAX_STANDBY_SOURCES = 250
MULTICAST_RTP_STANDBY_SOURCE_PLACEHOLDER = (
    "ex: https://radio.example.com/stream.mp3, rtp://239.255.0.1:2000, rtp://10.0.0.10:5004"
)
# Per-endpoint gain (dB) applied to the standby background audio. Each field is
# either an integer dB value in [MIN, MAX] or the string "mute" (background
# silenced). "on message/page/bell" duck the background while a broadcast of
# that class is in effect; the master field applies at all times.
MULTICAST_RTP_AMP_MIN_DB = -40
MULTICAST_RTP_AMP_MAX_DB = 20
MULTICAST_RTP_AMP_DEFAULTS = {
    "amp_master": "0",
    "amp_page": "-10",
    "amp_bell": "0",
    "amp_message": "mute",
}
MESSAGE_PRIORITY_ORDER = {"Low": 0, "Normal": 1, "High": 2, "Emergency": 3}
# How far ahead of real time deliver_broadcast feeds endpoint modules; this
# jitter cushion keeps the self-pacing RTP senders from underrunning.
MULTICAST_DELIVERY_LEAD_SECONDS = max(0.0, float(os.getenv("MULTICAST_DELIVERY_LEAD_SECONDS", "0.3")))
MULTICAST_GATEWAY_HOST = os.getenv("OPS_MULTICAST_GATEWAY_HOST", "127.0.0.1")
MULTICAST_GATEWAY_PORT = int(os.getenv("OPS_MULTICAST_GATEWAY_PORT", "8710"))
HTTP_REQUEST_MODULE = "httprequest"
HTTP_REQUEST_TABLE = "endpoints-output-httprequest"
HTTP_REQUEST_NAME = "HTTP Request"
HTTP_REQUEST_DESCRIPTION = "Send messages via HTTP requests"
HTTP_REQUEST_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD")
HTTP_REQUEST_AUTH_TYPES = {"none", "basic", "digest", "apikey"}
HTTP_REQUEST_DEFAULT_TIMEOUT = 30
HTTP_REQUEST_VARIABLE_RE = re.compile(r"\$\{(shortmessage|longmessage|color)(?::([^}]+))?\}", re.IGNORECASE)
SERVICE_MONITOR_MODULE = "servicemonitor"
SERVICE_MONITOR_TABLE = "endpoints-input-servicemonitor"
SERVICE_MONITOR_NAME = "Service Monitor"
SERVICE_MONITOR_DESCRIPTION = "Get notified when a service goes down"
SERVICE_MONITOR_TYPES = ("ping", "tcp", "http", "sip", "uptimekuma")
SERVICE_MONITOR_TYPE_LABELS = {
    "ping": "Ping",
    "tcp": "TCP Port",
    "http": "HTTP(s)",
    "sip": "SIP OPTIONS",
    "uptimekuma": "Uptime Kuma",
}
SERVICE_MONITOR_DEFAULT_INTERVAL = 60
SERVICE_MONITOR_MIN_INTERVAL = 5
SERVICE_MONITOR_MAX_INTERVAL = 86400
SERVICE_MONITOR_DEFAULT_RETRIES = 5
SERVICE_MONITOR_MIN_RETRIES = 0
SERVICE_MONITOR_MAX_RETRIES = 4096
# Grace window (seconds) after the monitor module boots during which probe
# failures are absorbed instead of latching a monitor offline or firing a
# "went down" alert. Startup load (SIP/DB/network stack still initialising) can
# make even a healthy host miss a ping, which on a 0-retry monitor would
# otherwise blast a false "server is down" broadcast seconds after boot. A down
# that genuinely begins during this window is not missed: it simply alerts once
# the window elapses and the failure is still present.
SERVICE_MONITOR_STARTUP_GRACE_SECONDS = 15
SERVICE_MONITOR_DEFAULT_WAIT_FOR_UP = 0
SERVICE_MONITOR_MIN_WAIT_FOR_UP = 0
SERVICE_MONITOR_MAX_WAIT_FOR_UP = 86400
SERVICE_MONITOR_DOWN_DEFAULTS_AUDIO = "OPS-ShortBlip10Second-400Tria.wav"
# Preferred default audio per direction, tried in order; the first file that
# exists in the asset library is used, otherwise no audio file is inserted.
SERVICE_MONITOR_DOWN_AUDIO_CANDIDATES = (
    "OPS-ShortBlip10Second-400Tria.wav",
    "OPS-400HZ-MedPulse.wav",
)
SERVICE_MONITOR_UP_AUDIO_CANDIDATES = (
    "OPS-DualChime.wav",
    "OPS-900HZ-SlowPulse.wav",
)
# HTTP failure token choices. "noresponse" and the 4xx/5xx families are the
# defaults; individual status codes can also be selected.
SERVICE_MONITOR_HTTP_FAIL_TOKENS = ("noresponse", "4xx", "5xx")
SERVICE_MONITOR_HTTP_DEFAULT_FAIL = ["noresponse", "4xx", "5xx"]
SERVICE_MONITOR_HTTP_CODE_CHOICES = (
    400, 401, 403, 404, 405, 408, 409, 410, 429,
    500, 501, 502, 503, 504, 505,
)


def linear_to_alaw(sample):
    sample = int(sample)
    if sample >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        sample = -sample - 8
    if sample < 0:
        sample = 0
    if sample > 32635:
        sample = 32635
    if sample >= 256:
        exponent = 7
        exp_mask = 0x4000
        while exponent > 0 and not (sample & exp_mask):
            exponent -= 1
            exp_mask >>= 1
        value = (exponent << 4) | ((sample >> (exponent + 3)) & 0x0F)
    else:
        value = sample >> 4
    return value ^ mask


def build_ulaw_to_alaw_table():
    values = []
    for index in range(256):
        ulaw = (~index) & 0xFF
        sign = ulaw & 0x80
        exponent = (ulaw >> 4) & 0x07
        mantissa = ulaw & 0x0F
        sample = (mantissa << 3) + 0x84
        sample <<= exponent
        sample -= 0x84
        if sign:
            sample = -sample
        values.append(linear_to_alaw(sample))
    return bytes(values)


ULAW_TO_ALAW_TABLE = build_ulaw_to_alaw_table()
MULTICAST_RTP_SILENCE_FRAME = b"\xff" * MULTICAST_RTP_FRAME_SIZE


def build_ulaw_to_linear_table():
    values = []
    for index in range(256):
        ulaw = (~index) & 0xFF
        sign = ulaw & 0x80
        exponent = (ulaw >> 4) & 0x07
        mantissa = ulaw & 0x0F
        sample = (mantissa << 3) + 0x84
        sample <<= exponent
        sample -= 0x84
        values.append(-sample if sign else sample)
    return values


ULAW_TO_LINEAR_TABLE = build_ulaw_to_linear_table()


def linear_to_ulaw(sample):
    return LINEAR_TO_ULAW_TABLE[int(sample) & 0xFFFF]


def _build_linear_to_ulaw_table():
    values = []
    for index in range(65536):
        sample = index
        if sample > 32767:
            sample -= 65536
        bias = 0x84
        clip = 32635
        sign = 0
        if sample < 0:
            sample = -sample
            sign = 0x80
        if sample > clip:
            sample = clip
        sample += bias
        exponent = 7
        exp_mask = 0x4000
        while exponent > 0 and not (sample & exp_mask):
            exponent -= 1
            exp_mask >>= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        values.append((~(sign | (exponent << 4) | mantissa)) & 0xFF)
    return tuple(values)


LINEAR_TO_ULAW_TABLE = _build_linear_to_ulaw_table()


def _build_alaw_to_ulaw_table():
    values = []
    for index in range(256):
        alaw = index ^ 0x55
        sign = alaw & 0x80
        exponent = (alaw >> 4) & 0x07
        mantissa = alaw & 0x0F
        sample = (mantissa << 4) + 8
        if exponent > 0:
            sample += 0x100
            sample <<= (exponent - 1)
        if not sign:
            sample = -sample
        values.append(linear_to_ulaw(sample))
    return bytes(values)


ALAW_TO_ULAW_TABLE = _build_alaw_to_ulaw_table()

ULAW_TO_PCM16LE_TABLE = tuple(
    struct.pack("<h", int(sample)) for sample in ULAW_TO_LINEAR_TABLE
)


def mix_ulaw_frames(frames):
    if not frames:
        return MULTICAST_RTP_SILENCE_FRAME
    if len(frames) == 1:
        return frames[0]
    mixed = bytearray(MULTICAST_RTP_FRAME_SIZE)
    frame_count = len(frames)
    for index in range(MULTICAST_RTP_FRAME_SIZE):
        total = 0
        for frame in frames:
            total += ULAW_TO_LINEAR_TABLE[frame[index]]
        # Sum then clip to 16-bit range; wrapping overflow causes severe
        # distortion when simultaneous sources (e.g. bell clusters) overlap.
        if total > 32767:
            total = 32767
        elif total < -32768:
            total = -32768
        mixed[index] = LINEAR_TO_ULAW_TABLE[total & 0xFFFF]
    return bytes(mixed)


def multicast_priority_value(metadata):
    if not isinstance(metadata, dict):
        return "Normal"
    priority = str(metadata.get("priority") or "Normal").strip().title()
    return priority if priority in VALID_MESSAGE_PRIORITIES else "Normal"


def multicast_broadcast_class(action, metadata):
    """Classify a dispatch as 'page', 'bell' or 'message' so standby background
    ducking can pick the matching amplify setting."""
    if action == "prepare_livepage":
        return "page"
    meta = metadata if isinstance(metadata, dict) else {}
    msg_type = str(meta.get("type") or "").strip().lower()
    if msg_type in ("page", "liveaudio"):
        return "page"
    sender = str(meta.get("sender") or "").strip().lower()
    template_id = str(meta.get("template_id") or "").strip().lower()
    if sender == "belld" or template_id.startswith("bell-"):
        return "bell"
    return "message"


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


def normalize_multicast_socket_destination(address):
    if not isinstance(address, tuple) or len(address) < 2:
        return None
    host = str(address[0] or "").split("%", 1)[0].strip()
    if not host:
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not ip.is_multicast:
        return None
    try:
        port = int(address[1])
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    return str(ip), port


def close_multicast_gateway_source():
    global multicast_gateway_source_sock
    if multicast_gateway_source_sock is None:
        return
    try:
        multicast_gateway_source_sock.close()
    except OSError:
        pass
    multicast_gateway_source_sock = None


def connect_multicast_gateway_source():
    global multicast_gateway_source_sock, multicast_gateway_source_next_retry
    if multicast_gateway_source_sock is not None:
        return multicast_gateway_source_sock
    now = time.monotonic()
    if now < multicast_gateway_source_next_retry:
        return None
    multicast_gateway_source_next_retry = now + 5.0
    infos = socket.getaddrinfo(MULTICAST_GATEWAY_HOST, MULTICAST_GATEWAY_PORT, socket.AF_UNSPEC, socket.SOCK_DGRAM)
    family, _socktype, _proto, _canonname, sockaddr = infos[0]
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.connect(sockaddr)
    multicast_gateway_source_sock = sock
    multicast_gateway_source_next_retry = 0.0
    return multicast_gateway_source_sock


def forward_multicast_packet(payload, address, port, family=None, ttl=None):
    destination = normalize_multicast_socket_destination((address, port))
    if destination is None:
        return False
    address, port = destination
    data = bytes(payload or b"")
    if not data:
        return False
    header = {"address": address, "port": port}
    if family == socket.AF_INET6 or ":" in address:
        header["family"] = 6
    else:
        header["family"] = 4
    if ttl not in (None, ""):
        try:
            header["ttl"] = int(ttl)
        except (TypeError, ValueError):
            pass
    with multicast_gateway_source_lock:
        try:
            sock = connect_multicast_gateway_source()
            if sock is None:
                return False
            sock.send(
                encode_local_source_packet(
                    address,
                    port,
                    data,
                    family=socket.AF_INET6 if header.get("family") == 6 else socket.AF_INET,
                    ttl=header.get("ttl"),
                )
            )
            return True
        except OSError:
            close_multicast_gateway_source()
            return False


def socket_multicast_ttl(sock):
    try:
        if sock.family == socket.AF_INET:
            value = sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL)
        elif sock.family == socket.AF_INET6:
            value = sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS)
        else:
            return None
    except OSError:
        return None
    if isinstance(value, bytes):
        if len(value) >= 4:
            return struct.unpack("!I", value[:4])[0]
        if len(value) == 1:
            return value[0]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def install_multicast_gateway_sendto_patch():
    global multicast_socket_sendto_patched
    if multicast_socket_sendto_patched:
        return
    original_sendto = socket.socket.sendto

    # Forward packets to the gateway from a background worker so the RTP
    # sender threads never block on locks, DNS lookups, or gateway sends.
    forward_queue = deque(maxlen=512)
    forward_wakeup = threading.Event()

    def forward_worker():
        while True:
            forward_wakeup.wait()
            forward_wakeup.clear()
            while True:
                try:
                    data, host, port, family, ttl = forward_queue.popleft()
                except IndexError:
                    break
                try:
                    forward_multicast_packet(data, host, port, family=family, ttl=ttl)
                except Exception:
                    pass

    threading.Thread(target=forward_worker, daemon=True).start()

    def patched_sendto(sock, data, *args):
        address = None
        if len(args) == 1:
            address = args[0]
        elif len(args) >= 2:
            address = args[1]
        destination = normalize_multicast_socket_destination(address)
        if destination is not None and sock.type == socket.SOCK_DGRAM:
            host, port = destination
            ttl = socket_multicast_ttl(sock)
            try:
                forward_queue.append((bytes(data), host, port, sock.family, ttl))
                forward_wakeup.set()
            except Exception:
                pass
        return original_sendto(sock, data, *args)

    socket.socket.sendto = patched_sendto
    multicast_socket_sendto_patched = True


def get_dict_db_connection():
    if pymysql is None:
        raise RuntimeError("PyMySQL is not installed")
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )


def sip_table_columns(cur, table):
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    return {row["Field"] for row in cur.fetchall() if row.get("Field")}


def ensure_multicast_rtp_schema():
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{MULTICAST_RTP_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`address` VARCHAR(100) NOT NULL DEFAULT '', "
                "`port` INT NOT NULL DEFAULT 0, "
                "`codec` VARCHAR(8) NOT NULL DEFAULT 'PCMU', "
                "`packet_ms` INT NOT NULL DEFAULT 20, "
                "`standby_mode` VARCHAR(16) NOT NULL DEFAULT 'stop', "
                "`standby_sources` MEDIUMTEXT NULL, "
                "`standby_msg_action` VARCHAR(16) NOT NULL DEFAULT 'stop', "
                "`standby_msg_priority` VARCHAR(12) NOT NULL DEFAULT 'Emergency', "
                "`emergency_sources` MEDIUMTEXT NULL, "
                "`amp_master` VARCHAR(8) NOT NULL DEFAULT '0', "
                "`amp_page` VARCHAR(8) NOT NULL DEFAULT '-10', "
                "`amp_bell` VARCHAR(8) NOT NULL DEFAULT '0', "
                "`amp_message` VARCHAR(8) NOT NULL DEFAULT 'mute', "
                "`mute_priority_enabled` TINYINT(1) NOT NULL DEFAULT 0, "
                "`mute_priority` VARCHAR(12) NOT NULL DEFAULT 'High', "
                "PRIMARY KEY (`id`), UNIQUE KEY `address_port_unique` (`address`, `port`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
            columns = sip_table_columns(cur, MULTICAST_RTP_TABLE)
            additions = {
                "name": "`name` VARCHAR(255) NOT NULL DEFAULT ''",
                "address": "`address` VARCHAR(100) NOT NULL DEFAULT ''",
                "port": "`port` INT NOT NULL DEFAULT 0",
                "codec": "`codec` VARCHAR(8) NOT NULL DEFAULT 'PCMU'",
                "packet_ms": "`packet_ms` INT NOT NULL DEFAULT 20",
                "standby_mode": "`standby_mode` VARCHAR(16) NOT NULL DEFAULT 'stop'",
                "standby_sources": "`standby_sources` MEDIUMTEXT NULL",
                "standby_msg_action": "`standby_msg_action` VARCHAR(16) NOT NULL DEFAULT 'stop'",
                "standby_msg_priority": "`standby_msg_priority` VARCHAR(12) NOT NULL DEFAULT 'Emergency'",
                "emergency_sources": "`emergency_sources` MEDIUMTEXT NULL",
                "amp_master": "`amp_master` VARCHAR(8) NOT NULL DEFAULT '0'",
                "amp_page": "`amp_page` VARCHAR(8) NOT NULL DEFAULT '-10'",
                "amp_bell": "`amp_bell` VARCHAR(8) NOT NULL DEFAULT '0'",
                "amp_message": "`amp_message` VARCHAR(8) NOT NULL DEFAULT 'mute'",
                "mute_priority_enabled": "`mute_priority_enabled` TINYINT(1) NOT NULL DEFAULT 0",
                "mute_priority": "`mute_priority` VARCHAR(12) NOT NULL DEFAULT 'High'",
            }
            for column, sql in additions.items():
                if column not in columns:
                    cur.execute(f"ALTER TABLE `{MULTICAST_RTP_TABLE}` ADD COLUMN {sql}")
        conn.commit()
    finally:
        conn.close()


def multicast_rtp_normalize_address(value):
    raw = str(value or "").strip()
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ValueError("Enter a valid multicast address.") from exc
    if not ip.is_multicast:
        raise ValueError("Enter a multicast address.")
    return str(ip)


def multicast_rtp_clean_port(value, require_even=False):
    try:
        port = int(str(value or "").strip())
    except ValueError as exc:
        raise ValueError("Enter a valid UDP port.") from exc
    if port < 1 or port > 65535:
        raise ValueError("Enter a valid UDP port.")
    if require_even and port % 2 != 0:
        raise ValueError("Enter an even UDP port.")
    return port


def multicast_rtp_clean_codec(value):
    codec = str(value or "PCMU").strip().upper()
    if codec not in MULTICAST_RTP_CODECS:
        raise ValueError("Choose a valid codec.")
    return codec


def multicast_rtp_clean_packet_ms(value):
    raw = str(value if value not in (None, "") else MULTICAST_RTP_DEFAULT_PACKET_MS).strip()
    try:
        packet_ms = int(raw)
    except ValueError as exc:
        raise ValueError("Enter a valid packet size.") from exc
    if packet_ms < MULTICAST_RTP_MIN_PACKET_MS or packet_ms > MULTICAST_RTP_MAX_PACKET_MS or packet_ms % 20 != 0:
        raise ValueError("Packet size must be a 20 ms increment between 20 and 200 ms.")
    return packet_ms


def multicast_rtp_clean_standby_mode(value):
    mode = str(value or "stop").strip().lower()
    if mode not in MULTICAST_RTP_STANDBY_MODES:
        raise ValueError("Choose a valid standby behaviour.")
    return mode


def multicast_rtp_clean_msg_action(value):
    action = str(value or "stop").strip().lower()
    if action not in MULTICAST_RTP_STANDBY_MSG_ACTIONS:
        raise ValueError("Choose a valid standby message action.")
    return action


def multicast_rtp_gain_options(selected):
    """Build <option> markup for an amplify dropdown: Mute at the top, then
    +20 dB down to -40 dB."""
    selected = str(selected or "0").strip().lower()
    opts = [f'<option value="mute"{" selected" if selected == "mute" else ""}>Mute</option>']
    for db in range(MULTICAST_RTP_AMP_MAX_DB, MULTICAST_RTP_AMP_MIN_DB - 1, -1):
        if db > 0:
            label = f"+{db} dB"
        elif db < 0:
            label = f"{db} dB"
        else:
            label = "0 dB"
        is_sel = selected == str(db)
        opts.append(f'<option value="{db}"{" selected" if is_sel else ""}>{label}</option>')
    return "".join(opts)


def multicast_rtp_clean_msg_priority(value):
    priority = str(value or "High").strip().title()
    if priority not in VALID_MESSAGE_PRIORITIES:
        raise ValueError("Choose a valid message priority.")
    return priority


def multicast_rtp_clean_gain(value, default="0"):
    """Normalize an amplify field to either 'mute' or a clamped integer dB
    string. Falls back to ``default`` for blank/garbage input."""
    raw = str(value if value not in (None, "") else default).strip().lower()
    if raw == "mute":
        return "mute"
    try:
        db = int(round(float(raw)))
    except (TypeError, ValueError):
        raw = str(default).strip().lower()
        if raw == "mute":
            return "mute"
        try:
            db = int(round(float(raw)))
        except (TypeError, ValueError):
            db = 0
    db = max(MULTICAST_RTP_AMP_MIN_DB, min(MULTICAST_RTP_AMP_MAX_DB, db))
    return str(db)


def multicast_rtp_gain_factor(spec):
    """Return a linear amplitude multiplier for an amplify spec. 'mute' -> 0.0,
    0 dB -> 1.0."""
    if spec == "mute":
        return 0.0
    try:
        db = float(spec)
    except (TypeError, ValueError):
        return 1.0
    return 10.0 ** (db / 20.0)


def multicast_rtp_combine_gain(a, b):
    """Sum two gain specs (dB) into one clamped spec; 'mute' is absorbing."""
    if a == "mute" or b == "mute":
        return "mute"
    try:
        total = int(round(float(a))) + int(round(float(b)))
    except (TypeError, ValueError):
        total = 0
    total = max(MULTICAST_RTP_AMP_MIN_DB, min(MULTICAST_RTP_AMP_MAX_DB, total))
    return str(total)


def apply_gain_ulaw_frame(frame, spec):
    """Apply a gain spec to a u-law frame, returning a new u-law frame. Fast
    paths for the common no-op (0 dB) and mute cases."""
    if spec == "mute":
        return MULTICAST_RTP_SILENCE_FRAME
    if spec in (None, "0", 0):
        return frame
    factor = multicast_rtp_gain_factor(spec)
    if factor == 1.0:
        return frame
    if factor == 0.0:
        return MULTICAST_RTP_SILENCE_FRAME
    out = bytearray(len(frame))
    for i in range(len(frame)):
        sample = int(ULAW_TO_LINEAR_TABLE[frame[i]] * factor)
        if sample > 32767:
            sample = 32767
        elif sample < -32768:
            sample = -32768
        out[i] = LINEAR_TO_ULAW_TABLE[sample & 0xFFFF]
    return bytes(out)


def multicast_rtp_parse_source(url):
    """Parse a single background audio source string into a descriptor.

    Accepts http(s) URLs and rtp://host:port addresses. Returns a dict with a
    ``kind`` of ``http``, ``multicast`` or ``unicast`` plus the normalized
    ``url``. Raises ValueError on anything that isn't a supported source.
    """
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("Enter a stream URL.")
    lowered = raw.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return {"kind": "http", "url": raw, "host": "", "port": 0}
    if lowered.startswith("rtp://"):
        rest = raw[6:]
        rest = rest.split("/", 1)[0]
        if rest.startswith("["):
            host, _, tail = rest[1:].partition("]")
            port_part = tail[1:] if tail.startswith(":") else ""
        else:
            host, _, port_part = rest.partition(":")
        host = host.strip()
        port_part = port_part.strip()
        if not host or not port_part:
            raise ValueError("Enter an rtp:// address as rtp://host:port.")
        try:
            port = int(port_part)
        except ValueError as exc:
            raise ValueError("Enter a valid rtp:// port.") from exc
        if port < 1 or port > 65535:
            raise ValueError("Enter a valid rtp:// port.")
        kind = "unicast"
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_multicast:
                kind = "multicast"
        except ValueError:
            kind = "unicast"
        return {"kind": kind, "url": raw, "host": host, "port": port}
    raise ValueError("Enter an http(s) URL or rtp://host:port address.")


def multicast_rtp_clean_source_list(value):
    """Normalize a background source list (JSON array or newline text) into a
    validated list of source URL strings (highest priority first)."""
    items = []
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        text = str(value or "").strip()
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    items = parsed
                else:
                    items = [text]
            except (ValueError, TypeError):
                items = [line for line in text.splitlines()]
    cleaned = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("url")
        url = str(item or "").strip()
        if not url:
            continue
        multicast_rtp_parse_source(url)
        cleaned.append(url)
        if len(cleaned) >= MULTICAST_RTP_MAX_STANDBY_SOURCES:
            break
    return cleaned


def multicast_rtp_unicast_port_in_use(port):
    """Best-effort check whether a UDP port can be bound locally. Used to warn
    when a unicast RTP background source targets a port already in use."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if port < 1 or port > 65535:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def multicast_rtp_standby_port_conflicts(source_urls):
    conflicts = []
    for url in source_urls or []:
        try:
            parsed = multicast_rtp_parse_source(url)
        except ValueError:
            continue
        if parsed["kind"] == "unicast" and multicast_rtp_unicast_port_in_use(parsed["port"]):
            conflicts.append(parsed["port"])
    return conflicts


def multicast_rtp_clean_values(values):
    name = str(values.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required.")
    standby_mode = multicast_rtp_clean_standby_mode(values.get("standby_mode"))
    standby_sources = multicast_rtp_clean_source_list(values.get("standby_sources"))
    msg_action = multicast_rtp_clean_msg_action(values.get("standby_msg_action"))
    emergency_sources = multicast_rtp_clean_source_list(values.get("emergency_sources"))
    if standby_mode == "rebroadcast" and not standby_sources:
        raise ValueError("Add at least one background audio source.")
    if standby_mode == "rebroadcast" and msg_action == "emergency" and not emergency_sources:
        raise ValueError("Add at least one emergency stream.")
    return {
        "name": name,
        "address": multicast_rtp_normalize_address(values.get("address")),
        "port": multicast_rtp_clean_port(values.get("port"), require_even=True),
        "codec": multicast_rtp_clean_codec(values.get("codec")),
        "packet_ms": multicast_rtp_clean_packet_ms(values.get("packet_ms")),
        "standby_mode": standby_mode,
        "standby_sources": json.dumps(standby_sources),
        "standby_msg_action": multicast_rtp_clean_msg_action(values.get("standby_msg_action")),
        "standby_msg_priority": multicast_rtp_clean_msg_priority(values.get("standby_msg_priority")),
        "emergency_sources": json.dumps(emergency_sources),
        "amp_master": multicast_rtp_clean_gain(values.get("amp_master"), MULTICAST_RTP_AMP_DEFAULTS["amp_master"]),
        "amp_page": multicast_rtp_clean_gain(values.get("amp_page"), MULTICAST_RTP_AMP_DEFAULTS["amp_page"]),
        "amp_bell": multicast_rtp_clean_gain(values.get("amp_bell"), MULTICAST_RTP_AMP_DEFAULTS["amp_bell"]),
        "amp_message": multicast_rtp_clean_gain(values.get("amp_message"), MULTICAST_RTP_AMP_DEFAULTS["amp_message"]),
        "mute_priority_enabled": 1 if str(values.get("mute_priority_enabled") or "").strip().lower() in ("1", "on", "true", "yes") else 0,
        "mute_priority": multicast_rtp_clean_msg_priority(values.get("mute_priority")),
    }


def multicast_rtp_form_values(form, row=None):
    row = row or {}
    defaults = {
        "name": str(row.get("name") or ""),
        "address": str(row.get("address") or ""),
        "port": str(row.get("port") or ""),
        "codec": multicast_rtp_clean_codec(row.get("codec") or "PCMU"),
        "packet_ms": str(row.get("packet_ms") or MULTICAST_RTP_DEFAULT_PACKET_MS),
        "standby_mode": str(row.get("standby_mode") or "stop"),
        "standby_sources": json.dumps(multicast_rtp_clean_source_list(row.get("standby_sources"))) if row else "[]",
        "standby_msg_action": str(row.get("standby_msg_action") or "stop"),
        "standby_msg_priority": str(row.get("standby_msg_priority") or "Emergency"),
        "emergency_sources": json.dumps(multicast_rtp_clean_source_list(row.get("emergency_sources"))) if row else "[]",
        "amp_master": multicast_rtp_clean_gain(row.get("amp_master"), MULTICAST_RTP_AMP_DEFAULTS["amp_master"]),
        "amp_page": multicast_rtp_clean_gain(row.get("amp_page"), MULTICAST_RTP_AMP_DEFAULTS["amp_page"]),
        "amp_bell": multicast_rtp_clean_gain(row.get("amp_bell"), MULTICAST_RTP_AMP_DEFAULTS["amp_bell"]),
        "amp_message": multicast_rtp_clean_gain(row.get("amp_message"), MULTICAST_RTP_AMP_DEFAULTS["amp_message"]),
        "mute_priority_enabled": "1" if str(row.get("mute_priority_enabled") or "0").strip() in ("1", "on", "true", "yes", "True") else "0",
        "mute_priority": str(row.get("mute_priority") or "High"),
    }
    result = {key: str(form.get(key, defaults[key]) if form is not None else defaults[key]).strip() for key in defaults}
    if form is not None:
        # An unchecked checkbox is simply absent from the POST body; treat that
        # as disabled so the toggle can actually be turned off.
        result["mute_priority_enabled"] = "1" if str(form.get("mute_priority_enabled") or "").strip() else "0"
    return result


MULTICAST_RTP_COLUMNS = (
    "`id`, `name`, `address`, `port`, `codec`, `packet_ms`, "
    "`standby_mode`, `standby_sources`, `standby_msg_action`, "
    "`standby_msg_priority`, `emergency_sources`, "
    "`amp_master`, `amp_page`, `amp_bell`, `amp_message`, "
    "`mute_priority_enabled`, `mute_priority`"
)


def multicast_rtp_rows():
    ensure_multicast_rtp_schema()
    return sip_query_all(
        f"SELECT {MULTICAST_RTP_COLUMNS} FROM `{MULTICAST_RTP_TABLE}` ORDER BY `name` ASC, `id` ASC"
    )


def multicast_rtp_row(row_id):
    ensure_multicast_rtp_schema()
    rows = sip_query_all(
        f"SELECT {MULTICAST_RTP_COLUMNS} FROM `{MULTICAST_RTP_TABLE}` WHERE id=%s LIMIT 1",
        (row_id,),
    )
    return rows[0] if rows else None


def multicast_rtp_notify_config_changed():
    """Best-effort nudge so the runtime reconciles standby broadcasters after a
    config change. Safe to call from the web process where the runtime module
    is not loaded (the runtime also reconciles from the DB periodically)."""
    runtime = globals().get("multicast_rtp_runtime")
    if runtime is not None and hasattr(runtime, "reconcile_standby"):
        try:
            runtime.reconcile_standby()
        except Exception as exc:
            log(f"multicast rtp standby reconcile error: {exc}")


def multicast_rtp_rows_for_targets(targets):
    tokens = {str(target or "").strip() for target in targets or [] if str(target or "").strip()}
    rows = multicast_rtp_rows()
    if any(token.lower() == "all" for token in tokens):
        return rows
    wanted_ids = set()
    for token in tokens:
        lowered = token.lower()
        if lowered.startswith("stream-"):
            _, _, suffix = lowered.partition("-")
            if suffix.isdigit():
                wanted_ids.add(suffix)
        elif lowered.isdigit():
            wanted_ids.add(lowered)
    return [row for row in rows if str(row.get("id")) in wanted_ids]


def ensure_httprequest_schema():
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{HTTP_REQUEST_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`method` VARCHAR(10) NOT NULL DEFAULT 'POST', "
                "`url` TEXT DEFAULT NULL, "
                "`body` TEXT DEFAULT NULL, "
                "`auth_type` VARCHAR(32) NOT NULL DEFAULT 'none', "
                "`auth_username` VARCHAR(255) NOT NULL DEFAULT '', "
                "`auth_password` VARCHAR(255) NOT NULL DEFAULT '', "
                "`auth_header_name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`auth_header_value` TEXT DEFAULT NULL, "
                "`headers_json` LONGTEXT DEFAULT NULL, "
                "`timeout` INT NOT NULL DEFAULT 30, "
                "`include_audio_only` TINYINT NOT NULL DEFAULT 1, "
                "PRIMARY KEY (`id`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
            columns = sip_table_columns(cur, HTTP_REQUEST_TABLE)
            additions = {
                "name": "`name` VARCHAR(255) NOT NULL DEFAULT ''",
                "method": "`method` VARCHAR(10) NOT NULL DEFAULT 'POST'",
                "url": "`url` TEXT DEFAULT NULL",
                "body": "`body` TEXT DEFAULT NULL",
                "auth_type": "`auth_type` VARCHAR(32) NOT NULL DEFAULT 'none'",
                "auth_username": "`auth_username` VARCHAR(255) NOT NULL DEFAULT ''",
                "auth_password": "`auth_password` VARCHAR(255) NOT NULL DEFAULT ''",
                "auth_header_name": "`auth_header_name` VARCHAR(255) NOT NULL DEFAULT ''",
                "auth_header_value": "`auth_header_value` TEXT DEFAULT NULL",
                "headers_json": "`headers_json` LONGTEXT DEFAULT NULL",
                "timeout": "`timeout` INT NOT NULL DEFAULT 30",
                "include_audio_only": "`include_audio_only` TINYINT NOT NULL DEFAULT 1",
            }
            for column, sql in additions.items():
                if column not in columns:
                    cur.execute(f"ALTER TABLE `{HTTP_REQUEST_TABLE}` ADD COLUMN {sql}")
        conn.commit()
    finally:
        conn.close()


def http_request_clean_method(value):
    method = str(value or "POST").strip().upper()
    if method not in HTTP_REQUEST_METHODS:
        raise ValueError("Choose a valid HTTP method.")
    return method


def http_request_clean_auth_type(value):
    auth_type = str(value or "none").strip().lower()
    if auth_type not in HTTP_REQUEST_AUTH_TYPES:
        raise ValueError("Choose a valid authentication type.")
    return auth_type


def http_request_clean_timeout(value):
    raw = str(value if value not in (None, "") else HTTP_REQUEST_DEFAULT_TIMEOUT).strip()
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise ValueError("Enter a valid timeout.") from exc
    if timeout < 1 or timeout > 600:
        raise ValueError("Timeout must be between 1 and 600 seconds.")
    return timeout


def http_request_form_headers(form, row=None):
    row = row or {}
    defaults = sip_clean_headers(row.get("headers") if isinstance(row.get("headers"), list) else row.get("headers_json"))
    if form is None:
        return defaults or [{"name": "", "value": ""}]
    names = form.getlist("header_name") if hasattr(form, "getlist") else []
    values = form.getlist("header_value") if hasattr(form, "getlist") else []
    headers = []
    for index in range(max(len(names), len(values))):
        headers.append(
            {
                "name": str(names[index] if index < len(names) else "").strip(),
                "value": str(values[index] if index < len(values) else "").strip(),
            }
        )
    return headers or [{"name": "", "value": ""}]


def http_request_form_values(form, row=None):
    row = row or {}
    include_audio_only_default = row.get("include_audio_only")
    if include_audio_only_default is None:
        include_audio_only_default = "1"
    else:
        include_audio_only_default = "1" if int(include_audio_only_default) else "0"
    values = {
        "name": str(row.get("name") or ""),
        "method": http_request_clean_method(row.get("method") or "POST"),
        "url": str(row.get("url") or ""),
        "body": str(row.get("body") or ""),
        "auth_type": http_request_clean_auth_type(row.get("auth_type") or "none"),
        "auth_username": str(row.get("auth_username") or ""),
        "auth_password": str(row.get("auth_password") or ""),
        "auth_header_name": str(row.get("auth_header_name") or ""),
        "auth_header_value": str(row.get("auth_header_value") or ""),
        "timeout": str(row.get("timeout") or HTTP_REQUEST_DEFAULT_TIMEOUT),
        "include_audio_only": include_audio_only_default,
        "headers": http_request_form_headers(None, row),
    }
    if form is None:
        return values
    return {
        "name": str(form.get("name", values["name"])).strip(),
        "method": str(form.get("method", values["method"])).strip().upper(),
        "url": str(form.get("url", values["url"])).strip(),
        "body": str(form.get("body", values["body"])),
        "auth_type": str(form.get("auth_type", values["auth_type"])).strip().lower(),
        "auth_username": str(form.get("auth_username", values["auth_username"])).strip(),
        "auth_password": str(form.get("auth_password", values["auth_password"])),
        "auth_header_name": str(form.get("auth_header_name", values["auth_header_name"])).strip(),
        "auth_header_value": str(form.get("auth_header_value", values["auth_header_value"])),
        "timeout": str(form.get("timeout", values["timeout"])).strip(),
        "include_audio_only": "1" if form.get("include_audio_only") else "0",
        "headers": http_request_form_headers(form, row),
    }


def http_request_clean_values(values):
    name = str(values.get("name") or "").strip()
    url = str(values.get("url") or "").strip()
    if not name:
        raise ValueError("Name is required.")
    if not url:
        raise ValueError("URL is required.")
    auth_type = http_request_clean_auth_type(values.get("auth_type"))
    auth_username = str(values.get("auth_username") or "").strip()
    auth_password = str(values.get("auth_password") or "")
    auth_header_name = str(values.get("auth_header_name") or "").strip()
    auth_header_value = str(values.get("auth_header_value") or "")
    if auth_type in {"basic", "digest"} and not auth_username:
        raise ValueError("Username is required for the selected authentication type.")
    if auth_type == "apikey":
        if not auth_header_name:
            raise ValueError("Header name is required for API Key authentication.")
        if ":" in auth_header_name or "\r" in auth_header_name or "\n" in auth_header_name:
            raise ValueError("API Key header name is invalid.")
        if "\r" in auth_header_value or "\n" in auth_header_value:
            raise ValueError("API Key header value is invalid.")
    return {
        "name": name,
        "method": http_request_clean_method(values.get("method")),
        "url": url,
        "body": str(values.get("body") or ""),
        "auth_type": auth_type,
        "auth_username": auth_username,
        "auth_password": auth_password,
        "auth_header_name": auth_header_name,
        "auth_header_value": auth_header_value,
        "headers": sip_clean_headers(values.get("headers") or []),
        "timeout": http_request_clean_timeout(values.get("timeout")),
        "include_audio_only": 1 if str(values.get("include_audio_only") or "1") == "1" else 0,
    }


def http_request_rows():
    ensure_httprequest_schema()
    return sip_query_all(
        f"SELECT `id`, `name`, `method`, `url`, `body`, `auth_type`, `auth_username`, `auth_password`, "
        f"`auth_header_name`, `auth_header_value`, `headers_json`, `timeout`, `include_audio_only` "
        f"FROM `{HTTP_REQUEST_TABLE}` ORDER BY `name` ASC, `id` ASC"
    )


def http_request_row(row_id):
    ensure_httprequest_schema()
    rows = sip_query_all(
        f"SELECT `id`, `name`, `method`, `url`, `body`, `auth_type`, `auth_username`, `auth_password`, "
        f"`auth_header_name`, `auth_header_value`, `headers_json`, `timeout`, `include_audio_only` "
        f"FROM `{HTTP_REQUEST_TABLE}` WHERE id=%s LIMIT 1",
        (row_id,),
    )
    return rows[0] if rows else None


def http_request_rows_for_targets(targets):
    tokens = {str(target or "").strip() for target in targets or [] if str(target or "").strip()}
    rows = http_request_rows()
    if any(token.lower() == "all" for token in tokens):
        return rows
    wanted_ids = set()
    for token in tokens:
        lowered = token.lower()
        if lowered.startswith("request-"):
            _, _, suffix = lowered.partition("-")
            if suffix.isdigit():
                wanted_ids.add(suffix)
        elif lowered.isdigit():
            wanted_ids.add(lowered)
    return [row for row in rows if str(row.get("id")) in wanted_ids]


SERVICE_MONITOR_MESSAGE_FIELDS = (
    "enabled", "send_all", "groups", "shortmessage", "longmessage",
    "icon", "color", "audio", "priority", "vendor_specific", "expires",
)


def ensure_servicemonitor_schema():
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{SERVICE_MONITOR_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`monitor_type` VARCHAR(32) NOT NULL DEFAULT 'ping', "
                "`check_interval` INT NOT NULL DEFAULT 60, "
                "`disabled` TINYINT NOT NULL DEFAULT 0, "
                "`host` VARCHAR(255) NOT NULL DEFAULT '', "
                "`port` INT NOT NULL DEFAULT 0, "
                "`http_url` TEXT DEFAULT NULL, "
                "`http_fail_codes` TEXT DEFAULT NULL, "
                "`uk_base_url` TEXT DEFAULT NULL, "
                "`uk_api_key` VARCHAR(512) NOT NULL DEFAULT '', "
                "`uk_monitor` VARCHAR(255) NOT NULL DEFAULT '', "
                "`last_state` VARCHAR(16) NOT NULL DEFAULT 'unchecked', "
                "`last_checked` DATETIME DEFAULT NULL, "
                "`last_error` TEXT DEFAULT NULL, "
                "`retries` INT NOT NULL DEFAULT 5, "
                "`wait_for_up` INT NOT NULL DEFAULT 0, "
                "`fail_count` INT NOT NULL DEFAULT 0, "
                "`down_broadcast_id` VARCHAR(64) DEFAULT NULL, "
                + "".join(
                    f"`{direction}_{field}` {sql}, "
                    for direction in ("down", "up")
                    for field, sql in (
                        ("enabled", "TINYINT NOT NULL DEFAULT 0"),
                        ("send_all", "TINYINT NOT NULL DEFAULT 0"),
                        ("groups", "TEXT DEFAULT NULL"),
                        ("shortmessage", "TEXT DEFAULT NULL"),
                        ("longmessage", "TEXT DEFAULT NULL"),
                        ("icon", "VARCHAR(255) NOT NULL DEFAULT ''"),
                        ("color", "VARCHAR(16) NOT NULL DEFAULT ''"),
                        ("audio", "TEXT DEFAULT NULL"),
                        ("priority", "VARCHAR(16) NOT NULL DEFAULT 'Normal'"),
                        ("vendor_specific", "LONGTEXT DEFAULT NULL"),
                        ("expires", "VARCHAR(255) NOT NULL DEFAULT 'manual'"),
                    )
                )
                + "PRIMARY KEY (`id`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
            columns = sip_table_columns(cur, SERVICE_MONITOR_TABLE)
            additions = {
                "name": "`name` VARCHAR(255) NOT NULL DEFAULT ''",
                "monitor_type": "`monitor_type` VARCHAR(32) NOT NULL DEFAULT 'ping'",
                "check_interval": "`check_interval` INT NOT NULL DEFAULT 60",
                "disabled": "`disabled` TINYINT NOT NULL DEFAULT 0",
                "host": "`host` VARCHAR(255) NOT NULL DEFAULT ''",
                "port": "`port` INT NOT NULL DEFAULT 0",
                "http_url": "`http_url` TEXT DEFAULT NULL",
                "http_fail_codes": "`http_fail_codes` TEXT DEFAULT NULL",
                "uk_base_url": "`uk_base_url` TEXT DEFAULT NULL",
                "uk_api_key": "`uk_api_key` VARCHAR(512) NOT NULL DEFAULT ''",
                "uk_monitor": "`uk_monitor` VARCHAR(255) NOT NULL DEFAULT ''",
                "last_state": "`last_state` VARCHAR(16) NOT NULL DEFAULT 'unchecked'",
                "last_checked": "`last_checked` DATETIME DEFAULT NULL",
                "last_error": "`last_error` TEXT DEFAULT NULL",
                "retries": "`retries` INT NOT NULL DEFAULT 5",
                "wait_for_up": "`wait_for_up` INT NOT NULL DEFAULT 0",
                "fail_count": "`fail_count` INT NOT NULL DEFAULT 0",
                "down_broadcast_id": "`down_broadcast_id` VARCHAR(64) DEFAULT NULL",
            }
            for direction in ("down", "up"):
                additions[f"{direction}_enabled"] = f"`{direction}_enabled` TINYINT NOT NULL DEFAULT 0"
                additions[f"{direction}_send_all"] = f"`{direction}_send_all` TINYINT NOT NULL DEFAULT 0"
                additions[f"{direction}_groups"] = f"`{direction}_groups` TEXT DEFAULT NULL"
                additions[f"{direction}_shortmessage"] = f"`{direction}_shortmessage` TEXT DEFAULT NULL"
                additions[f"{direction}_longmessage"] = f"`{direction}_longmessage` TEXT DEFAULT NULL"
                additions[f"{direction}_icon"] = f"`{direction}_icon` VARCHAR(255) NOT NULL DEFAULT ''"
                additions[f"{direction}_color"] = f"`{direction}_color` VARCHAR(16) NOT NULL DEFAULT ''"
                additions[f"{direction}_audio"] = f"`{direction}_audio` TEXT DEFAULT NULL"
                additions[f"{direction}_priority"] = f"`{direction}_priority` VARCHAR(16) NOT NULL DEFAULT 'Normal'"
                additions[f"{direction}_vendor_specific"] = f"`{direction}_vendor_specific` LONGTEXT DEFAULT NULL"
                additions[f"{direction}_expires"] = f"`{direction}_expires` VARCHAR(255) NOT NULL DEFAULT 'manual'"
            for column, sql in additions.items():
                if column not in columns:
                    cur.execute(f"ALTER TABLE `{SERVICE_MONITOR_TABLE}` ADD COLUMN {sql}")
        conn.commit()
    finally:
        conn.close()


SERVICE_MONITOR_COLUMNS = (
    "`id`, `name`, `monitor_type`, `check_interval`, `disabled`, `host`, `port`, "
    "`http_url`, `http_fail_codes`, `uk_base_url`, `uk_api_key`, `uk_monitor`, "
    "`last_state`, `last_checked`, `last_error`, `retries`, `wait_for_up`, `fail_count`, `down_broadcast_id`, "
    + ", ".join(
        f"`{direction}_{field}`"
        for direction in ("down", "up")
        for field in ("enabled", "send_all", "groups", "shortmessage", "longmessage",
                      "icon", "color", "audio", "priority", "vendor_specific", "expires")
    )
)


def service_monitor_rows():
    ensure_servicemonitor_schema()
    return sip_query_all(
        f"SELECT {SERVICE_MONITOR_COLUMNS} FROM `{SERVICE_MONITOR_TABLE}` ORDER BY `name` ASC, `id` ASC"
    )


def service_monitor_row(row_id):
    ensure_servicemonitor_schema()
    rows = sip_query_all(
        f"SELECT {SERVICE_MONITOR_COLUMNS} FROM `{SERVICE_MONITOR_TABLE}` WHERE id=%s LIMIT 1",
        (row_id,),
    )
    return rows[0] if rows else None


def service_monitor_clean_type(value):
    monitor_type = str(value or "ping").strip().lower()
    if monitor_type not in SERVICE_MONITOR_TYPES:
        raise ValueError("Choose a valid monitor type.")
    return monitor_type


def service_monitor_clean_interval(value):
    raw = str(value if value not in (None, "") else SERVICE_MONITOR_DEFAULT_INTERVAL).strip()
    try:
        interval = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid check interval.") from exc
    if interval < SERVICE_MONITOR_MIN_INTERVAL or interval > SERVICE_MONITOR_MAX_INTERVAL:
        raise ValueError(
            f"Check interval must be between {SERVICE_MONITOR_MIN_INTERVAL} and {SERVICE_MONITOR_MAX_INTERVAL} seconds."
        )
    return interval


def service_monitor_clean_retries(value):
    raw = str(value if value not in (None, "") else SERVICE_MONITOR_DEFAULT_RETRIES).strip()
    try:
        retries = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid number of retries.") from exc
    if retries < SERVICE_MONITOR_MIN_RETRIES or retries > SERVICE_MONITOR_MAX_RETRIES:
        raise ValueError(
            f"Retries must be between {SERVICE_MONITOR_MIN_RETRIES} and {SERVICE_MONITOR_MAX_RETRIES}."
        )
    return retries


def service_monitor_clean_wait_for_up(value):
    raw = str(value if value not in (None, "") else SERVICE_MONITOR_DEFAULT_WAIT_FOR_UP).strip()
    try:
        wait = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid Wait for Up value.") from exc
    if wait < SERVICE_MONITOR_MIN_WAIT_FOR_UP or wait > SERVICE_MONITOR_MAX_WAIT_FOR_UP:
        raise ValueError(
            f"Wait for Up must be between {SERVICE_MONITOR_MIN_WAIT_FOR_UP} and {SERVICE_MONITOR_MAX_WAIT_FOR_UP} seconds."
        )
    return wait
    raw = str(value if value not in (None, "") else "").strip()
    if not raw:
        if required:
            raise ValueError("Port is required.")
        return 0
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid port number.") from exc
    if port < 1 or port > 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return port


def service_monitor_parse_fail_codes(value):
    if isinstance(value, list):
        tokens = value
    else:
        try:
            tokens = json.loads(value) if value else []
        except (TypeError, ValueError):
            tokens = [tok.strip() for tok in str(value or "").split(",")]
    cleaned = []
    for token in tokens:
        token = str(token or "").strip().lower()
        if not token:
            continue
        if token in SERVICE_MONITOR_HTTP_FAIL_TOKENS:
            if token not in cleaned:
                cleaned.append(token)
        elif token.isdigit() and 100 <= int(token) <= 599:
            if token not in cleaned:
                cleaned.append(token)
    return cleaned


def service_monitor_http_status_is_failure(status_code, fail_tokens):
    if status_code is None:
        return "noresponse" in fail_tokens
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return False
    if str(code) in fail_tokens:
        return True
    if "4xx" in fail_tokens and 400 <= code <= 499:
        return True
    if "5xx" in fail_tokens and 500 <= code <= 599:
        return True
    return False


def service_monitor_address(row):
    monitor_type = str(row.get("monitor_type") or "ping").strip().lower()
    host = str(row.get("host") or "").strip()
    port = int(row.get("port") or 0)
    if monitor_type == "http":
        return str(row.get("http_url") or "").strip()
    if monitor_type == "uptimekuma":
        base = str(row.get("uk_base_url") or "").strip()
        monitor = str(row.get("uk_monitor") or "").strip()
        return f"{monitor} @ {base}" if monitor else base
    if monitor_type in ("tcp", "sip") and host and port:
        return f"{host}:{port}"
    return host


def service_monitor_state_label(state):
    state = str(state or "unchecked").strip().lower()
    if state == "online":
        return "Monitor online"
    if state == "offline":
        return "Monitor offline"
    if state == "kuma_down":
        return "Uptime Kuma down"
    if state == "disabled":
        return "Disabled"
    return "Unchecked"


def get_service_monitor_endpoint_status():
    endpoints = []
    for row in service_monitor_rows():
        row_id = row.get("id")
        disabled = bool(int(row.get("disabled") or 0))
        if disabled:
            state = "disabled"
        else:
            state = str(row.get("last_state") or "unchecked").strip().lower()
            if state not in ("online", "offline", "unchecked", "kuma_down"):
                state = "unchecked"
        monitor_type = str(row.get("monitor_type") or "ping").strip().lower()
        # kuma_down shows red like offline in the endpoints list but keeps its own label.
        css_state = "offline" if state == "kuma_down" else state
        endpoints.append(
            {
                "id": f"monitor-{row_id}",
                "name": str(row.get("name") or f"Service Monitor {row_id}"),
                "address": service_monitor_address(row),
                "model": "",
                "status": service_monitor_state_label(state),
                "status_state": css_state,
                "type": f"{SERVICE_MONITOR_TYPE_LABELS.get(monitor_type, 'Monitor')} Monitor",
                "direction": "Input",
                "input_type": "Input",
                "input_capable": True,
                "output_capable": False,
                "bell_capable": False,
                "available": not disabled,
                "capabilities": ["input"],
            }
        )
    return {
        "module": SERVICE_MONITOR_MODULE,
        "display_name": SERVICE_MONITOR_NAME,
        "name": SERVICE_MONITOR_NAME,
        "description": SERVICE_MONITOR_DESCRIPTION,
        "input_type": "Input",
        "system_builtin": True,
        "enabled": True,
        "loaded": True,
        "trusted": True,
        "can_load": True,
        "input_capable": True,
        "output_capable": False,
        "endpoints": endpoints,
    }


def service_monitor_endpoint_count():
    try:
        return len(service_monitor_rows())
    except Exception as exc:
        log(f"service monitor endpoint count error: {exc}")
        return 0


# --- Probes -----------------------------------------------------------------

def service_monitor_humanize_conn_error(exc):
    reason = exc
    if hasattr(exc, "reason") and getattr(exc, "reason") is not None:
        reason = getattr(exc, "reason")
    text = str(reason or exc or "").strip()
    lowered = text.lower()
    if isinstance(reason, (ConnectionRefusedError,)) or "refused" in lowered or "10061" in lowered:
        return "Host refused connection"
    if "unreachable" in lowered or "10065" in lowered or "no route" in lowered:
        return "Host unreachable"
    if "timed out" in lowered or "timeout" in lowered or isinstance(reason, socket.timeout):
        return "Connection timed out"
    if "name or service not known" in lowered or "getaddrinfo" in lowered or "nodename nor servname" in lowered or "name resolution" in lowered:
        return "Host name could not be resolved"
    return text or "Connection failed"


def service_monitor_probe_ping(host, timeout):
    host = str(host or "").strip()
    if not host:
        return False, "No host configured"
    timeout = max(1, int(timeout or 4))
    try:
        if os.name == "nt":
            args = ["ping", "-n", "4", "-w", str(timeout * 1000), host]
        else:
            args = ["ping", "-c", "4", "-W", str(timeout), host]
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=(timeout * 4) + 6,
            text=True,
        )
        output = (result.stdout or "").strip()
        if result.returncode == 0:
            return True, output
        return False, output or "No ping reply"
    except subprocess.TimeoutExpired:
        return False, "Ping timed out"
    except Exception as exc:
        return False, str(exc)


def service_monitor_probe_tcp(host, port, timeout):
    host = str(host or "").strip()
    if not host or not port:
        return False, "No host/port configured"
    try:
        with socket.create_connection((host, int(port)), timeout=max(1, int(timeout or 4))):
            return True, f"TCP port {port} on {host} is open"
    except Exception as exc:
        return False, service_monitor_humanize_conn_error(exc)


def service_monitor_probe_http(url, fail_tokens, timeout):
    url = str(url or "").strip()
    if not url:
        return False, "No URL configured"
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "http://" + url
    scheme_label = "HTTPS" if url.lower().startswith("https://") else "HTTP"
    request_obj = urllib.request.Request(url, headers={"User-Agent": "OpenPagingServer-Monitor"}, method="GET")
    status_code = None
    try:
        with urllib.request.urlopen(request_obj, timeout=max(1, int(timeout or 10))) as response:
            status_code = int(getattr(response, "status", 0) or response.getcode() or 0)
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
    except Exception as exc:
        detail = service_monitor_humanize_conn_error(exc)
        if service_monitor_http_status_is_failure(None, fail_tokens):
            return False, detail
        return True, detail
    if service_monitor_http_status_is_failure(status_code, fail_tokens):
        return False, f"URL returned {scheme_label} code {status_code}"
    return True, f"URL returned {scheme_label} code {status_code}"


def service_monitor_probe_sip(host, port, timeout):
    host = str(host or "").strip()
    port = int(port or 5060)
    if not host:
        return False, "No host configured"
    timeout = max(1, int(timeout or 4))
    call_id = uuid.uuid4().hex
    branch = "z9hG4bK" + uuid.uuid4().hex[:16]
    tag = uuid.uuid4().hex[:8]
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.bind(("", 0))
        local_ip = sock.getsockname()[0]
        local_port = sock.getsockname()[1]
        request = (
            f"OPTIONS sip:{host}:{port} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:monitor@{local_ip}>;tag={tag}\r\n"
            f"To: <sip:{host}:{port}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: 1 OPTIONS\r\n"
            f"Contact: <sip:monitor@{local_ip}:{local_port}>\r\n"
            f"User-Agent: OpenPagingServer-Monitor\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        sock.sendto(request.encode("utf-8"), (host, port))
        data, _addr = sock.recvfrom(4096)
        first_line = data.decode("utf-8", "replace").splitlines()[0] if data else ""
        if "200" in first_line:
            return True, ""
        # Any well-formed SIP response means the server is reachable/alive.
        if first_line.upper().startswith("SIP/2.0"):
            return False, first_line.strip()
        return False, "Unexpected SIP response"
    except socket.timeout:
        return False, "No SIP response"
    except Exception as exc:
        return False, str(exc) or "SIP probe failed"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def service_monitor_fetch_kuma_metrics(base_url, api_key, timeout=10):
    base_url = str(base_url or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("Uptime Kuma base URL is required.")
    if not re.match(r"^https?://", base_url, re.IGNORECASE):
        base_url = "http://" + base_url
    metrics_url = base_url + "/metrics"
    request_obj = urllib.request.Request(metrics_url, method="GET")
    token = base64.b64encode(f":{str(api_key or '')}".encode("utf-8")).decode("ascii")
    request_obj.add_header("Authorization", f"Basic {token}")
    request_obj.add_header("User-Agent", "OpenPagingServer-Monitor")
    with urllib.request.urlopen(request_obj, timeout=max(1, int(timeout or 10))) as response:
        return response.read().decode("utf-8", "replace")


SERVICE_MONITOR_KUMA_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def service_monitor_parse_kuma_status(metrics_text):
    """Return {monitor_name: up_bool} parsed from monitor_status metric lines."""
    monitors = {}
    for line in metrics_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("monitor_status"):
            continue
        try:
            label_part = line[line.index("{") + 1:line.rindex("}")]
            value_part = line[line.rindex("}") + 1:].strip()
        except ValueError:
            continue
        labels = {}
        for match in SERVICE_MONITOR_KUMA_LABEL_RE.finditer(label_part):
            labels[match.group(1)] = match.group(2).replace('\\"', '"').replace("\\\\", "\\")
        name = labels.get("monitor_name")
        if not name:
            continue
        try:
            value = float(value_part.split()[0])
        except (ValueError, IndexError):
            continue
        # monitor_status: 1=up, 0=down, 2=pending, 3=maintenance
        monitors[name] = value == 1
    return monitors


def service_monitor_list_kuma_monitors(base_url, api_key, timeout=10):
    metrics_text = service_monitor_fetch_kuma_metrics(base_url, api_key, timeout=timeout)
    return sorted(service_monitor_parse_kuma_status(metrics_text).keys(), key=lambda name: name.lower())


def service_monitor_probe_kuma(base_url, api_key, monitor_name):
    monitor_name = str(monitor_name or "").strip()
    if not monitor_name:
        return False, "No monitor selected", False
    try:
        metrics_text = service_monitor_fetch_kuma_metrics(base_url, api_key)
    except Exception:
        # Uptime Kuma itself is unreachable — the monitor is considered down.
        return False, "Uptime Kuma is down", True
    statuses = service_monitor_parse_kuma_status(metrics_text)
    if monitor_name not in statuses:
        return False, "Monitor not found in Uptime Kuma", False
    if statuses[monitor_name]:
        return True, "Uptime Kuma reports the monitor is up", False
    return False, "Uptime Kuma reports the monitor is down", False


def service_monitor_check_row(row):
    """Run the configured probe for a monitor row.

    Returns (is_up, detail, down_state) where down_state is the last_state to use
    when the probe fails ("offline" normally, "kuma_down" when Uptime Kuma itself
    is unreachable).
    """
    monitor_type = str(row.get("monitor_type") or "ping").strip().lower()
    interval = int(row.get("check_interval") or SERVICE_MONITOR_DEFAULT_INTERVAL)
    probe_timeout = max(3, min(interval - 1, 30)) if interval > 4 else 4
    if monitor_type == "ping":
        is_up, detail = service_monitor_probe_ping(row.get("host"), probe_timeout)
        return is_up, detail, "offline"
    if monitor_type == "tcp":
        is_up, detail = service_monitor_probe_tcp(row.get("host"), row.get("port"), probe_timeout)
        return is_up, detail, "offline"
    if monitor_type == "http":
        fail_tokens = service_monitor_parse_fail_codes(row.get("http_fail_codes")) or SERVICE_MONITOR_HTTP_DEFAULT_FAIL
        is_up, detail = service_monitor_probe_http(row.get("http_url"), fail_tokens, probe_timeout)
        return is_up, detail, "offline"
    if monitor_type == "sip":
        is_up, detail = service_monitor_probe_sip(row.get("host"), row.get("port") or 5060, probe_timeout)
        return is_up, detail, "offline"
    if monitor_type == "uptimekuma":
        is_up, detail, server_down = service_monitor_probe_kuma(
            row.get("uk_base_url"), row.get("uk_api_key"), row.get("uk_monitor")
        )
        return is_up, detail, ("kuma_down" if server_down else "offline")
    return False, "Unknown monitor type", "offline"


SERVICE_MONITOR_DRAFT_TABLE = "servicemonitor_drafts"


def ensure_servicemonitor_draft_schema():
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{SERVICE_MONITOR_DRAFT_TABLE}` ("
                "`token` VARCHAR(64) NOT NULL, "
                "`data` LONGTEXT DEFAULT NULL, "
                "`updated_at` DATETIME DEFAULT NULL, "
                "PRIMARY KEY (`token`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
        conn.commit()
    finally:
        conn.close()


def service_monitor_load_draft(token):
    ensure_servicemonitor_draft_schema()
    rows = sip_query_all(
        f"SELECT `data` FROM `{SERVICE_MONITOR_DRAFT_TABLE}` WHERE token=%s LIMIT 1",
        (token,),
    )
    if not rows:
        return None
    try:
        return json.loads(rows[0].get("data") or "{}")
    except (TypeError, ValueError):
        return {}


def service_monitor_save_draft(token, data):
    ensure_servicemonitor_draft_schema()
    payload = json.dumps(data or {})
    sip_execute(
        f"INSERT INTO `{SERVICE_MONITOR_DRAFT_TABLE}` (`token`, `data`, `updated_at`) VALUES (%s,%s,%s) "
        f"ON DUPLICATE KEY UPDATE `data`=VALUES(`data`), `updated_at`=VALUES(`updated_at`)",
        (token, payload, datetime.now()),
    )


def service_monitor_delete_draft(token):
    try:
        ensure_servicemonitor_draft_schema()
        sip_execute(f"DELETE FROM `{SERVICE_MONITOR_DRAFT_TABLE}` WHERE token=%s", (token,))
        # Opportunistically prune drafts older than a day.
        sip_execute(
            f"DELETE FROM `{SERVICE_MONITOR_DRAFT_TABLE}` WHERE `updated_at` IS NOT NULL AND `updated_at` < %s",
            (datetime.now() - _timedelta(days=1),),
        )
    except Exception as exc:
        log(f"service monitor draft cleanup error: {exc}")


def http_request_endpoint_count():
    try:
        return len(http_request_rows())
    except Exception as exc:
        log(f"http request endpoint count error: {exc}")
        return 0


def get_http_request_endpoint_status():
    endpoints = []
    for row in http_request_rows():
        row_id = row.get("id")
        method = http_request_clean_method(row.get("method") or "POST")
        endpoints.append(
            {
                "id": f"request-{row_id}",
                "name": str(row.get("name") or f"HTTP Request {row_id}"),
                "address": str(row.get("url") or ""),
                "model": "",
                "status": "",
                "type": f"{method} Request",
                "direction": "Output",
                "input_type": "Output",
                "output_capable": True,
                "bell_capable": False,
                "available": True,
                "capabilities": ["output"],
            }
        )
    return {
        "module": HTTP_REQUEST_MODULE,
        "display_name": HTTP_REQUEST_NAME,
        "name": HTTP_REQUEST_NAME,
        "description": HTTP_REQUEST_DESCRIPTION,
        "input_type": "Output",
        "system_builtin": True,
        "enabled": True,
        "loaded": True,
        "trusted": True,
        "can_load": True,
        "input_capable": False,
        "output_capable": True,
        "endpoints": endpoints,
    }


def multicast_rtp_endpoint_count():
    try:
        return len(multicast_rtp_rows())
    except Exception as exc:
        log(f"multicast rtp endpoint count error: {exc}")
        return 0


def get_multicast_rtp_endpoint_status():
    endpoints = []
    for row in multicast_rtp_rows():
        row_id = row.get("id")
        address = str(row.get("address") or "").strip()
        port = int(row.get("port") or 0)
        codec = multicast_rtp_clean_codec(row.get("codec") or "PCMU")
        packet_ms = multicast_rtp_clean_packet_ms(row.get("packet_ms") or MULTICAST_RTP_DEFAULT_PACKET_MS)
        endpoints.append(
            {
                "id": f"stream-{row_id}",
                "name": str(row.get("name") or f"Multicast RTP {row_id}"),
                "address": f"{address}:{port}" if address and port else address,
                "model": "",
                "status": "",
                "type": f"{codec} Stream",
                "direction": "Output",
                "input_type": "Output",
                "output_capable": True,
                "bell_capable": True,
                "available": True,
                "packet_ms": packet_ms,
                "capabilities": ["output", "bells"],
            }
        )
    return {
        "module": MULTICAST_RTP_MODULE,
        "display_name": MULTICAST_RTP_NAME,
        "name": MULTICAST_RTP_NAME,
        "description": MULTICAST_RTP_DESCRIPTION,
        "input_type": "Output",
        "system_builtin": True,
        "enabled": True,
        "loaded": True,
        "trusted": True,
        "can_load": True,
        "input_capable": False,
        "output_capable": True,
        "endpoints": endpoints,
    }


def ensure_siptrunks_schema():
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{SIP_TRUNK_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`status` VARCHAR(255) NOT NULL DEFAULT 'Offline', "
                "`auth` VARCHAR(32) NOT NULL DEFAULT 'IP', "
                "`trunk_type` VARCHAR(32) NOT NULL DEFAULT 'IP', "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`username` VARCHAR(255) DEFAULT NULL, "
                "`password` VARCHAR(255) DEFAULT NULL, "
                "`ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0', "
                "`holdbehavior` VARCHAR(32) NOT NULL DEFAULT 'passrtp', "
                "`callerid_number` VARCHAR(100) NOT NULL DEFAULT '', "
                "`callerid_name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`servers_json` LONGTEXT DEFAULT NULL, "
                "`outbound_nat` VARCHAR(16) NOT NULL DEFAULT 'auto', "
                "`connected_server` VARCHAR(255) NOT NULL DEFAULT '', "
                "`connected_transport` VARCHAR(16) NOT NULL DEFAULT '', "
                "PRIMARY KEY (`id`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{SIP_DIALPLAN_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`extension` VARCHAR(100) NOT NULL DEFAULT '', "
                "`group` VARCHAR(255) DEFAULT NULL, "
                "`trigger` VARCHAR(100) NOT NULL DEFAULT 'page', "
                "`passcode` VARCHAR(64) DEFAULT NULL, "
                "PRIMARY KEY (`id`), KEY `extension_idx` (`extension`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{SIP_OUTPUT_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`trunk_id` INT NOT NULL, "
                "`number` VARCHAR(255) NOT NULL DEFAULT '', "
                "`cid_number` VARCHAR(100) NOT NULL DEFAULT '', "
                "`cnam_name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`allow_cid_override` TINYINT(1) NOT NULL DEFAULT 0, "
                "`allow_cnam_override` TINYINT(1) NOT NULL DEFAULT 0, "
                "`mode` VARCHAR(16) NOT NULL DEFAULT 'page', "
                "`amd_enabled` TINYINT(1) NOT NULL DEFAULT 0, "
                "`amd_action` VARCHAR(16) NOT NULL DEFAULT 'hangup', "
                "`amd_retry_limit` INT NOT NULL DEFAULT 0, "
                "`amd_retry_delay` INT NOT NULL DEFAULT 5, "
                "`answer_timeout` INT NOT NULL DEFAULT 45, "
                "`answer_timeout_retry_limit` INT NOT NULL DEFAULT 0, "
                "`answer_timeout_retry_delay` INT NOT NULL DEFAULT 5, "
                "`alert_info_mode` VARCHAR(32) NOT NULL DEFAULT '', "
                "`alert_info_value` VARCHAR(255) NOT NULL DEFAULT '', "
                "`headers_json` LONGTEXT DEFAULT NULL, "
                "PRIMARY KEY (`id`), KEY `trunk_idx` (`trunk_id`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
            trunk_columns = sip_table_columns(cur, SIP_TRUNK_TABLE)
            trunk_additions = {
                "status": "`status` VARCHAR(255) NOT NULL DEFAULT 'Offline'",
                "auth": "`auth` VARCHAR(32) NOT NULL DEFAULT 'IP'",
                "trunk_type": "`trunk_type` VARCHAR(32) NOT NULL DEFAULT 'IP'",
                "name": "`name` VARCHAR(255) NOT NULL DEFAULT ''",
                "username": "`username` VARCHAR(255) DEFAULT NULL",
                "password": "`password` VARCHAR(255) DEFAULT NULL",
                "ipaddr": "`ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0'",
                "holdbehavior": "`holdbehavior` VARCHAR(32) NOT NULL DEFAULT 'passrtp'",
                "callerid_number": "`callerid_number` VARCHAR(100) NOT NULL DEFAULT ''",
                "callerid_name": "`callerid_name` VARCHAR(255) NOT NULL DEFAULT ''",
                "servers_json": "`servers_json` LONGTEXT DEFAULT NULL",
                "outbound_nat": "`outbound_nat` VARCHAR(16) NOT NULL DEFAULT 'auto'",
                "connected_server": "`connected_server` VARCHAR(255) NOT NULL DEFAULT ''",
                "connected_transport": "`connected_transport` VARCHAR(16) NOT NULL DEFAULT ''",
            }
            for column, sql in trunk_additions.items():
                if column not in trunk_columns:
                    cur.execute(f"ALTER TABLE `{SIP_TRUNK_TABLE}` ADD COLUMN {sql}")
            try:
                cur.execute(
                    f"ALTER TABLE `{SIP_TRUNK_TABLE}` "
                    f"MODIFY COLUMN `auth` VARCHAR(32) NOT NULL DEFAULT 'IP'"
                )
            except Exception:
                pass
            try:
                cur.execute(
                    f"ALTER TABLE `{SIP_TRUNK_TABLE}` "
                    f"MODIFY COLUMN `trunk_type` VARCHAR(32) NOT NULL DEFAULT 'IP'"
                )
            except Exception:
                pass
            try:
                cur.execute(
                    f"UPDATE `{SIP_TRUNK_TABLE}` SET trunk_type=%s "
                    f"WHERE (trunk_type IS NULL OR trunk_type='') AND auth='IP'",
                    (SIP_TRUNK_TYPE_IP,),
                )
                cur.execute(
                    f"UPDATE `{SIP_TRUNK_TABLE}` SET trunk_type=%s "
                    f"WHERE (trunk_type IS NULL OR trunk_type='') AND auth='USERPASS'",
                    (SIP_TRUNK_TYPE_INBOUND_AUTH,),
                )
                cur.execute(
                    f"UPDATE `{SIP_TRUNK_TABLE}` SET auth='OUTBOUND', trunk_type=%s "
                    f"WHERE COALESCE(servers_json,'')<>'' AND "
                    f"(auth IS NULL OR auth='' OR auth='IP' OR auth='USERPASS' OR trunk_type IS NULL OR trunk_type='' OR trunk_type='IP' OR trunk_type='USERPASS')",
                    (SIP_TRUNK_TYPE_OUTBOUND_AUTH,),
                )
            except Exception:
                pass
            dialplan_columns = sip_table_columns(cur, SIP_DIALPLAN_TABLE)
            dialplan_additions = {
                "name": "`name` VARCHAR(255) NOT NULL DEFAULT ''",
                "extension": "`extension` VARCHAR(100) NOT NULL DEFAULT ''",
                "group": "`group` VARCHAR(255) DEFAULT NULL",
                "trigger": "`trigger` VARCHAR(100) NOT NULL DEFAULT 'page'",
                "passcode": "`passcode` VARCHAR(64) DEFAULT NULL",
            }
            for column, sql in dialplan_additions.items():
                if column not in dialplan_columns:
                    cur.execute(f"ALTER TABLE `{SIP_DIALPLAN_TABLE}` ADD COLUMN {sql}")
            output_columns = sip_table_columns(cur, SIP_OUTPUT_TABLE)
            output_additions = {
                "name": "`name` VARCHAR(255) NOT NULL DEFAULT ''",
                "trunk_id": "`trunk_id` INT NOT NULL DEFAULT 0",
                "number": "`number` VARCHAR(255) NOT NULL DEFAULT ''",
                "cid_number": "`cid_number` VARCHAR(100) NOT NULL DEFAULT ''",
                "cnam_name": "`cnam_name` VARCHAR(255) NOT NULL DEFAULT ''",
                "allow_cid_override": "`allow_cid_override` TINYINT(1) NOT NULL DEFAULT 0",
                "allow_cnam_override": "`allow_cnam_override` TINYINT(1) NOT NULL DEFAULT 0",
                "mode": "`mode` VARCHAR(16) NOT NULL DEFAULT 'page'",
                "amd_enabled": "`amd_enabled` TINYINT(1) NOT NULL DEFAULT 0",
                "amd_action": "`amd_action` VARCHAR(16) NOT NULL DEFAULT 'hangup'",
                "amd_retry_limit": "`amd_retry_limit` INT NOT NULL DEFAULT 0",
                "amd_retry_delay": "`amd_retry_delay` INT NOT NULL DEFAULT 5",
                "answer_timeout": "`answer_timeout` INT NOT NULL DEFAULT 45",
                "answer_timeout_retry_limit": "`answer_timeout_retry_limit` INT NOT NULL DEFAULT 0",
                "answer_timeout_retry_delay": "`answer_timeout_retry_delay` INT NOT NULL DEFAULT 5",
                "alert_info_mode": "`alert_info_mode` VARCHAR(32) NOT NULL DEFAULT ''",
                "alert_info_value": "`alert_info_value` VARCHAR(255) NOT NULL DEFAULT ''",
                "headers_json": "`headers_json` LONGTEXT DEFAULT NULL",
            }
            for column, sql in output_additions.items():
                if column not in output_columns:
                    cur.execute(f"ALTER TABLE `{SIP_OUTPUT_TABLE}` ADD COLUMN {sql}")
        conn.commit()
    finally:
        conn.close()


def siptrunks_status_label(row):
    raw = str(row.get("status") or "").strip()
    if not raw:
        return "Offline"
    if siptrunks_is_outbound_row(row):
        connected_server = str(row.get("connected_server") or "").strip()
        if connected_server and raw.lower().startswith("online") and connected_server.lower() not in raw.lower():
            return f"{raw} ({connected_server})"
        return raw
    auth_type = str(row.get("auth") or "").upper()
    if auth_type == "USERPASS" and "," in raw and not raw.lower().startswith(("online", "offline")):
        ipaddr, user_agent = raw.split(",", 1)
        detail = " ".join(part for part in (ipaddr.strip(), user_agent.strip().strip("'\"")) if part)
        return f"Online ({detail})" if detail else "Online"
    if "," not in raw:
        return raw
    state, detail = raw.split(",", 1)
    state = state.strip() or "Online"
    detail = detail.strip().strip("'\"")
    return f"{state} ({detail})" if detail else state


def siptrunks_row_type(row):
    if siptrunks_is_outbound_row(row):
        return "Outbound-Authenticated SIP Trunk"
    trunk_type = str(row.get("trunk_type") or "").upper()
    auth_type = str(row.get("auth") or "").upper()
    if trunk_type == SIP_TRUNK_TYPE_INBOUND_AUTH or auth_type == "USERPASS":
        return "Inbound-Authenticated SIP Trunk"
    return "Basic SIP Trunk (IP)"


def siptrunks_row_name(row):
    name = str(row.get("name") or row.get("username") or row.get("ipaddr") or f"SIP Trunk {row.get('id')}")
    trunk_type = str(row.get("trunk_type") or "").upper()
    auth_type = str(row.get("auth") or "").upper()
    ipaddr = str(row.get("ipaddr") or "").strip()
    is_ip_trunk = (trunk_type == SIP_TRUNK_TYPE_IP or auth_type == "IP") and auth_type != "USERPASS" and trunk_type != SIP_TRUNK_TYPE_INBOUND_AUTH
    if is_ip_trunk and ipaddr:
        return f"{name} ({ipaddr})"
    return name


def siptrunks_dialplan_row_name(row):
    name = str(row.get("name") or row.get("extension") or f"SIP Extension {row.get('id')}")
    extension = str(row.get("extension") or "").strip()
    return f"{name} ({extension})" if extension else name


def sip_output_row_name(row):
    name = str(row.get("name") or row.get("number") or f"SIP Number {row.get('id')}")
    number = str(row.get("number") or "").strip()
    return f"{name} ({number})" if number and number not in name else name


def sip_output_row_type(row):
    return "SIP Telephone Number" if str(row.get("mode") or "").strip().lower() == SIP_OUTPUT_MODE_TELEPHONE else "SIP Page Number"


def siptrunks_is_outbound_row(row):
    auth_type = str(row.get("auth") or "").upper()
    trunk_type = str(row.get("trunk_type") or "").upper()
    if trunk_type == SIP_TRUNK_TYPE_OUTBOUND_AUTH or auth_type == "OUTBOUND":
        return True
    return bool(sip_parse_json_list(row.get("servers_json")))


def sip_parse_json_object(raw, default=None):
    if raw in (None, ""):
        return {} if default is None else default
    if isinstance(raw, dict):
        return dict(raw)
    try:
        decoded = json.loads(raw)
    except Exception:
        return {} if default is None else default
    return decoded if isinstance(decoded, dict) else ({} if default is None else default)


def sip_parse_json_list(raw):
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        return list(raw)
    try:
        decoded = json.loads(raw)
    except Exception:
        return []
    return decoded if isinstance(decoded, list) else []


def sip_clean_headers(value):
    headers = []
    items = value if isinstance(value, list) else sip_parse_json_list(value)
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            header_value = str(item.get("value") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            name = str(item[0] or "").strip()
            header_value = str(item[1] or "").strip()
        else:
            continue
        if not name or ":" in name or "\r" in name or "\n" in name:
            continue
        if "\r" in header_value or "\n" in header_value:
            continue
        headers.append({"name": name, "value": header_value})
    return headers


def sip_clean_output_mode(value):
    mode = str(value or SIP_OUTPUT_MODE_PAGE).strip().lower()
    return mode if mode in {SIP_OUTPUT_MODE_PAGE, SIP_OUTPUT_MODE_TELEPHONE} else SIP_OUTPUT_MODE_PAGE


def sip_clean_retry_limit(value):
    try:
        limit = int(str(value or "0").strip())
    except (TypeError, ValueError):
        limit = 0
    return max(0, min(8, limit))


def sip_clean_retry_delay(value):
    try:
        delay = int(str(value or "5").strip())
    except (TypeError, ValueError):
        delay = 5
    return max(5, min(60, delay))


def sip_clean_answer_timeout(value):
    try:
        timeout = int(str(value or "45").strip())
    except (TypeError, ValueError):
        timeout = 45
    return max(0, min(3600, timeout))


def sip_clean_alert_info_mode(value):
    mode = str(value or "").strip()
    return mode if mode in set(SIP_ALERT_INFO_PRESETS) | {"custom", ""} else ""


def sip_clean_outbound_nat(value):
    mode = str(value or "auto").strip().lower()
    return mode if mode in SIP_OUTPUT_NAT_MODES else "auto"


def sip_clean_transport(value):
    token = str(value or "udp").strip().lower()
    return token if token in {"dns", "udp", "tcp", "tls"} else "udp"


def sip_trunk_servers(row):
    servers = []
    data = sip_parse_json_list(row.get("servers_json"))
    if not data:
        ipaddr = str(row.get("ipaddr") or "").strip()
        if ipaddr:
            servers.append({"server": ipaddr, "outbound_proxy": "", "transport": "udp", "port": 5060, "expires": 300})
        return servers
    for item in data[:8]:
        if not isinstance(item, dict):
            continue
        server = str(item.get("server") or "").strip()
        proxy = str(item.get("outbound_proxy") or "").strip()
        transport = sip_clean_transport(item.get("transport") or "udp")
        try:
            port = int(str(item.get("port") or "").strip() or ("5061" if transport == "tls" else "5060"))
        except (TypeError, ValueError):
            port = 5061 if transport == "tls" else 5060
        if port < 1 or port > 65535:
            port = 5061 if transport == "tls" else 5060
        try:
            expires = int(str(item.get("expires") or "").strip() or "300")
        except (TypeError, ValueError):
            expires = 300
        expires = max(60, min(86400, expires))
        servers.append(
            {
                "server": server,
                "outbound_proxy": proxy,
                "transport": transport,
                "port": port,
                "expires": expires,
            }
        )
    return servers


def sip_fetch_trunk_rows(include_ip=False):
    ensure_siptrunks_schema()
    sql = (
        f"SELECT `id`, `name`, `auth`, `trunk_type`, `username`, `password`, `ipaddr`, `status`, "
        f"`holdbehavior`, `callerid_number`, `callerid_name`, `servers_json`, `outbound_nat`, "
        f"`connected_server`, `connected_transport` FROM `{SIP_TRUNK_TABLE}` ORDER BY `id` ASC"
    )
    rows = sip_query_all(sql)
    if include_ip:
        return rows
    return [
        row
        for row in rows
        if siptrunks_is_outbound_row(row) or str(row.get("auth") or "").upper() == "IP"
    ]


def sip_fetch_output_rows():
    ensure_siptrunks_schema()
    return sip_query_all(
        f"SELECT o.*, t.name AS trunk_name, t.status AS trunk_status, t.auth AS trunk_auth, "
        f"t.trunk_type AS trunk_trunk_type, t.connected_server AS trunk_connected_server, "
        f"t.username AS trunk_username, t.password AS trunk_password, t.ipaddr AS trunk_ipaddr, "
        f"t.servers_json AS trunk_servers_json, t.outbound_nat AS trunk_outbound_nat, "
        f"t.connected_transport AS trunk_connected_transport "
        f"FROM `{SIP_OUTPUT_TABLE}` o "
        f"LEFT JOIN `{SIP_TRUNK_TABLE}` t ON t.id = o.trunk_id "
        f"ORDER BY o.name ASC, o.id ASC"
    )


def sip_fetch_output_row(row_id):
    rows = sip_query_all(
        f"SELECT o.*, t.name AS trunk_name, t.status AS trunk_status, t.auth AS trunk_auth, "
        f"t.trunk_type AS trunk_trunk_type, t.connected_server AS trunk_connected_server, "
        f"t.username AS trunk_username, t.password AS trunk_password, t.ipaddr AS trunk_ipaddr, "
        f"t.servers_json AS trunk_servers_json, t.outbound_nat AS trunk_outbound_nat, "
        f"t.connected_transport AS trunk_connected_transport "
        f"FROM `{SIP_OUTPUT_TABLE}` o "
        f"LEFT JOIN `{SIP_TRUNK_TABLE}` t ON t.id = o.trunk_id "
        f"WHERE o.id=%s LIMIT 1",
        (row_id,),
    )
    return rows[0] if rows else None


def sip_output_endpoint_count():
    rows = sip_query_all(f"SELECT COUNT(*) AS total FROM `{SIP_OUTPUT_TABLE}`")
    if not rows:
        return 0
    return int(rows[0].get("total") or 0)


def sip_message_override_capabilities():
    rows = sip_query_all(
        f"SELECT "
        f"MAX(CASE WHEN allow_cid_override=1 THEN 1 ELSE 0 END) AS cid_enabled, "
        f"MAX(CASE WHEN allow_cnam_override=1 THEN 1 ELSE 0 END) AS cnam_enabled "
        f"FROM `{SIP_OUTPUT_TABLE}`"
    )
    if not rows:
        return {"cid": False, "cnam": False}
    row = rows[0]
    return {
        "cid": bool(int(row.get("cid_enabled") or 0)),
        "cnam": bool(int(row.get("cnam_enabled") or 0)),
    }


def get_siptrunks_endpoint_status():
    ensure_siptrunks_schema()
    endpoints = []
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    f"SELECT `id`, `name`, `auth`, `trunk_type`, `username`, `ipaddr`, `status`, `connected_server` "
                    f"FROM `{SIP_TRUNK_TABLE}` ORDER BY `id` ASC"
                )
                rows = cur.fetchall()
            except pymysql.MySQLError as exc:
                log(f"siptrunks endpoint status error: {exc}")
                rows = []
            try:
                cur.execute(
                    f"SELECT `id`, `name`, `extension`, `group`, `trigger`, `passcode` "
                    f"FROM `{SIP_DIALPLAN_TABLE}` ORDER BY `id` ASC"
                )
                dialplan_rows = cur.fetchall()
            except pymysql.MySQLError as exc:
                log(f"siptrunks dialplan status error: {exc}")
                dialplan_rows = []
            try:
                cur.execute(
                    f"SELECT o.`id`, o.`name`, o.`number`, o.`mode`, o.`trunk_id`, "
                    f"t.`name` AS trunk_name, t.`status` AS trunk_status, t.`connected_server` "
                    f"FROM `{SIP_OUTPUT_TABLE}` o "
                    f"LEFT JOIN `{SIP_TRUNK_TABLE}` t ON t.id = o.trunk_id "
                    f"ORDER BY o.`name` ASC, o.`id` ASC"
                )
                output_rows = cur.fetchall()
            except pymysql.MySQLError as exc:
                log(f"siptrunks output status error: {exc}")
                output_rows = []
    finally:
        conn.close()
    for row in rows:
        trunk_status = siptrunks_status_label(row)
        endpoints.append(
            {
                "id": f"trunk-{row.get('id')}",
                "name": siptrunks_row_name(row),
                "address": "",
                "model": "",
                "status": trunk_status,
                "type": siptrunks_row_type(row),
                "direction": "Input",
                "output_capable": False,
                "bell_capable": False,
                "capabilities": ["management", "sip"],
            }
        )
    for row in dialplan_rows:
        endpoints.append(
            {
                "id": f"dialplan-{row.get('id')}",
                "name": siptrunks_dialplan_row_name(row),
                "address": "",
                "model": "",
                "status": "",
                "type": "SIP Trunk Extension",
                "direction": "Input",
                "output_capable": False,
                "bell_capable": False,
                "capabilities": ["management", "sip"],
            }
        )
    for row in output_rows:
        trunk_name = str(row.get("trunk_name") or f"Trunk {row.get('trunk_id')}")
        trunk_status = str(row.get("trunk_status") or "").strip()
        connected_server = str(row.get("connected_server") or "").strip()
        meta = trunk_name
        if connected_server:
            meta = f"{meta} - {connected_server}"
        endpoints.append(
            {
                "id": f"number-{row.get('id')}",
                "name": sip_output_row_name(row),
                "address": str(row.get("number") or ""),
                "model": meta,
                "status": trunk_status if str(row.get("mode") or "").strip().lower() == SIP_OUTPUT_MODE_TELEPHONE else "",
                "type": sip_output_row_type(row),
                "direction": "Output",
                "output_capable": True,
                "bell_capable": True,
                "available": True,
                "capabilities": ["management", "sip", "output", "bells"],
            }
        )
    return {
        "module": "siptrunks",
        "display_name": "SIP Trunks",
        "name": "SIP Trunks",
        "description": "Interconnect Open Paging Server with a VoIP-based PBX or ITSP",
        "system_builtin": True,
        "enabled": True,
        "loaded": True,
        "trusted": True,
        "can_load": True,
        "input_capable": True,
        "output_capable": bool(output_rows),
        "endpoints": endpoints,
    }


def h(value):
    return html.escape("" if value is None else str(value), quote=True)


def sip_query_all(sql, params=None):
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def sip_execute(sql, params=None):
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()


def sip_valid_ip(value):
    import ipaddress

    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def sip_valid_ip_or_network(value):
    import ipaddress

    try:
        if "/" in str(value):
            ipaddress.ip_network(value, strict=False)
        else:
            ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def sip_clean_groups(raw):
    parts = raw if isinstance(raw, list) else re.split(r"[.,\s]+", str(raw or ""))
    clean = []
    for part in (str(part).strip() for part in parts):
        if part and part not in clean:
            clean.append(part)
    return ".".join(clean)


def sip_fetch_groups():
    try:
        rows = sip_query_all("SELECT `id`, `name` FROM `groups` ORDER BY CAST(`id` AS UNSIGNED), `id`")
    except Exception:
        rows = []
    return [{"id": "0", "name": "All Recipients"}] + list(rows)


def sip_fetch_messages():
    try:
        conn = get_dict_db_connection()
        try:
            with conn.cursor() as cur:
                columns = sip_table_columns(cur, "messages")
                id_column = "messageid" if "messageid" in columns else "id" if "id" in columns else None
                if not id_column:
                    return []
                name_column = "name" if "name" in columns else id_column
                cur.execute(
                    f"SELECT `{id_column}` AS id, `{name_column}` AS name "
                    f"FROM `messages` ORDER BY CAST(`{id_column}` AS UNSIGNED), `{id_column}`"
                )
                return cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return []


def sip_dialplan_trigger(trigger_type, message_id):
    if trigger_type == "message":
        return "message:" + str(message_id or "").strip()
    if trigger_type in {"page", "#testtone", "#echotest"}:
        return trigger_type
    return "page"


def sip_split_dialplan_trigger(value):
    value = str(value or "page").strip()
    if value.startswith("message:"):
        return "message", value.split(":", 1)[1]
    if value in {"page", "#testtone", "#echotest"}:
        return value, ""
    return "page", ""


def sip_form_frame(body):
    return (
        "<style>body{font-family:Tahoma,sans-serif;margin:0;padding:20px;color:#202124;background:#fff}"
        ".form-surface,.surface{max-width:720px;background:#fff;border:1px solid #e6e8eb;border-radius:8px;padding:18px;box-shadow:0 1px 3px rgba(0,0,0,.08)}"
        ".grid{display:grid;gap:14px}.row{display:grid;gap:6px}label{font-weight:500}.control,input,select{padding:10px 11px;border:1px solid #ccd1d5;border-radius:6px;font:inherit;box-sizing:border-box;width:100%;background:#fff;color:#202124}.short-control,.short{max-width:180px}"
        "button,.button{background:#1976D2;color:#fff;border:0;border-radius:6px;padding:10px 14px;font:inherit;cursor:pointer;justify-self:start;text-decoration:none}.danger{background:#C62828}"
        ".success{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}.notice{background:#FFF8E1;border:1px solid #FFE082;color:#5D4037;padding:10px;border-radius:6px;margin-bottom:12px;line-height:1.4}.meta{color:#5f6368;margin:0 0 14px}.advanced{border:1px solid #e6e8eb;border-radius:6px;overflow:hidden}.advanced summary{cursor:pointer;padding:10px 11px;font-weight:500}.advanced-body{border-top:1px solid #e6e8eb;padding:12px;display:grid;gap:14px}"
        ".dropdown-checklist{position:relative}.dropdown-checklist summary{list-style:none;cursor:pointer;padding:10px 11px;border:1px solid #ccd1d5;border-radius:6px;background:#fff}.dropdown-checklist summary::-webkit-details-marker{display:none}.dropdown-panel{position:absolute;top:calc(100% + 6px);left:0;right:0;z-index:20;border:1px solid #d8dde2;border-radius:6px;padding:8px;display:grid;gap:6px;max-height:220px;overflow:auto;background:#fff;box-shadow:0 8px 18px rgba(0,0,0,.14)}"
        ".md-checkbox-container{display:flex;align-items:center;position:relative;cursor:pointer;font-size:14px;font-weight:400;color:#202124;user-select:none;width:100%;gap:12px}.md-checkbox-container input{position:absolute;opacity:0;cursor:pointer;height:0;width:0}.md-checkmark{position:relative;display:inline-block;flex:0 0 auto;height:20px;width:20px;background:#fff;border:2px solid #5f6368;border-radius:2px;transition:all .2s}.md-checkbox-container:hover input ~ .md-checkmark{border-color:#202124}.md-checkbox-container input:checked ~ .md-checkmark{background:#1976D2;border-color:#1976D2}.md-checkmark:after{content:\"\";position:absolute;display:none;left:6px;top:2px;width:4px;height:10px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}.md-checkbox-container input:checked ~ .md-checkmark:after{display:block}.md-checkbox-text{flex:1 1 auto;min-width:0}.check.disabled{opacity:.55}.switch-row{display:flex;align-items:center;gap:10px}.switch{position:relative;width:44px;height:24px}.switch input{opacity:0;width:0;height:0}.slider{position:absolute;cursor:pointer;inset:0;background:#9aa0a6;border-radius:999px;transition:.2s}.slider:before{content:\"\";position:absolute;height:18px;width:18px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s;box-shadow:0 1px 2px rgba(0,0,0,.25)}.switch input:checked + .slider{background:#1976D2}.switch input:checked + .slider:before{transform:translateX(20px)}.hint{color:#5f6368;font-size:.9em}"
        "@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.form-surface,.surface{background:#232323;border-color:#333;box-shadow:none}.control,input,select,.dropdown-checklist summary,.dropdown-panel{background:#171717;border-color:#3a3a3a;color:#eee}.notice{background:#332800;border-color:#5f4b00;color:#FFE8A3}.advanced{border-color:#333}.advanced-body{border-top-color:#333}button,.button{background:#BB86FC;color:#000}.danger{background:#EF9A9A}.meta,.hint,.md-checkbox-container{color:#aaa}.md-checkmark{border-color:#9AA0A6;background:#171717}.md-checkbox-container:hover input ~ .md-checkmark{border-color:#E8EAED}.md-checkbox-container input:checked ~ .md-checkmark{background:#8AB4F8;border-color:#8AB4F8}.md-checkmark:after{border-color:#171717}.switch input:checked + .slider{background:#BB86FC}}</style>"
        + body
    )


def sip_dialplan_fields(values):
    selected_groups = set(str(values["group"] or "").split(".")) if values.get("group") else set()
    group_options = "".join(
        f"""<label class="check md-checkbox-container"><input type="checkbox" class="group-check" value="{h(row.get("id"))}" data-label="{h("All Recipients" if str(row.get("id")) == "0" else row.get("name") or row.get("id"))}"{" checked" if str(row.get("id")) in selected_groups else ""}><span class="md-checkmark"></span><span class="md-checkbox-text">{h("All Recipients" if str(row.get("id")) == "0" else str(row.get("id")) + (" - " + str(row.get("name")) if row.get("name") else ""))}</span></label>"""
        for row in sip_fetch_groups()
    )
    if not group_options:
        group_options = '<span class="hint">No groups configured.</span>'
    message_options = "".join(
        f'<option value="{h(row.get("id"))}"{" selected" if str(row.get("id")) == values["message_id"] else ""}>{h(row.get("id"))} - {h(row.get("name") or "")}</option>'
        for row in sip_fetch_messages()
    )
    trigger_options = "".join(
        f'<option value="{h(value)}"{" selected" if value == values["trigger_type"] else ""}>{h(label)}</option>'
        for value, label in (("page", "Paging"), ("message", "Send Message"), ("#testtone", "Milliwatt Test Tone"), ("#echotest", "Echo Test"))
    )
    return f"""<div class="row"><label>Name</label><input class="control" name="name" value="{h(values["name"])}" required></div>
<div class="row"><label>Extension</label><input class="control short-control" name="extension" id="extension" value="{h(values["extension"])}" required pattern="[0-9*#]*" inputmode="tel"></div>
<div class="row"><label>Trigger</label><select class="control" name="trigger_type" id="triggerType">{trigger_options}</select></div>
<div class="row trigger-extra" id="messageRow"><label>Message</label><select class="control" name="message_id"><option value="">Choose a message</option>{message_options}</select></div>
<div class="row trigger-extra" id="groupRow"><label>Groups</label><input type="hidden" name="group" id="groupValue" value="{h(values["group"])}"><details class="dropdown-checklist" id="groupDropdown"><summary id="groupSummary">Select groups</summary><div class="dropdown-panel">{group_options}</div></details></div>
<label class="switch-row"><span>Use a passcode</span><span class="switch"><input type="checkbox" name="require_passcode" value="1" id="requirePasscode"{" checked" if values.get("require_passcode") == "1" else ""}><span class="slider"></span></span></label>
<div class="row" id="passcodeRow"><label>Passcode</label><input class="control short-control" name="passcode" id="passcode" value="{h(values["passcode"])}" pattern="[0-9A-D]*" inputmode="text"></div>
<script>
const triggerType = document.getElementById('triggerType');
const groupRow = document.getElementById('groupRow');
const messageRow = document.getElementById('messageRow');
const requirePasscode = document.getElementById('requirePasscode');
const passcodeRow = document.getElementById('passcodeRow');
const passcode = document.getElementById('passcode');
const extension = document.getElementById('extension');
const groupValue = document.getElementById('groupValue');
const groupChecks = Array.from(document.querySelectorAll('.group-check'));
const groupSummary = document.getElementById('groupSummary');
function syncTrigger() {{
  const value = triggerType.value;
  groupRow.style.display = (value === 'page' || value === 'message') ? 'grid' : 'none';
  messageRow.style.display = value === 'message' ? 'grid' : 'none';
}}
function syncPasscode() {{
  passcodeRow.style.display = requirePasscode.checked ? 'grid' : 'none';
  if (!requirePasscode.checked) passcode.value = '';
}}
function syncGroupsFromChecks() {{
  const selectedInputs = groupChecks.filter(input => input.checked);
  const selected = selectedInputs.map(input => input.value);
  groupValue.value = selected.join('.');
  groupSummary.textContent = selectedInputs.length ? selectedInputs.map(input => input.dataset.label || input.value).join(', ') : 'Select groups';
}}
function syncAllRecipients() {{
  const all = groupChecks.find(input => input.value === '0');
  if (!all) {{
    syncGroupsFromChecks();
    return;
  }}
  if (all.checked) {{
    groupChecks.forEach(input => {{
      if (input !== all) {{
        input.checked = false;
        input.disabled = true;
        input.closest('.check')?.classList.add('disabled');
      }}
    }});
  }} else {{
    groupChecks.forEach(input => {{
      input.disabled = false;
      input.closest('.check')?.classList.remove('disabled');
    }});
  }}
  syncGroupsFromChecks();
}}
function blockInvalidInput(input, pattern) {{
  input.addEventListener('beforeinput', event => {{
    if (event.data && !pattern.test(event.data)) event.preventDefault();
  }});
}}
triggerType.addEventListener('change', syncTrigger);
requirePasscode.addEventListener('change', syncPasscode);
passcode.addEventListener('input', () => {{ passcode.value = passcode.value.toUpperCase().replace(/[^0-9A-D]/g, ''); }});
extension.addEventListener('input', () => {{ extension.value = extension.value.replace(/[^0-9*#]/g, ''); }});
blockInvalidInput(extension, /^[0-9*#]+$/);
blockInvalidInput(passcode, /^[0-9A-Da-d]+$/);
groupChecks.forEach(input => input.addEventListener('change', syncAllRecipients));
document.getElementById('dialplanForm').addEventListener('submit', syncGroupsFromChecks);
syncTrigger();
syncPasscode();
syncAllRecipients();
</script>"""


def sip_trunk_output_choices():
    choices = []
    for row in sip_fetch_trunk_rows(include_ip=True):
        auth_type = str(row.get("auth") or "").upper()
        trunk_type = str(row.get("trunk_type") or "").upper()
        if auth_type == "IP" or siptrunks_is_outbound_row(row):
            choices.append(row)
        elif trunk_type == SIP_TRUNK_TYPE_INBOUND_AUTH or auth_type == "USERPASS":
            choices.append(row)
    return choices


def sip_number_form_values(row=None):
    row = row or {}
    headers = sip_clean_headers(row.get("headers_json"))
    if not headers:
        headers = [{"name": "", "value": ""}]
    return {
        "name": str(row.get("name") or ""),
        "trunk_id": str(row.get("trunk_id") or ""),
        "number": str(row.get("number") or ""),
        "cid_number": str(row.get("cid_number") or ""),
        "cnam_name": str(row.get("cnam_name") or ""),
        "allow_cid_override": "1" if str(row.get("allow_cid_override") or "0") in {"1", "true", "True"} else "",
        "allow_cnam_override": "1" if str(row.get("allow_cnam_override") or "0") in {"1", "true", "True"} else "",
        "mode": sip_clean_output_mode(row.get("mode") or SIP_OUTPUT_MODE_PAGE),
        "amd_enabled": "1" if str(row.get("amd_enabled") or "0") in {"1", "true", "True"} else "",
        "amd_action": str(row.get("amd_action") or "hangup"),
        "amd_retry_limit": str(sip_clean_retry_limit(row.get("amd_retry_limit") or 0)),
        "amd_retry_delay": str(sip_clean_retry_delay(row.get("amd_retry_delay") or 5)),
        "answer_timeout": str(sip_clean_answer_timeout(row.get("answer_timeout") or 45)),
        "answer_timeout_retry_limit": str(sip_clean_retry_limit(row.get("answer_timeout_retry_limit") or 0)),
        "answer_timeout_retry_delay": str(sip_clean_retry_delay(row.get("answer_timeout_retry_delay") or 5)),
        "alert_info_mode": sip_clean_alert_info_mode(row.get("alert_info_mode") or ""),
        "alert_info_value": str(row.get("alert_info_value") or ""),
        "headers": headers,
    }


def sip_outbound_trunk_form_values(row=None):
    row = row or {}
    servers = row.get("servers") if isinstance(row.get("servers"), list) else sip_trunk_servers(row)
    clean_servers = []
    for item in servers[:8]:
        if not isinstance(item, dict):
            continue
        clean_servers.append(
            {
                "server": str(item.get("server") or ""),
                "outbound_proxy": str(item.get("outbound_proxy") or ""),
                "transport": sip_clean_transport(item.get("transport") or "udp"),
                "port": str(item.get("port") or (5061 if sip_clean_transport(item.get("transport") or "udp") == "tls" else 5060)),
                "expires": str(max(60, min(86400, int(item.get("expires") or 300)))),
                "transport_auto": "0" if str(item.get("server") or "").strip() else "1",
            }
        )
    while len(clean_servers) < 8:
        clean_servers.append(
            {
                "server": "",
                "outbound_proxy": "",
                "transport": "udp",
                "port": "",
                "expires": "300",
                "transport_auto": "1",
            }
        )
    return {
        "name": str(row.get("name") or ""),
        "username": str(row.get("username") or ""),
        "password": str(row.get("password") or ""),
        "callerid_number": str(row.get("callerid_number") or ""),
        "callerid_name": str(row.get("callerid_name") or ""),
        "outbound_nat": sip_clean_outbound_nat(row.get("outbound_nat") or "auto"),
        "servers": clean_servers,
    }


def sip_outbound_server_values_from_form(form):
    servers = []
    for index in range(1, 9):
        prefix = f"server_{index}_"
        transport = sip_clean_transport(form.get(prefix + "transport", "udp"))
        servers.append(
            {
                "server": str(form.get(prefix + "host", "") or "").strip(),
                "outbound_proxy": str(form.get(prefix + "proxy", "") or "").strip(),
                "transport": transport,
                "port": str(form.get(prefix + "port", "") or "").strip(),
                "expires": str(form.get(prefix + "expires", "300") or "300").strip() or "300",
                "transport_auto": "1" if str(form.get(prefix + "transport_auto", "0") or "0").strip() == "1" else "0",
            }
        )
    return servers


def sip_collect_outbound_servers(form):
    if not str(form.get("server_1_host", "") or "").strip():
        raise ValueError("SIP Server 1 is required.")
    servers = []
    for index in range(1, 9):
        prefix = f"server_{index}_"
        server = form.get(prefix + "host", "").strip()
        proxy = form.get(prefix + "proxy", "").strip()
        transport = sip_clean_transport(form.get(prefix + "transport", "udp"))
        port_raw = form.get(prefix + "port", "").strip()
        expires_raw = form.get(prefix + "expires", "300").strip()
        default_port = "5061" if transport == "tls" else "5060"
        if not server and not proxy and str(expires_raw or "300") == "300" and (not port_raw or port_raw == default_port):
            continue
        if not server:
            raise ValueError(f"SIP Server {index} is required.")
        try:
            port = int(port_raw or default_port)
        except ValueError:
            raise ValueError(f"SIP Server {index} port is invalid.")
        if port < 1 or port > 65535:
            raise ValueError(f"SIP Server {index} port is invalid.")
        try:
            expires = int(expires_raw or "300")
        except ValueError:
            raise ValueError(f"SIP Server {index} registration expiry is invalid.")
        if expires < 60 or expires > 86400:
            raise ValueError(f"SIP Server {index} registration expiry must be between 60 and 86400 seconds.")
        servers.append(
            {
                "server": server,
                "outbound_proxy": proxy,
                "transport": transport,
                "port": port,
                "expires": expires,
            }
        )
    if not servers:
        raise ValueError("At least one SIP server is required.")
    return servers


def sip_collect_header_rows(form):
    names = form.getlist("header_name[]")
    values = form.getlist("header_value[]")
    headers = []
    for name, value in zip(names, values):
        name = str(name or "").strip()
        value = str(value or "").strip()
        if not name and not value:
            continue
        headers.append({"name": name, "value": value})
    return sip_clean_headers(headers)


def sip_number_form_html(values, error, submit_label):
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    trunk_options = "".join(
        f'<option value="{h(row.get("id"))}"{" selected" if str(row.get("id")) == str(values.get("trunk_id") or "") else ""}>{h(siptrunks_row_name(row))}</option>'
        for row in sip_trunk_output_choices()
    )
    if not trunk_options:
        trunk_options = '<option value="">No compatible SIP trunks configured</option>'
    header_rows = "".join(
        f'<div class="header-row"><input class="control" name="header_name[]" placeholder="Header" value="{h(item.get("name"))}"><input class="control" name="header_value[]" placeholder="Value" value="{h(item.get("value"))}"><button class="button danger header-remove" type="button">-</button></div>'
        for item in values.get("headers") or [{"name": "", "value": ""}]
    )
    alert_options = "".join(
        f'<option value="{h(value)}"{" selected" if str(values.get("alert_info_mode") or "") == value else ""}>{h(label)}</option>'
        for value, label in (
            ("", "None"),
            ("ring-answer", "ring-answer"),
            ("intercom", "intercom"),
            ("answer-after-0", "answer-after=0"),
            ("alert-autoanswer", "info=alert-autoanswer"),
            ("auto-answer", "auto answer"),
            ("intercom-true", "intercom=true"),
            ("custom", "custom"),
        )
    )
    amd_options = "".join(
        f'<option value="{h(value)}"{" selected" if str(values.get("amd_action") or "hangup") == value else ""}>{h("Hang up and stop" if value == "hangup" else "Redial")}</option>'
        for value in ("hangup", "redial")
    )
    return f"""{error_html}<form method="post" class="grid form-surface" id="sipNumberForm">
<div class="row"><label>Name</label><input class="control" name="name" value="{h(values.get("name"))}" required></div>
<div class="row"><label>SIP Trunk</label><select class="control" name="trunk_id" id="sipNumberTrunk" required>{trunk_options}</select></div>
<div class="row"><label>Number</label><input class="control" name="number" value="{h(values.get("number"))}" required></div>
<div class="row"><label>CID Number</label><input class="control" name="cid_number" value="{h(values.get("cid_number"))}"><div class="hint">Ensure this is correct for your configuration or calls may fail.</div></div>
<div class="row"><label>CNAM Caller ID Name</label><input class="control" name="cnam_name" value="{h(values.get("cnam_name"))}"><div class="hint">Some configurations may override the CNAM. CNAM is not sent over the PSTN, so it will only be shown internally.</div></div>
<label class="check md-checkbox-container"><input type="checkbox" name="allow_cid_override" value="1"{" checked" if values.get("allow_cid_override") == "1" else ""}><span class="md-checkmark"></span><span class="md-checkbox-text">Allow per-message CID Number override</span></label>
<label class="check md-checkbox-container"><input type="checkbox" name="allow_cnam_override" value="1"{" checked" if values.get("allow_cnam_override") == "1" else ""}><span class="md-checkmark"></span><span class="md-checkbox-text">Allow per-message CNAM Caller ID Name override</span></label>
<div class="row"><label>Mode</label><select class="control short-control" name="mode" id="sipNumberMode"><option value="page"{" selected" if values.get("mode") == "page" else ""}>Page</option><option value="telephone"{" selected" if values.get("mode") == "telephone" else ""}>Telephone</option></select></div>
<div class="notice" id="sipModeNotice">Use page mode when the call is automatically picked up. Such as by a paging zone controller, PBX page group, etc. This endpoint will behave like a speaker where the broadcast will not start until all endpoints in a group including this one is ready to receive the page.<br><br>Use telephone mode if this is calling a person(s). Such as when calling a cellphone, telephone, ring group, etc. In this mode, broadcast audio is sent independently of all other endpoints so that, for example, a user picking up the phone a long time after a broadcast begins can hear the full broadcast while speakers inside of a building can freely play and finish the broadcast in the meantime.</div>
<div class="row"><label>Alert-Info Header</label><select class="control" name="alert_info_mode" id="alertInfoMode">{alert_options}</select><div class="hint">Most VoIP systems are a Back-to-Back User Agent (B2BUA) by design and may require a dialplan or configuration change to allow Alert-Info headers to pass through.</div></div>
<div class="row" id="alertInfoCustomRow"><label>Custom Alert-Info Value</label><input class="control" name="alert_info_value" id="alertInfoValue" value="{h(values.get("alert_info_value"))}"></div>
<div id="telephoneOnly">
<div class="row"><label>Answer Timeout (seconds)</label><input class="control short-control" type="number" min="0" max="3600" name="answer_timeout" id="answerTimeout" value="{h(values.get("answer_timeout"))}"><div class="hint">US: <span id="ringsUs"></span> rings | UK/AU: <span id="ringsUk"></span> rings | ETSI: <span id="ringsEtsi"></span> rings</div></div>
<div class="row"><label>Answer Timeout Retries</label><input class="control short-control" type="number" min="0" max="8" name="answer_timeout_retry_limit" value="{h(values.get("answer_timeout_retry_limit"))}"></div>
<div class="row"><label>Answer Timeout Retry Delay (seconds)</label><input class="control short-control" type="number" min="5" max="60" name="answer_timeout_retry_delay" value="{h(values.get("answer_timeout_retry_delay"))}"></div>
<label class="check md-checkbox-container"><input type="checkbox" name="amd_enabled" value="1" id="amdEnabled"{" checked" if values.get("amd_enabled") == "1" else ""}><span class="md-checkmark"></span><span class="md-checkbox-text">Enable Answering Machine Detection</span></label>
<div id="amdOptions">
<div class="row"><label>If a machine answers</label><select class="control short-control" name="amd_action" id="amdAction">{amd_options}</select></div>
<div class="row" id="amdRetryLimitRow"><label>Answering Machine Retries</label><input class="control short-control" type="number" min="0" max="8" name="amd_retry_limit" value="{h(values.get("amd_retry_limit"))}"></div>
<div class="row" id="amdRetryDelayRow"><label>Answering Machine Retry Delay (seconds)</label><input class="control short-control" type="number" min="5" max="60" name="amd_retry_delay" value="{h(values.get("amd_retry_delay"))}"></div>
</div>
</div>
<details class="advanced"><summary>Advanced options</summary><div class="advanced-body">
<div class="row"><label>Custom SIP Headers</label><div id="sipHeaderRows" class="grid">{header_rows}</div><button class="button" id="addHeaderRow" type="button">+</button><div class="hint">Add headers in Header: Value form.</div></div>
</div></details>
<button class="button" type="submit">{h(submit_label)}</button>
</form>
<script>
const sipNumberMode = document.getElementById('sipNumberMode');
const telephoneOnly = document.getElementById('telephoneOnly');
const alertInfoMode = document.getElementById('alertInfoMode');
const alertInfoCustomRow = document.getElementById('alertInfoCustomRow');
const answerTimeout = document.getElementById('answerTimeout');
const ringsUs = document.getElementById('ringsUs');
const ringsUk = document.getElementById('ringsUk');
const ringsEtsi = document.getElementById('ringsEtsi');
const amdEnabled = document.getElementById('amdEnabled');
const amdOptions = document.getElementById('amdOptions');
const amdAction = document.getElementById('amdAction');
const amdRetryLimitRow = document.getElementById('amdRetryLimitRow');
const amdRetryDelayRow = document.getElementById('amdRetryDelayRow');
const sipHeaderRows = document.getElementById('sipHeaderRows');
const addHeaderRow = document.getElementById('addHeaderRow');
function formatRings(seconds, cycle) {{
  const numeric = Number(seconds);
  if (!Number.isFinite(numeric) || numeric < 0) return '0';
  if (numeric === 0) return '\u221E';
  return (numeric / cycle).toFixed(1).replace(/\\.0$/, '');
}}
function syncRings() {{
  ringsUs.textContent = formatRings(answerTimeout.value, 6);
  ringsUk.textContent = formatRings(answerTimeout.value, 3);
  ringsEtsi.textContent = formatRings(answerTimeout.value, 5);
}}
function syncMode() {{
  telephoneOnly.style.display = sipNumberMode.value === 'telephone' ? 'grid' : 'none';
}}
function syncAlertInfo() {{
  alertInfoCustomRow.style.display = alertInfoMode.value === 'custom' ? 'grid' : 'none';
}}
function syncAmd() {{
  const enabled = amdEnabled.checked;
  const showRedialOptions = enabled && amdAction.value === 'redial';
  amdOptions.style.display = enabled ? 'grid' : 'none';
  amdRetryLimitRow.style.display = showRedialOptions ? 'grid' : 'none';
  amdRetryDelayRow.style.display = showRedialOptions ? 'grid' : 'none';
}}
function bindHeaderButtons() {{
  document.querySelectorAll('.header-remove').forEach(button => {{
    button.onclick = () => {{
      const rows = Array.from(document.querySelectorAll('#sipHeaderRows .header-row'));
      if (rows.length <= 1) {{
        rows[0].querySelectorAll('input').forEach(input => input.value = '');
        return;
      }}
      button.closest('.header-row')?.remove();
    }};
  }});
}}
addHeaderRow.addEventListener('click', () => {{
  const row = document.createElement('div');
  row.className = 'header-row';
  row.innerHTML = '<input class="control" name="header_name[]" placeholder="Header"><input class="control" name="header_value[]" placeholder="Value"><button class="button danger header-remove" type="button">-</button>';
  sipHeaderRows.appendChild(row);
  bindHeaderButtons();
}});
answerTimeout.addEventListener('input', syncRings);
sipNumberMode.addEventListener('change', syncMode);
alertInfoMode.addEventListener('change', syncAlertInfo);
amdEnabled.addEventListener('change', syncAmd);
amdAction.addEventListener('change', syncAmd);
bindHeaderButtons();
syncMode();
syncAlertInfo();
syncAmd();
syncRings();
</script>"""


def sip_outbound_trunk_form_html(values, error, submit_label):
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    server_sections = []
    for index, server in enumerate(values.get("servers") or [], start=1):
        default_open = " open" if index == 1 else ""
        summary = f"SIP Server {index}"
        if index > 1:
            summary += " (Optional)"
        transport_options = "".join(
            f'<option value="{h(value)}"{" selected" if server.get("transport") == value else ""}>{h(label)}</option>'
            for value, label in (("dns", "DNS"), ("udp", "UDP"), ("tcp", "TCP"), ("tls", "TLS"))
        )
        server_required = " required" if index == 1 else ""
        server_sections.append(
            f"""<details class="advanced"{default_open}>
<summary>{h(summary)}</summary>
<div class="advanced-body">
<div class="row"><label>Server</label><input class="control server-host" data-index="{index}" name="server_{index}_host" value="{h(server.get("server"))}"{server_required}></div>
<div class="row"><label>Outbound Proxy (Optional)</label><input class="control" name="server_{index}_proxy" value="{h(server.get("outbound_proxy"))}"></div>
<div class="row"><label>Transport</label><input type="hidden" class="server-transport-auto" data-index="{index}" name="server_{index}_transport_auto" value="{h(server.get("transport_auto") or "0")}"><select class="control short-control server-transport" data-index="{index}" name="server_{index}_transport">{transport_options}</select></div>
<div class="row server-port-row" data-index="{index}"><label>Port</label><input class="control short-control server-port" data-index="{index}" type="number" min="1" max="65535" name="server_{index}_port" value="{h(server.get("port"))}"></div>
<div class="row"><label>Registration Expires (seconds)</label><input class="control short-control" type="number" min="60" max="86400" name="server_{index}_expires" value="{h(server.get("expires"))}"></div>
</div>
</details>"""
        )
    nat_options = "".join(
        f'<option value="{h(value)}"{" selected" if values.get("outbound_nat") == value else ""}>{h(label)}</option>'
        for value, label in (("auto", "Automatic"), ("yes", "Yes"), ("no", "No"))
    )
    return f"""{error_html}<form method="post" class="grid form-surface" id="sipOutboundTrunkForm">
<div class="row"><label>Name</label><input class="control" name="name" value="{h(values.get("name"))}" required></div>
<div class="row"><label>Username</label><input class="control" name="username" value="{h(values.get("username"))}" required></div>
<div class="row"><label>Password</label><input class="control" type="password" name="password" value="{h(values.get("password"))}" required></div>
<div class="row"><label>Caller ID Number</label><input class="control" name="callerid_number" value="{h(values.get("callerid_number"))}"></div>
<div class="row"><label>Caller ID Name</label><input class="control" name="callerid_name" value="{h(values.get("callerid_name"))}"></div>
<div class="row"><label>NAT</label><select class="control short-control" name="outbound_nat">{nat_options}</select></div>
<div class="notice">Add multiple servers for redundancy. Servers will be attempted from top down.</div>
{''.join(server_sections)}
<button class="button" type="submit">{h(submit_label)}</button>
</form>
<script>
let sipDnsLookupCounter = 0;
const sipDnsTimers = {{}};
function isIpLiteral(value) {{
  const raw = String(value || '').trim();
  if (!raw) return false;
  if (/^\\[[0-9a-f:.]+\\]$/i.test(raw)) return true;
  if (/^[0-9a-f:.]+$/i.test(raw) && raw.includes(':')) return true;
  return /^(25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)(\\.(25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)){{3}}$/.test(raw);
}}
function applyAutoTransport(select, transport) {{
  if (!select) return;
  select.dataset.autoApplying = '1';
  select.value = transport;
  syncOutboundServerRows();
  select.dataset.autoApplying = '0';
}}
function scheduleSipDnsLookup(index) {{
  const hostInput = document.querySelector('.server-host[data-index="' + index + '"]');
  const select = document.querySelector('.server-transport[data-index="' + index + '"]');
  const autoInput = document.querySelector('.server-transport-auto[data-index="' + index + '"]');
  if (!hostInput || !select || !autoInput || autoInput.value !== '1') {{
    syncOutboundServerRows();
    return;
  }}
  const host = hostInput.value.trim();
  if (sipDnsTimers[index]) window.clearTimeout(sipDnsTimers[index]);
  sipDnsTimers[index] = window.setTimeout(() => {{
    if (!host || isIpLiteral(host)) {{
      applyAutoTransport(select, 'udp');
      return;
    }}
    const token = String(++sipDnsLookupCounter);
    hostInput.dataset.lookupToken = token;
    fetch('/admin/sip-dns-check?host=' + encodeURIComponent(host), {{
      headers: {{ 'X-Requested-With': 'XMLHttpRequest' }}
    }})
      .then(response => response.ok ? response.json() : {{ ok: false, has_service_records: false }})
      .then(data => {{
        if (hostInput.dataset.lookupToken !== token || autoInput.value !== '1') return;
        applyAutoTransport(select, data && data.ok && data.has_service_records ? 'dns' : 'udp');
      }})
      .catch(() => {{
        if (hostInput.dataset.lookupToken !== token || autoInput.value !== '1') return;
        applyAutoTransport(select, 'udp');
      }});
  }}, 300);
}}
function syncOutboundServerRows() {{
  document.querySelectorAll('.server-transport').forEach(select => {{
    const index = select.dataset.index;
    const portRow = document.querySelector('.server-port-row[data-index="' + index + '"]');
    const portInput = document.querySelector('.server-port[data-index="' + index + '"]');
    const transport = select.value;
    portRow.style.display = transport === 'dns' ? 'none' : 'grid';
    if (transport === 'tls' && !portInput.value) portInput.value = '5061';
    if ((transport === 'udp' || transport === 'tcp') && !portInput.value) portInput.value = '5060';
    if (transport === 'tls' && portInput.value === '5060') portInput.value = '5061';
    if ((transport === 'udp' || transport === 'tcp') && portInput.value === '5061') portInput.value = '5060';
  }});
}}
document.querySelectorAll('.server-transport').forEach(select => {{
  select.addEventListener('change', () => {{
    if (select.dataset.autoApplying !== '1') {{
      const autoInput = document.querySelector('.server-transport-auto[data-index="' + select.dataset.index + '"]');
      if (autoInput) autoInput.value = '0';
    }}
    syncOutboundServerRows();
  }});
}});
document.querySelectorAll('.server-host').forEach(input => {{
  input.addEventListener('input', () => scheduleSipDnsLookup(input.dataset.index));
}});
syncOutboundServerRows();
document.querySelectorAll('.server-host').forEach(input => {{
  if (String(input.value || '').trim()) scheduleSipDnsLookup(input.dataset.index);
}});
</script>"""


def sip_runtime_refresh():
    try:
        import sip.index as sip_index
        if hasattr(sip_index, "sip_server") and hasattr(sip_index.sip_server, "maintain_outbound_trunks"):
            sip_index.sip_server.maintain_outbound_trunks()
    except Exception:
        pass


class BuiltinSipTrunksWeb:
    def forms(self):
        return {
            "ip": {
                "label": "Basic SIP Trunk (IP)",
                "description": "IP-based authentication. No username/passwords. No registration. SIP/RTP ports must be reachable from Open Paging Server to the VoIP server and vise versa.",
            },
            "auth": {
                "label": "Inbound-Authenticated SIP Trunk",
                "description": "Trunk a VoIP server into Open Paging Server. Most flexible and recommended if using a IP-PBX. Requires the SIP/RTP ports of Open Paging Server to be reachable from the VoIP server.",
            },
            "outbound": {
                "label": "Outbound-Authenticated SIP Trunk",
                "description": "Trunk Open Paging Server into a VoIP server or ITSP. Does not require SIP/RTP to be open to the internet on this server.",
            },
            "dialplan": {"label": "Dial Plan Extension", "description": "Define where incoming calls from a SIP trunk go on a per DID basis. Paging, sending messages, test tone, and echo test."},
            "number": {"label": "Outbound Dial", "description": "Send broadcasts to any phone number or extension over a SIP trunk. Such as cellphones, POTS telephones, zone controllers, page groups, and more. Use outbound dial endpoints in any group."},
        }

    def render_message_vendor_specific(self, value="", field_name="", context=None):
        capabilities = sip_message_override_capabilities()
        if not capabilities["cid"] and not capabilities["cnam"]:
            return ""
        current = sip_parse_json_object(value)
        html_parts = []
        if capabilities["cnam"]:
            html_parts.append(
                f'<div class="row"><label>CNAM Caller ID Name</label><input class="control" name="{h(field_name)}__cnam_name" value="{h(current.get("cnam_name") or "")}"></div>'
            )
        if capabilities["cid"]:
            html_parts.append(
                f'<div class="row"><label>CID Number</label><input class="control" name="{h(field_name)}__cid_number" value="{h(current.get("cid_number") or "")}"></div>'
            )
        if not html_parts:
            return ""
        return {"title": "SIP Trunks", "html": '<div class="grid">' + "".join(html_parts) + "</div>"}

    def render_form(self, form_type, request, conn_factory, page, user):
        ensure_siptrunks_schema()
        if form_type not in self.forms():
            return page("Endpoint Form", "<h1>Endpoint form not found</h1>", "endpoints", user, status=404)
        error = ""
        values = {
            "ip": {"name": "", "ipaddr": ""},
            "auth": {"name": "", "username": "", "password": "", "ipaddr": "0.0.0.0"},
            "outbound": sip_outbound_trunk_form_values(),
            "dialplan": {"name": "", "extension": "", "group": "", "trigger_type": "page", "message_id": "", "require_passcode": "", "passcode": ""},
            "number": sip_number_form_values(),
        }[form_type]
        if request.method == "POST":
            if form_type == "ip":
                values["name"] = request.form.get("name", "").strip()
                values["ipaddr"] = request.form.get("ipaddr", "").strip()
                if not values["name"] or not values["ipaddr"]:
                    error = "Name and IP address are required."
                elif not sip_valid_ip(values["ipaddr"]):
                    error = "Enter a valid IP address."
                elif sip_query_all(f"SELECT id FROM `{SIP_TRUNK_TABLE}` WHERE auth='IP' AND ipaddr=%s", (values["ipaddr"],)):
                    error = "That SIP trunk IP already exists."
                else:
                    sip_execute(
                        f"INSERT INTO `{SIP_TRUNK_TABLE}` (name, auth, trunk_type, username, password, ipaddr, status) VALUES (%s,'IP',%s,NULL,NULL,%s,'Offline')",
                        (values["name"], SIP_TRUNK_TYPE_IP, values["ipaddr"]),
                    )
                    sip_runtime_refresh()
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            elif form_type == "auth":
                for key in values:
                    values[key] = request.form.get(key, values[key]).strip()
                if not values["name"] or not values["username"] or not values["password"]:
                    error = "Name, username, and password are required."
                elif not sip_valid_ip_or_network(values["ipaddr"] or "0.0.0.0"):
                    error = "Enter a valid IP restriction."
                elif sip_query_all(f"SELECT id FROM `{SIP_TRUNK_TABLE}` WHERE auth='USERPASS' AND username=%s", (values["username"],)):
                    error = "That SIP trunk username already exists."
                else:
                    sip_execute(
                        f"INSERT INTO `{SIP_TRUNK_TABLE}` (name, auth, trunk_type, username, password, ipaddr, status) VALUES (%s,'USERPASS',%s,%s,%s,%s,'Offline')",
                        (values["name"], SIP_TRUNK_TYPE_INBOUND_AUTH, values["username"], values["password"], values["ipaddr"] or "0.0.0.0"),
                    )
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            elif form_type == "outbound":
                values = sip_outbound_trunk_form_values()
                values["name"] = request.form.get("name", "").strip()
                values["username"] = request.form.get("username", "").strip()
                values["password"] = request.form.get("password", "").strip()
                values["callerid_number"] = request.form.get("callerid_number", "").strip()
                values["callerid_name"] = request.form.get("callerid_name", "").strip()
                values["outbound_nat"] = sip_clean_outbound_nat(request.form.get("outbound_nat", "auto"))
                values["servers"] = sip_outbound_server_values_from_form(request.form)
                try:
                    collected_servers = sip_collect_outbound_servers(request.form)
                except ValueError as exc:
                    error = str(exc)
                if not error and (not values["name"] or not values["username"] or not values["password"]):
                    error = "Name, username, and password are required."
                elif not error and sip_query_all(
                    f"SELECT id FROM `{SIP_TRUNK_TABLE}` WHERE trunk_type=%s AND username=%s",
                    (SIP_TRUNK_TYPE_OUTBOUND_AUTH, values["username"]),
                ):
                    error = "That outbound SIP trunk username already exists."
                if not error:
                    values["servers"] = collected_servers
                    sip_execute(
                        f"INSERT INTO `{SIP_TRUNK_TABLE}` "
                        f"(name, auth, trunk_type, username, password, ipaddr, status, callerid_number, callerid_name, servers_json, outbound_nat, connected_server, connected_transport) "
                        f"VALUES (%s,'OUTBOUND',%s,%s,%s,'0.0.0.0','Offline',%s,%s,%s,%s,'','')",
                        (
                            values["name"],
                            SIP_TRUNK_TYPE_OUTBOUND_AUTH,
                            values["username"],
                            values["password"],
                            values["callerid_number"],
                            values["callerid_name"],
                            json.dumps(values["servers"], separators=(",", ":")),
                            values["outbound_nat"],
                        ),
                    )
                    sip_runtime_refresh()
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            elif form_type == "number":
                values = sip_number_form_values()
                values["name"] = request.form.get("name", "").strip()
                values["trunk_id"] = request.form.get("trunk_id", "").strip()
                values["number"] = request.form.get("number", "").strip()
                values["cid_number"] = request.form.get("cid_number", "").strip()
                values["cnam_name"] = request.form.get("cnam_name", "").strip()
                values["allow_cid_override"] = "1" if request.form.get("allow_cid_override") else ""
                values["allow_cnam_override"] = "1" if request.form.get("allow_cnam_override") else ""
                values["mode"] = sip_clean_output_mode(request.form.get("mode"))
                values["amd_enabled"] = "1" if request.form.get("amd_enabled") else ""
                values["amd_action"] = str(request.form.get("amd_action") or "hangup").strip().lower()
                values["amd_retry_limit"] = str(sip_clean_retry_limit(request.form.get("amd_retry_limit")))
                values["amd_retry_delay"] = str(sip_clean_retry_delay(request.form.get("amd_retry_delay")))
                values["answer_timeout"] = str(sip_clean_answer_timeout(request.form.get("answer_timeout")))
                values["answer_timeout_retry_limit"] = str(sip_clean_retry_limit(request.form.get("answer_timeout_retry_limit")))
                values["answer_timeout_retry_delay"] = str(sip_clean_retry_delay(request.form.get("answer_timeout_retry_delay")))
                values["alert_info_mode"] = sip_clean_alert_info_mode(request.form.get("alert_info_mode"))
                values["alert_info_value"] = request.form.get("alert_info_value", "").strip()
                values["headers"] = sip_collect_header_rows(request.form) or [{"name": "", "value": ""}]
                valid_trunks = {str(item.get("id")): item for item in sip_trunk_output_choices()}
                if not values["name"] or not values["number"]:
                    error = "Name and number are required."
                elif values["trunk_id"] not in valid_trunks:
                    error = "Choose a valid SIP trunk."
                elif values["amd_action"] not in SIP_OUTPUT_AMD_ACTIONS:
                    error = "Choose a valid answering machine action."
                elif values["alert_info_mode"] == "custom" and not values["alert_info_value"]:
                    error = "Enter a custom Alert-Info value."
                if not error:
                    alert_value = values["alert_info_value"] if values["alert_info_mode"] == "custom" else SIP_ALERT_INFO_PRESETS.get(values["alert_info_mode"], "")
                    sip_execute(
                        f"INSERT INTO `{SIP_OUTPUT_TABLE}` "
                        f"(`name`, `trunk_id`, `number`, `cid_number`, `cnam_name`, `allow_cid_override`, `allow_cnam_override`, `mode`, `amd_enabled`, `amd_action`, `amd_retry_limit`, `amd_retry_delay`, `answer_timeout`, `answer_timeout_retry_limit`, `answer_timeout_retry_delay`, `alert_info_mode`, `alert_info_value`, `headers_json`) "
                        f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            values["name"],
                            values["trunk_id"],
                            values["number"],
                            values["cid_number"],
                            values["cnam_name"],
                            1 if values["allow_cid_override"] == "1" else 0,
                            1 if values["allow_cnam_override"] == "1" else 0,
                            values["mode"],
                            1 if values["amd_enabled"] == "1" else 0,
                            values["amd_action"],
                            values["amd_retry_limit"],
                            values["amd_retry_delay"],
                            values["answer_timeout"],
                            values["answer_timeout_retry_limit"],
                            values["answer_timeout_retry_delay"],
                            values["alert_info_mode"],
                            alert_value,
                            json.dumps(sip_clean_headers(values["headers"]), separators=(",", ":")),
                        ),
                    )
                    sip_runtime_refresh()
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            else:
                for key in values:
                    values[key] = request.form.get(key, values[key]).strip()
                values["require_passcode"] = "1" if request.form.get("require_passcode") else ""
                values["group"] = sip_clean_groups(values["group"] or request.form.getlist("group_item"))
                values["passcode"] = values["passcode"].upper() if values["require_passcode"] == "1" else ""
                trigger = sip_dialplan_trigger(values["trigger_type"], values["message_id"])
                if values["trigger_type"] not in {"page", "message"}:
                    values["group"] = ""
                if not values["name"] or not values["extension"]:
                    error = "Name and extension are required."
                elif not re.fullmatch(r"[0-9*#]+", values["extension"]):
                    error = "Extension can only contain 0-9, *, and #."
                elif values["trigger_type"] not in {"page", "message", "#testtone", "#echotest"}:
                    error = "Choose a valid trigger."
                elif values["trigger_type"] == "message" and not values["message_id"]:
                    error = "Choose a message."
                elif values["trigger_type"] in {"page", "message"} and not values["group"]:
                    error = "Choose at least one group."
                elif values["passcode"] and not re.fullmatch(r"[0-9A-D]+", values["passcode"]):
                    error = "Passcode can only contain 0-9 and A-D."
                elif sip_query_all(f"SELECT id FROM `{SIP_DIALPLAN_TABLE}` WHERE extension=%s", (values["extension"],)):
                    error = "That SIP extension already exists."
                else:
                    sip_execute(
                        f"INSERT INTO `{SIP_DIALPLAN_TABLE}` (`name`, `extension`, `group`, `trigger`, `passcode`) VALUES (%s,%s,%s,%s,%s)",
                        (values["name"], values["extension"], values["group"] or None, trigger, values["passcode"] or None),
                    )
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
        if form_type == "ip":
            body = f"""<form method="post" class="grid surface">
<div class="row"><label>Name</label><input class="control" name="name" value="{h(values["name"])}" required></div>
<div class="row"><label>IP Address</label><input class="control" name="ipaddr" value="{h(values["ipaddr"])}" required></div>
<button type="submit">Add Basic SIP Trunk (IP)</button></form>"""
        elif form_type == "auth":
            body = f"""<form method="post" class="grid surface">
<div class="row"><label>Name</label><input class="control" name="name" value="{h(values["name"])}" required></div>
<div class="row"><label>Username</label><input class="control" name="username" value="{h(values["username"])}" required></div>
<div class="row"><label>Password</label><input class="control" type="password" name="password" value="{h(values["password"])}" required></div>
<div class="row"><label>IP Restriction</label><input class="control" name="ipaddr" value="{h(values["ipaddr"])}" required></div>
<button type="submit">Add Inbound-Authenticated SIP Trunk</button></form>"""
        elif form_type == "outbound":
            body = sip_outbound_trunk_form_html(values, error, "Add Outbound-Authenticated SIP Trunk")
        elif form_type == "number":
            body = sip_number_form_html(values, error, "Add SIP Number Endpoint")
        else:
            body = f'<form method="post" class="grid form-surface" id="dialplanForm">{sip_dialplan_fields(values)}<button class="button" type="submit">Add SIP Dial Plan Extension</button></form>'
        if error and form_type in {"ip", "auth", "dialplan"}:
            body = f'<div class="error">{h(error)}</div>' + body
        return page(self.forms()[form_type]["label"], sip_form_frame(body), "endpoints", user)

    def render_action(self, action, endpoint_id, request, conn_factory, page, user):
        ensure_siptrunks_schema()
        kind, _, row_id = str(endpoint_id or "").partition("-")
        if action not in {"edit", "delete"} or kind not in {"trunk", "dialplan", "number"} or not row_id.isdigit():
            return page("Endpoint Action", "<h1>Invalid endpoint action</h1>", "endpoints", user, status=400)
        table = SIP_TRUNK_TABLE if kind == "trunk" else SIP_DIALPLAN_TABLE if kind == "dialplan" else SIP_OUTPUT_TABLE
        rows = sip_query_all(f"SELECT * FROM `{table}` WHERE id=%s LIMIT 1", (row_id,))
        if not rows:
            return page("Endpoint Action", "<h1>Endpoint not found</h1>", "endpoints", user, status=404)
        row = rows[0]
        error = ""
        if request.method == "POST":
            if action == "delete":
                if kind == "trunk":
                    if sip_query_all(f"SELECT id FROM `{SIP_OUTPUT_TABLE}` WHERE trunk_id=%s LIMIT 1", (row_id,)):
                        error = "Delete SIP number endpoints that use this trunk first."
                    else:
                        sip_execute(f"DELETE FROM `{table}` WHERE id=%s", (row_id,))
                        sip_runtime_refresh()
                        return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
                else:
                    sip_execute(f"DELETE FROM `{table}` WHERE id=%s", (row_id,))
                    if kind == "number":
                        sip_runtime_refresh()
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            elif kind == "trunk":
                auth_type = str(row.get("auth") or "IP").upper()
                if auth_type == "IP":
                    name = request.form.get("name", "").strip()
                    ipaddr = request.form.get("ipaddr", "").strip()
                    holdbehavior = request.form.get("holdbehavior", "passrtp").strip().lower()
                    if not name:
                        error = "Name is required."
                    elif holdbehavior not in {"passrtp", "pausertp", "endcall"}:
                        error = "Choose a valid hold behavior."
                    elif not ipaddr or not sip_valid_ip(ipaddr):
                        error = "Enter a valid IP address."
                    else:
                        sip_execute(
                            f"UPDATE `{table}` SET name=%s, trunk_type=%s, username=NULL, password=NULL, ipaddr=%s, holdbehavior=%s WHERE id=%s",
                            (name, SIP_TRUNK_TYPE_IP, ipaddr, holdbehavior, row_id),
                        )
                        sip_runtime_refresh()
                        return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
                    row.update({"name": name, "ipaddr": ipaddr, "holdbehavior": holdbehavior, "trunk_type": SIP_TRUNK_TYPE_IP})
                elif siptrunks_is_outbound_row(row):
                    values = sip_outbound_trunk_form_values(row)
                    values["name"] = request.form.get("name", "").strip()
                    values["username"] = request.form.get("username", "").strip()
                    values["password"] = request.form.get("password", "").strip()
                    values["callerid_number"] = request.form.get("callerid_number", "").strip()
                    values["callerid_name"] = request.form.get("callerid_name", "").strip()
                    values["outbound_nat"] = sip_clean_outbound_nat(request.form.get("outbound_nat", "auto"))
                    values["servers"] = sip_outbound_server_values_from_form(request.form)
                    try:
                        collected_servers = sip_collect_outbound_servers(request.form)
                    except ValueError as exc:
                        error = str(exc)
                    if not error and (not values["name"] or not values["username"] or not values["password"]):
                        error = "Name, username, and password are required."
                    elif not error and sip_query_all(
                        f"SELECT id FROM `{SIP_TRUNK_TABLE}` WHERE trunk_type=%s AND username=%s AND id<>%s",
                        (SIP_TRUNK_TYPE_OUTBOUND_AUTH, values["username"], row_id),
                    ):
                        error = "That outbound SIP trunk username already exists."
                    if not error:
                        values["servers"] = collected_servers
                        sip_execute(
                            f"UPDATE `{table}` SET name=%s, auth='OUTBOUND', trunk_type=%s, username=%s, password=%s, callerid_number=%s, callerid_name=%s, servers_json=%s, outbound_nat=%s WHERE id=%s",
                            (
                                values["name"],
                                SIP_TRUNK_TYPE_OUTBOUND_AUTH,
                                values["username"],
                                values["password"],
                                values["callerid_number"],
                                values["callerid_name"],
                                json.dumps(values["servers"], separators=(",", ":")),
                                values["outbound_nat"],
                                row_id,
                            ),
                        )
                        sip_runtime_refresh()
                        return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
                    row.update(values)
                else:
                    name = request.form.get("name", "").strip()
                    username = request.form.get("username", "").strip()
                    password = request.form.get("password", "").strip()
                    ipaddr = request.form.get("ipaddr", "").strip()
                    holdbehavior = request.form.get("holdbehavior", "passrtp").strip().lower()
                    if not name:
                        error = "Name is required."
                    elif holdbehavior not in {"passrtp", "pausertp", "endcall"}:
                        error = "Choose a valid hold behavior."
                    elif not username or not password:
                        error = "Username and password are required."
                    elif not sip_valid_ip_or_network(ipaddr or "0.0.0.0"):
                        error = "Enter a valid IP restriction."
                    else:
                        sip_execute(
                            f"UPDATE `{table}` SET name=%s, trunk_type=%s, username=%s, password=%s, ipaddr=%s, holdbehavior=%s WHERE id=%s",
                            (name, SIP_TRUNK_TYPE_INBOUND_AUTH, username, password, ipaddr or "0.0.0.0", holdbehavior, row_id),
                        )
                        return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
                    row.update({"name": name, "username": username, "password": password, "ipaddr": ipaddr, "holdbehavior": holdbehavior, "trunk_type": SIP_TRUNK_TYPE_INBOUND_AUTH})
            elif kind == "number":
                values = sip_number_form_values(row)
                values["name"] = request.form.get("name", "").strip()
                values["trunk_id"] = request.form.get("trunk_id", "").strip()
                values["number"] = request.form.get("number", "").strip()
                values["cid_number"] = request.form.get("cid_number", "").strip()
                values["cnam_name"] = request.form.get("cnam_name", "").strip()
                values["allow_cid_override"] = "1" if request.form.get("allow_cid_override") else ""
                values["allow_cnam_override"] = "1" if request.form.get("allow_cnam_override") else ""
                values["mode"] = sip_clean_output_mode(request.form.get("mode"))
                values["amd_enabled"] = "1" if request.form.get("amd_enabled") else ""
                values["amd_action"] = str(request.form.get("amd_action") or "hangup").strip().lower()
                values["amd_retry_limit"] = str(sip_clean_retry_limit(request.form.get("amd_retry_limit")))
                values["amd_retry_delay"] = str(sip_clean_retry_delay(request.form.get("amd_retry_delay")))
                values["answer_timeout"] = str(sip_clean_answer_timeout(request.form.get("answer_timeout")))
                values["answer_timeout_retry_limit"] = str(sip_clean_retry_limit(request.form.get("answer_timeout_retry_limit")))
                values["answer_timeout_retry_delay"] = str(sip_clean_retry_delay(request.form.get("answer_timeout_retry_delay")))
                values["alert_info_mode"] = sip_clean_alert_info_mode(request.form.get("alert_info_mode"))
                values["alert_info_value"] = request.form.get("alert_info_value", "").strip()
                values["headers"] = sip_collect_header_rows(request.form) or [{"name": "", "value": ""}]
                valid_trunks = {str(item.get("id")): item for item in sip_trunk_output_choices()}
                if not values["name"] or not values["number"]:
                    error = "Name and number are required."
                elif values["trunk_id"] not in valid_trunks:
                    error = "Choose a valid SIP trunk."
                elif values["amd_action"] not in SIP_OUTPUT_AMD_ACTIONS:
                    error = "Choose a valid answering machine action."
                elif values["alert_info_mode"] == "custom" and not values["alert_info_value"]:
                    error = "Enter a custom Alert-Info value."
                if not error:
                    alert_value = values["alert_info_value"] if values["alert_info_mode"] == "custom" else SIP_ALERT_INFO_PRESETS.get(values["alert_info_mode"], "")
                    sip_execute(
                        f"UPDATE `{table}` SET `name`=%s, `trunk_id`=%s, `number`=%s, `cid_number`=%s, `cnam_name`=%s, `allow_cid_override`=%s, `allow_cnam_override`=%s, `mode`=%s, `amd_enabled`=%s, `amd_action`=%s, `amd_retry_limit`=%s, `amd_retry_delay`=%s, `answer_timeout`=%s, `answer_timeout_retry_limit`=%s, `answer_timeout_retry_delay`=%s, `alert_info_mode`=%s, `alert_info_value`=%s, `headers_json`=%s WHERE id=%s",
                        (
                            values["name"],
                            values["trunk_id"],
                            values["number"],
                            values["cid_number"],
                            values["cnam_name"],
                            1 if values["allow_cid_override"] == "1" else 0,
                            1 if values["allow_cnam_override"] == "1" else 0,
                            values["mode"],
                            1 if values["amd_enabled"] == "1" else 0,
                            values["amd_action"],
                            values["amd_retry_limit"],
                            values["amd_retry_delay"],
                            values["answer_timeout"],
                            values["answer_timeout_retry_limit"],
                            values["answer_timeout_retry_delay"],
                            values["alert_info_mode"],
                            alert_value,
                            json.dumps(sip_clean_headers(values["headers"]), separators=(",", ":")),
                            row_id,
                        ),
                    )
                    sip_runtime_refresh()
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
                row.update(values)
            else:
                name = request.form.get("name", "").strip()
                extension = request.form.get("extension", "").strip()
                trigger_type = request.form.get("trigger_type", "page").strip()
                message_id = request.form.get("message_id", "").strip()
                group = sip_clean_groups(request.form.get("group", "") or request.form.getlist("group_item"))
                passcode = request.form.get("passcode", "").strip().upper() if request.form.get("require_passcode") else ""
                trigger = sip_dialplan_trigger(trigger_type, message_id)
                duplicate = sip_query_all(f"SELECT id FROM `{SIP_DIALPLAN_TABLE}` WHERE extension=%s AND id<>%s", (extension, row_id))
                if trigger_type not in {"page", "message"}:
                    group = ""
                if not name or not extension:
                    error = "Enter a name and extension."
                elif not re.fullmatch(r"[0-9*#]+", extension):
                    error = "Extension can only contain 0-9, *, and #."
                elif trigger_type not in {"page", "message", "#testtone", "#echotest"}:
                    error = "Choose a valid trigger."
                elif trigger_type == "message" and not message_id:
                    error = "Choose a message."
                elif trigger_type in {"page", "message"} and not group:
                    error = "Choose at least one group."
                elif passcode and not re.fullmatch(r"[0-9A-D]+", passcode):
                    error = "Passcode can only contain 0-9 and A-D."
                elif duplicate:
                    error = "A dial plan entry already exists for that extension."
                else:
                    sip_execute(
                        f"UPDATE `{table}` SET `name`=%s, `extension`=%s, `group`=%s, `trigger`=%s, `passcode`=%s WHERE `id`=%s",
                        (name, extension, group or None, trigger, passcode or None, row_id),
                    )
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
                row.update({"name": name, "extension": extension, "group": group, "trigger": trigger, "passcode": passcode})
        if action == "delete":
            body = f'<form method="post" class="grid surface"><p class="meta">Delete {h(row.get("name") or endpoint_id)}?</p><button class="danger" type="submit">Delete Endpoint</button></form>'
            if error:
                body = f'<div class="error">{h(error)}</div>' + body
            return page("Endpoint Action", sip_form_frame(body), "endpoints", user)
        if kind == "trunk":
            auth_type = str(row.get("auth") or "IP").upper()
            hold_value = str(row.get("holdbehavior") or "passrtp").lower()
            if siptrunks_is_outbound_row(row):
                values = sip_outbound_trunk_form_values(row)
                values["name"] = str(row.get("name") or values["name"])
                values["username"] = str(row.get("username") or values["username"])
                values["password"] = str(row.get("password") or values["password"])
                values["callerid_number"] = str(row.get("callerid_number") or values["callerid_number"])
                values["callerid_name"] = str(row.get("callerid_name") or values["callerid_name"])
                values["outbound_nat"] = sip_clean_outbound_nat(row.get("outbound_nat") or values["outbound_nat"])
                body = sip_outbound_trunk_form_html(values, error, "Save SIP Trunk")
            else:
                options = "".join(
                    f'<option value="{h(value)}"{" selected" if hold_value == value else ""}>{h(label)}</option>'
                    for value, label in (("passrtp", "Pass RTP"), ("pausertp", "Pause RTP"), ("endcall", "End Call"))
                )
                auth_fields = (
                    f'<div class="row"><label>IP Address</label><input class="control" name="ipaddr" value="{h(row.get("ipaddr"))}" required></div>'
                    if auth_type == "IP"
                    else f'<div class="row"><label>Username</label><input class="control" name="username" value="{h(row.get("username"))}" required></div><div class="row"><label>Password</label><input class="control" type="password" name="password" value="{h(row.get("password"))}" required></div><div class="row"><label>IP Restriction</label><input class="control" name="ipaddr" value="{h(row.get("ipaddr") or "0.0.0.0")}" required></div>'
                )
                body = f"""<form method="post" class="grid surface">
<p class="meta">Current status: {h(siptrunks_status_label(row))}</p>
<div class="row"><label>Name</label><input class="control" name="name" value="{h(row.get("name"))}" required></div>
{auth_fields}<div class="row"><label>Hold Behavior</label><select class="control" name="holdbehavior">{options}</select></div>
<button type="submit">Save SIP Trunk</button></form>"""
                if error:
                    body = f'<div class="error">{h(error)}</div>' + body
        elif kind == "number":
            body = sip_number_form_html(sip_number_form_values(row), error, "Save SIP Number Endpoint")
        else:
            trigger_type, message_id = sip_split_dialplan_trigger(row.get("trigger"))
            values = {
                "name": str(row.get("name") or ""),
                "extension": str(row.get("extension") or ""),
                "group": str(row.get("group") or ""),
                "trigger_type": trigger_type,
                "message_id": message_id,
                "require_passcode": "1" if row.get("passcode") else "",
                "passcode": str(row.get("passcode") or ""),
            }
            body = f'<form method="post" class="grid form-surface" id="dialplanForm">{sip_dialplan_fields(values)}<button class="button" type="submit">Save SIP Dial Plan Extension</button></form>'
            if error:
                body = f'<div class="error">{h(error)}</div>' + body
        return page("Endpoint Action", sip_form_frame(body), "endpoints", user)

    def render_settings(self, request, conn_factory, page, user):
        return page("SIP Trunk Settings", "<p>No additional settings are required for SIP trunks.</p>", "endpoints", user)


def sip_parse_datetime_value(value):
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text.split(".", 1)[0], pattern)
        except ValueError:
            continue
    return None


def sip_output_vendor_overrides(metadata):
    try:
        from broadcasts import parse_vendor_specific
    except Exception:
        return {}
    parsed = parse_vendor_specific((metadata or {}).get("vendor_specific") or "")
    value = parsed.get("siptrunks") or {}
    return value if isinstance(value, dict) else {}


def sip_output_alert_value(row):
    mode = sip_clean_alert_info_mode(row.get("alert_info_mode"))
    return str(row.get("alert_info_value") or "").strip() if mode == "custom" else SIP_ALERT_INFO_PRESETS.get(mode, "")


def sip_output_headers(row):
    return sip_clean_headers(row.get("headers_json"))


def sip_output_caller_values(row, metadata):
    overrides = sip_output_vendor_overrides(metadata)
    cid_number = str(row.get("cid_number") or "").strip()
    cnam_name = str(row.get("cnam_name") or "").strip()
    if str(row.get("allow_cid_override") or "0") in {"1", "true", "True"}:
        cid_number = str(overrides.get("cid_number") or cid_number).strip()
    if str(row.get("allow_cnam_override") or "0") in {"1", "true", "True"}:
        cnam_name = str(overrides.get("cnam_name") or cnam_name).strip()
    return cid_number, cnam_name


def sip_parse_rtp_payload(packet):
    if len(packet) < 12:
        return b""
    cc = packet[0] & 0x0F
    ext = (packet[0] & 0x10) >> 4
    offset = 12 + cc * 4
    if ext:
        if len(packet) < offset + 4:
            return b""
        ext_len = struct.unpack(">H", packet[offset + 2:offset + 4])[0]
        offset += 4 + ext_len * 4
    return packet[offset:] if offset < len(packet) else b""


def sip_latchable_rtp_packet(packet):
    if len(packet) < 12:
        return False
    if ((packet[:1] or b"\x00")[0] >> 6) != 2:
        return False
    packet_type = packet[1] if len(packet) > 1 else 0
    return not (192 <= packet_type <= 223)


class SipBroadcastRecorder:
    def __init__(self, stream_id):
        self.stream_id = str(stream_id or uuid.uuid4().hex)
        self.partial = bytearray()
        self.lock = threading.Lock()
        self.finished = threading.Event()
        self.runtime_dir = Path(tempfile.gettempdir()) / "openpagingserver-runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime_dir / f"sip-broadcast-{self.stream_id}.mulaw"
        self.handle = open(self.path, "wb")
        self.bytes_written = 0

    def write_audio(self, chunk):
        if self.finished.is_set():
            return
        data = bytes(chunk or b"")
        if not data:
            return
        with self.lock:
            self.partial.extend(data)
            while len(self.partial) >= SIP_OUTPUT_FRAME_BYTES:
                frame = bytes(self.partial[:SIP_OUTPUT_FRAME_BYTES])
                del self.partial[:SIP_OUTPUT_FRAME_BYTES]
                self.handle.write(frame)
                self.bytes_written += len(frame)
            self.handle.flush()

    def finish_input(self):
        with self.lock:
            if self.finished.is_set():
                return
            if self.partial:
                frame = bytes(self.partial).ljust(SIP_OUTPUT_FRAME_BYTES, b"\xff")
                self.handle.write(frame)
                self.bytes_written += len(frame)
                self.partial.clear()
            self.handle.flush()
            self.handle.close()
            self.finished.set()

    def cleanup(self):
        self.finish_input()
        try:
            self.path.unlink()
        except OSError:
            pass


def sip_rtp_socket_name(sock):
    try:
        host, port = sock.getsockname()[:2]
        return f"{host}:{port}"
    except Exception:
        return "unknown"


class SipRtpSender:
    def __init__(self, call):
        self.call = call
        if not hasattr(self.call, "rtp_sequence"):
            self.call.rtp_sequence = random.randrange(0, 65536)
        if not hasattr(self.call, "rtp_timestamp"):
            self.call.rtp_timestamp = random.randrange(0, 4294967296)
        if not hasattr(self.call, "rtp_ssrc"):
            self.call.rtp_ssrc = random.randrange(0, 4294967296)
        if not hasattr(self.call, "rtp_packets_sent"):
            self.call.rtp_packets_sent = 0
        if not hasattr(self.call, "rtp_packets_received"):
            self.call.rtp_packets_received = 0
        self.codec_encoder = None

    def call_finished(self):
        if bool(getattr(self.call, "released", False)):
            return True
        for attr in ("finished_event", "disconnected_event"):
            event = getattr(self.call, attr, None)
            if event is not None:
                try:
                    if event.is_set():
                        return True
                except Exception:
                    pass
        return False

    def learn_source(self, max_packets=4):
        if self.call_finished():
            return False
        if not getattr(self.call, "rtp_latching_enabled", False):
            return False
        sock = getattr(self.call, "rtp_socket", None)
        if sock is None:
            return False
        learned = False
        for _ in range(max(1, int(max_packets or 1))):
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
            try:
                source_port = int(addr[1] or 0)
            except Exception:
                source_port = 0
            current_port = int(getattr(self.call, "remote_media_port", 0) or 0)
            if (
                not source_ip
                or source_port <= 0
                or not sip_latchable_rtp_packet(packet)
                or (current_port > 0 and current_port % 2 == 0 and source_port == current_port + 1 and source_port % 2 == 1)
            ):
                continue
            old_ip = str(getattr(self.call, "remote_media_ip", "") or "")
            old_port = int(getattr(self.call, "remote_media_port", 0) or 0)
            self.call.remote_media_ip = source_ip
            self.call.remote_media_port = source_port
            self.call.rtp_packets_received = int(getattr(self.call, "rtp_packets_received", 0) or 0) + 1
            if (old_ip, old_port) != (source_ip, source_port) or self.call.rtp_packets_received <= 3 or self.call.rtp_packets_received % 50 == 0:
                page_debug(
                    f"sip rtp learned call={getattr(self.call, 'call_id', '')} "
                    f"packet={self.call.rtp_packets_received} old={old_ip}:{old_port} "
                    f"new={source_ip}:{source_port} local={sip_rtp_socket_name(sock)} bytes={len(packet)}"
                )
            learned = True
        return learned

    def prime(self, frame_count=12):
        for _ in range(max(0, int(frame_count))):
            if not self.send_frame(SIP_OUTPUT_SILENCE_FRAME):
                break
            time.sleep(0.02)

    def send_frame(self, payload):
        if self.call_finished():
            return False
        self.learn_source()
        if not self.call.remote_media_ip or not self.call.remote_media_port:
            return False
        codec = str(getattr(self.call, "negotiated_codec", "") or "PCMU").upper()
        codec_info = SIP_EDGE_CODEC_PAYLOADS.get(codec) or SIP_EDGE_CODEC_PAYLOADS["PCMU"]
        encoded, self.codec_encoder = encode_edge_rtp_payload(
            codec,
            bytes(payload or SIP_OUTPUT_SILENCE_FRAME),
            encoder_state=self.codec_encoder,
        )
        if not encoded:
            # Encoder still priming (first G722/Opus frame): frame consumed,
            # nothing to put on the wire yet.
            return True
        packet = struct.pack(
            "!BBHII",
            0x80,
            int(codec_info.get("payload_type", 0)) & 0x7F,
            int(self.call.rtp_sequence) & 0xFFFF,
            int(self.call.rtp_timestamp) & 0xFFFFFFFF,
            int(self.call.rtp_ssrc) & 0xFFFFFFFF,
        ) + encoded
        try:
            self.call.rtp_socket.sendto(packet, (self.call.remote_media_ip, int(self.call.remote_media_port)))
        except OSError as exc:
            page_debug(
                f"sip rtp send failed call={getattr(self.call, 'call_id', '')} "
                f"local={sip_rtp_socket_name(getattr(self.call, 'rtp_socket', None))} "
                f"remote={self.call.remote_media_ip}:{int(self.call.remote_media_port)} error={exc}"
            )
            return False
        self.call.rtp_packets_sent = int(getattr(self.call, "rtp_packets_sent", 0) or 0) + 1
        if self.call.rtp_packets_sent <= 3 or self.call.rtp_packets_sent % 50 == 0:
            page_debug(
                f"sip rtp sent call={getattr(self.call, 'call_id', '')} "
                f"packet={self.call.rtp_packets_sent} local={sip_rtp_socket_name(self.call.rtp_socket)} "
                f"remote={self.call.remote_media_ip}:{int(self.call.remote_media_port)} bytes={len(packet)}"
            )
        self.call.rtp_sequence = (int(self.call.rtp_sequence) + 1) & 0xFFFF
        self.call.rtp_timestamp = (int(self.call.rtp_timestamp) + int(codec_info.get("samples_per_frame", SIP_OUTPUT_FRAME_BYTES))) & 0xFFFFFFFF
        return True


class SipOutputSession:
    def __init__(self, row, metadata, recorder, on_ready, on_done, module_name="siptrunks", stream_id=""):
        self.row = dict(row or {})
        self.metadata = dict(metadata or {})
        self.recorder = recorder
        self.on_ready = on_ready
        self.on_done = on_done
        self.module_name = str(module_name or "siptrunks")
        self.stream_id = str(stream_id or "")
        self.mode = sip_clean_output_mode(self.row.get("mode"))
        self.stop_event = threading.Event()
        self.input_finished = threading.Event()
        self.ready_sent = False
        self.call = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.live_lock = threading.Lock()
        self.live_frames = deque()

    def start(self):
        self.thread.start()

    def receive_audio(self, chunk):
        if self.mode != SIP_OUTPUT_MODE_PAGE or self.stop_event.is_set():
            return
        data = bytes(chunk or b"")
        if not data:
            return
        with self.live_lock:
            for offset in range(0, len(data), SIP_OUTPUT_FRAME_BYTES):
                frame = data[offset:offset + SIP_OUTPUT_FRAME_BYTES]
                if len(frame) < SIP_OUTPUT_FRAME_BYTES:
                    frame = frame.ljust(SIP_OUTPUT_FRAME_BYTES, b"\xff")
                self.live_frames.append(frame)

    def finish_input_stream(self):
        self.input_finished.set()

    def stop(self):
        self.stop_event.set()
        if self.call is not None:
            try:
                self.call.hangup()
            except Exception:
                pass

    def should_retry_after_finish(self):
        broadcast_id = str(self.metadata.get("broadcast_id") or "").strip()
        expires_rule = str(self.metadata.get("expires_rule") or "").strip().lower()
        expires_at = sip_parse_datetime_value(self.metadata.get("expires"))
        if expires_at and datetime.now() >= expires_at:
            return False
        if not broadcast_id:
            return not self.recorder.finished.is_set()
        record = fetch_active_broadcast(broadcast_id)
        if record is None:
            return False
        if message_expiration_is_immediate(expires_rule) and self.recorder.finished.is_set():
            return False
        return True

    def amd_average_energy(self, payload):
        if len(payload) < SIP_OUTPUT_FRAME_BYTES:
            return 0.0
        total = 0
        for byte in payload[:SIP_OUTPUT_FRAME_BYTES]:
            total += abs(ULAW_TO_LINEAR_TABLE[byte])
        return total / SIP_OUTPUT_FRAME_BYTES

    def amd_linear_samples(self, payload):
        if len(payload) < SIP_OUTPUT_FRAME_BYTES:
            return []
        return [ULAW_TO_LINEAR_TABLE[byte] for byte in payload[:SIP_OUTPUT_FRAME_BYTES]]

    def amd_goertzel_power(self, samples, frequency, sample_rate=8000.0):
        if not samples:
            return 0.0
        coeff = 2.0 * math.cos((2.0 * math.pi * float(frequency)) / float(sample_rate))
        prev = 0.0
        prev2 = 0.0
        for sample in samples:
            current = float(sample) + (coeff * prev) - prev2
            prev2 = prev
            prev = current
        return (prev2 * prev2) + (prev * prev) - (coeff * prev * prev2)

    def amd_beep_detected(self, payload, average_energy):
        if average_energy < SIP_AMD_BEEP_MIN_ENERGY:
            return False
        samples = self.amd_linear_samples(payload)
        if not samples:
            return False
        total_power = 0.0
        for sample in samples:
            total_power += float(sample) * float(sample)
        if total_power <= 0.0:
            return False
        strongest_ratio = 0.0
        for frequency in SIP_AMD_BEEP_FREQUENCIES:
            ratio = self.amd_goertzel_power(samples, frequency) / total_power
            if ratio > strongest_ratio:
                strongest_ratio = ratio
        return strongest_ratio >= SIP_AMD_BEEP_POWER_RATIO

    def detect_answering_machine(self, call, listen_seconds=SIP_AMD_LISTEN_SECONDS):
        deadline = time.time() + max(0.5, float(listen_seconds))
        noise_floor = 120.0
        current_speech_ms = 0
        longest_speech_ms = 0
        total_speech_ms = 0
        silence_after_speech_ms = 0
        beep_run_ms = 0
        speech_segments = []

        def finish_segment():
            nonlocal current_speech_ms, longest_speech_ms
            if current_speech_ms <= 0:
                return
            if current_speech_ms > longest_speech_ms:
                longest_speech_ms = current_speech_ms
            speech_segments.append(current_speech_ms)
            current_speech_ms = 0

        def human_greeting_detected():
            first_segment_ms = speech_segments[0] if speech_segments else 0
            return (
                first_segment_ms >= SIP_AMD_HUMAN_MIN_GREETING_MS
                and first_segment_ms <= SIP_AMD_HUMAN_MAX_GREETING_MS
                and total_speech_ms <= SIP_AMD_HUMAN_TOTAL_MS
                and silence_after_speech_ms >= SIP_AMD_HUMAN_PAUSE_MS
            )

        def machine_greeting_detected():
            return (
                longest_speech_ms >= SIP_AMD_MACHINE_AFTER_PAUSE_MS
                or total_speech_ms >= SIP_AMD_MACHINE_TOTAL_MS
            )

        # AMD analysis operates on 8 kHz u-law samples. Inbound RTP may use any
        # negotiated codec (PCMA, G722, OPUS, ...), so decode every payload to
        # u-law first; without this AMD only ever worked on PCMU.
        codec = str(getattr(call, "negotiated_codec", "") or "PCMU")
        decoder_state = None
        try:
            while time.time() < deadline and not self.stop_event.is_set():
                try:
                    packet, addr = call.rtp_socket.recvfrom(4096)
                except socket.timeout:
                    if current_speech_ms > 0:
                        finish_segment()
                    if speech_segments:
                        silence_after_speech_ms += SIP_AMD_TIMEOUT_STEP_MS
                        if human_greeting_detected():
                            return False
                        if machine_greeting_detected():
                            return True
                    continue
                except OSError:
                    return False
                if not sip_latchable_rtp_packet(packet):
                    continue
                if getattr(call, "rtp_latching_enabled", False) and addr and len(addr) >= 2:
                    source_ip = str(addr[0] or "").strip()
                    source_port = int(addr[1] or 0)
                    if source_ip and source_port > 0:
                        call.remote_media_ip = source_ip
                        call.remote_media_port = source_port
                payload, decoder_state = decode_edge_rtp_payload(
                    codec, sip_parse_rtp_payload(packet), decoder_state
                )
                if len(payload) < SIP_OUTPUT_FRAME_BYTES:
                    continue
                average = self.amd_average_energy(payload)
                if self.amd_beep_detected(payload, average):
                    beep_run_ms += SIP_AMD_FRAME_MS
                    if beep_run_ms >= SIP_AMD_BEEP_MS:
                        return True
                else:
                    beep_run_ms = 0
                voice_threshold = max(SIP_AMD_MIN_VOICE_AVERAGE, noise_floor * SIP_AMD_NOISE_MULTIPLIER)
                is_voice = average >= voice_threshold
                if is_voice:
                    current_speech_ms += SIP_AMD_FRAME_MS
                    total_speech_ms += SIP_AMD_FRAME_MS
                    silence_after_speech_ms = 0
                    if current_speech_ms >= SIP_AMD_MACHINE_CONTINUOUS_MS:
                        return True
                else:
                    noise_floor = (noise_floor * 0.9) + (average * 0.1)
                    if current_speech_ms > 0:
                        finish_segment()
                    if speech_segments:
                        silence_after_speech_ms += SIP_AMD_FRAME_MS
                        if human_greeting_detected():
                            return False
                        if machine_greeting_detected():
                            return True
            finish_segment()
            if longest_speech_ms >= SIP_AMD_MACHINE_AFTER_PAUSE_MS or total_speech_ms >= SIP_AMD_MACHINE_TOTAL_MS:
                return True
            return False
        finally:
            closer = getattr(decoder_state, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass

    def next_page_frame(self):
        with self.live_lock:
            if self.live_frames:
                return self.live_frames.popleft()
        return SIP_OUTPUT_SILENCE_FRAME

    def playback_page_audio(self, call):
        sender = SipRtpSender(call)
        sender.prime()
        next_send = time.monotonic()
        while not self.stop_event.is_set():
            if self.input_finished.is_set():
                with self.live_lock:
                    if not self.live_frames:
                        break
            if not sender.send_frame(self.next_page_frame()):
                break
            next_send += 0.02
            sleep_for = next_send - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_send = time.monotonic()

    def playback_recording(self, call):
        sender = SipRtpSender(call)
        next_send = time.monotonic()
        with open(self.recorder.path, "rb") as handle:
            while not self.stop_event.is_set():
                frame = handle.read(SIP_OUTPUT_FRAME_BYTES)
                if len(frame) == SIP_OUTPUT_FRAME_BYTES:
                    if not sender.send_frame(frame):
                        break
                elif self.recorder.finished.is_set():
                    # Drain any frames written between our read and the
                    # finished flag being set to avoid cutting off the tail.
                    while not self.stop_event.is_set():
                        tail = handle.read(SIP_OUTPUT_FRAME_BYTES)
                        if len(tail) == SIP_OUTPUT_FRAME_BYTES:
                            if not sender.send_frame(tail):
                                break
                            next_send += 0.02
                            sleep_for = next_send - time.monotonic()
                            if sleep_for > 0:
                                time.sleep(sleep_for)
                            else:
                                next_send = time.monotonic()
                        else:
                            if tail:
                                sender.send_frame(tail.ljust(SIP_OUTPUT_FRAME_BYTES, b"\xff"))
                            break
                    break
                else:
                    if not sender.send_frame(SIP_OUTPUT_SILENCE_FRAME):
                        break
                next_send += 0.02
                sleep_for = next_send - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_send = time.monotonic()

    def place_call(self, answer_timeout):
        import sip.index as sip_index

        cid_number, cnam_name = sip_output_caller_values(self.row, self.metadata)
        trunk_id = str(self.row.get("trunk_id") or "").strip()
        if not trunk_id:
            raise RuntimeError("SIP output row is missing trunk_id")
        log(
            f"siptrunks place_call row_id={self.row.get('id')} trunk_id={trunk_id} "
            f"number={self.row.get('number')} mode={self.mode}"
        )
        trunk_fallback = {
            "id": trunk_id,
            "name": self.row.get("trunk_name"),
            "status": self.row.get("trunk_status"),
            "auth": self.row.get("trunk_auth"),
            "trunk_type": self.row.get("trunk_trunk_type"),
            "username": self.row.get("trunk_username"),
            "password": self.row.get("trunk_password"),
            "ipaddr": self.row.get("trunk_ipaddr"),
            "servers_json": self.row.get("trunk_servers_json"),
            "outbound_nat": self.row.get("trunk_outbound_nat"),
            "connected_server": self.row.get("trunk_connected_server"),
            "connected_transport": self.row.get("trunk_connected_transport"),
        }
        place = sip_index.sip_server.place_outbound_call
        supports_fallback = True
        try:
            import inspect
            supports_fallback = "trunk_fallback" in inspect.signature(place).parameters
        except (TypeError, ValueError):
            supports_fallback = True
        kwargs = dict(
            caller_id_number=cid_number,
            caller_id_name=cnam_name,
            alert_info_value=sip_output_alert_value(self.row),
            custom_headers=sip_output_headers(self.row),
            answer_timeout=answer_timeout,
        )
        if supports_fallback:
            kwargs["trunk_fallback"] = trunk_fallback
        return place(trunk_id, self.row.get("number"), **kwargs)

    def run_page_mode(self):
        answer_timeout = 10
        call = self.place_call(answer_timeout)
        self.call = call
        if not getattr(call, "answered", False):
            return
        if not self.ready_sent:
            self.ready_sent = True
            self.on_ready(self)
        self.playback_page_audio(call)
        call.hangup()

    def run_telephone_mode(self):
        answer_timeout = sip_clean_answer_timeout(self.row.get("answer_timeout") or 45)
        timeout_retries = sip_clean_retry_limit(self.row.get("answer_timeout_retry_limit") or 0)
        timeout_delay = sip_clean_retry_delay(self.row.get("answer_timeout_retry_delay") or 5)
        amd_enabled = str(self.row.get("amd_enabled") or "0") in {"1", "true", "True"}
        amd_action = str(self.row.get("amd_action") or "hangup").strip().lower()
        amd_retry_limit = sip_clean_retry_limit(self.row.get("amd_retry_limit") or 0)
        amd_retry_delay = sip_clean_retry_delay(self.row.get("amd_retry_delay") or 5)
        timeout_attempts = 0
        amd_attempts = 0
        while not self.stop_event.is_set():
            call = self.place_call(answer_timeout)
            self.call = call
            if not getattr(call, "answered", False):
                reason = str(getattr(call, "failure_reason", "") or "").lower()
                if "timed out" in reason and timeout_attempts < timeout_retries and self.should_retry_after_finish():
                    timeout_attempts += 1
                    time.sleep(timeout_delay)
                    continue
                return
            # Signal readiness as soon as the call is answered so the broadcast
            # delivery starts feeding the shared recorder immediately. Without
            # this the stream is never marked ready and deliver_broadcast holds
            # all audio until its ~10s fallback deadline, which on longer
            # messages plays back as a large startup delay / dead air. Firing
            # here (before AMD) also gives the recorder a head start so playback
            # has a jitter cushion instead of underrunning at the first frame.
            if not self.ready_sent:
                self.ready_sent = True
                self.on_ready(self)
            SipRtpSender(call).prime()
            if amd_enabled and self.detect_answering_machine(call):
                call.hangup("Answering machine detected")
                if amd_action == "redial" and amd_attempts < amd_retry_limit and self.should_retry_after_finish():
                    amd_attempts += 1
                    time.sleep(amd_retry_delay)
                    continue
                return
            self.playback_recording(call)
            call.hangup()
            return

    def run(self):
        try:
            if self.mode == SIP_OUTPUT_MODE_PAGE:
                self.run_page_mode()
            else:
                self.run_telephone_mode()
        except Exception as exc:
            log(f"siptrunks session_error row_id={self.row.get('id')} mode={self.mode} error={exc}")
            if self.stream_id:
                mark_failed(self.module_name, self.stream_id)
        finally:
            self.on_done(self)


class SipTrunksStreamState:
    def __init__(self, module_name, stream_id, metadata, on_empty):
        self.module_name = module_name
        self.stream_id = stream_id
        self.metadata = dict(metadata or {})
        self.on_empty = on_empty
        self.recorder = SipBroadcastRecorder(stream_id)
        self.sessions = []
        self.lock = threading.Lock()
        self.page_pending = 0
        self.ready_marked = False

    def add_session(self, session):
        with self.lock:
            self.sessions.append(session)
            # Only page-mode sessions gate stream readiness. Telephone-mode
            # dials are independent: the broadcast must start immediately and
            # play on other endpoints while the phone rings, so they never
            # block (and a phone that is never answered must not fail or stall
            # the broadcast).
            if session.mode == SIP_OUTPUT_MODE_PAGE:
                self.page_pending += 1

    def mark_nonblocking_ready(self):
        should_mark = False
        with self.lock:
            if not self.ready_marked and self.page_pending <= 0:
                self.ready_marked = True
                should_mark = True
        if should_mark:
            mark_ready(self.module_name, self.stream_id)

    def session_ready(self, session):
        should_mark = False
        with self.lock:
            if session.mode == SIP_OUTPUT_MODE_PAGE and self.page_pending > 0:
                self.page_pending -= 1
            if not self.ready_marked and self.page_pending <= 0:
                self.ready_marked = True
                should_mark = True
        if should_mark:
            mark_ready(self.module_name, self.stream_id)

    def session_done(self, session):
        empty = False
        should_mark = False
        with self.lock:
            # A page-mode session that finishes without ever becoming ready
            # (e.g. the page zone never answered) must release its hold so the
            # broadcast is not blocked forever.
            if session.mode == SIP_OUTPUT_MODE_PAGE and not session.ready_sent and self.page_pending > 0:
                self.page_pending -= 1
                if not self.ready_marked and self.page_pending <= 0:
                    self.ready_marked = True
                    should_mark = True
            self.sessions = [item for item in self.sessions if item is not session]
            empty = not self.sessions
        if should_mark:
            mark_ready(self.module_name, self.stream_id)
        if empty:
            self.recorder.cleanup()
            self.on_empty(self.stream_id)

    def receive_audio(self, chunk):
        self.recorder.write_audio(chunk)
        with self.lock:
            sessions = list(self.sessions)
        for session in sessions:
            session.receive_audio(chunk)

    def finish_input(self):
        self.recorder.finish_input()
        with self.lock:
            sessions = list(self.sessions)
        for session in sessions:
            session.finish_input_stream()

    def stop_all(self):
        self.recorder.finish_input()
        with self.lock:
            sessions = list(self.sessions)
        for session in sessions:
            session.stop()


class BuiltinSipTrunksRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.streams = {}

    def get_endpoint_status(self):
        return get_siptrunks_endpoint_status()

    def resolve_output_rows(self, sub_targets):
        rows = sip_fetch_output_rows()
        if any(str(target).strip().lower() == "all" for target in sub_targets):
            return rows
        wanted = {str(target).strip() for target in sub_targets if str(target).strip()}
        matched = []
        for row in rows:
            row_id = str(row.get("id") or "").strip()
            if f"number-{row_id}" not in wanted and row_id not in wanted:
                continue
            canonical = sip_fetch_output_row(row_id) if row_id.isdigit() else None
            matched.append(canonical or row)
        return matched

    def handle_dispatch(self, action, stream_id, msg_id, sub_targets, metadata=None):
        if action not in {"prepare_audio", "prepare_livepage"}:
            mark_ready("siptrunks", stream_id)
            return
        rows = self.resolve_output_rows(sub_targets)
        state = SipTrunksStreamState("siptrunks", stream_id, metadata or {}, self.remove_stream)
        with self.lock:
            self.streams[stream_id] = state
        if not rows:
            mark_failed("siptrunks", stream_id)
            return
        for row in rows:
            if not str(row.get("trunk_id") or "").strip():
                log(f"siptrunks dispatch invalid_row stream={stream_id} row_id={row.get('id')} reason=missing_trunk_id")
                mark_failed("siptrunks", stream_id)
                return
            session = SipOutputSession(
                row,
                metadata or {},
                state.recorder,
                state.session_ready,
                state.session_done,
                module_name="siptrunks",
                stream_id=stream_id,
            )
            state.add_session(session)
            session.start()
        state.mark_nonblocking_ready()

    def receive_audio(self, chunk, stream_id):
        with self.lock:
            state = self.streams.get(stream_id)
        if state is not None:
            state.receive_audio(chunk)

    def end_stream(self, stream_id):
        with self.lock:
            state = self.streams.get(stream_id)
        if state is not None:
            state.finish_input()

    def remove_stream(self, stream_id):
        with self.lock:
            self.streams.pop(stream_id, None)

    def shutdown(self):
        with self.lock:
            states = list(self.streams.values())
            self.streams.clear()
        for state in states:
            state.stop_all()


MULTICAST_RTP_FORM_SCRIPT = r"""<script>
(function() {
  function escapeAttr(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function isIpv4MulticastHost(host) {
    var parts = String(host || '').trim().split('.');
    if (parts.length !== 4) return false;
    var first = Number(parts[0]);
    return Number.isInteger(first) && first >= 224 && first <= 239;
  }
  function isMulticastHost(host) {
    host = String(host || '').trim().toLowerCase().replace(/^\[|\]$/g, '');
    if (host.indexOf(':') >= 0) return host.indexOf('ff') === 0;
    return isIpv4MulticastHost(host);
  }
  function parseRtp(url) {
    var m = /^rtp:\/\/(.+)$/i.exec(String(url || '').trim());
    if (!m) return null;
    var rest = m[1].split('/')[0];
    var host, port;
    if (rest.charAt(0) === '[') {
      var idx = rest.indexOf(']');
      host = rest.slice(1, idx);
      port = rest.slice(idx + 2);
    } else {
      var bits = rest.split(':');
      host = bits[0];
      port = bits[1];
    }
    return { host: host, port: Number(port) };
  }
  function looksLikeSource(url) {
    var u = String(url || '').trim().toLowerCase();
    return u.indexOf('http://') === 0 || u.indexOf('https://') === 0 || u.indexOf('rtp://') === 0;
  }
  function warnFor(url) {
    var rtp = parseRtp(url);
    if (!rtp || !rtp.port) return '';
    if (isMulticastHost(rtp.host)) return '';
    if (STANDBY_PORT_CONFLICTS.indexOf(rtp.port) >= 0) return 'Port ' + rtp.port + ' is already in use';
    return '';
  }
  var DRAG = '\u2630', UP = '\u25B4', DOWN = '\u25BE', TRASH = '\uD83D\uDDD1', WARN = '\u26A0';

  function setupStreamList(listId, hiddenId) {
    var listEl = document.getElementById(listId);
    var hiddenEl = document.getElementById(hiddenId);
    if (!listEl || !hiddenEl) return null;
    var items;
    try { items = JSON.parse(hiddenEl.value || '[]'); } catch (e) { items = []; }
    if (!Array.isArray(items)) items = [];
    items = items.map(function(x) { return typeof x === 'string' ? x : (x && x.url ? x.url : ''); });
    if (items.length === 0) items = [''];
    var dragIndex = null;

    function sync() {
      hiddenEl.value = JSON.stringify(items.map(function(u) { return String(u || '').trim(); }).filter(function(u) { return u !== ''; }));
    }
    function rowHtml(url, i) {
      var warn = warnFor(url);
      return '<div class="stream-row" data-idx="' + i + '">' +
        '<span class="drag-handle" draggable="true" data-idx="' + i + '" title="Drag to reorder">' + DRAG + '</span>' +
        '<input class="control stream-url" data-idx="' + i + '" value="' + escapeAttr(url) + '" placeholder="' + escapeAttr(STANDBY_PLACEHOLDER) + '">' +
        '<span class="stream-warn' + (warn ? ' show' : '') + '">' + WARN + ' <span class="warn-text">' + escapeHtml(warn) + '</span></span>' +
        '<button type="button" class="icon-btn" data-act="add" data-idx="' + i + '" title="Add stream">+</button>' +
        '<button type="button" class="icon-btn" data-act="up" data-idx="' + i + '"' + (i === 0 ? ' disabled' : '') + ' title="Move up">' + UP + '</button>' +
        '<button type="button" class="icon-btn" data-act="down" data-idx="' + i + '"' + (i === items.length - 1 ? ' disabled' : '') + ' title="Move down">' + DOWN + '</button>' +
        '<button type="button" class="icon-btn trash" data-act="remove" data-idx="' + i + '" title="Remove">' + TRASH + '</button>' +
        '</div>';
    }
    function render() {
      var html = '';
      for (var i = 0; i < items.length; i++) html += rowHtml(items[i], i);
      listEl.innerHTML = html;
      sync();
    }
    listEl.addEventListener('click', function(event) {
      var btn = event.target.closest('.icon-btn');
      if (!btn) return;
      var idx = Number(btn.getAttribute('data-idx'));
      var act = btn.getAttribute('data-act');
      if (act === 'add') {
        if (items.length >= STANDBY_MAX_SOURCES) return;
        items.splice(idx + 1, 0, '');
      } else if (act === 'remove') {
        items.splice(idx, 1);
        if (items.length === 0) items = [''];
      } else if (act === 'up' && idx > 0) {
        var a = items[idx]; items[idx] = items[idx - 1]; items[idx - 1] = a;
      } else if (act === 'down' && idx < items.length - 1) {
        var b = items[idx]; items[idx] = items[idx + 1]; items[idx + 1] = b;
      } else { return; }
      render();
    });
    listEl.addEventListener('input', function(event) {
      if (!event.target.classList.contains('stream-url')) return;
      var idx = Number(event.target.getAttribute('data-idx'));
      items[idx] = event.target.value;
      sync();
      var row = event.target.closest('.stream-row');
      var warnEl = row ? row.querySelector('.stream-warn') : null;
      if (warnEl) {
        var warn = warnFor(items[idx]);
        warnEl.classList.toggle('show', !!warn);
        var wt = warnEl.querySelector('.warn-text');
        if (wt) wt.textContent = warn;
      }
    });
    listEl.addEventListener('dragstart', function(event) {
      var handle = event.target.closest('.drag-handle');
      if (!handle) { event.preventDefault(); return; }
      dragIndex = Number(handle.getAttribute('data-idx'));
      event.dataTransfer.effectAllowed = 'move';
      try { event.dataTransfer.setData('text/plain', String(dragIndex)); } catch (e) {}
      var row = handle.closest('.stream-row');
      if (row) row.classList.add('dragging');
    });
    listEl.addEventListener('dragover', function(event) { event.preventDefault(); });
    listEl.addEventListener('drop', function(event) {
      event.preventDefault();
      var row = event.target.closest('.stream-row');
      if (row == null || dragIndex == null) { dragIndex = null; return; }
      var target = Number(row.getAttribute('data-idx'));
      if (target === dragIndex) { dragIndex = null; render(); return; }
      var moved = items.splice(dragIndex, 1)[0];
      items.splice(target, 0, moved);
      dragIndex = null;
      render();
    });
    listEl.addEventListener('dragend', function() { dragIndex = null; render(); });
    render();
    return {
      count: function() { return items.map(function(u) { return String(u || '').trim(); }).filter(function(u) { return u !== ''; }).length; },
      invalid: function() { return items.some(function(u) { var t = String(u || '').trim(); return t !== '' && !looksLikeSource(t); }); },
    };
  }

  var multicastForm = document.getElementById('multicastRtpForm');
  var multicastAddress = document.getElementById('multicastAddress');
  var multicastPort = document.getElementById('multicastPort');
  var packetMs = document.getElementById('packetMs');
  var saveMulticastRtp = document.getElementById('saveMulticastRtp');
  var multicastClientError = document.getElementById('multicastClientError');
  var standbyModeRadios = document.getElementsByName('standby_mode');
  function getStandbyMode() {
    for (var i = 0; i < standbyModeRadios.length; i++) {
      if (standbyModeRadios[i].checked) return standbyModeRadios[i].value;
    }
    return 'stop';
  }
  var standbyRebroadcast = document.getElementById('standbyRebroadcast');
  var standbyEmergency = document.getElementById('standbyEmergency');
  var standbyMsgAction = document.getElementById('standbyMsgAction');
  var standbyMsgPriority = document.getElementById('standbyMsgPriority');
  var standbyKeepText = document.getElementById('standbyKeepText');
  var standbyThresholdText = document.getElementById('standbyThresholdText');
  var standbyOrHigher = document.getElementById('standbyOrHigher');
  var mutePriority = document.getElementById('mutePriority');
  var mutePriorityOrHigher = document.getElementById('mutePriorityOrHigher');
  var multicastCodec = document.getElementById('multicastCodec');
  var codecChangeWarn = document.getElementById('codecChangeWarn');
  var codecHdInfo = document.getElementById('codecHdInfo');
  var codecHdInfoText = document.getElementById('codecHdInfoText');

  function syncCodecNotes() {
    if (!multicastCodec) return;
    var codec = String(multicastCodec.value || '').toUpperCase();
    var isHd = codec !== 'PCMU' && codec !== 'PCMA';
    if (codecHdInfoText) {
      codecHdInfoText.textContent = MC_SHOW_DOCS
        ? "Many telephony endpoints don't support HD audio for multicast streams. For more information, view documentation."
        : "Many telephony endpoints don't support HD audio for multicast streams";
    }
    if (codecHdInfo) codecHdInfo.style.display = isHd ? '' : 'none';
    var changed = codec !== String(MC_ORIGINAL_CODEC || '').toUpperCase();
    if (codecChangeWarn) codecChangeWarn.style.display = (changed && MC_STREAM_ACTIVE) ? '' : 'none';
  }
  if (multicastCodec) multicastCodec.addEventListener('change', syncCodecNotes);
  syncCodecNotes();

  var standbyList = setupStreamList('standbyStreamList', 'standbySourcesInput');
  var emergencyList = setupStreamList('emergencyStreamList', 'emergencySourcesInput');

  function isIpv4Multicast(value) {
    var parts = String(value || '').trim().split('.');
    if (parts.length !== 4) return false;
    var octets = parts.map(function(p) { return Number(p); });
    if (octets.some(function(o, i) { return !Number.isInteger(o) || o < 0 || o > 255 || String(o) !== parts[i]; })) return false;
    return octets[0] >= 224 && octets[0] <= 239;
  }
  function isIpv6Multicast(value) {
    var normalized = String(value || '').trim().toLowerCase();
    if (normalized.indexOf(':') < 0 || !/^[0-9a-f:.]+$/.test(normalized)) return false;
    var first = normalized.split(':', 1)[0];
    return first.length > 0 && first.length <= 4 && first.indexOf('ff') === 0;
  }
  function isMulticastAddress(value) { return isIpv4Multicast(value) || isIpv6Multicast(value); }
  function isValidPort(value) { var p = Number(value); return Number.isInteger(p) && p >= 2 && p <= 65534 && p % 2 === 0; }
  function isValidPacketMs(value) { var ms = Number(value); return Number.isInteger(ms) && ms >= MC_MIN_MS && ms <= MC_MAX_MS && ms % 20 === 0; }

  function syncStandbyVisibility() {
    var mode = getStandbyMode();
    if (standbyRebroadcast) standbyRebroadcast.classList.toggle('show', mode === 'rebroadcast');
    var action = standbyMsgAction ? standbyMsgAction.value : 'keep';
    if (standbyKeepText) standbyKeepText.style.display = action === 'keep' ? '' : 'none';
    if (standbyThresholdText) standbyThresholdText.style.display = action === 'keep' ? 'none' : '';
    if (standbyOrHigher) standbyOrHigher.style.display = (standbyMsgPriority && standbyMsgPriority.value === 'Emergency') ? 'none' : '';
    if (standbyEmergency) standbyEmergency.classList.toggle('show', mode === 'rebroadcast' && action === 'emergency');
    if (mutePriorityOrHigher) mutePriorityOrHigher.style.display = (mutePriority && mutePriority.value === 'Emergency') ? 'none' : '';
  }

  function syncMulticastForm() {
    var errors = [];
    if (!isMulticastAddress(multicastAddress.value)) errors.push('Enter a multicast address.');
    if (!isValidPort(multicastPort.value)) errors.push('Enter an even UDP port.');
    if (!isValidPacketMs(packetMs.value)) errors.push('Packet size must be a 20 ms increment between 20 and 200 ms.');
    var mode = getStandbyMode();
    if (mode === 'rebroadcast') {
      if (standbyList && standbyList.count() === 0) errors.push('Add at least one background audio source.');
      if (standbyList && standbyList.invalid()) errors.push('Sources must be http(s):// or rtp:// URLs.');
      if (standbyMsgAction && standbyMsgAction.value === 'emergency') {
        if (emergencyList && emergencyList.count() === 0) errors.push('Add at least one emergency stream.');
        if (emergencyList && emergencyList.invalid()) errors.push('Emergency streams must be http(s):// or rtp:// URLs.');
      }
    }
    multicastAddress.setCustomValidity(errors.some(function(e) { return e.indexOf('multicast') >= 0; }) ? 'Enter a multicast address.' : '');
    multicastPort.setCustomValidity(errors.some(function(e) { return e.indexOf('port') >= 0; }) ? 'Enter an even UDP port.' : '');
    packetMs.setCustomValidity(errors.some(function(e) { return e.indexOf('Packet size') >= 0; }) ? 'Packet size must be a 20 ms increment between 20 and 200 ms.' : '');
    multicastClientError.textContent = errors.join(' ');
    multicastClientError.style.display = errors.length ? 'block' : 'none';
    saveMulticastRtp.disabled = errors.length > 0;
    return errors.length === 0;
  }

  [multicastAddress, multicastPort, packetMs].forEach(function(input) { input.addEventListener('input', syncMulticastForm); });
  Array.prototype.forEach.call(standbyModeRadios, function(radio) {
    radio.addEventListener('change', function() { syncStandbyVisibility(); syncMulticastForm(); });
  });
  if (standbyMsgAction) standbyMsgAction.addEventListener('change', function() { syncStandbyVisibility(); syncMulticastForm(); });
  if (standbyMsgPriority) standbyMsgPriority.addEventListener('change', syncStandbyVisibility);
  if (mutePriority) mutePriority.addEventListener('change', syncStandbyVisibility);
  if (multicastForm) multicastForm.addEventListener('submit', function(event) {
    syncStandbyVisibility();
    if (!syncMulticastForm()) { event.preventDefault(); multicastForm.reportValidity(); }
  });
  document.addEventListener('input', function(event) {
    if (event.target && event.target.classList && event.target.classList.contains('stream-url')) syncMulticastForm();
  });
  document.addEventListener('click', function(event) {
    if (event.target && event.target.closest && event.target.closest('.icon-btn')) setTimeout(syncMulticastForm, 0);
  });
  syncStandbyVisibility();
  syncMulticastForm();
})();
</script>"""


MULTICAST_RTP_FORM_CSS = """<style>
.standby-block{display:none;grid-template-columns:1fr;gap:10px;border:1px solid #e6e8eb;border-radius:6px;padding:12px;margin-top:4px}
.standby-block.show{display:grid}
.stream-list{display:grid;gap:8px}
.stream-row{display:flex;align-items:center;gap:6px}
.stream-row .drag-handle{cursor:grab;color:#9aa0a6;font-size:15px;line-height:1;user-select:none;padding:0 2px}
.stream-row.dragging{opacity:.5}
.stream-url{flex:1 1 auto;min-width:0}
.icon-btn{background:transparent;border:0;padding:2px 4px;margin:0;color:#5f6368;cursor:pointer;font-size:14px;line-height:1;border-radius:0;box-shadow:none}
.icon-btn:hover{color:#1976D2}
.icon-btn.trash{color:#C62828}
.icon-btn.trash:hover{color:#B71C1C}
.icon-btn:disabled{opacity:.35;cursor:default}
.stream-warn{display:none;align-items:center;gap:4px;color:#B7791F;font-size:.82em;white-space:nowrap}
.stream-warn.show{display:inline-flex}
.stream-priority-line{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:6px;line-height:1.9}
.stream-priority-line select{width:auto;min-width:0;display:inline-block}
.amp-grid{display:grid;gap:8px;margin-top:6px}
.amp-row{display:flex;align-items:center;justify-content:space-between;gap:12px}
.amp-row .amp-label{flex:1 1 auto;min-width:0}
.amp-row select{width:auto;min-width:120px;flex:0 0 auto}
.amp-mute-line{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:2px;line-height:1.9}
.amp-mute-line select{width:auto;min-width:0;display:inline-block}
.amp-check{display:inline-flex;align-items:center;gap:6px}
.amp-check input{width:auto;margin:0}
.stream-hint{color:#5f6368;font-size:.84em;margin:2px 0 0}
.standby-mode-control{max-width:360px}
.mdc-radio-group{display:grid;gap:2px}
.mdc-radio-row{display:flex;align-items:flex-start;gap:10px;padding:8px;border-radius:6px;cursor:pointer;position:relative}
.mdc-radio-row:hover{background:rgba(25,118,210,.06)}
.mdc-radio-input{position:absolute;opacity:0;width:0;height:0;margin:0}
.mdc-radio-circle{flex:0 0 auto;position:relative;width:20px;height:20px;margin-top:1px;border:2px solid #5f6368;border-radius:50%;box-sizing:border-box;transition:border-color .15s ease}
.mdc-radio-circle::after{content:"";position:absolute;top:50%;left:50%;width:10px;height:10px;border-radius:50%;background:#1976D2;transform:translate(-50%,-50%) scale(0);transition:transform .15s ease}
.mdc-radio-input:checked + .mdc-radio-circle{border-color:#1976D2}
.mdc-radio-input:checked + .mdc-radio-circle::after{transform:translate(-50%,-50%) scale(1)}
.mdc-radio-input:focus-visible + .mdc-radio-circle{box-shadow:0 0 0 4px rgba(25,118,210,.2)}
.mdc-radio-text{display:flex;flex-direction:column;line-height:1.35}
.mdc-radio-title{font-size:.95em}
.mdc-radio-desc{color:#5f6368;font-size:.82em}
.codec-note{display:flex;align-items:flex-start;gap:6px;font-size:.84em;margin:4px 0 0;max-width:360px}
.codec-note .codec-note-icon{flex:0 0 auto;line-height:1.4}
.codec-note.warn{color:#B7791F}
.codec-note.info{color:#1976D2}
@media(prefers-color-scheme:dark){.standby-block{border-color:#333}.icon-btn{color:#bbb}.icon-btn.trash{color:#EF9A9A}.stream-warn{color:#F6C244}.stream-hint{color:#aaa}.codec-note.warn{color:#F6C244}.codec-note.info{color:#64B5F6}.mdc-radio-row:hover{background:rgba(100,181,246,.12)}.mdc-radio-circle{border-color:#aaa}.mdc-radio-circle::after{background:#64B5F6}.mdc-radio-input:checked + .mdc-radio-circle{border-color:#64B5F6}.mdc-radio-desc{color:#aaa}}
</style>"""


def multicast_rtp_show_online_docs():
    """Read the server's show_online_docs setting (defaults to enabled)."""
    try:
        conn = get_db_connection()
    except Exception:
        return True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM systemsettings WHERE parameter=%s LIMIT 1", ("show_online_docs",))
            row = cur.fetchone()
        if not row:
            return True
        value = row.get("value") if isinstance(row, dict) else row[0]
        return str(value if value is not None else "1") == "1"
    except Exception:
        return True
    finally:
        conn.close()


def multicast_rtp_endpoint_streaming(row_id):
    """True when the resident channel for this endpoint is currently emitting
    audio (an active broadcast, or rebroadcast background music)."""
    with loaded_modules_lock:
        mod = loaded_modules.get(MULTICAST_RTP_MODULE)
    if mod is None or not hasattr(mod, "endpoint_streaming"):
        return False
    try:
        return bool(mod.endpoint_streaming(row_id))
    except Exception:
        return False


def multicast_rtp_form_html(values, error, submit_label, stream_active=False):
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    show_docs = multicast_rtp_show_online_docs()
    selected_codec = str(values.get("codec") or "PCMU").strip().upper()
    codec_options = "".join(
        f'<option value="{h(codec)}"{" selected" if selected_codec == codec else ""}>{h(codec)}</option>'
        for codec in ("PCMU", "PCMA", "G722", "G7221C", "G726-32", "OPUS")
    )
    standby_mode = str(values.get("standby_mode") or "stop").strip().lower()
    if standby_mode not in MULTICAST_RTP_STANDBY_MODES:
        standby_mode = "stop"
    standby_mode_radios = "".join(
        '<label class="mdc-radio-row">'
        f'<input type="radio" class="mdc-radio-input" name="standby_mode" value="{h(value)}"'
        f' id="standbyMode_{h(value)}"{" checked" if standby_mode == value else ""}>'
        '<span class="mdc-radio-circle"></span>'
        '<span class="mdc-radio-text">'
        f'<span class="mdc-radio-title">{h(title)}</span>'
        f'<span class="mdc-radio-desc">{h(desc)}</span>'
        '</span>'
        '</label>'
        for value, title, desc in (
            ("stop", "Stop stream", "Recommended for phones"),
            ("rebroadcast", "Rebroadcast external source", "Play background audio while idle"),
            ("silence", "Send silent frames", "Useful for debugging"),
        )
    )
    msg_action = str(values.get("standby_msg_action") or "stop").strip().lower()
    if msg_action not in MULTICAST_RTP_STANDBY_MSG_ACTIONS:
        msg_action = "stop"
    msg_action_options = "".join(
        f'<option value="{h(value)}"{" selected" if msg_action == value else ""}>{h(label)}</option>'
        for value, label in (
            ("keep", "Keep rebroadcasting audio"),
            ("stop", "Stop audio"),
            ("silence", "Send silence frames"),
            ("emergency", "Switch to emergency streams"),
        )
    )
    msg_priority = str(values.get("standby_msg_priority") or "Emergency").strip().title()
    if msg_priority not in VALID_MESSAGE_PRIORITIES:
        msg_priority = "Emergency"
    priority_options = "".join(
        f'<option value="{h(name)}"{" selected" if msg_priority == name else ""}>{h(name)}</option>'
        for name in ("Low", "Normal", "High", "Emergency")
    )
    amp_master_options = multicast_rtp_gain_options(multicast_rtp_clean_gain(values.get("amp_master"), MULTICAST_RTP_AMP_DEFAULTS["amp_master"]))
    amp_page_options = multicast_rtp_gain_options(multicast_rtp_clean_gain(values.get("amp_page"), MULTICAST_RTP_AMP_DEFAULTS["amp_page"]))
    amp_bell_options = multicast_rtp_gain_options(multicast_rtp_clean_gain(values.get("amp_bell"), MULTICAST_RTP_AMP_DEFAULTS["amp_bell"]))
    amp_message_options = multicast_rtp_gain_options(multicast_rtp_clean_gain(values.get("amp_message"), MULTICAST_RTP_AMP_DEFAULTS["amp_message"]))
    mute_priority = str(values.get("mute_priority") or "High").strip().title()
    if mute_priority not in VALID_MESSAGE_PRIORITIES:
        mute_priority = "High"
    mute_priority_options = "".join(
        f'<option value="{h(name)}"{" selected" if mute_priority == name else ""}>{h(name)}</option>'
        for name in ("Low", "Normal", "High", "Emergency")
    )
    mute_priority_checked = " checked" if str(values.get("mute_priority_enabled") or "").strip() in ("1", "on", "true", "yes") else ""
    mute_or_higher_style = ' style="display:none"' if mute_priority == "Emergency" else ""
    try:
        standby_sources_list = json.loads(values.get("standby_sources") or "[]")
        if not isinstance(standby_sources_list, list):
            standby_sources_list = []
    except (ValueError, TypeError):
        standby_sources_list = []
    try:
        emergency_sources_list = json.loads(values.get("emergency_sources") or "[]")
        if not isinstance(emergency_sources_list, list):
            emergency_sources_list = []
    except (ValueError, TypeError):
        emergency_sources_list = []
    conflicts = multicast_rtp_standby_port_conflicts(standby_sources_list + emergency_sources_list)
    standby_sources_attr = h(json.dumps(standby_sources_list))
    emergency_sources_attr = h(json.dumps(emergency_sources_list))
    rebroadcast_show = " show" if standby_mode == "rebroadcast" else ""
    emergency_show = " show" if (standby_mode == "rebroadcast" and msg_action == "emergency") else ""
    js_config = (
        "<script>\n"
        f"const STANDBY_PLACEHOLDER = {json.dumps(MULTICAST_RTP_STANDBY_SOURCE_PLACEHOLDER)};\n"
        f"const STANDBY_MAX_SOURCES = {MULTICAST_RTP_MAX_STANDBY_SOURCES};\n"
        f"const STANDBY_PORT_CONFLICTS = {json.dumps(conflicts)};\n"
        f"const MC_MIN_MS = {MULTICAST_RTP_MIN_PACKET_MS};\n"
        f"const MC_MAX_MS = {MULTICAST_RTP_MAX_PACKET_MS};\n"
        f"const MC_ORIGINAL_CODEC = {json.dumps(str(values.get('codec') or 'PCMU').strip().upper())};\n"
        f"const MC_STREAM_ACTIVE = {json.dumps(bool(stream_active))};\n"
        f"const MC_SHOW_DOCS = {json.dumps(bool(show_docs))};\n"
        "</script>"
    )
    body = f"""{MULTICAST_RTP_FORM_CSS}{error_html}<form method="post" class="grid form-surface" id="multicastRtpForm">
<div class="notice">{h(MULTICAST_RTP_WARNING)}</div>
<div class="row"><label>Name</label><input class="control" name="name" value="{h(values.get("name"))}" required></div>
<div class="row"><label>Multicast Address</label><input class="control" name="address" id="multicastAddress" value="{h(values.get("address"))}" required></div>
<div class="row"><label>Port</label><input class="control short-control" type="number" name="port" id="multicastPort" value="{h(values.get("port"))}" min="2" max="65534" step="2" required></div>
<div class="row"><label>Codec</label><select class="control short-control" name="codec" id="multicastCodec">{codec_options}</select></div>
<div class="codec-note warn" id="codecChangeWarn" style="display:none"><span class="codec-note-icon">&#9888;</span><span>Changing codec will cause audio to stop momentarily</span></div>
<div class="codec-note info" id="codecHdInfo" style="display:none"><span class="codec-note-icon">&#9432;</span><span id="codecHdInfoText"></span></div>
<div class="row"><label>On standby</label><div class="mdc-radio-group standby-mode-control" id="standbyMode">{standby_mode_radios}</div></div>
<div class="standby-block{rebroadcast_show}" id="standbyRebroadcast">
  <label>Background audio source</label>
  <input type="hidden" name="standby_sources" id="standbySourcesInput" value="{standby_sources_attr}">
  <div class="stream-list" id="standbyStreamList"></div>
  <div class="stream-priority-line">
    <select class="control short-control" name="standby_msg_action" id="standbyMsgAction">{msg_action_options}</select>
    <span id="standbyKeepText">during standby when a message is in effect</span>
    <span id="standbyThresholdText">when a message of <select class="control short-control" name="standby_msg_priority" id="standbyMsgPriority">{priority_options}</select> priority <span id="standbyOrHigher">or higher </span>is in effect while in standby</span>
  </div>
  <div class="standby-block{emergency_show}" id="standbyEmergency">
    <label>Emergency streams</label>
    <input type="hidden" name="emergency_sources" id="emergencySourcesInput" value="{emergency_sources_attr}">
    <div class="stream-list" id="emergencyStreamList"></div>
  </div>
  <div class="amp-grid">
    <div class="amp-row"><span class="amp-label">Amplify audio</span><select class="control short-control" name="amp_master">{amp_master_options}</select></div>
    <div class="amp-row"><span class="amp-label">Amplify audio on page</span><select class="control short-control" name="amp_page">{amp_page_options}</select></div>
    <div class="amp-row"><span class="amp-label">Amplify audio on bell</span><select class="control short-control" name="amp_bell">{amp_bell_options}</select></div>
    <div class="amp-row"><span class="amp-label">Amplify audio on message</span><select class="control short-control" name="amp_message">{amp_message_options}</select></div>
  </div>
  <div class="amp-mute-line">
    <label class="amp-check"><input type="checkbox" name="mute_priority_enabled" id="mutePriorityEnabled" value="1"{mute_priority_checked}> Mute audio during broadcast of a message</label>
    <select class="control short-control" name="mute_priority" id="mutePriority">{mute_priority_options}</select>
    <span>priority <span id="mutePriorityOrHigher"{mute_or_higher_style}>or higher </span></span>
  </div>
</div>
<details class="advanced"><summary>Advanced options</summary><div class="advanced-body"><div class="row"><label>Packet Size (ms)</label><input class="control short-control" type="number" name="packet_ms" id="packetMs" value="{h(values.get("packet_ms") or MULTICAST_RTP_DEFAULT_PACKET_MS)}" min="{MULTICAST_RTP_MIN_PACKET_MS}" max="{MULTICAST_RTP_MAX_PACKET_MS}" step="20" required></div></div></details>
<div class="error" id="multicastClientError" style="display:none"></div>
<button class="button" id="saveMulticastRtp" type="submit">{h(submit_label)}</button>
</form>"""
    return body + js_config + MULTICAST_RTP_FORM_SCRIPT


class BuiltinMulticastRTPWeb:
    def forms(self):
        return {
            "stream": {"label": MULTICAST_RTP_NAME, "description": MULTICAST_RTP_DESCRIPTION},
        }

    def render_form(self, form_type, request, conn_factory, page, user):
        ensure_multicast_rtp_schema()
        if form_type not in self.forms():
            return page("Endpoint Form", "<h1>Endpoint form not found</h1>", "endpoints", user, status=404)
        error = ""
        values = multicast_rtp_form_values(request.form if request.method == "POST" else None)
        if request.method == "POST":
            try:
                clean = multicast_rtp_clean_values(values)
                duplicate = sip_query_all(
                    f"SELECT id FROM `{MULTICAST_RTP_TABLE}` WHERE address=%s AND port=%s LIMIT 1",
                    (clean["address"], clean["port"]),
                )
                if duplicate:
                    raise ValueError("That multicast address and port already exists.")
                sip_execute(
                    f"INSERT INTO `{MULTICAST_RTP_TABLE}` (`name`, `address`, `port`, `codec`, `packet_ms`, `standby_mode`, `standby_sources`, `standby_msg_action`, `standby_msg_priority`, `emergency_sources`, `amp_master`, `amp_page`, `amp_bell`, `amp_message`, `mute_priority_enabled`, `mute_priority`) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (clean["name"], clean["address"], clean["port"], clean["codec"], clean["packet_ms"], clean["standby_mode"], clean["standby_sources"], clean["standby_msg_action"], clean["standby_msg_priority"], clean["emergency_sources"], clean["amp_master"], clean["amp_page"], clean["amp_bell"], clean["amp_message"], clean["mute_priority_enabled"], clean["mute_priority"]),
                )
                multicast_rtp_notify_config_changed()
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            except ValueError as exc:
                error = str(exc)
        body = multicast_rtp_form_html(values, error, "Add Multicast RTP")
        return page(MULTICAST_RTP_NAME, sip_form_frame(body), "endpoints", user)

    def render_action(self, action, endpoint_id, request, conn_factory, page, user):
        ensure_multicast_rtp_schema()
        kind, _, row_id = str(endpoint_id or "").partition("-")
        if action not in {"edit", "delete"} or kind != "stream" or not row_id.isdigit():
            return page("Endpoint Action", "<h1>Invalid endpoint action</h1>", "endpoints", user, status=400)
        row = multicast_rtp_row(row_id)
        if not row:
            return page("Endpoint Action", "<h1>Endpoint not found</h1>", "endpoints", user, status=404)
        error = ""
        if request.method == "POST":
            if action == "delete":
                sip_execute(f"DELETE FROM `{MULTICAST_RTP_TABLE}` WHERE id=%s", (row_id,))
                multicast_rtp_notify_config_changed()
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            values = multicast_rtp_form_values(request.form, row)
            try:
                clean = multicast_rtp_clean_values(values)
                duplicate = sip_query_all(
                    f"SELECT id FROM `{MULTICAST_RTP_TABLE}` WHERE address=%s AND port=%s AND id<>%s LIMIT 1",
                    (clean["address"], clean["port"], row_id),
                )
                if duplicate:
                    raise ValueError("That multicast address and port already exists.")
                sip_execute(
                    f"UPDATE `{MULTICAST_RTP_TABLE}` SET `name`=%s, `address`=%s, `port`=%s, `codec`=%s, `packet_ms`=%s, `standby_mode`=%s, `standby_sources`=%s, `standby_msg_action`=%s, `standby_msg_priority`=%s, `emergency_sources`=%s, `amp_master`=%s, `amp_page`=%s, `amp_bell`=%s, `amp_message`=%s, `mute_priority_enabled`=%s, `mute_priority`=%s WHERE id=%s",
                    (clean["name"], clean["address"], clean["port"], clean["codec"], clean["packet_ms"], clean["standby_mode"], clean["standby_sources"], clean["standby_msg_action"], clean["standby_msg_priority"], clean["emergency_sources"], clean["amp_master"], clean["amp_page"], clean["amp_bell"], clean["amp_message"], clean["mute_priority_enabled"], clean["mute_priority"], row_id),
                )
                multicast_rtp_notify_config_changed()
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            except ValueError as exc:
                error = str(exc)
                row.update(values)
        if action == "delete":
            error_html = f'<div class="error">{h(error)}</div>' if error else ""
            body = f"""{error_html}<form method="post" class="grid surface">
<p class="meta">Delete {h(row.get("name") or endpoint_id)}?</p>
<button class="danger" type="submit">Delete Endpoint</button></form>"""
        else:
            body = multicast_rtp_form_html(multicast_rtp_form_values(None, row), error, "Save Multicast RTP", stream_active=multicast_rtp_endpoint_streaming(row_id))
        return page("Endpoint Action", sip_form_frame(body), "endpoints", user)

    def render_settings(self, request, conn_factory, page, user):
        return page(MULTICAST_RTP_NAME, "<p>No additional settings are required for Multicast RTP.</p>", "endpoints", user)


class MulticastRTPSender:
    def __init__(self, row):
        self.row_id = str(row.get("id") or "")
        self.address = multicast_rtp_normalize_address(row.get("address"))
        self.port = multicast_rtp_clean_port(row.get("port"))
        self.codec = multicast_rtp_clean_codec(row.get("codec") or "PCMU")
        self.packet_ms = multicast_rtp_clean_packet_ms(row.get("packet_ms") or MULTICAST_RTP_DEFAULT_PACKET_MS)
        self.payload_type = MULTICAST_RTP_CODECS[self.codec]
        self.frames_per_packet = max(1, self.packet_ms // MULTICAST_RTP_FRAME_MS)
        self.pending_payload = bytearray()
        self.pending_frames = 0
        self.codec_encoder = None
        self.sequence = random.randrange(0, 65536)
        self.timestamp = random.randrange(0, 4294967296)
        self.ssrc = random.randrange(0, 4294967296)
        ip = ipaddress.ip_address(self.address)
        self.family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        self.sock = socket.socket(self.family, socket.SOCK_DGRAM)
        if self.family == socket.AF_INET:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            interface = default_ipv4_multicast_interface()
            if interface:
                try:
                    self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
                except OSError:
                    pass
            self.destination = (self.address, self.port)
        else:
            self.sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 1)
            self.destination = (self.address, self.port, 0, 0)

    def encode_frame(self, payload):
        payload = bytes(payload or b"")[:MULTICAST_RTP_FRAME_SIZE].ljust(MULTICAST_RTP_FRAME_SIZE, b"\xff")
        encoded, self.codec_encoder = encode_edge_rtp_payload(
            self.codec,
            payload,
            encoder_state=self.codec_encoder,
        )
        return encoded

    def send_frame(self, payload):
        frame = self.encode_frame(payload)
        if not frame:
            # Encoder is still priming (first G722/Opus frame); don't emit an
            # empty RTP packet or advance the frame count.
            return
        self.pending_payload.extend(frame)
        self.pending_frames += 1
        if self.pending_frames >= self.frames_per_packet:
            self.flush()

    def flush(self):
        if self.pending_frames <= 0:
            return
        header = struct.pack("!BBHII", 0x80, self.payload_type, self.sequence, self.timestamp, self.ssrc)
        self.sock.sendto(header + bytes(self.pending_payload), self.destination)
        self.sequence = (self.sequence + 1) & 0xFFFF
        codec_info = SIP_EDGE_CODEC_PAYLOADS.get(self.codec, {})
        # Multicast listeners have no SDP, so they expect the RTP timestamp to
        # advance at the codec's true sample rate (e.g. 320/frame for G722's
        # 16 kHz), not the RFC 3551 8 kHz signalling-only clock.
        samples_per_frame = int(codec_info.get("sample_rate_wire", 0) or 0) // 50 or int(codec_info.get("samples_per_frame", MULTICAST_RTP_FRAME_SIZE))
        self.timestamp = (self.timestamp + (self.pending_frames * samples_per_frame)) & 0xFFFFFFFF
        self.pending_payload.clear()
        self.pending_frames = 0

    def close(self):
        try:
            self.flush()
        finally:
            self.sock.close()


class MulticastRTPSource:
    def __init__(self, priority="Normal", klass="message", instant_ready=False):
        self.lock = threading.Lock()
        self.partial_frame = bytearray()
        self.closed = False
        self.preroll_frames = 0
        self.ready_sent = False
        self.priority = priority if priority in VALID_MESSAGE_PRIORITIES else "Normal"
        self.klass = klass if klass in ("message", "page", "bell") else "message"
        # When the multicast group is already primed (a resident standby channel
        # is broadcasting), skip the preroll wait so message audio interrupts the
        # background instantly instead of buffering behind the ready gate.
        self.instant_ready = bool(instant_ready)
        self.had_audio = False

    def receive_audio(self, chunk):
        if not chunk:
            return
        with self.lock:
            if self.closed:
                return
            self.partial_frame.extend(chunk)

    def next_frame(self, discard=False):
        with self.lock:
            if len(self.partial_frame) >= MULTICAST_RTP_FRAME_SIZE:
                frame = bytes(self.partial_frame[:MULTICAST_RTP_FRAME_SIZE])
                del self.partial_frame[:MULTICAST_RTP_FRAME_SIZE]
                self.had_audio = True
                if discard:
                    return None, False
                return frame, False
            if self.closed and self.partial_frame:
                frame = bytes(self.partial_frame).ljust(MULTICAST_RTP_FRAME_SIZE, b"\xff")
                self.partial_frame.clear()
                self.had_audio = True
                if discard:
                    return None, True
                return frame, True
            return None, self.closed and not self.partial_frame

    def is_emergency(self):
        return self.priority == "Emergency"

    def note_preroll_frame(self):
        with self.lock:
            if self.ready_sent:
                return False
            if self.instant_ready:
                self.ready_sent = True
                return True
            self.preroll_frames += 1
            if self.preroll_frames >= MULTICAST_RTP_READY_SILENCE_FRAMES:
                self.ready_sent = True
                return True
            return False

    def close(self):
        with self.lock:
            self.closed = True


# --- Standby (background audio) subsystem -----------------------------------
# These provide a continuous background stream for a multicast endpoint while no
# message is in effect (the "On standby" behaviour). Audio is normalised to
# 8 kHz mono u-law 160-byte frames so it can be mixed with message audio and
# encoded by the endpoint's configured codec.
STANDBY_BUFFER_FRAMES = 50            # ~1s of jitter buffer per source
STANDBY_HEALTHY_WINDOW = 2.0          # seconds since last frame to be "healthy"
STANDBY_RECONNECT_BACKOFF = 1.0       # seconds between reconnect attempts
MULTICAST_RTP_PT_TO_CODEC = {payload: codec for codec, payload in MULTICAST_RTP_CODECS.items()}


class StandbyReaderBase:
    def __init__(self, url):
        self.url = url
        self.frames = deque(maxlen=STANDBY_BUFFER_FRAMES)
        self.lock = threading.Lock()
        self.last_frame_ts = 0.0
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._safe_run, daemon=True)
            self.thread.start()

    def _safe_run(self):
        try:
            self._run()
        except Exception as exc:
            log(f"standby source error url={self.url}: {exc}")

    def _run(self):
        raise NotImplementedError

    def _push_ulaw(self, buffer):
        """Split a growing bytearray into 160-byte frames, pushing complete
        ones. Returns the buffer trimmed to the trailing partial frame."""
        while len(buffer) >= MULTICAST_RTP_FRAME_SIZE:
            frame = bytes(buffer[:MULTICAST_RTP_FRAME_SIZE])
            del buffer[:MULTICAST_RTP_FRAME_SIZE]
            with self.lock:
                self.frames.append(frame)
                self.last_frame_ts = time.monotonic()
        return buffer

    def read_frame(self):
        with self.lock:
            if self.frames:
                return self.frames.popleft()
        return None

    def healthy(self):
        with self.lock:
            return (time.monotonic() - self.last_frame_ts) < STANDBY_HEALTHY_WINDOW

    def stop(self):
        self.stop_event.set()


class StandbyHttpReader(StandbyReaderBase):
    def _run(self):
        while not self.stop_event.is_set():
            proc = None
            try:
                proc = subprocess.Popen(
                    [
                        "ffmpeg", "-v", "quiet", "-nostdin",
                        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                        "-i", self.url,
                        "-ar", "8000", "-ac", "1", "-f", "mulaw", "-flush_packets", "1", "pipe:1",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                log("standby source requires ffmpeg but it was not found")
                return
            except OSError as exc:
                log(f"standby http source spawn error url={self.url}: {exc}")
                proc = None
            if proc is not None:
                buffer = bytearray()
                try:
                    while not self.stop_event.is_set():
                        chunk = proc.stdout.read(MULTICAST_RTP_FRAME_SIZE)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        buffer = self._push_ulaw(buffer)
                finally:
                    try:
                        proc.stdout.close()
                    except Exception:
                        pass
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            if self.stop_event.wait(STANDBY_RECONNECT_BACKOFF):
                break


class StandbyRtpReader(StandbyReaderBase):
    def __init__(self, url, host, port, multicast):
        super().__init__(url)
        self.host = host
        self.port = port
        self.multicast = multicast

    def _open_socket(self):
        try:
            ip = ipaddress.ip_address(self.host)
            family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        except ValueError:
            family = socket.AF_INET
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_addr = "" if family == socket.AF_INET else "::"
        sock.bind((bind_addr, self.port))
        if self.multicast:
            if family == socket.AF_INET:
                mreq = struct.pack("4s4s", socket.inet_aton(self.host), socket.inet_aton("0.0.0.0"))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            else:
                group = socket.inet_pton(socket.AF_INET6, self.host)
                mreq = group + struct.pack("@I", 0)
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq)
        sock.settimeout(1.0)
        return sock

    def _expected_ips(self):
        if self.multicast:
            return None
        try:
            infos = socket.getaddrinfo(self.host, None, proto=socket.IPPROTO_UDP)
            return {info[4][0] for info in infos}
        except OSError:
            return None

    def _run(self):
        while not self.stop_event.is_set():
            sock = None
            try:
                sock = self._open_socket()
            except OSError as exc:
                log(f"standby rtp bind error url={self.url}: {exc}")
                if self.stop_event.wait(STANDBY_RECONNECT_BACKOFF):
                    break
                continue
            expected = self._expected_ips()
            decoder_state = None
            buffer = bytearray()
            try:
                while not self.stop_event.is_set():
                    try:
                        packet, addr = sock.recvfrom(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if expected is not None and addr[0] not in expected:
                        continue
                    if len(packet) < 12:
                        continue
                    payload_type = packet[1] & 0x7F
                    csrc_count = packet[0] & 0x0F
                    header_len = 12 + (csrc_count * 4)
                    payload = packet[header_len:]
                    if not payload:
                        continue
                    codec = MULTICAST_RTP_PT_TO_CODEC.get(payload_type, "PCMU")
                    try:
                        ulaw, decoder_state = decode_edge_rtp_payload(codec, payload, decoder_state)
                    except Exception:
                        ulaw = b""
                    if ulaw:
                        buffer.extend(ulaw)
                        buffer = self._push_ulaw(buffer)
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
            if self.stop_event.wait(STANDBY_RECONNECT_BACKOFF):
                break


def make_standby_reader(url):
    parsed = multicast_rtp_parse_source(url)
    if parsed["kind"] == "http":
        return StandbyHttpReader(url)
    return StandbyRtpReader(url, parsed["host"], parsed["port"], parsed["kind"] == "multicast")


def standby_lenient_sources(value):
    urls = []
    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        text = str(value or "").strip()
        raw = []
        if text:
            try:
                parsed = json.loads(text)
                raw = parsed if isinstance(parsed, list) else [text]
            except (ValueError, TypeError):
                raw = text.splitlines()
    for item in raw:
        if isinstance(item, dict):
            item = item.get("url")
        candidate = str(item or "").strip()
        if not candidate:
            continue
        try:
            multicast_rtp_parse_source(candidate)
        except ValueError:
            continue
        urls.append(candidate)
        if len(urls) >= MULTICAST_RTP_MAX_STANDBY_SOURCES:
            break
    return urls


def standby_params(row):
    mode = str(row.get("standby_mode") or "stop").strip().lower()
    if mode not in ("rebroadcast", "silence"):
        return None
    action = str(row.get("standby_msg_action") or "stop").strip().lower()
    if action not in MULTICAST_RTP_STANDBY_MSG_ACTIONS:
        action = "stop"
    priority = str(row.get("standby_msg_priority") or "Emergency").strip().title()
    if priority not in VALID_MESSAGE_PRIORITIES:
        priority = "Emergency"
    sources = standby_lenient_sources(row.get("standby_sources")) if mode == "rebroadcast" else []
    emergency = (
        standby_lenient_sources(row.get("emergency_sources"))
        if (mode == "rebroadcast" and action == "emergency")
        else []
    )
    mute_priority = str(row.get("mute_priority") or "High").strip().title()
    if mute_priority not in VALID_MESSAGE_PRIORITIES:
        mute_priority = "High"
    return {
        "mode": mode,
        "action": action,
        "priority": priority,
        "sources": sources,
        "emergency": emergency,
        "amp": {
            "master": multicast_rtp_clean_gain(row.get("amp_master"), MULTICAST_RTP_AMP_DEFAULTS["amp_master"]),
            "page": multicast_rtp_clean_gain(row.get("amp_page"), MULTICAST_RTP_AMP_DEFAULTS["amp_page"]),
            "bell": multicast_rtp_clean_gain(row.get("amp_bell"), MULTICAST_RTP_AMP_DEFAULTS["amp_bell"]),
            "message": multicast_rtp_clean_gain(row.get("amp_message"), MULTICAST_RTP_AMP_DEFAULTS["amp_message"]),
        },
        "mute_priority_enabled": str(row.get("mute_priority_enabled") or "0").strip() in ("1", "on", "true", "yes", "True"),
        "mute_priority": mute_priority,
    }


def standby_stream_key(row):
    """Structural signature that determines whether the underlying standby
    audio readers must be rebuilt. Only the mode and the actual source chains
    matter here — volume/priority/action tweaks are applied live without
    restarting the streams. The emergency chain only exists when the action is
    'emergency', so it is part of the structure."""
    params = standby_params(row)
    if params is None:
        return None
    emergency = tuple(params["emergency"]) if (params["action"] == "emergency" and params["emergency"]) else ()
    return (params["mode"], tuple(params["sources"]), emergency)


def standby_config_key(row):
    params = standby_params(row)
    if params is None:
        return None
    amp = params["amp"]
    return (
        params["mode"],
        params["action"],
        params["priority"],
        tuple(params["sources"]),
        tuple(params["emergency"]),
        amp["master"], amp["page"], amp["bell"], amp["message"],
        params["mute_priority_enabled"], params["mute_priority"],
    )


class StandbySourceChain:
    def __init__(self, urls):
        self.readers = []
        for url in urls:
            try:
                self.readers.append(make_standby_reader(url))
            except ValueError:
                continue

    def start(self):
        for reader in self.readers:
            reader.start()

    def next_frame(self):
        for reader in self.readers:
            if reader.healthy():
                frame = reader.read_frame()
                return frame if frame is not None else MULTICAST_RTP_SILENCE_FRAME
        return MULTICAST_RTP_SILENCE_FRAME

    def stop(self):
        for reader in self.readers:
            reader.stop()


class StandbyEngine:
    def __init__(self, row):
        params = standby_params(row) or {}
        self.mode = params.get("mode", "silence")
        self.msg_action = params.get("action", "keep")
        self.msg_priority = params.get("priority", "High")
        self.config_key = standby_config_key(row)
        self.stream_key = standby_stream_key(row)
        self.amp = params.get("amp") or dict(MULTICAST_RTP_AMP_DEFAULTS)
        self.amp = {
            "master": self.amp.get("master", "0"),
            "page": self.amp.get("page", "-10"),
            "bell": self.amp.get("bell", "0"),
            "message": self.amp.get("message", "mute"),
        }
        self.mute_priority_enabled = bool(params.get("mute_priority_enabled"))
        self.mute_priority = params.get("mute_priority", "High")
        self.normal = StandbySourceChain(params.get("sources", [])) if self.mode == "rebroadcast" else None
        self.emergency = (
            StandbySourceChain(params.get("emergency", []))
            if (self.mode == "rebroadcast" and self.msg_action == "emergency" and params.get("emergency"))
            else None
        )

    def amp_for_class(self, klass):
        """Return the gain spec ('mute' or int-string) that applies to the
        background while a broadcast of ``klass`` (page/bell/message) is active.
        Falls back to the master gain for unknown classes."""
        return self.amp.get(klass, self.amp.get("master", "0"))

    def apply_live(self, row):
        """Update the parameters that can change without rebuilding the audio
        readers (volumes, message action/priority, mute-priority). Attributes
        are reassigned atomically so the broadcasting thread picks up the new
        values on its next frame with no stream restart."""
        params = standby_params(row) or {}
        amp = params.get("amp") or dict(MULTICAST_RTP_AMP_DEFAULTS)
        self.amp = {
            "master": amp.get("master", "0"),
            "page": amp.get("page", "-10"),
            "bell": amp.get("bell", "0"),
            "message": amp.get("message", "mute"),
        }
        self.msg_action = params.get("action", "keep")
        self.msg_priority = params.get("priority", "High")
        self.mute_priority_enabled = bool(params.get("mute_priority_enabled"))
        self.mute_priority = params.get("mute_priority", "High")
        self.config_key = standby_config_key(row)

    def start(self):
        if self.normal is not None:
            self.normal.start()
        if self.emergency is not None:
            self.emergency.start()

    def next_frame(self, context="normal"):
        if self.mode != "rebroadcast":
            return MULTICAST_RTP_SILENCE_FRAME
        chain = self.emergency if (context == "emergency" and self.emergency is not None) else self.normal
        if chain is None:
            return MULTICAST_RTP_SILENCE_FRAME
        return chain.next_frame()

    def stop(self):
        if self.normal is not None:
            self.normal.stop()
        if self.emergency is not None:
            self.emergency.stop()


def build_standby_engine(row):
    if standby_config_key(row) is None:
        return None
    engine = StandbyEngine(row)
    engine.start()
    return engine


class MulticastRTPEndpointChannel:
    def __init__(self, row, on_idle):
        self.key = str(row.get("id") or "")
        self.lock = threading.Lock()
        self.sender = MulticastRTPSender(row)
        self.sources = {}
        self.active_broadcasts = {}
        self.active_check_at = 0.0
        self.stop_event = threading.Event()
        self.idle_since = None
        self.closing = False
        self.on_idle = on_idle
        self.standby = build_standby_engine(row)
        self.persistent = self.standby is not None
        self.codec_restart_until = 0.0
        self.pending_sender_row = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def is_streaming_audio(self):
        """True when the channel is actively emitting audio right now: either a
        broadcast source is attached, or the standby engine is rebroadcasting
        background music."""
        with self.lock:
            if self.sources:
                return True
            standby = self.standby
        return standby is not None and standby.mode == "rebroadcast"

    def update_config(self, row):
        """Apply configuration changes to the running standby engine. Volume,
        message action/priority and mute-priority changes are applied live
        without restarting the audio streams; only structural changes (mode or
        the source chains themselves) rebuild the engine. Setting standby to
        None (mode 'stop') lets the channel idle-close once no message is in
        effect."""
        # A codec change requires rebuilding the RTP sender. Detect it up front
        # (the codec is not part of standby_config_key, so the early-returns
        # below would otherwise miss it) and hand the new row to the run thread,
        # which owns self.sender: it stops the stream for a few seconds, then
        # rebuilds the sender with the new codec.
        try:
            new_codec = multicast_rtp_clean_codec(row.get("codec") or "PCMU")
        except Exception:
            new_codec = None
        if new_codec is not None:
            with self.lock:
                sender = self.sender
                pending = self.pending_sender_row
            pending_codec = None
            if pending is not None:
                try:
                    pending_codec = multicast_rtp_clean_codec(pending.get("codec") or "PCMU")
                except Exception:
                    pending_codec = None
                current_codec = pending_codec
            else:
                current_codec = getattr(sender, "codec", None)
            if current_codec is not None and new_codec != current_codec:
                with self.lock:
                    self.pending_sender_row = dict(row)
                    self.codec_restart_until = time.monotonic() + MULTICAST_RTP_CODEC_RESTART_SECONDS
        new_key = standby_config_key(row)
        new_stream = standby_stream_key(row)
        with self.lock:
            current = self.standby
            current_key = current.config_key if current is not None else None
            if new_key == current_key:
                return
            current_stream = current.stream_key if current is not None else None
            if current is not None and new_stream is not None and new_stream == current_stream:
                current.apply_live(row)
                return
        engine = build_standby_engine(row)
        with self.lock:
            old = self.standby
            self.standby = engine
            self.persistent = engine is not None
            if engine is not None:
                self.idle_since = None
        if old is not None:
            old.stop()

    def attach_source(self, stream_id, priority="Normal", klass="message"):
        source = MulticastRTPSource(priority=priority, klass=klass, instant_ready=self.persistent)
        with self.lock:
            if self.closing:
                return None
            previous = self.sources.pop(stream_id, None)
            self.sources[stream_id] = source
            self.idle_since = None
        if previous is not None:
            previous.close()
        return source

    def register_broadcast(self, broadcast_id, priority, klass, metadata):
        """Record a broadcast that targets this endpoint so its standby action
        (stop / switch to emergency) stays in effect for the whole time the
        broadcast is active on the server — not just while its audio frames are
        streaming.

        Tracking keys off the broadcast's PRIORITY and is independent of the
        page/bell/message class (that class only picks the amplify-ducking
        value). The server's active-broadcast store is the source of truth for
        whether a broadcast is still in effect: the entry is dropped as soon as
        the store no longer lists it (expired, cancelled or otherwise cleared).
        Transient broadcasts that never appear in the store (e.g. live pages)
        fall away after a short grace window, so they only affect the background
        while their audio is actually playing."""
        broadcast_id = str(broadcast_id or "").strip()
        if not broadcast_id or not self.persistent:
            return
        meta = metadata or {}
        expires_at = sip_parse_datetime_value(meta.get("expires"))
        # Probe the store now so a broadcast that is already active (the common
        # case — records are written before dispatch) is confirmed immediately
        # and tracked for its whole lifetime.
        try:
            record = fetch_active_broadcast(broadcast_id)
        except Exception:
            record = None
        confirmed = isinstance(record, dict) and str(record.get("delivery") or "").strip().lower() not in {"expired", "cancelled"}
        with self.lock:
            existing = self.active_broadcasts.get(broadcast_id)
            self.active_broadcasts[broadcast_id] = {
                "priority": priority if priority in VALID_MESSAGE_PRIORITIES else "Normal",
                "klass": klass,
                "expires_at": expires_at,
                "registered_at": time.monotonic(),
                "confirmed": confirmed or bool(existing and existing.get("confirmed")),
            }
            self.idle_since = None
            # Force a fresh store re-check on the next frame so a just-attached
            # broadcast is validated promptly.
            self.active_check_at = 0.0
        if existing is None:
            log(f"multicast rtp standby track broadcast={broadcast_id} channel={self.key} "
                f"priority={priority} klass={klass} in_store={confirmed}")

    def current_active_broadcasts(self):
        """Return the messages currently in effect for this endpoint, using the
        server's active-broadcast store as the authority. A tracked broadcast is
        dropped once the store no longer lists it (with a short grace window
        right after it is registered to absorb dispatch/store write races), or
        once its own expiry time passes. The store is polled at most about once
        per second to keep the per-frame path cheap."""
        with self.lock:
            if not self.active_broadcasts:
                return []
            items = list(self.active_broadcasts.items())
        now_mono = time.monotonic()
        now_dt = datetime.now()
        do_check = now_mono >= self.active_check_at
        if do_check:
            self.active_check_at = now_mono + 1.0
        drop = []
        alive = []
        for bid, info in items:
            exp = info.get("expires_at")
            if exp is not None and now_dt >= exp:
                drop.append(bid)
                continue
            if do_check:
                try:
                    record = fetch_active_broadcast(bid)
                except Exception:
                    record = False  # store error: keep, retry next time
                if record is None:
                    # Not in the store. Keep briefly if we have never seen it
                    # confirmed (dispatch may beat the store write); otherwise it
                    # is genuinely gone -> restore the audio.
                    grace = (now_mono - info.get("registered_at", now_mono)) < 15.0
                    if info.get("confirmed") or not grace:
                        drop.append(bid)
                        continue
                elif isinstance(record, dict):
                    delivery = str(record.get("delivery") or "").strip().lower()
                    if delivery in {"expired", "cancelled"}:
                        drop.append(bid)
                        continue
                    info["confirmed"] = True
            alive.append(info)
        if drop:
            with self.lock:
                for bid in drop:
                    self.active_broadcasts.pop(bid, None)
            log(f"multicast rtp standby untrack channel={self.key} broadcasts={drop}")
        return alive

    def stop_source(self, stream_id):
        with self.lock:
            source = self.sources.get(stream_id)
        if source is not None:
            source.close()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.closing = True
            sources = list(self.sources.values())
            standby = self.standby
        for source in sources:
            source.close()
        if standby is not None:
            standby.stop()

    def compose_frame(self, standby, message_frames, in_effect, dominant_class, audio_msg_priority, msg_lifetime_priority):
        """Combine message audio with the standby background.

        Two independent controls decide what happens to the background:

          * The **standby message action** (keep / stop / switch-to-emergency,
            gated by ``standby_msg_priority``) applies for the WHOLE time a
            qualifying message is in effect on the server — during its audio and
            afterwards, until the message is no longer active. ``stop`` silences
            the background; ``emergency`` swaps it for the emergency streams.

          * The **mute-during-broadcast checkbox** (``mute_priority``) only
            applies while message audio is actually playing.

        Independently, the master amplify gain always applies, and while a
        broadcast is producing audio the background is ducked/muted by the
        matching amplify-on-<class> setting (message defaults to Mute).
        """
        has_audio = bool(message_frames)
        if standby is None or standby.mode != "rebroadcast":
            return mix_ulaw_frames(message_frames) if has_audio else MULTICAST_RTP_SILENCE_FRAME

        threshold = MESSAGE_PRIORITY_ORDER.get(standby.msg_priority, 2)
        action_triggered = msg_lifetime_priority is not None and msg_lifetime_priority >= threshold
        use_emergency = standby.msg_action == "emergency" and in_effect and action_triggered
        background = standby.next_frame("emergency" if use_emergency else "normal")

        gain = standby.amp.get("master", "0")
        muted = gain == "mute"
        # Stop / silence actions: hold the background silenced for the whole
        # message lifetime, even between/after the message audio frames.
        # "stop" additionally sends no RTP at all (the stream truly stops);
        # "silence" keeps emitting silence frames.
        stop_action = in_effect and standby.msg_action == "stop" and action_triggered
        silence_action = in_effect and standby.msg_action == "silence" and action_triggered
        if stop_action or silence_action:
            muted = True
        # Mute-during-broadcast checkbox: only while message audio is playing.
        if has_audio and standby.mute_priority_enabled:
            mute_thr = MESSAGE_PRIORITY_ORDER.get(standby.mute_priority, 2)
            if audio_msg_priority is not None and audio_msg_priority >= mute_thr:
                muted = True
        if has_audio and not use_emergency:
            type_amp = standby.amp_for_class(dominant_class or "message")
            if type_amp == "mute":
                muted = True
            else:
                gain = multicast_rtp_combine_gain(gain, type_amp)

        background_out = MULTICAST_RTP_SILENCE_FRAME if muted else apply_gain_ulaw_frame(background, gain)
        if not has_audio:
            # The stop action stops the stream entirely: emit nothing.
            if stop_action:
                return None
            return background_out
        message_mix = mix_ulaw_frames(message_frames)
        if background_out == MULTICAST_RTP_SILENCE_FRAME:
            return message_mix
        return mix_ulaw_frames([background_out, message_mix])

    def run(self):
        next_send = time.monotonic()
        try:
            while not self.stop_event.is_set():
                ready_sources = []
                finished_stream_ids = []
                message_frames = []
                audio_msg_priority = None
                msg_lifetime_priority = None
                best_audio_order = None
                dominant_class = None
                best_any_order = None
                best_any_class = None
                now = time.monotonic()
                # Codec change: stop the stream during the restart window, then
                # rebuild the RTP sender with the new codec. The run thread is
                # the sole owner of self.sender, so the swap happens here.
                with self.lock:
                    pending_row = self.pending_sender_row
                    restart_until = self.codec_restart_until
                in_codec_restart = False
                if pending_row is not None:
                    if now < restart_until:
                        in_codec_restart = True
                    else:
                        try:
                            new_sender = MulticastRTPSender(pending_row)
                        except Exception as exc:
                            log(f"multicast rtp codec restart error channel={self.key}: {exc}")
                            new_sender = None
                        if new_sender is not None:
                            old_sender = self.sender
                            self.sender = new_sender
                            try:
                                old_sender.close()
                            except Exception:
                                pass
                            log(f"multicast rtp codec restart applied channel={self.key} codec={new_sender.codec}")
                        with self.lock:
                            self.pending_sender_row = None
                            self.codec_restart_until = 0.0
                with self.lock:
                    source_items = list(self.sources.items())
                    standby = self.standby
                in_effect = bool(source_items)
                if source_items:
                    with self.lock:
                        self.idle_since = None
                    emergency_active = any(source.is_emergency() for _stream_id, source in source_items)
                    for stream_id, source in source_items:
                        discard = emergency_active and not source.is_emergency()
                        frame, finished = source.next_frame(discard=discard)
                        if frame is not None:
                            message_frames.append(frame)
                        order = MESSAGE_PRIORITY_ORDER.get(source.priority, 1)
                        # The stop / emergency / mute logic keys off broadcast
                        # PRIORITY for any broadcast in effect — independent of
                        # the page/bell/message class (which only selects the
                        # amplify-ducking value below).
                        if msg_lifetime_priority is None or order > msg_lifetime_priority:
                            msg_lifetime_priority = order
                        if frame is not None and (audio_msg_priority is None or order > audio_msg_priority):
                            audio_msg_priority = order
                        if best_any_order is None or order > best_any_order:
                            best_any_order = order
                            best_any_class = source.klass
                        if frame is not None and (best_audio_order is None or order > best_audio_order):
                            best_audio_order = order
                            dominant_class = source.klass
                        if source.note_preroll_frame():
                            ready_sources.append(stream_id)
                        if finished:
                            finished_stream_ids.append(stream_id)
                    if dominant_class is None:
                        dominant_class = best_any_class
                # A qualifying message keeps the standby *action* (stop / switch
                # to emergency) in effect for its whole server lifetime — during
                # its audio and afterwards until it is no longer active. This is
                # separate from the mute-during-broadcast checkbox, which only
                # tracks live audio (audio_msg_priority above).
                if standby is not None:
                    for info in self.current_active_broadcasts():
                        in_effect = True
                        order = MESSAGE_PRIORITY_ORDER.get(info.get("priority"), 1)
                        if msg_lifetime_priority is None or order > msg_lifetime_priority:
                            msg_lifetime_priority = order
                mixed_frame = self.compose_frame(
                    standby, message_frames, in_effect, dominant_class, audio_msg_priority, msg_lifetime_priority
                )
                # During a codec restart the stream is stopped; emit nothing.
                # A None frame means the standby stop action wants the stream
                # silenced with no RTP either.
                if not in_codec_restart and mixed_frame is not None:
                    try:
                        self.sender.send_frame(mixed_frame)
                    except OSError as exc:
                        # Transient network errors must not kill the channel
                        # thread mid-broadcast; skip the frame and keep pacing.
                        log(f"multicast rtp send error channel={self.key}: {exc}")
                for stream_id in ready_sources:
                    self.on_idle("ready", self.key, stream_id, self)
                if finished_stream_ids:
                    with self.lock:
                        for stream_id in finished_stream_ids:
                            source = self.sources.get(stream_id)
                            if source is not None and source.closed:
                                self.sources.pop(stream_id, None)
                # A channel with standby configured stays resident and keeps
                # broadcasting background audio; only non-standby channels idle
                # out once their message finishes.
                if standby is None:
                    with self.lock:
                        if not self.sources:
                            if self.idle_since is None:
                                self.idle_since = now
                            elif now - self.idle_since >= MULTICAST_RTP_IDLE_SECONDS:
                                self.closing = True
                    if self.closing:
                        break
                next_send += MULTICAST_RTP_FRAME_MS / 1000.0
                sleep_for = next_send - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_send = time.monotonic()
        finally:
            with self.lock:
                standby = self.standby
            try:
                self.sender.close()
            finally:
                if standby is not None:
                    standby.stop()
                self.on_idle("close", self.key, None, self)


class MulticastRTPStreamState:
    def __init__(self, stream_id):
        self.stream_id = stream_id
        self.lock = threading.Lock()
        self.sources = []
        self.ready_keys = set()

    def add_source(self, channel_key, source):
        with self.lock:
            self.sources.append((channel_key, source))

    def receive_audio(self, chunk):
        with self.lock:
            sources = [source for _channel_key, source in self.sources]
        for source in sources:
            source.receive_audio(chunk)

    def mark_source_ready(self, channel_key):
        with self.lock:
            if channel_key in self.ready_keys:
                return
            self.ready_keys.add(channel_key)
            all_ready = len(self.ready_keys) >= len(self.sources)
        if all_ready:
            mark_ready(MULTICAST_RTP_MODULE, self.stream_id)

    def close(self):
        with self.lock:
            sources = list(self.sources)
            self.sources.clear()
        for _channel_key, source in sources:
            source.close()


class BuiltinMulticastRTPModule:
    def __init__(self):
        self.lock = threading.Lock()
        self.streams = {}
        self.channels = {}
        self.reconcile_stop = threading.Event()
        self.reconcile_thread = threading.Thread(target=self._reconcile_loop, daemon=True)
        self.reconcile_thread.start()

    def _reconcile_loop(self):
        while not self.reconcile_stop.is_set():
            self.reconcile_standby()
            self.reconcile_stop.wait(5.0)

    def reconcile_standby(self):
        """Ensure persistent standby broadcasters exist for every multicast
        endpoint whose 'On standby' behaviour is rebroadcast/silence, refresh
        their configuration, and tear down standby for endpoints that no longer
        want it. Safe to call from any thread/process with DB access."""
        try:
            rows = multicast_rtp_rows()
        except Exception as exc:
            log(f"multicast rtp standby reconcile query error: {exc}")
            return
        wanted = {}
        for row in rows:
            if standby_config_key(row) is not None:
                wanted[str(row.get("id") or "")] = row
        for channel_key, row in wanted.items():
            try:
                channel = self.ensure_channel(row)
                channel.update_config(row)
            except Exception as exc:
                log(f"multicast rtp standby start error channel={channel_key}: {exc}")
        with self.lock:
            channels = dict(self.channels)
        for channel_key, channel in channels.items():
            if channel_key not in wanted and getattr(channel, "persistent", False):
                try:
                    channel.update_config({"standby_mode": "stop"})
                except Exception as exc:
                    log(f"multicast rtp standby stop error channel={channel_key}: {exc}")

    def get_endpoint_status(self):
        return get_multicast_rtp_endpoint_status()

    def endpoint_streaming(self, row_id):
        with self.lock:
            channel = self.channels.get(str(row_id))
        return bool(channel and not channel.closing and channel.is_streaming_audio())

    def handle_channel_event(self, action, channel_key, stream_id, channel):
        if action == "ready" and stream_id is not None:
            with self.lock:
                state = self.streams.get(stream_id)
            if state is not None:
                state.mark_source_ready(channel_key)
            return
        if action == "close":
            with self.lock:
                if self.channels.get(channel_key) is channel:
                    self.channels.pop(channel_key, None)

    def ensure_channel(self, row):
        channel_key = str(row.get("id") or "")
        with self.lock:
            channel = self.channels.get(channel_key)
            if channel is None or channel.closing:
                channel = MulticastRTPEndpointChannel(row, self.handle_channel_event)
                self.channels[channel_key] = channel
                return channel
        channel.update_config(row)
        return channel

    def handle_dispatch(self, action, stream_id, msg_id, sub_targets, metadata=None):
        if action not in {"prepare_audio", "prepare_livepage"}:
            return
        rows = multicast_rtp_rows_for_targets(sub_targets)
        if not rows:
            mark_ready(MULTICAST_RTP_MODULE, stream_id)
            return
        state = MulticastRTPStreamState(stream_id)
        priority = multicast_priority_value(metadata)
        klass = multicast_broadcast_class(action, metadata)
        for row in rows:
            source = None
            for _attempt in range(3):
                channel = self.ensure_channel(row)
                source = channel.attach_source(stream_id, priority=priority, klass=klass)
                if source is not None:
                    break
                # Channel was shutting down between lookup and attach — drop
                # it so ensure_channel builds a fresh one.
                with self.lock:
                    if self.channels.get(channel.key) is channel:
                        self.channels.pop(channel.key, None)
            if source is None:
                log(f"multicast rtp attach failed stream={stream_id} channel={row.get('id')}")
                continue
            channel.register_broadcast(msg_id, priority, klass, metadata)
            state.add_source(channel.key, source)
        with self.lock:
            previous = self.streams.pop(stream_id, None)
            self.streams[stream_id] = state
        if previous is not None:
            try:
                previous.close()
            except Exception as exc:
                log(f"multicast rtp stream replace error stream={stream_id}: {exc}")
        # Do not mark ready here: readiness is gated on the channel preroll
        # (handle_channel_event -> mark_source_ready) so the call is not
        # answered until the multicast group has been primed and the
        # receiving devices have had time to join. Only short-circuit when
        # there is nothing to wait for.
        if not state.sources:
            mark_ready(MULTICAST_RTP_MODULE, stream_id)

    def receive_audio(self, chunk, stream_id):
        with self.lock:
            state = self.streams.get(stream_id)
        if state is None:
            return
        state.receive_audio(chunk)

    def end_stream(self, stream_id):
        with self.lock:
            state = self.streams.pop(stream_id, None)
        if state is None:
            return
        state.close()

    def shutdown(self):
        self.reconcile_stop.set()
        with self.lock:
            states = list(self.streams.values())
            self.streams.clear()
            channels = list(self.channels.values())
            self.channels.clear()
        for state in states:
            try:
                state.close()
            except Exception:
                pass
        for channel in channels:
            try:
                channel.stop()
            except Exception:
                pass


def http_request_headers_rows_html(headers):
    rows_html = []
    safe_headers = list(headers or [])
    if not safe_headers:
        safe_headers = [{"name": "", "value": ""}]
    for idx, header in enumerate(safe_headers):
        uid = f"hdrval_init_{idx}"
        rows_html.append(
            f"""<div class="http-header-row" style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr) auto;gap:10px;align-items:end">
<div class="row"><label>Header Name</label><input class="control" name="header_name" value="{h(header.get("name"))}" placeholder="ex: Content-Type"></div>
<div class="row"><label>Header Value</label><div class="http-var-wrap"><input class="control" id="{uid}" name="header_value" value="{h(header.get("value"))}" placeholder="ex: application/json"><button type="button" class="http-var-badge" onclick="openHttpVarPicker('{uid}')" title="Insert Variable">&#36;&#123;x&#125;</button></div></div>
<button class="danger" type="button" onclick="removeHttpHeaderRow(this)">Remove</button>
</div>"""
        )
    return "".join(rows_html)


def http_request_form_html(values, error, submit_label):
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    method = http_request_clean_method(values.get("method") or "POST")
    auth_type = http_request_clean_auth_type(values.get("auth_type") or "none")
    method_options = "".join(
        f'<option value="{h(item)}"{" selected" if method == item else ""}>{h(item)}</option>'
        for item in HTTP_REQUEST_METHODS
    )
    auth_options = "".join(
        f'<option value="{h(item)}"{" selected" if auth_type == item else ""}>{h(item.title() if item != "apikey" else "API Key")}</option>'
        for item in ("none", "basic", "digest", "apikey")
    )
    headers_html = http_request_headers_rows_html(values.get("headers"))
    return f"""{error_html}<form method="post" class="grid form-surface" id="httpRequestForm">
<div class="row"><label>Name <span style="color:#C62828">*</span></label><input class="control" name="name" value="{h(values.get("name"))}" required></div>
<div class="row"><label>HTTP Method</label><select class="control short-control" name="method">{method_options}</select></div>
<div class="row"><label>URL <span style="color:#C62828">*</span></label><div class="http-var-wrap"><input class="control" id="httpUrl" name="url" value="{h(values.get("url"))}" placeholder="ex: https://api.example.com/sendmsg/${{shortmessage}}/${{longmessage}}" required><button type="button" class="http-var-badge" onclick="openHttpVarPicker('httpUrl')" title="Insert Variable">&#36;&#123;x&#125;</button></div></div>
<div class="row"><label>Body</label><div class="http-var-wrap http-var-wrap-long"><textarea class="control" id="httpBody" name="body" rows="6">{h(values.get("body"))}</textarea><button type="button" class="http-var-badge" onclick="openHttpVarPicker('httpBody')" title="Insert Variable">&#36;&#123;x&#125;</button></div></div>
<div class="row"><label>Authentication</label><select class="control short-control" id="httpAuthType" name="auth_type">{auth_options}</select></div>
<div id="httpAuthUserFields" style="display:none;gap:14px">
<div class="row"><label>Username</label><input class="control" name="auth_username" value="{h(values.get("auth_username"))}"></div>
<div class="row"><label>Password</label><input class="control" type="password" name="auth_password" value="{h(values.get("auth_password"))}"></div>
</div>
<div id="httpAuthApiKeyFields" style="display:none;gap:14px">
<div class="row"><label>Header Name</label><input class="control" name="auth_header_name" value="{h(values.get("auth_header_name"))}" placeholder="ex: X-API-Key"></div>
<div class="row"><label>Header Value</label><div class="http-var-wrap"><input class="control" id="httpAuthHeaderValue" name="auth_header_value" value="{h(values.get("auth_header_value"))}"><button type="button" class="http-var-badge" onclick="openHttpVarPicker('httpAuthHeaderValue')" title="Insert Variable">&#36;&#123;x&#125;</button></div></div>
</div>
<details class="advanced" open><summary>Headers</summary><div class="advanced-body">
<div id="httpHeaderRows" style="display:grid;gap:10px">{headers_html}</div>
<button class="button" type="button" id="addHttpHeaderRow">Add Header</button>
</div></details>
<div class="row"><label>Timeout (seconds)</label><input class="control short-control" type="number" name="timeout" value="{h(values.get("timeout") or HTTP_REQUEST_DEFAULT_TIMEOUT)}" min="1" max="600" required></div>
<div class="row"><label style="display:flex;align-items:center;gap:8px;cursor:pointer"><input type="checkbox" name="include_audio_only" value="1"{"" if values.get("include_audio_only") == "0" else " checked"} style="width:16px;height:16px;margin:0;cursor:pointer"> Include audio only messages</label></div>
<button class="button" type="submit">{h(submit_label)}</button>
</form>
<div id="httpVarBackdrop" class="http-var-modal-backdrop" onclick="closeHttpVarPicker()"></div>
<div id="httpVarModal" class="http-var-modal" role="dialog" aria-modal="true">
<div class="http-var-modal-header">
<h2 id="httpVarTitle">Insert Variable</h2>
<div class="http-var-modal-header-actions">
<button type="button" id="httpVarBack" class="http-var-modal-back" onclick="httpVarShowList()">Back</button>
<button type="button" class="http-var-modal-close" onclick="closeHttpVarPicker()" aria-label="Close">&times;</button>
</div>
</div>
<div class="http-var-modal-body">
<div id="httpVarList" class="http-var-list">
<button type="button" class="http-var-choice" onclick="httpVarWizard('shortmessage')">Short Message</button>
<button type="button" class="http-var-choice" onclick="httpVarWizard('longmessage')">Long Message</button>
<button type="button" class="http-var-choice" onclick="httpVarInsert('${{color}}')">Color</button>
<button type="button" class="http-var-choice" onclick="httpVarWizard('date')">Date</button>
<button type="button" class="http-var-choice" onclick="httpVarWizard('datetime')">Date + Time</button>
<button type="button" class="http-var-choice" onclick="httpVarWizard('time')">Time</button>
<button type="button" class="http-var-choice" onclick="httpVarWizard('sender')">Sender</button>
<button type="button" class="http-var-choice" onclick="httpVarInsert('${{productname}}')">Product Name</button>
</div>
<div id="httpVarWizardShortmessage" class="http-var-wizard">
<div class="http-var-row"><label>Choose a space format</label>
<div class="http-var-option-list">
<button type="button" class="http-var-option" onclick="httpVarInsert('${{shortmessage:%20}}')"><strong>%20</strong><span>RFC 3986 space encoding (recommended)</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{shortmessage:_}}')"><strong>_</strong><span>Insert a underscore in place of spaces</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{shortmessage:+}}')"><strong>+</strong><span>Insert a plus in place of spaces</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{shortmessage:nospace}}')"><strong>Omit spaces</strong><span>Remove spaces and send words clustered together</span></button>
</div></div>
</div>
<div id="httpVarWizardLongmessage" class="http-var-wizard">
<div class="http-var-row"><label>Choose a space format</label>
<div class="http-var-option-list">
<button type="button" class="http-var-option" onclick="httpVarInsert('${{longmessage:%20}}')"><strong>%20</strong><span>RFC 3986 space encoding (recommended)</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{longmessage:_}}')"><strong>_</strong><span>Insert a underscore in place of spaces</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{longmessage:+}}')"><strong>+</strong><span>Insert a plus in place of spaces</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{longmessage:nospace}}')"><strong>Omit spaces</strong><span>Remove spaces and send words clustered together</span></button>
</div></div>
</div>
<div id="httpVarWizardDate" class="http-var-wizard">
<div class="http-var-row"><label>Choose a date format</label>
<div class="http-var-option-list">
<button type="button" class="http-var-option" onclick="httpVarInsert('${{date}}')"><strong>Default</strong><span>06/22/2026</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{date:MM/DD/YYYY}}')"><strong>US Long</strong><span>06/22/2026</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{date:MM/DD/YY}}')"><strong>US Short</strong><span>06/22/26</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{date:YYYY-MM-DD}}')"><strong>ISO</strong><span>2026-06-22</span></button>
</div></div>
</div>
<div id="httpVarWizardDatetime" class="http-var-wizard">
<div class="http-var-row"><label>Choose a date and time format</label>
<div class="http-var-option-list">
<button type="button" class="http-var-option" onclick="httpVarInsert('${{date+time}}')"><strong>Default</strong><span>06/22/2026 03:04 PM</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{date+time:MM/DD/YYYY hh:mm A}}')"><strong>US 12-Hour</strong><span>06/22/2026 03:04 PM</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{date+time:MM/DD/YYYY HH:mm:ss}}')"><strong>US 24-Hour With Seconds</strong><span>06/22/2026 15:04:05</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{date+time:YYYY-MM-DD HH:mm:ss}}')"><strong>ISO</strong><span>2026-06-22 15:04:05</span></button>
</div></div>
</div>
<div id="httpVarWizardTime" class="http-var-wizard">
<div class="http-var-row"><label>Choose a time format</label>
<div class="http-var-option-list">
<button type="button" class="http-var-option" onclick="httpVarInsert('${{time}}')"><strong>Default</strong><span>03:04 PM</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{time:hh:mm A}}')"><strong>12-Hour</strong><span>03:04 PM</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{time:hh:mm:ss A}}')"><strong>12-Hour With Seconds</strong><span>03:04:05 PM</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{time:HH:mm}}')"><strong>24-Hour</strong><span>15:04</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{time:HH:mm:ss}}')"><strong>24-Hour With Seconds</strong><span>15:04:05</span></button>
</div></div>
</div>
<div id="httpVarWizardSender" class="http-var-wizard">
<div class="http-var-row"><label>Choose sender information</label>
<div class="http-var-option-list">
<button type="button" class="http-var-option" onclick="httpVarInsert('${{sender}}')"><strong>Default</strong><span>Name and number when available</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{sender:[CNAM] [CID]}}')"><strong>Name + Number</strong><span>Caller name followed by caller ID number</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{sender:[CNAM]}}')"><strong>Name Only</strong><span>Caller or sender name only</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{sender:[CID]}}')"><strong>Number Only</strong><span>Caller ID number only</span></button>
<button type="button" class="http-var-option" onclick="httpVarInsert('${{sender:[USERNAME]}}')"><strong>Username Only</strong><span>Web or API username only</span></button>
</div></div>
</div>
</div>
</div>
<style>
.http-var-wrap {{ position: relative; }}
.http-var-wrap .control {{ padding-right: 42px; }}
.http-var-badge {{ position: absolute; top: 50%; right: 10px; transform: translateY(-50%); width: 24px; height: 24px; border: none; border-radius: 0; background: transparent; color: rgba(25, 118, 210, 0.78); font-size: 0.95em; font-weight: 700; font-family: "Times New Roman", serif; cursor: pointer; display: flex; align-items: center; justify-content: center; padding: 0; z-index: 2; pointer-events: auto; }}
.http-var-badge:hover {{ background: transparent; color: rgba(25, 118, 210, 1); }}
.http-var-wrap-long .http-var-badge {{ top: auto; bottom: 10px; right: 10px; transform: none; }}
.http-var-modal-backdrop {{ display: none; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); z-index: 1600; }}
.http-var-modal-backdrop.open {{ display: block; }}
.http-var-modal {{ display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: min(760px, calc(100vw - 32px)); max-height: calc(100vh - 40px); overflow-y: auto; background: #FFF; border-radius: 14px; box-shadow: 0 18px 50px rgba(0, 0, 0, 0.28); z-index: 1650; font-family: "Tahoma", sans-serif; }}
.http-var-modal.open {{ display: block; }}
.http-var-modal-header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 20px; border-bottom: 1px solid #EEE; }}
.http-var-modal-header h2 {{ margin: 0; font-size: 1.2em; font-weight: 500; }}
.http-var-modal-header-actions {{ display: flex; align-items: center; gap: 8px; }}
.http-var-modal-close, .http-var-modal-back {{ border: none; background: transparent; color: #666; font-size: 1.4em; cursor: pointer; line-height: 1; padding: 4px 6px; }}
.http-var-modal-close:hover, .http-var-modal-back:hover {{ background: transparent; color: #111; }}
.http-var-modal-back {{ display: none; font-size: 0.95em; font-weight: 600; }}
.http-var-modal-back.visible {{ display: inline-flex; align-items: center; }}
.http-var-modal-body {{ padding: 20px; }}
.http-var-list {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
.http-var-choice {{ width: 100%; border: 1px solid #DDE6F1; border-radius: 12px; background: #F8FBFF; color: #0F3F77; padding: 18px 16px; text-align: left; font-size: 1em; font-weight: 600; cursor: pointer; font-family: "Tahoma", sans-serif; }}
.http-var-choice:hover {{ background: #EDF5FF; border-color: #BCD2EC; }}
.http-var-wizard {{ display: none; }}
.http-var-wizard.open {{ display: block; }}
.http-var-row {{ margin-bottom: 16px; }}
.http-var-row:last-child {{ margin-bottom: 0; }}
.http-var-row label {{ display: block; margin-bottom: 6px; font-weight: 500; }}
.http-var-option-list {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
.http-var-option {{ width: 100%; border: 1px solid #DDE6F1; border-radius: 12px; background: #FAFCFF; color: #1F2937; padding: 14px; text-align: left; cursor: pointer; font-family: "Tahoma", sans-serif; }}
.http-var-option:hover {{ background: #F0F7FF; border-color: #BCD2EC; }}
.http-var-option strong {{ display: block; font-size: 1em; font-weight: 600; margin-bottom: 4px; color: #0F3F77; font-family: "Tahoma", sans-serif; }}
.http-var-option span {{ display: block; font-size: 0.92em; color: #5B6470; font-family: "Tahoma", sans-serif; }}
.http-var-modal button, .http-var-modal input, .http-var-modal textarea, .http-var-modal select {{ font-family: "Tahoma", sans-serif; }}
@media(prefers-color-scheme:dark){{
.http-var-badge {{ background: transparent; color: rgba(187, 134, 252, 0.82); }}
.http-var-badge:hover {{ background: transparent; color: rgba(187, 134, 252, 1); }}
.http-var-modal {{ background: #1E1E1E; }}
.http-var-modal-header {{ border-bottom-color: #333; }}
.http-var-modal-close, .http-var-modal-back {{ color: #AAA; }}
.http-var-modal-close:hover, .http-var-modal-back:hover {{ background: transparent; color: #FFF; }}
.http-var-choice {{ background: #252525; border-color: #3A3A3A; color: #E5E7EB; }}
.http-var-choice:hover {{ background: #2E2E2E; border-color: #4A4A4A; }}
.http-var-option {{ background: #252525; border-color: #3A3A3A; color: #E5E7EB; }}
.http-var-option:hover {{ background: #2E2E2E; border-color: #4A4A4A; }}
.http-var-option strong {{ color: #EAF2FF; }}
.http-var-option span {{ color: #BFC6CF; }}
.http-var-row label {{ color: #E0E0E0; }}
}}
</style>
<script>
var httpVarTargetField = null;
var httpAuthType = document.getElementById('httpAuthType');
var httpAuthUserFields = document.getElementById('httpAuthUserFields');
var httpAuthApiKeyFields = document.getElementById('httpAuthApiKeyFields');
var httpHeaderRows = document.getElementById('httpHeaderRows');
function syncHttpAuthFields() {{
  var authType = (httpAuthType.value || 'none').toLowerCase();
  httpAuthUserFields.style.display = authType === 'basic' || authType === 'digest' ? 'grid' : 'none';
  httpAuthApiKeyFields.style.display = authType === 'apikey' ? 'grid' : 'none';
}}
function openHttpVarPicker(fieldId) {{
  httpVarTargetField = document.getElementById(fieldId);
  document.getElementById('httpVarBackdrop').classList.add('open');
  document.getElementById('httpVarModal').classList.add('open');
  document.documentElement.style.overflow = 'hidden';
  if (window.parent && window.parent !== window) {{
    window.parent.postMessage({{ type: 'ops-frame-height-lock' }}, window.location.origin);
  }}
  httpVarShowList();
}}
function closeHttpVarPicker() {{
  document.getElementById('httpVarBackdrop').classList.remove('open');
  document.getElementById('httpVarModal').classList.remove('open');
  document.documentElement.style.overflow = '';
  if (window.parent && window.parent !== window) {{
    window.parent.postMessage({{ type: 'ops-frame-height-unlock' }}, window.location.origin);
  }}
  httpVarTargetField = null;
}}
function httpVarShowList() {{
  document.getElementById('httpVarList').style.display = 'grid';
  var wizards = document.querySelectorAll('.http-var-wizard');
  for (var i = 0; i < wizards.length; i++) wizards[i].classList.remove('open');
  document.getElementById('httpVarBack').classList.remove('visible');
  document.getElementById('httpVarTitle').textContent = 'Insert Variable';
}}
function httpVarWizard(key) {{
  document.getElementById('httpVarList').style.display = 'none';
  var wizards = document.querySelectorAll('.http-var-wizard');
  for (var i = 0; i < wizards.length; i++) wizards[i].classList.remove('open');
  var titles = {{'shortmessage':'Short Message','longmessage':'Long Message','date':'Date','datetime':'Date + Time','time':'Time','sender':'Sender'}};
  document.getElementById('httpVarTitle').textContent = titles[key] || 'Insert Variable';
  var id = 'httpVarWizard' + key.charAt(0).toUpperCase() + key.slice(1);
  var wizard = document.getElementById(id);
  if (wizard) wizard.classList.add('open');
  document.getElementById('httpVarBack').classList.add('visible');
}}
function httpVarInsert(snippet) {{
  if (!httpVarTargetField) {{ closeHttpVarPicker(); return; }}
  var field = httpVarTargetField;
  var val = field.value || '';
  var start = typeof field.selectionStart === 'number' ? field.selectionStart : val.length;
  var end = typeof field.selectionEnd === 'number' ? field.selectionEnd : val.length;
  field.value = val.slice(0, start) + snippet + val.slice(end);
  var caret = start + snippet.length;
  if (typeof field.setSelectionRange === 'function') field.setSelectionRange(caret, caret);
  field.focus();
  closeHttpVarPicker();
}}
function removeHttpHeaderRow(button) {{
  var row = button.closest('.http-header-row');
  if (row) row.remove();
  if (!httpHeaderRows.children.length) addHttpHeaderRow();
}}
function addHttpHeaderRow(name, value) {{
  name = name || ''; value = value || '';
  var wrapper = document.createElement('div');
  wrapper.className = 'http-header-row';
  wrapper.style.display = 'grid';
  wrapper.style.gridTemplateColumns = 'minmax(0,1fr) minmax(0,2fr) auto';
  wrapper.style.gap = '10px';
  wrapper.style.alignItems = 'end';
  var uid = 'hdrval_' + Date.now() + '_' + Math.random().toString(36).slice(2,8);
  wrapper.innerHTML = '<div class="row"><label>Header Name</label><input class="control" name="header_name" placeholder="ex: Content-Type"></div><div class="row"><label>Header Value</label><div class="http-var-wrap"><input class="control" id="' + uid + '" name="header_value" placeholder="ex: application/json"><button type="button" class="http-var-badge" onclick="openHttpVarPicker(\\\'' + uid + '\\\')" title="Insert Variable">&#36;&#123;x&#125;</button></div></div><button class="danger" type="button">Remove</button>';
  wrapper.querySelector('input[name="header_name"]').value = name;
  wrapper.querySelector('#' + uid).value = value;
  wrapper.querySelector('button.danger').addEventListener('click', function() {{ removeHttpHeaderRow(this); }});
  httpHeaderRows.appendChild(wrapper);
}}
document.getElementById('addHttpHeaderRow').addEventListener('click', function() {{ addHttpHeaderRow(); }});
httpAuthType.addEventListener('change', syncHttpAuthFields);
syncHttpAuthFields();
document.addEventListener('keydown', function(e) {{ if (e.key === 'Escape') closeHttpVarPicker(); }});
</script>"""




def http_request_space_value(text, space_format="%20"):
    raw_text = str(text or "")
    token = str(space_format or "%20").strip().lower() or "%20"
    if token == "_":
        return raw_text.replace(" ", "_")
    if token == "+":
        return raw_text.replace(" ", "+")
    if token == "nospace":
        return raw_text.replace(" ", "")
    return raw_text.replace(" ", "%20")


def http_request_color_value(value):
    raw = re.sub(r"[^0-9a-fA-F]", "", str(value or "").strip().lstrip("#"))
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) >= 6:
        raw = raw[:6]
    return raw.upper() if len(raw) == 6 else ""


def http_request_expand_variables_with_cursor(text, metadata, cursor, space_format="%20"):
    raw_text = str(text or "")
    if not raw_text:
        return raw_text
    payload = metadata if isinstance(metadata, dict) else {}
    issued = payload.get("issued")
    if not isinstance(issued, datetime):
        issued = datetime.now()

    def replace(match):
        key = str(match.group(1) or "").strip().lower()
        option = str(match.group(2) or "").strip()
        if key in {"shortmessage", "longmessage"}:
            return http_request_space_value(payload.get(key) or "", option or space_format)
        if key == "color":
            return http_request_color_value(payload.get("color"))
        return match.group(0)

    replaced = HTTP_REQUEST_VARIABLE_RE.sub(replace, raw_text)
    return expand_message_variables(
        replaced,
        cursor,
        sender=str(payload.get("sender") or "").strip(),
        sender_context=payload,
        now=issued,
    )


def http_request_expand_variables(text, metadata, space_format="%20"):
    raw_text = str(text or "")
    if not raw_text:
        return raw_text
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            return http_request_expand_variables_with_cursor(raw_text, metadata or {}, cur, space_format=space_format)
    finally:
        conn.close()


class BuiltinHttpRequestWeb:
    def forms(self):
        return {
            "request": {"label": HTTP_REQUEST_NAME, "description": HTTP_REQUEST_DESCRIPTION},
        }

    def render_form(self, form_type, request, conn_factory, page, user):
        ensure_httprequest_schema()
        if form_type not in self.forms():
            return page("Endpoint Form", "<h1>Endpoint form not found</h1>", "endpoints", user, status=404)
        error = ""
        values = http_request_form_values(request.form if request.method == "POST" else None)
        if request.method == "POST":
            try:
                clean = http_request_clean_values(values)
                sip_execute(
                    f"INSERT INTO `{HTTP_REQUEST_TABLE}` (`name`, `method`, `url`, `body`, `auth_type`, `auth_username`, `auth_password`, `auth_header_name`, `auth_header_value`, `headers_json`, `timeout`, `include_audio_only`) "
                    f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        clean["name"],
                        clean["method"],
                        clean["url"],
                        clean["body"],
                        clean["auth_type"],
                        clean["auth_username"],
                        clean["auth_password"],
                        clean["auth_header_name"],
                        clean["auth_header_value"],
                        json.dumps(clean["headers"]),
                        clean["timeout"],
                        clean["include_audio_only"],
                    ),
                )
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            except ValueError as exc:
                error = str(exc)
        body = http_request_form_html(values, error, "Add HTTP Request")
        return page(HTTP_REQUEST_NAME, sip_form_frame(body), "endpoints", user)

    def render_action(self, action, endpoint_id, request, conn_factory, page, user):
        ensure_httprequest_schema()
        kind, _, row_id = str(endpoint_id or "").partition("-")
        if action not in {"edit", "delete"} or kind != "request" or not row_id.isdigit():
            return page("Endpoint Action", "<h1>Invalid endpoint action</h1>", "endpoints", user, status=400)
        row = http_request_row(row_id)
        if not row:
            return page("Endpoint Action", "<h1>Endpoint not found</h1>", "endpoints", user, status=404)
        error = ""
        if request.method == "POST":
            if action == "delete":
                sip_execute(f"DELETE FROM `{HTTP_REQUEST_TABLE}` WHERE id=%s", (row_id,))
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            values = http_request_form_values(request.form, row)
            try:
                clean = http_request_clean_values(values)
                sip_execute(
                    f"UPDATE `{HTTP_REQUEST_TABLE}` SET `name`=%s, `method`=%s, `url`=%s, `body`=%s, `auth_type`=%s, `auth_username`=%s, `auth_password`=%s, `auth_header_name`=%s, `auth_header_value`=%s, `headers_json`=%s, `timeout`=%s, `include_audio_only`=%s WHERE id=%s",
                    (
                        clean["name"],
                        clean["method"],
                        clean["url"],
                        clean["body"],
                        clean["auth_type"],
                        clean["auth_username"],
                        clean["auth_password"],
                        clean["auth_header_name"],
                        clean["auth_header_value"],
                        json.dumps(clean["headers"]),
                        clean["timeout"],
                        clean["include_audio_only"],
                        row_id,
                    ),
                )
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            except ValueError as exc:
                error = str(exc)
                row.update(values)
        if action == "delete":
            error_html = f'<div class="error">{h(error)}</div>' if error else ""
            body = f"""{error_html}<form method="post" class="grid surface">
<p class="meta">Delete {h(row.get("name") or endpoint_id)}?</p>
<button class="danger" type="submit">Delete Endpoint</button></form>"""
        else:
            body = http_request_form_html(http_request_form_values(None, row), error, "Save HTTP Request")
        return page("Endpoint Action", sip_form_frame(body), "endpoints", user)

    def render_settings(self, request, conn_factory, page, user):
        return page(HTTP_REQUEST_NAME, "<p>No additional settings are required for HTTP Request.</p>", "endpoints", user)


class BuiltinHttpRequestModule:
    def get_endpoint_status(self):
        return get_http_request_endpoint_status()

    def handle_dispatch(self, action, stream_id, msg_id, sub_targets, metadata=None):
        if action in {"prepare_audio", "prepare_livepage"}:
            mark_ready(HTTP_REQUEST_MODULE, stream_id)
        # Always ignore live pages
        if action == "prepare_livepage":
            return
        if action not in {"prepare_audio", "sendmsg"}:
            return
        rows = http_request_rows_for_targets(sub_targets)
        if not rows:
            if action == "prepare_audio":
                mark_ready(HTTP_REQUEST_MODULE, stream_id)
            return
        threading.Thread(
            target=self.dispatch_requests,
            args=(stream_id, msg_id, rows, dict(metadata or {})),
            daemon=True,
        ).start()

    def dispatch_requests(self, stream_id, msg_id, rows, metadata):
        msg_type = str(metadata.get("type") or "").strip()
        msg_type_lower = msg_type.lower()
        if msg_type_lower in {"page", "liveaudio"}:
            mark_ready(HTTP_REQUEST_MODULE, stream_id)
            return
        # Detect bells: AudioMessage type with no visual text — always skip
        has_text = bool(str(metadata.get("shortmessage") or "").strip() or str(metadata.get("longmessage") or "").strip())
        if msg_type_lower == "audiomessage" and not has_text:
            return
        # Check if this is an audio-only message (no visual/text component)
        is_audio_only = msg_type_lower in ("audio", "audiomessage") or (
            is_audio_type(msg_type) and not has_text
        )
        any_failed = False
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                for row in rows:
                    # Skip audio-only messages if the endpoint has include_audio_only disabled
                    iao = row.get("include_audio_only")
                    if is_audio_only and not int(iao if iao is not None else 1):
                        continue
                    try:
                        self.send_request(row, metadata, cur)
                        log(f"http request sent endpoint={row.get('id')} stream={stream_id} msg={msg_id}")
                    except Exception as exc:
                        any_failed = True
                        log(f"http request failed endpoint={row.get('id')} stream={stream_id} msg={msg_id}: {exc}")
        except Exception as exc:
            any_failed = True
            log(f"http request dispatch error stream={stream_id} msg={msg_id}: {exc}")
        finally:
            if conn is not None:
                conn.close()
        if any_failed:
            mark_failed(HTTP_REQUEST_MODULE, stream_id)

    def send_request(self, row, metadata, cursor):
        method = http_request_clean_method(row.get("method") or "POST")
        url = http_request_expand_variables_with_cursor(row.get("url"), metadata or {}, cursor)
        if not str(url or "").strip():
            raise ValueError("URL is empty after variable expansion.")
        headers = {"User-Agent": "OpenPagingServer"}
        for item in sip_clean_headers(row.get("headers_json")):
            name = http_request_expand_variables_with_cursor(item.get("name"), metadata or {}, cursor, space_format="_").strip()
            value = http_request_expand_variables_with_cursor(item.get("value"), metadata or {}, cursor)
            if not name or ":" in name or "\r" in name or "\n" in name:
                continue
            if "\r" in value or "\n" in value:
                continue
            headers[name] = value
        auth_type = http_request_clean_auth_type(row.get("auth_type") or "none")
        if auth_type == "apikey":
            api_header_name = http_request_expand_variables_with_cursor(row.get("auth_header_name"), metadata or {}, cursor, space_format="_").strip()
            api_header_value = http_request_expand_variables_with_cursor(row.get("auth_header_value"), metadata or {}, cursor)
            if api_header_name and ":" not in api_header_name and "\r" not in api_header_name and "\n" not in api_header_name and "\r" not in api_header_value and "\n" not in api_header_value:
                headers[api_header_name] = api_header_value
        body = http_request_expand_variables_with_cursor(row.get("body"), metadata or {}, cursor)
        body_bytes = body.encode("utf-8") if body != "" else None
        timeout = http_request_clean_timeout(row.get("timeout"))
        request_obj = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        if auth_type == "basic":
            credentials = f"{str(row.get('auth_username') or '')}:{str(row.get('auth_password') or '')}"
            token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            request_obj.add_header("Authorization", f"Basic {token}")
            opener = urllib.request.build_opener()
        elif auth_type == "digest":
            password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            password_mgr.add_password(
                None,
                url,
                str(row.get("auth_username") or ""),
                str(row.get("auth_password") or ""),
            )
            opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(password_mgr))
        else:
            opener = urllib.request.build_opener()
        try:
            with opener.open(request_obj, timeout=timeout) as response:
                status_code = int(getattr(response, "status", 0) or response.getcode() or 0)
                if status_code >= 400:
                    raise RuntimeError(f"HTTP {status_code}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(str(getattr(exc, "reason", exc) or exc)) from exc
        except TimeoutError as exc:
            raise RuntimeError("request timed out") from exc
        except OSError as exc:
            raise RuntimeError(str(exc) or "request failed") from exc

    def receive_audio(self, chunk, stream_id):
        pass

    def end_stream(self, stream_id):
        pass

    def shutdown(self):
        pass


SERVICE_MONITOR_PRIORITIES = ("Low", "Normal", "High", "Emergency")
SERVICE_MONITOR_SUCCESS_REDIRECT = (
    "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>"
)


def service_monitor_default_tts_token(text):
    """Encode a TTS token using the highest-priority local voice, else Google English."""
    try:
        from tts import available_tts_voices, encode_tts_token
    except Exception:
        return None
    try:
        voices = available_tts_voices()
    except Exception:
        voices = []
    chosen = None
    for voice in voices:
        if str(voice.get("engine") or "").strip().lower() in ("swift", "festival", "piper"):
            chosen = voice
            break
    if chosen is None:
        for preferred in ("en", "en-us", "en-gb"):
            for voice in voices:
                if str(voice.get("engine") or "").strip().lower() == "google" and str(voice.get("voice") or "").strip().lower() == preferred:
                    chosen = voice
                    break
            if chosen is not None:
                break
    if chosen is None:
        for voice in voices:
            if str(voice.get("engine") or "").strip().lower() == "google":
                chosen = voice
                break
    if chosen is None:
        return None
    try:
        return encode_tts_token({"engine": chosen.get("engine"), "voice": chosen.get("voice"), "text": text})
    except Exception:
        return None


def service_monitor_default_audio_entries(tts_text, candidates):
    entries = []
    try:
        from srv.web import app as webapp

        available = set(webapp.audio_files())
        for name in candidates:
            if name in available:
                entries.append(name)
                break
    except Exception:
        pass
    token = service_monitor_default_tts_token(tts_text)
    if token:
        entries.extend([token] * 5)
    return entries


def service_monitor_default_message(direction):
    ts = "${date+time:MM/DD/YYYY HH:mm:ss}"
    if direction == "down":
        short = "${monitorname} is down at " + ts
        longmsg = "${monitorname} went down at " + ts + ". Please investigate immediately.\n\n${status}"
        tts_text = "Attention! ${monitorname} went down at " + ts + ". Please investigate immediately."
        color = "D11010"
        priority = "High"
        audio = service_monitor_default_audio_entries(tts_text, SERVICE_MONITOR_DOWN_AUDIO_CANDIDATES)
        expires = "onup"
    else:
        short = "${monitorname} is up at " + ts
        longmsg = "${monitorname} is back up at " + ts + ".\n\n${status}"
        tts_text = "${monitorname} is back up at " + ts + "."
        color = "10D113"
        priority = "Normal"
        audio = service_monitor_default_audio_entries(tts_text, SERVICE_MONITOR_UP_AUDIO_CANDIDATES)
        expires = "0m"
    return {
        "shortmessage": short,
        "longmessage": longmsg,
        "icon": "",
        "color": color,
        "audio": audio,
        "priority": priority,
        "vendor_specific": "",
        "expires": expires,
        "send_all": "0",
        "groups": [],
    }


def service_monitor_draft_defaults():
    return {
        "name": "",
        "monitor_type": "ping",
        "check_interval": str(SERVICE_MONITOR_DEFAULT_INTERVAL),
        "retries": str(SERVICE_MONITOR_DEFAULT_RETRIES),
        "wait_for_up": str(SERVICE_MONITOR_DEFAULT_WAIT_FOR_UP),
        "disabled": "0",
        "host": "",
        "port": "",
        "http_url": "",
        "http_fail_codes": list(SERVICE_MONITOR_HTTP_DEFAULT_FAIL),
        "uk_base_url": "",
        "uk_api_key": "",
        "uk_monitor": "",
        "down_enabled": "0",
        "up_enabled": "0",
        "down": service_monitor_default_message("down"),
        "up": service_monitor_default_message("up"),
    }


def service_monitor_message_from_row(row, direction):
    return {
        "shortmessage": str(row.get(f"{direction}_shortmessage") or ""),
        "longmessage": str(row.get(f"{direction}_longmessage") or ""),
        "icon": str(row.get(f"{direction}_icon") or ""),
        "color": str(row.get(f"{direction}_color") or ""),
        "audio": [a for a in str(row.get(f"{direction}_audio") or "").split(":") if a],
        "priority": str(row.get(f"{direction}_priority") or "Normal"),
        "vendor_specific": str(row.get(f"{direction}_vendor_specific") or ""),
        "expires": str(row.get(f"{direction}_expires") or "manual"),
        "send_all": "1" if int(row.get(f"{direction}_send_all") or 0) else "0",
        "groups": [g for g in str(row.get(f"{direction}_groups") or "").split(".") if g],
    }


def service_monitor_draft_from_row(row):
    draft = service_monitor_draft_defaults()
    draft["name"] = str(row.get("name") or "")
    draft["monitor_type"] = str(row.get("monitor_type") or "ping")
    draft["check_interval"] = str(row.get("check_interval") or SERVICE_MONITOR_DEFAULT_INTERVAL)
    draft["retries"] = str(row.get("retries") if row.get("retries") is not None else SERVICE_MONITOR_DEFAULT_RETRIES)
    draft["wait_for_up"] = str(row.get("wait_for_up") if row.get("wait_for_up") is not None else SERVICE_MONITOR_DEFAULT_WAIT_FOR_UP)
    draft["disabled"] = "1" if int(row.get("disabled") or 0) else "0"
    draft["host"] = str(row.get("host") or "")
    draft["port"] = str(row.get("port")) if int(row.get("port") or 0) else ""
    draft["http_url"] = str(row.get("http_url") or "")
    draft["http_fail_codes"] = service_monitor_parse_fail_codes(row.get("http_fail_codes")) or list(
        SERVICE_MONITOR_HTTP_DEFAULT_FAIL
    )
    draft["uk_base_url"] = str(row.get("uk_base_url") or "")
    draft["uk_api_key"] = str(row.get("uk_api_key") or "")
    draft["uk_monitor"] = str(row.get("uk_monitor") or "")
    for direction in ("down", "up"):
        draft[f"{direction}_enabled"] = "1" if int(row.get(f"{direction}_enabled") or 0) else "0"
        draft[direction] = service_monitor_message_from_row(row, direction)
    return draft


def service_monitor_collect_config(draft, form):
    draft["name"] = str(form.get("name") or "").strip()
    draft["monitor_type"] = str(form.get("monitor_type") or "ping").strip().lower()
    draft["check_interval"] = str(form.get("check_interval") or "").strip()
    draft["retries"] = str(form.get("retries") or "").strip()
    draft["wait_for_up"] = str(form.get("wait_for_up") or "").strip()
    draft["disabled"] = "1" if form.get("disabled") else "0"
    draft["host"] = str(form.get("host") or "").strip()
    draft["port"] = str(form.get("port") or "").strip()
    draft["http_url"] = str(form.get("http_url") or "").strip()
    draft["http_fail_codes"] = service_monitor_parse_fail_codes(form.getlist("http_fail_codes"))
    draft["uk_base_url"] = str(form.get("uk_base_url") or "").strip()
    new_key = str(form.get("uk_api_key") or "").strip()
    if new_key:
        draft["uk_api_key"] = new_key
    draft["uk_monitor"] = str(form.get("uk_monitor") or "").strip()
    draft["down_enabled"] = "1" if form.get("down_enabled") else "0"
    draft["up_enabled"] = "1" if form.get("up_enabled") else "0"
    return draft


def service_monitor_collect_message(form):
    from srv.web.pages.messages.form_common import (
        message_expiration_from_form,
        message_multiline_text,
        resolve_message_icon_value,
        vendor_specific_from_form,
    )

    return {
        "shortmessage": str(form.get("shortmessage") or ""),
        "longmessage": message_multiline_text(form.get("longmessage") or ""),
        "icon": resolve_message_icon_value(form.get("icon") or ""),
        "color": str(form.get("color") or "").strip().lstrip("#").upper(),
        "audio": [v.strip() for v in form.getlist("audio_files[]") if v.strip()],
        "priority": str(form.get("priority") or "Normal"),
        "vendor_specific": vendor_specific_from_form(form),
        "expires": message_expiration_from_form(form),
        "send_all": "1" if form.get("send_all") else "0",
        "groups": [str(g).strip() for g in form.getlist("groups[]") if str(g).strip()],
    }


def service_monitor_row_values_from_draft(draft):
    name = str(draft.get("name") or "").strip()
    if not name:
        raise ValueError("Enter a name for this monitor.")
    monitor_type = service_monitor_clean_type(draft.get("monitor_type"))
    interval = service_monitor_clean_interval(draft.get("check_interval"))
    retries = service_monitor_clean_retries(draft.get("retries"))
    wait_for_up = service_monitor_clean_wait_for_up(draft.get("wait_for_up"))
    disabled = 1 if str(draft.get("disabled")) == "1" else 0
    host = str(draft.get("host") or "").strip()
    port = 0
    http_url = ""
    http_fail = []
    uk_base = ""
    uk_key = str(draft.get("uk_api_key") or "")
    uk_mon = ""
    if monitor_type == "ping":
        if not host:
            raise ValueError("Enter a host to ping.")
    elif monitor_type == "tcp":
        if not host:
            raise ValueError("Enter a host to probe.")
        port = service_monitor_clean_port(draft.get("port"))
    elif monitor_type == "sip":
        if not host:
            raise ValueError("Enter a SIP host.")
        port = service_monitor_clean_port(draft.get("port") or 5060)
    elif monitor_type == "http":
        http_url = str(draft.get("http_url") or "").strip()
        if not http_url:
            raise ValueError("Enter a URL to check.")
        http_fail = service_monitor_parse_fail_codes(draft.get("http_fail_codes")) or list(
            SERVICE_MONITOR_HTTP_DEFAULT_FAIL
        )
    elif monitor_type == "uptimekuma":
        uk_base = str(draft.get("uk_base_url") or "").strip()
        if not uk_base:
            raise ValueError("Enter the Uptime Kuma base URL.")
        if not uk_key:
            raise ValueError("Enter the Uptime Kuma API key.")
        uk_mon = str(draft.get("uk_monitor") or "").strip()
        if not uk_mon:
            raise ValueError("Select an Uptime Kuma monitor.")
    values = {
        "name": name,
        "monitor_type": monitor_type,
        "check_interval": interval,
        "retries": retries,
        "wait_for_up": wait_for_up,
        "disabled": disabled,
        "host": host,
        "port": port,
        "http_url": http_url,
        "http_fail_codes": json.dumps(http_fail),
        "uk_base_url": uk_base,
        "uk_api_key": uk_key,
        "uk_monitor": uk_mon,
    }
    for direction in ("down", "up"):
        enabled = 1 if str(draft.get(f"{direction}_enabled")) == "1" else 0
        msg = draft.get(direction) or {}
        send_all = False
        groups = []
        if enabled:
            has_text = bool(
                str(msg.get("shortmessage") or "").strip() or str(msg.get("longmessage") or "").strip()
            )
            has_audio = bool(msg.get("audio"))
            if not has_text and not has_audio:
                raise ValueError(f"Add a message or audio for the monitor {direction} notification.")
            send_all = str(msg.get("send_all")) == "1"
            groups = list(msg.get("groups") or [])
            if not send_all and not groups:
                raise ValueError(f"Choose recipients for the monitor {direction} notification.")
        else:
            msg = {}
        values[f"{direction}_enabled"] = enabled
        values[f"{direction}_send_all"] = 1 if (enabled and send_all) else 0
        values[f"{direction}_groups"] = ".".join(groups)
        values[f"{direction}_shortmessage"] = str(msg.get("shortmessage") or "")
        values[f"{direction}_longmessage"] = str(msg.get("longmessage") or "")
        values[f"{direction}_icon"] = str(msg.get("icon") or "")
        values[f"{direction}_color"] = str(msg.get("color") or "")
        values[f"{direction}_audio"] = ":".join(msg.get("audio") or [])
        values[f"{direction}_priority"] = str(msg.get("priority") or "Normal")
        values[f"{direction}_vendor_specific"] = str(msg.get("vendor_specific") or "")
        values[f"{direction}_expires"] = str(msg.get("expires") or "manual")
    return values


def service_monitor_insert(values):
    ensure_servicemonitor_schema()
    cols = list(values.keys())
    sip_execute(
        f"INSERT INTO `{SERVICE_MONITOR_TABLE}` ({', '.join('`' + c + '`' for c in cols)}, `last_state`) "
        f"VALUES ({', '.join(['%s'] * len(cols))}, 'unchecked')",
        tuple(values[c] for c in cols),
    )


def service_monitor_update(row_id, values):
    ensure_servicemonitor_schema()
    cols = list(values.keys())
    sets = ", ".join(f"`{c}`=%s" for c in cols)
    sip_execute(
        f"UPDATE `{SERVICE_MONITOR_TABLE}` SET {sets}, `last_state`='unchecked', `last_checked`=NULL, `last_error`=NULL, `fail_count`=0 WHERE id=%s",
        tuple(values[c] for c in cols) + (row_id,),
    )


def service_monitor_message_summary(draft, direction):
    msg = draft.get(direction) or {}
    has_text = bool(str(msg.get("shortmessage") or "").strip() or str(msg.get("longmessage") or "").strip())
    has_audio = bool(msg.get("audio"))
    if not has_text and not has_audio:
        return "No message configured yet."
    if str(msg.get("send_all")) == "1":
        who = "All recipients"
    else:
        count = len(msg.get("groups") or [])
        who = f"{count} group(s)" if count else "No recipients selected"
    return f"Message ready \u2014 {who}."


SERVICE_MONITOR_CONFIG_SCRIPT = """
<style>
.sm-section{border:1px solid #e6e8eb;border-radius:6px;padding:12px;display:grid;gap:10px}
.sm-msg{display:block}
.sm-editor-frame{width:100%;border:1px solid #e6e8eb;border-radius:6px;background:#fff;display:block;min-height:220px}
@media(prefers-color-scheme:dark){.sm-section{border-color:#333}.sm-editor-frame{border-color:#333;background:#171717}}
html.sm-modal-active,html.sm-modal-active body{background:transparent !important;overflow:hidden !important;}
html.sm-modal-active body>*{visibility:hidden !important;}
html.sm-modal-active .sm-editor-frame.sm-active{visibility:visible !important;}
</style>
<script>
(function(){
  var typeSel = document.getElementById('smType');
  function show(sel, on){ document.querySelectorAll(sel).forEach(function(el){ el.style.display = on ? '' : 'none'; }); }
  function syncType(){
    var t = typeSel.value;
    show('.sm-host', t==='ping'||t==='tcp'||t==='sip');
    show('.sm-port', t==='tcp'||t==='sip');
    show('.sm-http', t==='http');
    show('.sm-uk', t==='uptimekuma');
  }
  typeSel.addEventListener('change', syncType);
  syncType();
  function syncMsg(cbId, blockId){
    var cb = document.getElementById(cbId), block = document.getElementById(blockId);
    if (cb && block) block.style.display = cb.checked ? '' : 'none';
  }
  var de = document.getElementById('smDownEnabled'), ue = document.getElementById('smUpEnabled');
  if (de) de.addEventListener('change', function(){ syncMsg('smDownEnabled','smDownBlock'); });
  if (ue) ue.addEventListener('change', function(){ syncMsg('smUpEnabled','smUpBlock'); });
  syncMsg('smDownEnabled','smDownBlock');
  syncMsg('smUpEnabled','smUpBlock');
  var loadBtn = document.getElementById('smUkLoad');
  if (loadBtn) loadBtn.addEventListener('click', function(){
    var status = document.getElementById('smUkStatus');
    var base = document.getElementById('smUkBase').value;
    var key = document.getElementById('smUkKey').value;
    var epEl = document.querySelector('input[name="endpoint_id"]');
    status.textContent = 'Loading...';
    var body = new URLSearchParams();
    body.append('base_url', base);
    body.append('api_key', key);
    if (epEl) body.append('endpoint_id', epEl.value);
    fetch('/admin/servicemonitor-kuma-monitors', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body: body.toString(), credentials:'same-origin'})
      .then(function(r){ return r.json(); })
      .then(function(data){
        if (!data.ok){ status.textContent = data.error || 'Failed to load monitors.'; return; }
        var sel = document.getElementById('smUkMonitor');
        var current = sel.value;
        sel.innerHTML = '';
        if (!data.monitors.length){ status.textContent = 'No monitors found.'; }
        else { status.textContent = data.monitors.length + ' monitor(s) loaded.'; }
        data.monitors.forEach(function(name){
          var opt = document.createElement('option');
          opt.value = name; opt.textContent = name;
          if (name === current) opt.selected = true;
          sel.appendChild(opt);
        });
      })
      .catch(function(){ status.textContent = 'Could not reach Uptime Kuma.'; });
  });
  // Relay editor iframe heights so each inline editor sizes to its content.
  window.addEventListener('message', function(ev){
    var d = ev.data;
    if (!d) return;
    if (d.type === 'sm-modal-overlay'){
      var frames2 = document.querySelectorAll('.sm-editor-frame');
      for (var j=0;j<frames2.length;j++){
        if (frames2[j].contentWindow === ev.source){
          var f = frames2[j];
          if (d.open){
            if (f.getAttribute('data-sm-prev') === null){
              f.setAttribute('data-sm-prev', f.getAttribute('style') || '');
            }
            document.documentElement.classList.add('sm-modal-active');
            f.classList.add('sm-active');
            // Use viewport units so the frame re-measures automatically as each
            // ancestor iframe expands (the overlay relay runs inner->outer, so a
            // pixel snapshot taken here would capture the pre-expansion size).
            f.style.position='fixed'; f.style.top='0'; f.style.left='0';
            f.style.width='100vw'; f.style.height='100vh';
            f.style.right='auto'; f.style.bottom='auto'; f.style.margin='0';
            f.style.border='0'; f.style.borderRadius='0';
            f.style.background='transparent'; f.style.minHeight='0';
            f.style.zIndex='2147483000';
          } else {
            f.setAttribute('style', f.getAttribute('data-sm-prev') || '');
            f.removeAttribute('data-sm-prev');
            f.classList.remove('sm-active');
            document.documentElement.classList.remove('sm-modal-active');
          }
          break;
        }
      }
      try { window.parent.postMessage({type:'sm-modal-overlay', open: d.open}, '*'); } catch(e){}
      return;
    }
    if (d.type !== 'ops-frame-height') return;
    var frames = document.querySelectorAll('.sm-editor-frame');
    for (var i=0;i<frames.length;i++){
      if (frames[i].contentWindow === ev.source){
        if (frames[i].classList.contains('sm-active')) break;
        frames[i].style.height = (Number(d.height) + 4) + 'px';
        break;
      }
    }
  });
  // On save, flush visible editors (persist any unsaved keystrokes) before submit.
  var form = document.getElementById('smForm');
  var submitting = false;
  function visibleEditorFrames(){
    return Array.prototype.filter.call(document.querySelectorAll('.sm-editor-frame'), function(f){
      var block = f.closest('.sm-msg');
      return block && block.style.display !== 'none';
    });
  }
  function flushEditors(done){
    var frames = visibleEditorFrames();
    var i = 0;
    function next(){
      if (i >= frames.length){ done(); return; }
      var f = frames[i++];
      var acked = false;
      function settle(){ if (acked) return; acked = true; clearTimeout(timer); window.removeEventListener('message', onMsg); next(); }
      function onMsg(ev){
        if (!ev.data || ev.data.type !== 'sm-flushed') return;
        if (ev.source && f.contentWindow && ev.source !== f.contentWindow) return;
        settle();
      }
      var timer = setTimeout(settle, 2500);
      window.addEventListener('message', onMsg);
      try { f.contentWindow.postMessage({type:'sm-flush'}, window.location.origin); }
      catch (e) { settle(); }
    }
    next();
  }
  if (form) form.addEventListener('submit', function(ev){
    if (submitting) return;
    ev.preventDefault();
    flushEditors(function(){ submitting = true; form.submit(); });
  });
})();
</script>
"""


def service_monitor_config_html(mode, endpoint_id, token, draft, error):
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    monitor_type = str(draft.get("monitor_type") or "ping")
    type_options = "".join(
        f'<option value="{h(t)}"{" selected" if monitor_type == t else ""}>{h(SERVICE_MONITOR_TYPE_LABELS[t])}</option>'
        for t in SERVICE_MONITOR_TYPES
    )
    fail_selected = {str(x) for x in (draft.get("http_fail_codes") or [])}
    fail_choices = [
        ("noresponse", "No response (connection failed / timeout)"),
        ("4xx", "All 4xx client errors"),
        ("5xx", "All 5xx server errors"),
    ] + [(str(c), f"HTTP {c}") for c in SERVICE_MONITOR_HTTP_CODE_CHOICES]
    fail_rows = "".join(
        f'<label class="md-checkbox-container"><input type="checkbox" name="http_fail_codes" value="{h(value)}"'
        f'{" checked" if value in fail_selected else ""}><span class="md-checkmark"></span>'
        f'<span class="md-checkbox-text">{h(label)}</span></label>'
        for value, label in fail_choices
    )
    interval = h(draft.get("check_interval") or SERVICE_MONITOR_DEFAULT_INTERVAL)
    retries = h(draft.get("retries") if draft.get("retries") not in (None, "") else SERVICE_MONITOR_DEFAULT_RETRIES)
    wait_for_up = h(draft.get("wait_for_up") if draft.get("wait_for_up") not in (None, "") else SERVICE_MONITOR_DEFAULT_WAIT_FOR_UP)
    disabled_checked = " checked" if str(draft.get("disabled")) == "1" else ""
    uk_monitor = str(draft.get("uk_monitor") or "")
    uk_monitor_option = f'<option value="{h(uk_monitor)}" selected>{h(uk_monitor)}</option>' if uk_monitor else ""
    down_checked = " checked" if str(draft.get("down_enabled")) == "1" else ""
    up_checked = " checked" if str(draft.get("up_enabled")) == "1" else ""
    if mode == "edit":
        editor_base = (
            f"/admin/endpoint-action-frame?module=servicemonitor&amp;action=edit"
            f"&amp;id={h(endpoint_id)}&amp;view=editor"
        )
    else:
        editor_base = (
            f"/admin/endpoint-form-frame?module=servicemonitor&amp;type=monitor"
            f"&amp;view=editor&amp;token={h(token)}"
        )
    submit_label = "Save Monitor" if mode == "edit" else "Add Service Monitor"
    api_key_note = "Leave blank to keep the current key." if (mode == "edit" and draft.get("uk_api_key")) else ""
    endpoint_hidden = f'<input type="hidden" name="endpoint_id" value="{h(endpoint_id)}">' if endpoint_id else ""
    body = f"""{error_html}<form method="post" class="grid form-surface" id="smForm">
<input type="hidden" name="token" value="{h(token)}">
{endpoint_hidden}
<div class="row"><label>Name <span style="color:#C62828">*</span></label><input class="control" name="name" value="{h(draft.get("name"))}" required></div>
<div class="row"><label>Monitor Type</label><select class="control short-control" name="monitor_type" id="smType">{type_options}</select></div>
<div class="row sm-field sm-host" id="smHostRow"><label>Host</label><input class="control" name="host" id="smHost" value="{h(draft.get("host"))}" placeholder="ex: 192.168.1.10 or host.example.com"></div>
<div class="row sm-field sm-port" id="smPortRow"><label>Port</label><input class="control short-control" name="port" id="smPort" value="{h(draft.get("port"))}" inputmode="numeric" placeholder="ex: 5060"></div>
<div class="row sm-field sm-http" id="smUrlRow"><label>URL</label><input class="control" name="http_url" id="smUrl" value="{h(draft.get("http_url"))}" placeholder="ex: https://example.com/health"></div>
<div class="row sm-field sm-http" id="smFailRow"><label>Failure conditions</label><details class="dropdown-checklist"><summary>Select failure responses</summary><div class="dropdown-panel">{fail_rows}</div></details><span class="hint">The service is down when a check matches any selected condition.</span></div>
<div class="row sm-field sm-uk" id="smUkBaseRow"><label>Uptime Kuma URL</label><input class="control" name="uk_base_url" id="smUkBase" value="{h(draft.get("uk_base_url"))}" placeholder="ex: https://kuma.example.com"></div>
<div class="row sm-field sm-uk" id="smUkKeyRow"><label>API Key</label><input class="control" type="password" name="uk_api_key" id="smUkKey" value="" placeholder="Uptime Kuma API key" autocomplete="new-password"><span class="hint">{h(api_key_note)}</span></div>
<div class="row sm-field sm-uk" id="smUkLoadRow"><button type="button" class="button" id="smUkLoad">Load monitors</button> <span class="hint" id="smUkStatus"></span></div>
<div class="row sm-field sm-uk" id="smUkMonitorRow"><label>Monitor</label><select class="control" name="uk_monitor" id="smUkMonitor">{uk_monitor_option}</select></div>
<div class="row"><label>Check interval (seconds)</label><input class="control short-control" type="number" name="check_interval" value="{interval}" min="{SERVICE_MONITOR_MIN_INTERVAL}" max="{SERVICE_MONITOR_MAX_INTERVAL}" required></div>
<div class="row"><label>Retries</label><input class="control short-control" type="number" name="retries" value="{retries}" min="{SERVICE_MONITOR_MIN_RETRIES}" max="{SERVICE_MONITOR_MAX_RETRIES}" required><span class="hint">Grace period before marking the monitor offline (0\u2013{SERVICE_MONITOR_MAX_RETRIES} failed checks).</span></div>
<div class="row"><label>Wait for Up (seconds)</label><input class="control short-control" type="number" name="wait_for_up" value="{wait_for_up}" min="{SERVICE_MONITOR_MIN_WAIT_FOR_UP}" max="{SERVICE_MONITOR_MAX_WAIT_FOR_UP}" required><span class="hint">Wait this long after recovery and re-check before sending an up alert (0 = immediate).</span></div>
<label class="md-checkbox-container"><input type="checkbox" name="disabled" value="1"{disabled_checked}><span class="md-checkmark"></span><span class="md-checkbox-text">Disabled \u2014 pause checking and alerts for this monitor</span></label>
<div class="sm-section">
<label class="md-checkbox-container"><input type="checkbox" name="down_enabled" value="1" id="smDownEnabled"{down_checked}><span class="md-checkmark"></span><span class="md-checkbox-text">Monitor down message</span></label>
<div class="sm-msg" id="smDownBlock"><iframe class="sm-editor-frame" data-dir="down" title="Monitor down message" src="{editor_base}&amp;direction=down" scrolling="no"></iframe></div>
</div>
<div class="sm-section">
<label class="md-checkbox-container"><input type="checkbox" name="up_enabled" value="1" id="smUpEnabled"{up_checked}><span class="md-checkmark"></span><span class="md-checkbox-text">Monitor up message</span></label>
<div class="sm-msg" id="smUpBlock"><iframe class="sm-editor-frame" data-dir="up" title="Monitor up message" src="{editor_base}&amp;direction=up" scrolling="no"></iframe></div>
</div>
<button class="button" type="submit" name="action" value="save">{h(submit_label)}</button>
</form>
"""
    return body + SERVICE_MONITOR_CONFIG_SCRIPT


def service_monitor_message_editor_html(direction, token, draft, user, error):
    from srv.web import app as webapp
    from srv.web.pages.messages.form_common import (
        MESSAGE_FORM_SCRIPT,
        MESSAGE_FORM_STYLE,
        audio_transfer_html,
        message_expiration_field_html,
        message_icon_field_html,
        message_variable_field_html,
        message_variable_guide_html,
        vendor_specific_editor_html,
    )

    msg = draft.get(direction) or {}
    short = str(msg.get("shortmessage") or "")
    longmsg = str(msg.get("longmessage") or "")
    icon = str(msg.get("icon") or "")
    color = str(msg.get("color") or "")
    audio_sel = list(msg.get("audio") or [])
    priority = str(msg.get("priority") or "Normal")
    expires = str(msg.get("expires") or "manual")
    vendor = str(msg.get("vendor_specific") or "")
    send_all = str(msg.get("send_all")) == "1"
    sel_groups = {str(g) for g in (msg.get("groups") or [])}
    try:
        group_rows = webapp.filter_group_rows_for_user(
            user, webapp.query_all("SELECT id, name FROM `groups` ORDER BY name ASC, id ASC")
        )
    except Exception:
        group_rows = []
    rows_html = ""
    for group in group_rows:
        gid = str(group.get("id") or "").strip()
        if not gid:
            continue
        rows_html += (
            f'<label class="md-checkbox-container"><input type="checkbox" name="groups[]" value="{h(gid)}" '
            f'class="group-checkbox"{" disabled" if send_all else ""}{" checked" if gid in sel_groups else ""}>'
            f'<span class="md-checkmark"></span><span class="text">{h(group.get("name") or gid)}</span></label>'
        )
    if not rows_html:
        rows_html = '<p class="help-text">No groups are available.</p>'
    try:
        expiration_messages = webapp.query_all(
            "SELECT messageid, name FROM messages ORDER BY name ASC, messageid ASC"
        )
    except Exception:
        expiration_messages = []
    transfer = audio_transfer_html(webapp.audio_files(), audio_sel)
    vendor_html = vendor_specific_editor_html(current_vendor_specific=vendor, context={"mode": "message_custom"})
    color_default = "#" + color if re.fullmatch(r"[A-Fa-f0-9]{6}", color or "") else "#000000"
    variable_fields = message_variable_field_html(
        "shortmessage",
        "Short Message",
        f'<input type="text" name="shortmessage" id="shortmessage" class="form-control" value="{h(short)}">',
        "",
    ) + message_variable_field_html(
        "longmessage",
        "Long Message",
        f'<textarea name="longmessage" id="longmessage" class="form-control textarea-long" rows="7" wrap="soft">{h(longmsg)}</textarea>',
        "",
    )
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    direction_js = json.dumps(direction)
    priority_options = "".join(
        f'<option value="{h(p)}"{" selected" if priority == p else ""}>{h(p)}</option>'
        for p in SERVICE_MONITOR_PRIORITIES
    )
    return f"""<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet"/>
<style>{MESSAGE_FORM_STYLE}
body,html{{overflow:visible;height:auto;}}
.btn-send{{background:#2E7D32;color:#FFF;border:none;padding:10px 16px;border-radius:6px;font-size:14px;cursor:pointer;}}
.btn-cancel{{background:none;border:none;color:#777;cursor:pointer;font-size:14px;text-decoration:underline;}}
.custom-form-actions{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:10px;}}
/* Modal pass-through: while a picker is open, make this editor iframe a
   transparent window that shows only the modal + backdrop, so the popup
   appears centred over the whole app page (like the Messages tab) with the
   page dimmed behind it rather than being clipped inside the iframe. */
html.sm-modal-active,html.sm-modal-active body{{background:transparent !important;overflow:hidden !important;}}
html.sm-modal-active body>*{{visibility:hidden !important;}}
html.sm-modal-active .message-variable-modal.open,
html.sm-modal-active .message-variable-modal-backdrop.open,
html.sm-modal-active .audio-block-modal.open,
html.sm-modal-active .audio-block-modal-backdrop.open,
html.sm-modal-active .message-icon-picker-modal.open,
html.sm-modal-active .message-icon-picker-backdrop.open{{visibility:visible !important;}}
</style>
<div style="padding:16px;box-sizing:border-box;">
<div class="info-card">
{error_html}
<form method="post" id="smMsgForm">
<input type="hidden" name="token" value="{h(token)}">
<input type="hidden" name="direction" value="{h(direction)}">
<div class="form-group">
<label class="main-label">Recipients</label>
<div class="checkbox-row">
<label class="md-checkbox-container"><input type="checkbox" name="send_all" id="send_all" value="1" onchange="toggleRecipients()"{" checked" if send_all else ""}><span class="md-checkmark"></span><span class="text" style="font-weight:bold;color:#1976D2;">All Recipients</span></label>
{rows_html}
</div>
</div>
<div id="audio-fields" class="form-group">
<label class="main-label">Audio</label>
{transfer}
</div>
<div id="visual-fields">
{variable_fields}
{message_icon_field_html(icon)}
<div class="form-group"><label class="main-label">Color</label>
<div class="color-picker-container">
<input type="color" id="colorPicker" value="{h(color_default)}" class="color-picker-input">
<input type="text" name="color" id="colorHex" class="form-control" style="width:150px;" placeholder="000000" maxlength="6" value="{h(color)}">
</div></div>
</div>
{message_expiration_field_html(expiration_messages, expires, include_on_up=(direction == "down"))}
<div class="form-group"><label class="main-label" for="priority">Priority</label>
<select name="priority" id="priority" class="form-control">{priority_options}</select></div>
{vendor_html}
</form>
</div>
{message_variable_guide_html('<button type="button" class="message-variable-choice" onclick="openVariableWizard(&#39;monitorname&#39;)">Monitor Name</button><button type="button" id="messageVariableStatusChoice" class="message-variable-choice" onclick="openVariableWizard(&#39;status&#39;)">Status</button>')}
</div>
<script>{MESSAGE_FORM_SCRIPT}
function toggleRecipients(){{
  var all = document.getElementById('send_all').checked;
  document.querySelectorAll('.group-checkbox').forEach(function(cb){{ cb.disabled = all; if (all) cb.checked = false; }});
}}
toggleRecipients();
(function(){{
  var DIR = {direction_js};
  var form = document.getElementById('smMsgForm');
  if (!form) return;
  function saveNow(){{
    var fd = new FormData(form);
    fd.set('action', 'autosave_message_' + DIR);
    return fetch(window.location.href, {{method:'POST', body: fd, credentials:'same-origin'}});
  }}
  // Persist only when the parent requests a flush (on Save). This avoids a
  // POST per keystroke, which would otherwise exhaust the web POST rate limit
  // and cause the final Save to be rejected.
  window.addEventListener('message', function(ev){{
    if (!ev.data || ev.data.type !== 'sm-flush') return;
    function ack(){{ if (window.parent && window.parent !== window) window.parent.postMessage({{type:'sm-flushed', direction: DIR}}, window.location.origin); }}
    saveNow().then(ack, ack);
  }});
}})();
(function(){{
  // Hoist editor modals to the top page: when any modal opens inside this
  // content-sized iframe, notify ancestors so the frame chain expands to the
  // top viewport (position:fixed modals otherwise render off-screen).
  var SELECTORS = ['.message-variable-modal','.message-variable-modal-backdrop','.audio-block-modal','.audio-block-modal-backdrop','.message-icon-picker-modal','.message-icon-picker-backdrop'];
  var query = SELECTORS.map(function(s){{ return s + '.open'; }}).join(',');
  var lastOpen = false;
  function check(){{
    var open = !!document.querySelector(query);
    if (open === lastOpen) return;
    lastOpen = open;
    try {{ document.documentElement.classList.toggle('sm-modal-active', open); }} catch(e){{}}
    try {{ window.parent.postMessage({{type:'sm-modal-overlay', open: open}}, '*'); }} catch(e){{}}
  }}
  var obs = new MutationObserver(check);
  obs.observe(document.documentElement, {{attributes:true, subtree:true, attributeFilter:['class']}});
}})();
</script>"""


class BuiltinServiceMonitorWeb:
    def forms(self):
        return {
            "monitor": {"label": SERVICE_MONITOR_NAME, "description": SERVICE_MONITOR_DESCRIPTION},
        }

    def _handle(self, mode, endpoint_id, request, page, user):
        ensure_servicemonitor_schema()
        row = None
        row_id = None
        if mode == "edit":
            kind, _, row_id = str(endpoint_id or "").partition("-")
            if kind != "monitor" or not row_id.isdigit():
                return page("Endpoint Action", "<h1>Invalid endpoint action</h1>", "endpoints", user, status=400)
            row = service_monitor_row(row_id)
            if not row:
                return page("Endpoint Action", "<h1>Endpoint not found</h1>", "endpoints", user, status=404)
            token = f"edit-monitor-{row_id}"
        view = str(request.args.get("view") or "").strip().lower()
        direction = str(request.args.get("direction") or "down").strip().lower()
        if direction not in ("down", "up"):
            direction = "down"
        if request.method == "GET":
            if view == "editor":
                if mode == "new":
                    token = str(request.args.get("token") or "").strip() or uuid.uuid4().hex
                    draft = service_monitor_load_draft(token)
                    if draft is None:
                        draft = service_monitor_draft_defaults()
                        service_monitor_save_draft(token, draft)
                else:
                    draft = service_monitor_load_draft(token)
                    if draft is None:
                        draft = service_monitor_draft_from_row(row)
                        service_monitor_save_draft(token, draft)
                body = service_monitor_message_editor_html(direction, token, draft, user, "")
                return page(SERVICE_MONITOR_NAME, body, "endpoints", user)
            if mode == "edit":
                draft = service_monitor_draft_from_row(row)
            else:
                token = uuid.uuid4().hex
                draft = service_monitor_draft_defaults()
            service_monitor_save_draft(token, draft)
            body = service_monitor_config_html(mode, endpoint_id, token, draft, "")
            return page(SERVICE_MONITOR_NAME, sip_form_frame(body), "endpoints", user)
        form = request.form
        if mode == "new":
            token = str(form.get("token") or "").strip() or uuid.uuid4().hex
        draft = service_monitor_load_draft(token)
        if draft is None:
            draft = service_monitor_draft_from_row(row) if mode == "edit" else service_monitor_draft_defaults()
        action = str(form.get("action") or "save").strip()
        if action in ("autosave_message_down", "autosave_message_up"):
            msg_dir = "down" if action.endswith("down") else "up"
            draft[msg_dir] = service_monitor_collect_message(form)
            service_monitor_save_draft(token, draft)
            return page(SERVICE_MONITOR_NAME, "ok", "endpoints", user)
        service_monitor_collect_config(draft, form)
        service_monitor_save_draft(token, draft)
        try:
            values = service_monitor_row_values_from_draft(draft)
        except ValueError as exc:
            body = service_monitor_config_html(mode, endpoint_id, token, draft, str(exc))
            return page(SERVICE_MONITOR_NAME, sip_form_frame(body), "endpoints", user)
        if mode == "edit":
            service_monitor_update(row_id, values)
        else:
            service_monitor_insert(values)
        service_monitor_delete_draft(token)
        return page("Endpoint Saved", SERVICE_MONITOR_SUCCESS_REDIRECT, "endpoints", user)

    def render_form(self, form_type, request, conn_factory, page, user):
        ensure_servicemonitor_schema()
        if form_type not in self.forms():
            return page("Endpoint Form", "<h1>Endpoint form not found</h1>", "endpoints", user, status=404)
        return self._handle("new", None, request, page, user)

    def render_action(self, action, endpoint_id, request, conn_factory, page, user):
        ensure_servicemonitor_schema()
        kind, _, row_id = str(endpoint_id or "").partition("-")
        if action not in {"edit", "delete"} or kind != "monitor" or not row_id.isdigit():
            return page("Endpoint Action", "<h1>Invalid endpoint action</h1>", "endpoints", user, status=400)
        row = service_monitor_row(row_id)
        if not row:
            return page("Endpoint Action", "<h1>Endpoint not found</h1>", "endpoints", user, status=404)
        if action == "delete":
            if request.method == "POST":
                sip_execute(f"DELETE FROM `{SERVICE_MONITOR_TABLE}` WHERE id=%s", (row_id,))
                service_monitor_delete_draft(f"edit-monitor-{row_id}")
                return page("Endpoint Saved", SERVICE_MONITOR_SUCCESS_REDIRECT, "endpoints", user)
            body = (
                '<form method="post" class="grid surface">'
                f'<p class="meta">Delete {h(row.get("name") or endpoint_id)}?</p>'
                '<button class="danger" type="submit">Delete Endpoint</button></form>'
            )
            return page("Endpoint Action", sip_form_frame(body), "endpoints", user)
        return self._handle("edit", endpoint_id, request, page, user)

    def render_settings(self, request, conn_factory, page, user):
        return page(
            SERVICE_MONITOR_NAME,
            "<p>No additional settings are required for Service Monitor.</p>",
            "endpoints",
            user,
        )


def service_monitor_all_group_rows():
    try:
        return sip_query_all("SELECT `id`, `name` FROM `groups` ORDER BY `name` ASC, `id` ASC")
    except Exception as exc:
        log(f"service monitor group list error: {exc}")
        return []


def service_monitor_all_groups_value():
    ids = [str(row.get("id") or "").strip() for row in service_monitor_all_group_rows() if str(row.get("id") or "").strip()]
    return ".".join(ids)


class BuiltinServiceMonitorModule:
    def __init__(self):
        self.stop_event = threading.Event()
        self.inflight = set()
        self.inflight_lock = threading.Lock()
        # Monitors that have been confirmed ONLINE at least once in THIS process.
        # A "went down" alert is only ever sent for a monitor in this set, so a
        # stale DB last_state, a skipped boot reset, retries=0, or a flaky first
        # probe can never fire a false "server is down" broadcast on startup.
        self.seen_online = set()
        self.seen_online_lock = threading.Lock()
        self.boot_monotonic = time.monotonic()
        try:
            ensure_servicemonitor_schema()
            # On boot every monitor is "Unchecked" until it has been probed once.
            sip_execute(
                f"UPDATE `{SERVICE_MONITOR_TABLE}` SET `last_state`='unchecked', `last_checked`=NULL, `fail_count`=0"
            )
        except Exception as exc:
            log(f"service monitor init error: {exc}")
        self._clear_stale_monitor_broadcasts()
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()

    def _clear_stale_monitor_broadcasts(self):
        # Neutralise any Service Monitor broadcast left in the active store by a
        # previous run. The broadcast watcher re-delivers every record whose
        # delivery is '' or 'pending', and a monitor down message (manual / "on
        # monitor up" expiration) never time-expires — so without this a stale
        # "went down" message is re-broadcast on EVERY server restart. On boot
        # the poll loop hasn't run yet, so any monitor-sender broadcast still
        # sitting here is stale and must be stopped + expired. Fresh alerts are
        # re-created by the poll loop as needed.
        try:
            from broadcasts import list_active_broadcasts, mark_active_broadcast_delivery

            removed = 0
            for record in list_active_broadcasts(limit=5000):
                if str(record.get("sender") or "").strip() != SERVICE_MONITOR_NAME:
                    continue
                rid = str(record.get("id") or "").strip()
                if not rid:
                    continue
                request_active_broadcast_stop(rid)
                mark_active_broadcast_delivery(rid, "expired")
                removed += 1
            try:
                sip_execute(f"UPDATE `{SERVICE_MONITOR_TABLE}` SET `down_broadcast_id`=NULL")
            except Exception as exc:
                log(f"service monitor boot down-broadcast clear error: {exc}")
            if removed:
                log(f"service monitor boot cleanup removed {removed} stale monitor broadcast(s)")
        except Exception as exc:
            log(f"service monitor boot cleanup error: {exc}")

    def get_endpoint_status(self):
        return get_service_monitor_endpoint_status()

    def handle_dispatch(self, action, stream_id, msg_id, sub_targets, metadata=None):
        # Service Monitor is an input-only module; it never receives broadcasts.
        if action in {"prepare_audio", "prepare_livepage"}:
            mark_ready(SERVICE_MONITOR_MODULE, stream_id)

    def _poll_loop(self):
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                log(f"service monitor poll error: {exc}")
            self.stop_event.wait(2.0)

    def _tick(self):
        try:
            rows = service_monitor_rows()
        except Exception as exc:
            log(f"service monitor rows error: {exc}")
            return
        now = datetime.now()
        for row in rows:
            if int(row.get("disabled") or 0):
                continue
            row_id = str(row.get("id"))
            interval = int(row.get("check_interval") or SERVICE_MONITOR_DEFAULT_INTERVAL)
            last_checked = row.get("last_checked")
            due = True
            if last_checked:
                try:
                    lc = last_checked if isinstance(last_checked, datetime) else datetime.fromisoformat(str(last_checked))
                    due = (now - lc).total_seconds() >= interval
                except Exception:
                    due = True
            if not due:
                continue
            with self.inflight_lock:
                if row_id in self.inflight:
                    continue
                self.inflight.add(row_id)
            threading.Thread(target=self._run_check, args=(dict(row),), daemon=True).start()

    def _store_state(self, row_id, state, checked_at, detail, fail_count):
        try:
            sip_execute(
                f"UPDATE `{SERVICE_MONITOR_TABLE}` SET `last_state`=%s, `last_checked`=%s, "
                f"`last_error`=%s, `fail_count`=%s WHERE id=%s",
                (state, checked_at, str(detail or "")[:2000], int(fail_count), row_id),
            )
        except Exception as exc:
            log(f"service monitor state update error id={row_id}: {exc}")

    def _store_down_broadcast(self, row_id, broadcast_id):
        try:
            sip_execute(
                f"UPDATE `{SERVICE_MONITOR_TABLE}` SET `down_broadcast_id`=%s WHERE id=%s",
                (str(broadcast_id) if broadcast_id else None, row_id),
            )
        except Exception as exc:
            log(f"service monitor down-broadcast store error id={row_id}: {exc}")

    def _maybe_expire_down_broadcast(self, row):
        # When the down message's expiration is "On monitor up", tear down the
        # active down broadcast now that the monitor has recovered. If it is
        # still mid-broadcast we must first STOP the live playback (so the down
        # audio/message is cut off on the endpoints), then expire the record,
        # and only after that does the caller send the "up" message.
        row_id = str(row.get("id"))
        broadcast_id = str(row.get("down_broadcast_id") or "").strip()
        expires = str(row.get("down_expires") or "")
        tokens = {t.strip().lower() for t in expires.split("|") if t.strip()}
        if broadcast_id and "onup" in tokens:
            try:
                from broadcasts import list_active_broadcasts, mark_active_broadcast_delivery

                related_ids = [broadcast_id]
                for record in list_active_broadcasts(limit=5000):
                    if str(record.get("source_broadcast_id") or "").strip() == broadcast_id:
                        child_id = str(record.get("id") or "").strip()
                        if child_id and child_id not in related_ids:
                            related_ids.append(child_id)
                # 1) Signal the live delivery loops to stop feeding audio. They
                #    poll active_broadcast_stop_requested (~every 0.5s) and break.
                for token in related_ids:
                    request_active_broadcast_stop(token)
                # 2) Wait (bounded) for any in-progress delivery of these ids to
                #    actually finish before removing the records. We must NOT
                #    expire first: expiring deletes the stop-control row, which
                #    would clear the stop flag before the delivery loop ever
                #    observed it, so the down audio would keep playing to the end.
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    with broadcast_delivery_lock:
                        still_running = any(t in broadcast_delivery_ids for t in related_ids)
                    if not still_running:
                        break
                    time.sleep(0.1)
                # 3) Now that playback has stopped, expire (remove) the records.
                for token in related_ids:
                    mark_active_broadcast_delivery(token, "expired")
                log(f"service monitor down message stopped+auto-expired id={row_id} broadcast={broadcast_id}")
            except Exception as exc:
                log(f"service monitor down-broadcast expire error id={row_id}: {exc}")
        if broadcast_id:
            self._store_down_broadcast(row_id, None)

    def _run_check(self, row):
        row_id = str(row.get("id"))
        try:
            old_state = str(row.get("last_state") or "unchecked").strip().lower()
            fail_count = int(row.get("fail_count") or 0)
            retries = int(row.get("retries") or 0)
            wait_for_up = int(row.get("wait_for_up") or 0)
            try:
                is_up, detail, down_state = service_monitor_check_row(row)
            except Exception as exc:
                is_up, detail, down_state = False, str(exc) or "Probe error", "offline"
            now = datetime.now()
            detail_text = str(detail or "")[:2000]

            if is_up:
                # Recovery debounce: if we were down and a Wait for Up window is set,
                # wait it out and re-probe. Only recover if the service is still up.
                if old_state in ("offline", "kuma_down") and wait_for_up > 0:
                    self.stop_event.wait(wait_for_up)
                    if self.stop_event.is_set():
                        return
                    try:
                        is_up2, detail2, _down2 = service_monitor_check_row(row)
                    except Exception as exc:
                        is_up2, detail2, _down2 = False, str(exc) or "Probe error", "offline"
                    if not is_up2:
                        # Flapped back down during the wait: stay down, no messages.
                        self._store_state(row_id, old_state, datetime.now(), str(detail2 or detail_text), fail_count)
                        return
                    detail_text = str(detail2 or detail_text)[:2000]
                self._store_state(row_id, "online", datetime.now(), detail_text, 0)
                with self.seen_online_lock:
                    self.seen_online.add(row_id)
                if old_state in ("offline", "kuma_down"):
                    fresh = service_monitor_row(row_id) or row
                    # Only announce recovery if we actually announced the
                    # outage. A monitor can reach "offline" silently on boot
                    # (the startup race that turns unchecked -> offline without
                    # an alert); firing "back up" there would be a false
                    # recovery broadcast on every startup.
                    had_down_alert = bool(str(fresh.get("down_broadcast_id") or "").strip())
                    self._maybe_expire_down_broadcast(fresh)
                    if had_down_alert and not int(fresh.get("disabled") or 0):
                        self.send_monitor_message(fresh, "up", detail_text)
                return

            # Probe failed.
            fail_count += 1
            # Startup warmup: absorb failures for a short window after boot
            # without latching offline or alerting. We hold the CURRENT state
            # (never advancing an online/unchecked monitor to offline), so the
            # down "edge" is preserved — a real outage that begins during the
            # window still alerts once the window elapses.
            if old_state in ("online", "unchecked") and (time.monotonic() - self.boot_monotonic) < SERVICE_MONITOR_STARTUP_GRACE_SECONDS:
                self._store_state(row_id, old_state, now, detail_text, fail_count)
                return
            if fail_count <= retries and old_state in ("online", "unchecked"):
                # Still inside the retries grace period — hold the current state, no alert.
                self._store_state(row_id, old_state, now, detail_text, fail_count)
                return
            new_state = down_state or "offline"
            already_down = old_state in ("offline", "kuma_down")
            self._store_state(row_id, new_state, now, detail_text, fail_count)
            # Fire the "went down" alert exactly once, on the TRANSITION into a
            # down state (edge-triggered) — never again while it stays down, or
            # every failed check would re-alert forever.
            #
            # It is additionally gated on the monitor having been confirmed
            # ONLINE at least once in THIS process (seen_online). That gate can't
            # be fooled by a stale DB last_state carried over from a previous
            # run, a skipped boot reset, retries=0, or a flaky first probe while
            # the server is still starting up — all of which otherwise blast a
            # false "server is down" broadcast on startup.
            if already_down:
                return
            with self.seen_online_lock:
                confirmed_online = row_id in self.seen_online
            if confirmed_online:
                fresh = service_monitor_row(row_id) or row
                if not int(fresh.get("disabled") or 0):
                    broadcast_id = self.send_monitor_message(fresh, "down", detail_text)
                    self._store_down_broadcast(row_id, broadcast_id)
        finally:
            with self.inflight_lock:
                self.inflight.discard(row_id)

    def send_monitor_message(self, row, direction, status_message=""):
        if not int(row.get(f"{direction}_enabled") or 0):
            return
        shortmessage = str(row.get(f"{direction}_shortmessage") or "")
        longmessage = str(row.get(f"{direction}_longmessage") or "")
        audio_value = str(row.get(f"{direction}_audio") or "")
        has_text = bool(shortmessage.strip() or longmessage.strip())
        has_audio = bool(audio_value.strip())
        if not has_text and not has_audio:
            log(f"service monitor {direction} message skipped id={row.get('id')}: no content")
            return
        if int(row.get(f"{direction}_send_all") or 0):
            groups_value = service_monitor_all_groups_value()
        else:
            groups_value = str(row.get(f"{direction}_groups") or "").strip()
        if not groups_value or groups_value == "0":
            log(f"service monitor {direction} message skipped id={row.get('id')}: no recipients")
            return
        msg_type = "text+audio" if (has_text and has_audio) else ("audio" if has_audio else "text")
        monitor_name = str(row.get("name") or SERVICE_MONITOR_NAME)
        status_text = str(status_message or "").strip()
        if not status_text:
            status_text = "Service is responding." if direction == "up" else "Service is not responding."
        values = {
            "name": monitor_name,
            "shortmessage": shortmessage if has_text else "",
            "longmessage": longmessage if has_text else "",
            "icon": str(row.get(f"{direction}_icon") or "") if has_text else "",
            "color": str(row.get(f"{direction}_color") or "") if has_text else "",
            "audio": audio_value,
            "priority": str(row.get(f"{direction}_priority") or "Normal"),
            "expires": str(row.get(f"{direction}_expires") or "manual"),
            "type": msg_type,
            "sender": SERVICE_MONITOR_NAME,
            "vendor_specific": str(row.get(f"{direction}_vendor_specific") or ""),
            "monitorname": monitor_name,
            "status": status_text,
        }
        try:
            ensure_message_vendor_schema()
            from broadcasts import (
                create_custom_broadcast,
                expire_any_message_rule_broadcasts,
                expire_message_rule_broadcasts,
            )

            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    groups = validate_group_value(cur, groups_value)
                    broadcast_id, expires_rule = create_custom_broadcast(
                        cur, values, groups=groups, sender=SERVICE_MONITOR_NAME
                    )
                    if str(values.get("priority") or "").strip().lower() != "emergency":
                        expire_message_rule_broadcasts(cur, expires_rule, [broadcast_id], trigger_groups=groups)
                        expire_any_message_rule_broadcasts(cur, [broadcast_id], trigger_groups=groups)
                conn.commit()
                log(f"service monitor {direction} message sent id={row.get('id')} broadcast={broadcast_id}")
                return broadcast_id
            finally:
                conn.close()
        except Exception as exc:
            log(f"service monitor {direction} message send error id={row.get('id')}: {exc}")
        return None

    def receive_audio(self, chunk, stream_id):
        pass

    def end_stream(self, stream_id):
        pass

    def shutdown(self):
        self.stop_event.set()


def ensure_builtin_modules_loaded():
    global siptrunks_runtime, multicast_rtp_runtime, http_request_runtime, service_monitor_runtime
    if siptrunks_runtime is None:
        siptrunks_runtime = BuiltinSipTrunksRuntime()
    with loaded_modules_lock:
        loaded_modules["siptrunks"] = siptrunks_runtime
        module_load_errors.pop("siptrunks", None)
    if multicast_rtp_runtime is None:
        multicast_rtp_runtime = BuiltinMulticastRTPModule()
    with loaded_modules_lock:
        loaded_modules[MULTICAST_RTP_MODULE] = multicast_rtp_runtime
        module_load_errors.pop(MULTICAST_RTP_MODULE, None)
    if http_request_runtime is None:
        http_request_runtime = BuiltinHttpRequestModule()
    with loaded_modules_lock:
        loaded_modules[HTTP_REQUEST_MODULE] = http_request_runtime
        module_load_errors.pop(HTTP_REQUEST_MODULE, None)
    if service_monitor_runtime is None:
        service_monitor_runtime = BuiltinServiceMonitorModule()
    with loaded_modules_lock:
        loaded_modules[SERVICE_MONITOR_MODULE] = service_monitor_runtime
        module_load_errors.pop(SERVICE_MONITOR_MODULE, None)


def load_endpoint_web_module(module, missing_ok=False):
    if module == "siptrunks":
        return BuiltinSipTrunksWeb()
    if module == MULTICAST_RTP_MODULE:
        return BuiltinMulticastRTPWeb()
    if module == HTTP_REQUEST_MODULE:
        return BuiltinHttpRequestWeb()
    if module == SERVICE_MONITOR_MODULE:
        return BuiltinServiceMonitorWeb()
    if not safe_module_name(module):
        if missing_ok:
            return None
        raise FileNotFoundError("invalid endpoint module")
    package = discover_endpoint_packages(extract_if_trusted=True).get(module)
    if not package or not package.get("trusted"):
        if missing_ok:
            return None
        raise FileNotFoundError("endpoint module is not loadable")
    web_path = Path(package.get("web_path") or "") / "web.py"
    if not web_path.is_file():
        if missing_ok:
            return None
        raise FileNotFoundError("endpoint module has no web.py")
    spec = importlib.util.spec_from_file_location(f"endpoint_module_web_{module}", web_path)
    if spec is None or spec.loader is None:
        if missing_ok:
            return None
        raise FileNotFoundError("endpoint module web.py is not importable")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"endpoint_module_web_{module}"] = mod
    spec.loader.exec_module(mod)
    return mod


def endpoint_module_web_root(module):
    if module == "siptrunks":
        return None
    if module == HTTP_REQUEST_MODULE:
        return None
    package = discover_endpoint_packages(extract_if_trusted=True).get(module)
    if not package or not package.get("trusted"):
        return None
    return Path(package.get("web_path") or "")


def normalize_target_entry(target):
    if isinstance(target, (list, tuple)):
        return [str(item or "").strip() for item in target if str(item or "").strip()]
    text = str(target or "").strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except Exception:
            parsed = None
        if isinstance(parsed, (list, tuple)):
            return [str(item or "").strip() for item in parsed if str(item or "").strip()]
    return [text]


def is_8k_ulaw(file_path):
    try:
        with wave.open(file_path, "rb") as wav_file:
            n_channels, _sample_width, framerate, _n_frames, compression, _ = wav_file.getparams()
            return framerate == 8000 and compression == "ULAW" and n_channels == 1
    except Exception:
        return False


def resolve_audio_file(audio_file):
    candidate = Path(audio_file)
    if candidate.is_file():
        return str(candidate)
    raw = str(audio_file or "").replace("\0", "").replace("\\", "/").split("/")[-1].strip()
    candidates = []
    if raw:
        candidates.append(raw)
        secure = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._")
        if secure and secure not in candidates:
            candidates.append(secure)
    search_roots = [Path("/var/lib/openpagingserver/assets/"), BASE_DIR / "assets", BASE_DIR / "sip" / "audio"]
    for root in search_roots:
        for name in candidates:
            path = root / name
            if path.exists():
                return str(path)
        lowered = {name.lower() for name in candidates}
        try:
            for path in root.iterdir():
                if path.is_file() and path.name.lower() in lowered:
                    return str(path)
        except OSError:
            continue
    return None


def audio_frames(audio_files_str):
    for audio_file in split_audio_entries(audio_files_str):
        if not audio_file:
            continue
        if audio_file.startswith("%silence(") and audio_file.endswith(")"):
            try:
                duration = float(audio_file[9:-1])
            except ValueError:
                continue
            for _ in range(int(duration * 8000 / 160)):
                yield b"\xff" * 160
            continue
        tts_payload = decode_tts_token(audio_file)
        if tts_payload:
            yield from iter_tts_ffmpeg_chunks(
                tts_payload,
                ["-ar", str(8000), "-ac", "1", "-f", "mulaw", "-flush_packets", "1", "pipe:1"],
                chunk_size=160,
                pad_byte=b"\xff",
            )
            continue
        file_path = resolve_audio_file(audio_file)
        if not file_path:
            continue
        if is_8k_ulaw(file_path):
            with open(file_path, "rb") as handle:
                while True:
                    chunk = handle.read(160)
                    if not chunk:
                        break
                    yield chunk.ljust(160, b"\xff")
            continue
        ffmpeg = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "quiet",
                "-i",
                file_path,
                "-ar",
                str(8000),
                "-ac",
                "1",
                "-f",
                "mulaw",
                "-flush_packets",
                "1",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        while True:
            chunk = ffmpeg.stdout.read(160)
            if not chunk:
                break
            yield chunk.ljust(160, b"\xff")
        ffmpeg.stdout.close()
        ffmpeg.wait()


def fetch_broadcast(broadcast_id):
    return fetch_active_broadcast(broadcast_id)


class BroadcastRecordingWriter:
    def __init__(self, broadcast_id):
        self.broadcast_id = str(broadcast_id or "").strip() or uuid.uuid4().hex
        self.runtime_dir = RUNTIME_DIR / "broadcast-recordings"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime_dir / f"broadcast-{self.broadcast_id}.wav"
        self.wave_file = wave.open(str(self.path), "wb")
        self.wave_file.setnchannels(1)
        self.wave_file.setsampwidth(2)
        self.wave_file.setframerate(8000)
        self.closed = False

    def write_frame(self, frame):
        if self.closed:
            return
        payload = bytes(frame or b"")
        if not payload:
            return
        self.wave_file.writeframesraw(
            b"".join(map(ULAW_TO_PCM16LE_TABLE.__getitem__, payload))
        )

    def close(self):
        if self.closed:
            return
        try:
            self.wave_file.close()
        finally:
            self.closed = True


def broadcast_target_tokens(record):
    explicit = (record or {}).get("explicit_targets")
    exclude = (record or {}).get("exclude_targets")
    targets = []
    seen = set()
    if isinstance(explicit, (list, tuple, set)):
        source = explicit
    elif explicit:
        source = str(explicit).replace(",", " ").split()
    else:
        source = resolve_group_targets((record or {}).get("groups"))
    for item in source:
        token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            targets.append(token)
    excluded = set()
    if isinstance(exclude, (list, tuple, set)):
        excluded = {str(item or "").strip() for item in exclude if str(item or "").strip()}
    elif exclude:
        excluded = {token for token in str(exclude).replace(",", " ").split() if token}
    if not excluded:
        return targets
    return [token for token in targets if token not in excluded]


def hydrate_active_record_from_history(record):
    hydrated = dict(record or {})
    broadcast_id = str(hydrated.get("id") or "").strip()
    if not broadcast_id:
        return hydrated
    conn = pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM broadcasts")
            columns = {row["Field"] for row in cur.fetchall() if row.get("Field")}
            wanted = [
                "id",
                "name",
                "shortmessage",
                "longmessage",
                "icon",
                "color",
                "vendor_specific",
                "type",
                "expires",
                "issued",
                "groups",
                "image",
                "audio",
                "sender",
                "priority",
                "delivery",
                "template_id",
                "expires_rule",
            ]
            selected = [column for column in wanted if column in columns]
            if not selected:
                return hydrated
            select_sql = ", ".join(f"`{column}`" for column in selected)
            cur.execute(f"SELECT {select_sql} FROM broadcasts WHERE id=%s LIMIT 1", (broadcast_id,))
            history_row = cur.fetchone()
            if not history_row:
                return hydrated
            for key, value in history_row.items():
                if value is not None:
                    hydrated[key] = value
            return hydrated
    finally:
        conn.close()


def fetch_pending_broadcast_ids(limit=20):
    return list_pending_active_broadcast_ids(limit=limit, exclude_sender="sendmsgd")


def claim_broadcast_delivery(broadcast_id, stream_id):
    return claim_active_broadcast_delivery(broadcast_id, stream_id)


def mark_broadcast_history_delivery(broadcast_id, status):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE broadcasts SET delivery=%s WHERE id=%s", (status, broadcast_id))
        conn.commit()
    finally:
        conn.close()


def resolve_group_targets(group_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            group_ids = []
            for gid in str(group_id or "").split("."):
                token = gid.strip()
                if token and token not in group_ids:
                    group_ids.append(token)
            rows = fetch_group_rows(cur, None if "0" in group_ids else group_ids)
            targets = regular_group_targets(rows)
            if targets:
                return targets
            if "0" in group_ids:
                target_list = set()
                for module_name in output_module_names():
                    target_list.add(f"{module_name}/all")
                return sorted(target_list)
            return []
    finally:
        conn.close()


def enabled_module_dirs():
    discovered = discover_modules()
    states = module_enabled_states(discovered)
    return {module_name for module_name, is_enabled in states.items() if is_enabled and module_name in discovered}


def normalize_module_name(value):
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def module_type_tokens(value):
    normalized = str(value or "").strip().lower()
    if not normalized:
        return set()
    return set(re.findall(r"input|output", normalized))


def module_type_has_input(value):
    return "input" in module_type_tokens(value)


def module_type_has_output(value):
    return "output" in module_type_tokens(value)


def resolve_module_name(module_name, discovered=None):
    if discovered is None:
        discovered = discover_modules()
    if module_name in discovered:
        return module_name
    wanted = normalize_module_name(module_name)
    for candidate in discovered:
        normalized = normalize_module_name(candidate)
        if normalized == wanted or wanted.startswith(normalized) or normalized.startswith(wanted):
            return candidate
    return module_name


def module_info_type(module_name):
    if module_name == "siptrunks":
        return "Input+Output"
    if module_name == MULTICAST_RTP_MODULE:
        return "Output"
    if module_name == HTTP_REQUEST_MODULE:
        return "Output"
    if module_name == SERVICE_MONITOR_MODULE:
        return "Input"
    discovered = discover_modules()
    entry = discovered.get(module_name)
    if entry is None:
        return ""
    info = module_info_from_entry(module_name, entry)
    return str(info.get("input_type") or "")


def module_is_input_capable(module_name):
    return module_type_has_input(module_info_type(module_name))


def endpoint_is_output_capable(endpoint):
    if not isinstance(endpoint, dict):
        return False
    if endpoint.get("output_capable") is False:
        return False
    direction = str(endpoint.get("direction") or endpoint.get("input_type") or "").lower()
    if "output" in direction:
        return True
    capabilities = endpoint.get("capabilities")
    if isinstance(capabilities, list):
        lowered = {str(item).strip().lower() for item in capabilities}
        if "output" in lowered or "bells" in lowered:
            return True
    return bool(endpoint.get("bell_capable"))


def module_is_output_capable(module_name, mod=None):
    module_type = module_info_type(module_name).lower()
    return module_type_has_output(module_type)


def output_module_names():
    with loaded_modules_lock:
        modules_snapshot = list(loaded_modules.items())
    names = []
    for module_name, mod in modules_snapshot:
        if module_name == MULTICAST_RTP_MODULE and multicast_rtp_endpoint_count() <= 0:
            continue
        if module_name == HTTP_REQUEST_MODULE and http_request_endpoint_count() <= 0:
            continue
        if module_is_output_capable(module_name, mod):
            names.append(module_name)
    return names


def discover_modules():
    discovered = {}
    for module_name, package in discover_endpoint_packages(extract_if_trusted=True).items():
        if not package.get("trusted"):
            continue
        entry = Path(package["payload_path"]) / "index.py"
        if entry.is_file():
            discovered[module_name] = entry
    return discovered


def endpoint_module_registry_columns(cur):
    try:
        cur.execute("SHOW COLUMNS FROM endpointmodulesloaded")
        return {row[0] for row in cur.fetchall()}
    except Exception:
        return set()


def ensure_module_registry_table():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS endpointmodulesloaded ("
                "`dir` VARCHAR(100) NOT NULL, "
                "enabled VARCHAR(10) NOT NULL DEFAULT 'true', "
                "`tables` TEXT DEFAULT NULL, "
                "package_path TEXT DEFAULT NULL, "
                "trusted VARCHAR(10) NOT NULL DEFAULT 'false', "
                "signature_state VARCHAR(32) NOT NULL DEFAULT 'unsigned', "
                "signer VARCHAR(255) DEFAULT NULL, "
                "load_error TEXT DEFAULT NULL, "
                "manifest_json LONGTEXT DEFAULT NULL, "
                "PRIMARY KEY (`dir`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
            columns = endpoint_module_registry_columns(cur)
            additions = {
                "tables": "`tables` TEXT DEFAULT NULL",
                "package_path": "package_path TEXT DEFAULT NULL",
                "trusted": "trusted VARCHAR(10) NOT NULL DEFAULT 'false'",
                "signature_state": "signature_state VARCHAR(32) NOT NULL DEFAULT 'unsigned'",
                "signer": "signer VARCHAR(255) DEFAULT NULL",
                "load_error": "load_error TEXT DEFAULT NULL",
                "manifest_json": "manifest_json LONGTEXT DEFAULT NULL",
            }
            for column, sql in additions.items():
                if column not in columns:
                    cur.execute(f"ALTER TABLE endpointmodulesloaded ADD COLUMN {sql}")
        conn.commit()
    finally:
        conn.close()


def upsert_module_package_registry(packages=None):
    packages = packages or discover_endpoint_packages(extract_if_trusted=False)
    ensure_module_registry_table()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for module_name, package in packages.items():
                manifest = package.get("manifest") or {}
                verification = package.get("verification") or {}
                cur.execute("SELECT enabled, `tables` FROM endpointmodulesloaded WHERE `dir`=%s", (module_name,))
                existing = cur.fetchone()
                enabled = existing[0] if existing else ("true" if package.get("trusted") else "false")
                tables = existing[1] if existing else ""
                cur.execute(
                    "INSERT INTO endpointmodulesloaded "
                    "(`dir`, enabled, `tables`, package_path, trusted, signature_state, signer, load_error, manifest_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE "
                    "package_path=VALUES(package_path), trusted=VALUES(trusted), "
                    "signature_state=VALUES(signature_state), signer=VALUES(signer), "
                    "load_error=VALUES(load_error), manifest_json=VALUES(manifest_json)",
                    (
                        module_name,
                        enabled,
                        tables,
                        str(package.get("bundle_path") or ""),
                        "true" if package.get("trusted") else "false",
                        verification.get("signature_state") or "unsigned",
                        verification.get("organization") or "",
                        package.get("load_error") or "",
                        json.dumps(manifest, sort_keys=True),
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def module_enabled_states(discovered=None):
    if discovered is None:
        discovered = discover_modules()
    upsert_module_package_registry()
    ensure_module_registry_table()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT `dir`, enabled FROM endpointmodulesloaded")
            rows = cur.fetchall()
    finally:
        conn.close()
    states = {}
    for row in rows:
        module_name = resolve_module_name(row[0], discovered)
        if module_name:
            states[module_name] = str(row[1] or "").strip().lower() == "true"
    if not rows:
        for module_name in discovered:
            states[module_name] = True
    else:
        for module_name in discovered:
            states.setdefault(module_name, False)
    return states


def make_thirdparty_preexec(record, original_preexec=None):
    def preexec():
        os.initgroups(record.pw_name, record.pw_gid)
        os.setgid(record.pw_gid)
        os.setuid(record.pw_uid)
        if original_preexec is not None:
            original_preexec()

    return preexec


def prepare_thirdparty_popen_kwargs(kwargs, record):
    prepared = dict(kwargs)
    original_preexec = prepared.get("preexec_fn")
    prepared["preexec_fn"] = make_thirdparty_preexec(record, original_preexec)
    env = prepared.get("env")
    prepared_env = dict(os.environ if env is None else env)
    prepared_env.setdefault("USER", record.pw_name)
    prepared_env.setdefault("LOGNAME", record.pw_name)
    if getattr(record, "pw_dir", None):
        prepared_env.setdefault("HOME", record.pw_dir)
    prepared["env"] = prepared_env
    return prepared


def endpoint_module_popen(module_name, args, **kwargs):
    record = resolve_thirdparty_user_record(f"endpoint module {module_name}")
    if record is None:
        return subprocess.Popen(args, **kwargs)
    try:
        return subprocess.Popen(args, **prepare_thirdparty_popen_kwargs(kwargs, record))
    except Exception as exc:
        log_thirdparty_warning(
            f"thirdparty-popen-{module_name}-{record.pw_name}",
            f"Unable to start endpoint module process for {module_name} as THIRDPARTY_USER={record.pw_name!r}: {exc}; using the main OPS user",
        )
        return subprocess.Popen(args, **kwargs)


def endpoint_module_run(module_name, args, **kwargs):
    record = resolve_thirdparty_user_record(f"endpoint module {module_name}")
    if record is None:
        return subprocess.run(args, **kwargs)
    try:
        return subprocess.run(args, **prepare_thirdparty_popen_kwargs(kwargs, record))
    except Exception as exc:
        log_thirdparty_warning(
            f"thirdparty-run-{module_name}-{record.pw_name}",
            f"Unable to run endpoint module process for {module_name} as THIRDPARTY_USER={record.pw_name!r}: {exc}; using the main OPS user",
        )
        return subprocess.run(args, **kwargs)


class EndpointModuleCoreProxy:
    def __init__(self, module_name, parent_core):
        self.module_name = module_name
        self.parent_core = parent_core

    def log(self, msg):
        module_log(self.module_name, msg)
        if self.parent_core is not None and hasattr(self.parent_core, "log"):
            self.parent_core.log(f"[endpoint:{self.module_name}] {msg}")

    def request_table(self, logical_name, create_sql=None):
        return request_module_table(self.module_name, logical_name, create_sql)

    def system_info(self, key):
        return endpoint_system_info(key)

    def thirdparty_user(self):
        return configured_thirdparty_user()

    def popen(self, args, **kwargs):
        return endpoint_module_popen(self.module_name, args, **kwargs)

    def run(self, args, **kwargs):
        return endpoint_module_run(self.module_name, args, **kwargs)

    def forward_multicast_packet(self, address, port, payload, family=None, ttl=None):
        return forward_multicast_packet(payload, address, port, family=family, ttl=ttl)

    def send_message(self, message_id, group_id, sender_id=None, sender=None, priority=None, vendor_specific=None):
        return input_module_send_message(
            self.module_name,
            message_id,
            group_id,
            sender_id=sender_id,
            sender=sender,
            priority=priority,
            vendor_specific=vendor_specific,
        )

    def send_custom_message(self, group_id, **values):
        return input_module_send_custom_message(self.module_name, group_id, values)


def module_log(module_name, msg):
    if not DEBUG:
        return
    safe_name = package_module_name(module_name)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        target = MODULE_LOG_DIR / safe_name / "module.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def module_owned_tables(module_name):
    ensure_module_registry_table()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT `tables` FROM endpointmodulesloaded WHERE `dir`=%s", (module_name,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return []
    return [item.strip() for item in str(row[0]).split(",") if item.strip()]


def set_module_owned_tables(module_name, tables):
    clean_tables = []
    for table in tables:
        table = str(table or "").strip()
        if table.startswith("endpoints-") and table not in clean_tables:
            clean_tables.append(table)
    ensure_module_registry_table()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE endpointmodulesloaded SET `tables`=%s WHERE `dir`=%s",
                (",".join(clean_tables), module_name),
            )
        conn.commit()
    finally:
        conn.close()


def request_module_table(module_name, logical_name, create_sql=None):
    logical = re.sub(r"[^A-Za-z0-9_-]+", "-", str(logical_name or "").strip()).strip("-")
    if not logical:
        raise ValueError("table name is required")
    real_table = logical if logical.startswith("endpoints-") else f"endpoints-{logical}"
    owned = module_owned_tables(module_name)
    if real_table not in owned:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT `dir` FROM endpointmodulesloaded WHERE FIND_IN_SET(%s, `tables`) AND `dir`<>%s LIMIT 1",
                    (real_table, module_name),
                )
                if cur.fetchone():
                    raise ValueError(f"table {logical} is already owned by another module")
            if create_sql:
                with conn.cursor() as cur:
                    cur.execute(str(create_sql).replace(f"`{logical}`", f"`{real_table}`"))
            conn.commit()
        finally:
            conn.close()
        owned.append(real_table)
        set_module_owned_tables(module_name, owned)
    return logical


def endpoint_system_info(key):
    key = str(key or "").strip().lower()
    allowed_settings = {
        "product_name": "productname",
        "productname": "productname",
        "site_name": "sitename",
        "sitename": "sitename",
    }
    parameter = allowed_settings.get(key)
    if not parameter:
        return ""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM systemsettings WHERE parameter=%s LIMIT 1", (parameter,))
            row = cur.fetchone()
            return row[0] if row else ""
    finally:
        conn.close()


def ensure_message_vendor_schema():
    global message_vendor_schema_ready
    if message_vendor_schema_ready:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM messages")
            message_columns = {row[0] for row in cur.fetchall()}
            if "vendor_specific" not in message_columns:
                cur.execute("ALTER TABLE messages ADD COLUMN vendor_specific TEXT DEFAULT NULL")
            cur.execute("SHOW COLUMNS FROM messages LIKE 'audio'")
            message_audio = cur.fetchone()
            if message_audio and "text" not in str(message_audio[1] or "").lower():
                cur.execute("ALTER TABLE messages MODIFY COLUMN audio TEXT DEFAULT NULL")
            cur.execute("SHOW COLUMNS FROM broadcasts")
            broadcast_columns = {row[0] for row in cur.fetchall()}
            if "vendor_specific" not in broadcast_columns:
                cur.execute("ALTER TABLE broadcasts ADD COLUMN vendor_specific TEXT DEFAULT NULL")
            else:
                cur.execute("ALTER TABLE broadcasts MODIFY COLUMN vendor_specific TEXT DEFAULT NULL")
            cur.execute("SHOW COLUMNS FROM broadcasts LIKE 'audio'")
            broadcast_audio = cur.fetchone()
            if broadcast_audio and "text" not in str(broadcast_audio[1] or "").lower():
                cur.execute("ALTER TABLE broadcasts MODIFY COLUMN audio TEXT DEFAULT NULL")
        conn.commit()
        message_vendor_schema_ready = True
    finally:
        conn.close()


def clean_group_value(value):
    raw = value if isinstance(value, (list, tuple, set)) else str(value or "").replace(",", ".").split(".")
    parts = []
    for item in raw:
        for part in str(item or "").replace(",", ".").split("."):
            part = part.strip()
            if part and part not in parts:
                parts.append(part)
    return ".".join(parts)


def validate_group_value(cursor, value):
    groups = clean_group_value(value)
    if not groups:
        raise ValueError("group_id is required")
    if groups == "0":
        return groups
    for group_id in groups.split("."):
        cursor.execute("SELECT 1 FROM `groups` WHERE id=%s LIMIT 1", (group_id,))
        if cursor.fetchone() is None:
            raise ValueError(f"group {group_id} was not found")
    return groups


def resolve_sender_value(cursor, sender_id=None, sender=None):
    sender = str(sender or "").strip()
    if sender:
        return sender[:100]
    sender_id = str(sender_id or "").strip()
    if sender_id:
        cursor.execute("SELECT username FROM users WHERE id=%s LIMIT 1", (sender_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError("sender user was not found")
        if isinstance(row, dict):
            return str(row.get("username") or f"user:{sender_id}")[:100]
        return str(row[0] or f"user:{sender_id}")[:100]
    return "Endpoint Module"


def validate_message_priority(priority):
    if priority in (None, ""):
        return None
    priority = str(priority).strip()
    if priority not in VALID_MESSAGE_PRIORITIES:
        raise ValueError("priority must be Low, Normal, High, or Emergency")
    return priority


def vendor_specific_for_module(module_name, value):
    if value in (None, ""):
        return None
    from broadcasts import parse_vendor_specific, serialize_vendor_specific

    if isinstance(value, dict):
        parsed = parse_vendor_specific(value)
        if module_name in parsed:
            return serialize_vendor_specific(parsed)
    else:
        parsed = parse_vendor_specific(value)
        if parsed and module_name in parsed:
            return serialize_vendor_specific(parsed)
    return serialize_vendor_specific({module_name: value})


def ensure_module_can_send(module_name):
    if not module_is_input_capable(module_name):
        raise PermissionError("module type must be Input or Input+Output to send messages")


def int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def input_rate_limit_exceeded(scope, key, limit, window_seconds):
    if limit <= 0 or window_seconds <= 0:
        return False, 0
    now = time.monotonic()
    bucket_key = (scope, str(key or "unknown"))
    with input_rate_limit_lock:
        bucket = input_rate_limit_buckets.setdefault(bucket_key, deque())
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(window_seconds - (now - bucket[0])))
            return True, retry_after
        bucket.append(now)
    return False, 0


def enforce_input_module_send_rate_limit(module_name):
    if str(os.getenv("OPS_INPUT_MODULE_RATE_LIMIT_ENABLE", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return
    clean_module = str(module_name or "unknown").strip() or "unknown"
    checks = [
        (
            "input-module-send-minute",
            clean_module,
            int_env("OPS_INPUT_MODULE_SEND_RATE_LIMIT_PER_MINUTE", 30),
            60,
        ),
        (
            "input-module-send-hour",
            clean_module,
            int_env("OPS_INPUT_MODULE_SEND_RATE_LIMIT_PER_HOUR", 300),
            3600,
        ),
        (
            "input-module-send-global-minute",
            "all",
            int_env("OPS_INPUT_MODULE_GLOBAL_SEND_RATE_LIMIT_PER_MINUTE", 180),
            60,
        ),
    ]
    for scope, key, limit, window_seconds in checks:
        limited, retry_after = input_rate_limit_exceeded(scope, key, limit, window_seconds)
        if limited:
            raise RateLimitExceeded(retry_after)


def input_module_send_message(module_name, message_id, group_id, sender_id=None, sender=None, priority=None, vendor_specific=None):
    ensure_module_can_send(module_name)
    enforce_input_module_send_rate_limit(module_name)
    ensure_message_vendor_schema()
    from broadcasts import (
        create_broadcast_from_template,
        expire_any_message_rule_broadcasts,
        expire_broadcasts_triggered_by_template,
        expire_message_rule_broadcasts,
        fetch_template,
    )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            groups = validate_group_value(cur, group_id)
            sender_value = resolve_sender_value(cur, sender_id=sender_id, sender=sender)
            template = fetch_template(cur, message_id)
            if not template:
                raise ValueError("message was not found")
            overrides = {}
            priority_value = validate_message_priority(priority)
            if priority_value:
                overrides["priority"] = priority_value
            vendor_value = vendor_specific_for_module(module_name, vendor_specific)
            if vendor_value is not None:
                overrides["vendor_specific"] = vendor_value
            broadcast_id, expires_rule = create_broadcast_from_template(
                cur,
                template,
                groups,
                sender_value,
                overrides=overrides or None,
            )
            trigger_priority = (overrides or {}).get("priority") or template.get("priority")
            if str(trigger_priority or "").strip().lower() != "emergency":
                expire_message_rule_broadcasts(
                    cur,
                    expires_rule,
                    [broadcast_id],
                    trigger_groups=groups,
                )
                expire_broadcasts_triggered_by_template(
                    cur,
                    message_id,
                    [broadcast_id],
                    trigger_groups=groups,
                )
        conn.commit()
        return broadcast_id
    finally:
        conn.close()


def input_module_send_custom_message(module_name, group_id, values):
    ensure_module_can_send(module_name)
    enforce_input_module_send_rate_limit(module_name)
    ensure_message_vendor_schema()
    from broadcasts import create_custom_broadcast, expire_any_message_rule_broadcasts, expire_message_rule_broadcasts

    values = dict(values or {})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            groups = validate_group_value(cur, group_id)
            sender_value = resolve_sender_value(
                cur,
                sender_id=values.pop("sender_id", None) or values.pop("user_id", None),
                sender=values.pop("sender", None),
            )
            priority_value = validate_message_priority(values.get("priority") or "Normal")
            values["priority"] = priority_value or "Normal"
            values["name"] = values.get("name") or "Custom message"
            if "vendor_specific" in values:
                values["vendor_specific"] = vendor_specific_for_module(module_name, values.get("vendor_specific")) or ""
            elif "vendor_parameters" in values:
                values["vendor_specific"] = vendor_specific_for_module(module_name, values.pop("vendor_parameters")) or ""
            broadcast_id, expires_rule = create_custom_broadcast(cur, values, groups=groups, sender=sender_value)
            trigger_priority = values.get("priority")
            if str(trigger_priority or "").strip().lower() != "emergency":
                expire_message_rule_broadcasts(
                    cur,
                    expires_rule,
                    [broadcast_id],
                    trigger_groups=groups,
                )
                expire_any_message_rule_broadcasts(
                    cur,
                    [broadcast_id],
                    trigger_groups=groups,
                )
        conn.commit()
        return broadcast_id
    finally:
        conn.close()


def apply_module_install_sql(module_name, entry):
    install_sql = Path(entry).parent / "install.sql"
    if not install_sql.is_file():
        return
    sql = install_sql.read_text(encoding="utf-8", errors="ignore")
    tables = module_tables_from_install_sql(sql)
    if tables:
        owned = module_owned_tables(module_name)
        for table in tables:
            if table not in owned:
                owned.append(table)
        set_module_owned_tables(module_name, owned)


def load_module(module_dir, entry):
    spec_name = f"endpoint_module_{module_dir.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(spec_name, entry)
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = mod
    apply_module_install_sql(module_dir, entry)
    spec.loader.exec_module(mod)
    if hasattr(mod, "init"):
        mod.init(EndpointModuleCoreProxy(module_dir, core))
    with loaded_modules_lock:
        loaded_modules[module_dir] = mod
        module_load_errors.pop(module_dir, None)
    with module_status_lock:
        module_status_cache.pop(module_dir, None)
        module_status_tasks.pop(module_dir, None)
    log(f"load_module {module_dir}")


def mark_module_load_error(module_dir, exc):
    with loaded_modules_lock:
        module_load_errors[module_dir] = str(exc)
    with module_status_lock:
        module_status_cache.pop(module_dir, None)
        module_status_tasks.pop(module_dir, None)
    log(f"load_module error {module_dir}: {exc}")


def unload_module(module_dir):
    with loaded_modules_lock:
        mod = loaded_modules.get(module_dir)
    if mod is None:
        return
    if hasattr(mod, "shutdown"):
        mod.shutdown()
    with loaded_modules_lock:
        loaded_modules.pop(module_dir, None)
        module_load_errors.pop(module_dir, None)
    with module_status_lock:
        module_status_cache.pop(module_dir, None)
        module_status_tasks.pop(module_dir, None)
    log(f"unload_module {module_dir}")


def sync_modules():
    ensure_builtin_modules_loaded()
    enabled = enabled_module_dirs()
    discovered = discover_modules()
    log(f"sync_modules enabled={sorted(enabled)} discovered={sorted(discovered)}")
    for module_dir in enabled:
        with loaded_modules_lock:
            already_loaded = module_dir in loaded_modules
        if not already_loaded and module_dir in discovered:
            try:
                load_module(module_dir, discovered[module_dir])
            except Exception as exc:
                mark_module_load_error(module_dir, exc)
                continue
    with loaded_modules_lock:
        loaded_names = list(loaded_modules.keys())
    for module_dir in loaded_names:
        if module_dir in {"siptrunks", MULTICAST_RTP_MODULE, HTTP_REQUEST_MODULE, SERVICE_MONITOR_MODULE}:
            continue
        if module_dir not in enabled:
            try:
                unload_module(module_dir)
            except Exception as exc:
                log(f"unload_module error {module_dir}: {exc}")


def shutdown_all():
    global server_socket
    broadcast_watcher_stop.set()
    close_multicast_gateway_source()
    for module_dir in list(loaded_modules.keys()):
        unload_module(module_dir)
    if server_socket is not None:
        try:
            server_socket.close()
        except OSError:
            pass
        server_socket = None
    if supports_unix_sockets():
        try:
            if ENDPOINT_IPC_SOCKET_PATH.exists() or ENDPOINT_IPC_SOCKET_PATH.is_socket():
                ENDPOINT_IPC_SOCKET_PATH.unlink()
        except OSError:
            pass


def normalize_targets(targets):
    target_map = {}
    with loaded_modules_lock:
        module_names = list(loaded_modules.keys())
    discovered = discover_modules()
    page_debug(
        f"normalize_targets_start raw={targets} loaded={module_names} discovered={sorted(discovered.keys())}"
    )
    for raw_target in targets:
        for target in normalize_target_entry(raw_target):
            target = target.strip()
            if not target:
                continue
            if "/" in target:
                module_name, sub_target = target.split("/", 1)
                module_name = resolve_module_name(module_name, discovered)
                if module_name in loaded_modules and module_is_output_capable(module_name):
                    target_map.setdefault(module_name, [])
                    if sub_target not in target_map[module_name]:
                        target_map[module_name].append(sub_target)
                continue
            for module_name in output_module_names():
                target_map.setdefault(module_name, [])
                if target not in target_map[module_name]:
                    target_map[module_name].append(target)
    log(f"normalize_targets raw={targets} mapped={target_map}")
    page_debug(f"normalize_targets_done raw={targets} mapped={target_map}")
    return target_map


def dispatch_to_module(module_name, action, stream_id, msg_id, sub_targets, metadata=None):
    with loaded_modules_lock:
        mod = loaded_modules.get(module_name)
    if mod is None:
        log(f"dispatch_to_module missing module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        page_debug(f"dispatch_to_module_missing module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        mark_failed(module_name, stream_id)
        return
    try:
        log(f"dispatch_to_module start module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        page_debug(f"dispatch_to_module_start module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        if hasattr(mod, "handle_dispatch"):
            mod.handle_dispatch(action, stream_id, msg_id, list(sub_targets), metadata)
        elif hasattr(mod, "api_endpoint"):
            for sub_target in sub_targets:
                mod.api_endpoint(f"{action} {sub_target} {stream_id} {msg_id}")
        else:
            mark_ready(module_name, stream_id)
        log(f"dispatch_to_module done module={module_name} action={action} stream={stream_id}")
        page_debug(f"dispatch_to_module_done module={module_name} action={action} stream={stream_id}")
    except Exception as exc:
        log(f"dispatch error in {module_name}: {exc}")
        page_debug(f"dispatch_to_module_error module={module_name} action={action} stream={stream_id} error={exc.__class__.__name__}: {exc}")
        mark_failed(module_name, stream_id)


def dispatch(action, stream_id, msg_id, targets, metadata=None):
    target_map = normalize_targets(targets)
    if not target_map:
        log(f"dispatch no_targets action={action} stream={stream_id} msg={msg_id}")
        page_debug(f"dispatch_no_targets action={action} stream={stream_id} msg={msg_id} targets={targets}")
        return {}
    log(f"dispatch action={action} stream={stream_id} msg={msg_id} target_map={target_map}")
    page_debug(f"dispatch_start action={action} stream={stream_id} msg={msg_id} target_map={target_map}")
    for module_name, sub_targets in target_map.items():
        threading.Thread(
            target=dispatch_to_module,
            args=(module_name, action, stream_id, msg_id, tuple(sub_targets), metadata),
            daemon=True,
        ).start()
    return target_map


def create_stream_state(stream_id, target_map):
    state = StreamState(stream_id, target_map)
    if not state.pending_modules:
        state.ready_event.set()
    with stream_states_lock:
        stream_states[stream_id] = state
    log(f"create_stream_state stream={stream_id} pending={sorted(state.pending_modules)}")
    page_debug(f"create_stream_state stream={stream_id} pending={sorted(state.pending_modules)} target_map={target_map}")
    return state


def pop_stream_state(stream_id):
    with stream_states_lock:
        state = stream_states.pop(stream_id, None)
    log(f"pop_stream_state stream={stream_id} found={state is not None}")
    return state


def mark_ready(module_name, stream_id):
    with stream_states_lock:
        state = stream_states.get(stream_id)
    if state is None:
        log(f"mark_ready missing_state module={module_name} stream={stream_id}")
        page_debug(f"mark_ready_missing_state module={module_name} stream={stream_id}")
        return
    state.mark_ready(module_name)
    log(f"mark_ready module={module_name} stream={stream_id} ready={sorted(state.ready_modules)} pending={sorted(state.pending_modules)}")
    page_debug(f"mark_ready module={module_name} stream={stream_id} ready={sorted(state.ready_modules)} pending={sorted(state.pending_modules)}")


def mark_failed(module_name, stream_id):
    with stream_states_lock:
        state = stream_states.get(stream_id)
    if state is None:
        log(f"mark_failed missing_state module={module_name} stream={stream_id}")
        page_debug(f"mark_failed_missing_state module={module_name} stream={stream_id}")
        return
    state.mark_failed(module_name)
    log(f"mark_failed module={module_name} stream={stream_id} failed={sorted(state.failed_modules)} ready={sorted(state.ready_modules)} pending={sorted(state.pending_modules)}")
    page_debug(f"mark_failed module={module_name} stream={stream_id} failed={sorted(state.failed_modules)} ready={sorted(state.ready_modules)} pending={sorted(state.pending_modules)}")


def finish_stream(stream_id):
    with loaded_modules_lock:
        modules_snapshot = list(loaded_modules.items())
    log(f"finish_stream stream={stream_id} modules={[name for name, _ in modules_snapshot]}")
    page_debug(f"finish_stream stream={stream_id} modules={[name for name, _ in modules_snapshot]}")
    for module_name, mod in modules_snapshot:
        if hasattr(mod, "end_stream"):
            try:
                mod.end_stream(stream_id)
            except Exception as exc:
                log(f"end_stream error in {module_name}: {exc}")
    pop_stream_state(stream_id)


def recv_line(conn, limit=65536):
    # Never consume bytes past the newline: audio data can follow the
    # command line on the same socket (PREPARE/PREPARELIVE streams).
    data = bytearray()
    while len(data) < limit:
        try:
            peeked = conn.recv(4096, socket.MSG_PEEK)
        except OSError:
            break
        if not peeked:
            break
        newline_index = peeked.find(b"\n")
        take = newline_index + 1 if newline_index != -1 else len(peeked)
        chunk = conn.recv(take)
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in data:
            break
    line, _, _ = bytes(data).partition(b"\n")
    return line


def send_ipc_json(conn, payload):
    conn.sendall(json.dumps(payload, default=str).encode("utf-8") + b"\n")


def decode_ipc_json_token(token):
    raw = base64.b64decode(str(token or "").encode("ascii"), validate=True)
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("IPC payload is empty")
    return json.loads(text)


def start_ipc_server():
    global server_socket
    server_socket, endpoint = create_endpoint_ipc_server_socket()
    server_socket.listen()
    log(f"ipc_server listening endpoint={endpoint}")
    while True:
        try:
            conn, _ = server_socket.accept()
        except OSError:
            break
        tune_ipc_stream_socket(conn)
        threading.Thread(target=handle_ipc_client, args=(conn,), daemon=True).start()


def pump_ipc_audio_stream(conn, deliver_frame, on_chunk=None, frame_size=SIP_OUTPUT_FRAME_BYTES, pad_final=True):
    pending = bytearray()
    chunk_count = 0
    byte_count = 0
    frame_count = 0
    partial_flushed = False
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunk_count += 1
        byte_count += len(chunk)
        if on_chunk is not None:
            on_chunk(chunk_count, byte_count, len(chunk))
        pending.extend(chunk)
        while len(pending) >= frame_size:
            frame = bytes(pending[:frame_size])
            del pending[:frame_size]
            deliver_frame(frame)
            frame_count += 1
    if pad_final and pending:
        deliver_frame(bytes(pending).ljust(frame_size, b"\xff"))
        frame_count += 1
        partial_flushed = True
    return chunk_count, byte_count, frame_count, partial_flushed


def handle_prepare(conn, parts):
    if len(parts) < 4:
        conn.sendall(b"ERROR\n")
        return
    stream_id = parts[1]
    msg_id = parts[2]
    targets = parts[3:]
    target_map = normalize_targets(targets)
    state = create_stream_state(stream_id, target_map)
    dispatch("prepare_audio", stream_id, msg_id, targets)
    ready = state.ready_event.wait(10.0)
    log(f"handle_prepare waited stream={stream_id} ready={ready}")
    conn.sendall(b"OK\n")
    def deliver_frame(frame):
        for module_name in target_map:
            with loaded_modules_lock:
                mod = loaded_modules.get(module_name)
            if mod and hasattr(mod, "receive_audio"):
                try:
                    mod.receive_audio(frame, stream_id)
                except Exception as exc:
                    log(f"receive_audio error in {module_name}: {exc}")
    def log_chunk(chunk_count, byte_count, last_chunk_size):
        log(
            f"handle_prepare audio_chunk stream={stream_id} chunks={chunk_count} "
            f"bytes={byte_count} last_chunk={last_chunk_size} modules={list(target_map.keys())}"
        )
    chunk_count, byte_count, frame_count, partial_flushed = pump_ipc_audio_stream(
        conn,
        deliver_frame,
        on_chunk=log_chunk,
    )
    log(
        f"handle_prepare stream_end stream={stream_id} chunks={chunk_count} bytes={byte_count} "
        f"frames={frame_count} partial_flushed={partial_flushed}"
    )
    finish_stream(stream_id)


def handle_stream_prepare(conn, parts, action_name):
    page_debug(f"handle_stream_prepare_start action={action_name} parts={parts}")
    if len(parts) < 4:
        page_debug(f"handle_stream_prepare_bad_parts action={action_name} parts={parts}")
        conn.sendall(b"ERROR\n")
        return
    stream_id = parts[1]
    msg_id = parts[2]
    targets = parts[3:]
    try:
        sync_modules()
    except Exception as exc:
        log(f"handle_stream_prepare sync_modules error stream={stream_id}: {exc}")
        page_debug(f"handle_stream_prepare_sync_error stream={stream_id} error={exc.__class__.__name__}: {exc}")
    target_map = normalize_targets(targets)
    if not target_map:
        log(f"handle_stream_prepare no_target_modules action={action_name} stream={stream_id} msg={msg_id} targets={targets}")
        page_debug(f"handle_stream_prepare_no_target_modules action={action_name} stream={stream_id} msg={msg_id} targets={targets}")
        conn.sendall(b"ERROR\n")
        return
    state = create_stream_state(stream_id, target_map)
    dispatch(action_name, stream_id, msg_id, targets)
    ready = state.ready_event.wait(10.0)
    log(f"handle_stream_prepare action={action_name} waited stream={stream_id} ready={ready}")
    page_debug(
        f"handle_stream_prepare_ready action={action_name} stream={stream_id} ready={ready} "
        f"ready_modules={sorted(state.ready_modules)} failed_modules={sorted(state.failed_modules)} pending={sorted(state.pending_modules)}"
    )
    failed_modules = sorted(state.failed_modules)
    if not ready or not state.ready_modules:
        pop_stream_state(stream_id)
        page_debug(
            f"handle_stream_prepare_error action={action_name} stream={stream_id} "
            f"ready_modules={sorted(state.ready_modules)} failed_modules={failed_modules} "
            f"pending={sorted(state.pending_modules)}"
        )
        conn.sendall(b"ERROR\n")
        return
    if failed_modules:
        page_debug(
            f"handle_stream_prepare_partial action={action_name} stream={stream_id} "
            f"ready_modules={sorted(state.ready_modules)} failed_modules={failed_modules} "
            f"pending={sorted(state.pending_modules)}"
        )
    conn.sendall(b"OK\n")
    page_debug(f"handle_stream_prepare_ok action={action_name} stream={stream_id}")
    def deliver_frame(frame):
        for module_name in target_map:
            with loaded_modules_lock:
                mod = loaded_modules.get(module_name)
            if mod and hasattr(mod, "receive_audio"):
                try:
                    mod.receive_audio(frame, stream_id)
                except Exception as exc:
                    log(f"receive_audio error in {module_name}: {exc}")
                    page_debug(f"receive_audio_error module={module_name} stream={stream_id} error={exc.__class__.__name__}: {exc}")
    def log_chunk(chunk_count, byte_count, last_chunk_size):
        if chunk_count == 1 or chunk_count % 50 == 0:
            page_debug(
                f"handle_stream_prepare_audio action={action_name} stream={stream_id} "
                f"chunks={chunk_count} bytes={byte_count} last_chunk={last_chunk_size} modules={list(target_map.keys())}"
            )
        log(
            f"handle_stream_prepare action={action_name} audio_chunk stream={stream_id} "
            f"chunks={chunk_count} bytes={byte_count} last_chunk={last_chunk_size} modules={list(target_map.keys())}"
        )
    chunk_count, byte_count, frame_count, partial_flushed = pump_ipc_audio_stream(
        conn,
        deliver_frame,
        on_chunk=log_chunk,
    )
    page_debug(
        f"handle_stream_prepare_end action={action_name} stream={stream_id} "
        f"chunks={chunk_count} bytes={byte_count} frames={frame_count} partial_flushed={partial_flushed}"
    )
    finish_stream(stream_id)


def handle_sendmsg(conn, parts):
    if len(parts) < 4:
        conn.sendall(b"ERROR\n")
        return
    stream_id = parts[1]
    msg_id = parts[2]
    targets = parts[3:]
    log(f"handle_sendmsg stream={stream_id} msg={msg_id} targets={targets}")
    dispatch("sendmsg", stream_id, msg_id, targets)
    conn.sendall(b"DONE\n")


def deliver_broadcast(stream_id, broadcast_id):
    from clientd import (
        send_stream_frame,
        start_desktop_broadcast_stream,
    )

    broadcast = fetch_broadcast(broadcast_id)
    if not broadcast:
        log(f"handle_broadcast missing broadcast={broadcast_id}")
        return "failed"
    if active_broadcast_stop_requested(broadcast_id):
        return "stopped"
    targets = broadcast_target_tokens(broadcast)
    desktop_targeted = any(
        token == "guest" or str(token or "").strip().startswith("user/")
        for token in targets
    )
    if not targets:
        log(f"handle_broadcast no_targets stream={stream_id} broadcast={broadcast_id} groups={broadcast.get('groups')}")
        return "failed"
    msg_type = broadcast.get("type")
    audio_files = broadcast.get("audio") or ""
    metadata = {
        "broadcast_id": broadcast_id,
        "groups": str(broadcast.get("groups") or ""),
        "type": msg_type,
        "sender": broadcast.get("sender") or "",
        "priority": broadcast.get("priority") or "",
        "template_id": broadcast.get("template_id") or "",
        "vendor_specific": broadcast.get("vendor_specific") or "",
        "expires": broadcast.get("expires"),
        "expires_rule": broadcast.get("expires_rule") or "",
        "issued": broadcast.get("issued"),
        "shortmessage": broadcast.get("shortmessage") or "",
        "longmessage": broadcast.get("longmessage") or "",
        "color": broadcast.get("color") or "",
    }
    if is_audio_type(msg_type):
        gen = audio_frames(audio_files)
        try:
            first_frame = next(gen)
            has_audio = True
        except StopIteration:
            has_audio = False
        if has_audio:
            target_map = normalize_targets(targets)
            # Kick off endpoint module preparation in background — don't block
            # desktop delivery on the up-to-10-second ready wait.
            endpoint_state = None
            endpoint_wait_deadline = time.perf_counter() + 10.0
            endpoints_ready = not bool(target_map)
            if target_map:
                endpoint_state = create_stream_state(stream_id, target_map)
                dispatch("prepare_audio", stream_id, broadcast_id, targets, metadata)
            # Open desktop IPC immediately (before endpoint modules are ready)
            desktop_sock = None
            desktop_result = {}
            try:
                desktop_sock, desktop_result = start_desktop_broadcast_stream(
                    broadcast_id,
                    codec="mulaw",
                    sample_rate=8000,
                    broadcast=broadcast,
                )
                if desktop_sock is not None:
                    # Never let a stalled desktop pipe starve endpoint audio.
                    desktop_sock.settimeout(1.0)
            except Exception as exc:
                desktop_sock = None
                desktop_result = {}
                log(f"desktop broadcast start error broadcast={broadcast_id}: {exc}")
            desktop_matched = int(desktop_result.get("matched") or 0)
            if not target_map and desktop_matched <= 0 and not desktop_targeted:
                log(f"handle_broadcast no_target_modules stream={stream_id} broadcast={broadcast_id} targets={targets}")
                if endpoint_state is not None:
                    pop_stream_state(stream_id)
                return "failed"
            recording = None
            try:
                recording = BroadcastRecordingWriter(broadcast_id)
            except Exception as exc:
                log(f"broadcast recording start error broadcast={broadcast_id}: {exc}")
            # Unified paced frame loop — desktop gets audio immediately;
            # endpoint modules receive frames once they signal ready. Frames
            # produced before readiness are buffered and flushed so multicast
            # endpoints receive the full audio from the first frame.
            # The loop runs slightly ahead of real time (delivery_lead) so the
            # self-pacing endpoint senders always have a jitter cushion and
            # never underrun into audible dropouts.
            frame_duration = 160 / 8000
            delivery_lead = MULTICAST_DELIVERY_LEAD_SECONDS
            next_send_time = time.perf_counter() - delivery_lead
            stop_check_interval = max(1, int(0.5 / frame_duration))
            frames_since_stop_check = 0
            stopped = False

            def deliver_endpoint_frame(endpoint_frame):
                for module_name in target_map:
                    with loaded_modules_lock:
                        mod = loaded_modules.get(module_name)
                    if mod and hasattr(mod, "receive_audio"):
                        try:
                            mod.receive_audio(endpoint_frame, stream_id)
                        except Exception as exc:
                            log(f"receive_audio error in {module_name}: {exc}")

            pending_endpoint_frames = []
            for frame in itertools.chain([first_frame], gen):
                if frames_since_stop_check <= 0:
                    frames_since_stop_check = stop_check_interval
                    if active_broadcast_stop_requested(broadcast_id):
                        stopped = True
                        break
                frames_since_stop_check -= 1
                if recording is not None:
                    try:
                        recording.write_frame(frame)
                    except Exception as exc:
                        log(f"broadcast recording write error broadcast={broadcast_id}: {exc}")
                        try:
                            recording.close()
                        except Exception:
                            pass
                        recording = None
                # Desktop: always deliver with no readiness gate
                if desktop_sock is not None:
                    try:
                        send_stream_frame(desktop_sock, frame)
                    except Exception as exc:
                        log(f"desktop broadcast frame error broadcast={broadcast_id}: {exc}")
                        desktop_sock = None
                # Endpoint modules: buffer until ready, then flush + deliver
                if target_map:
                    if not endpoints_ready:
                        if endpoint_state.ready_event.is_set() or time.perf_counter() >= endpoint_wait_deadline:
                            endpoints_ready = True
                            log(f"handle_broadcast endpoints_ready stream={stream_id} ready={endpoint_state.ready_event.is_set()}")
                    if endpoints_ready:
                        if pending_endpoint_frames:
                            for queued_frame in pending_endpoint_frames:
                                deliver_endpoint_frame(queued_frame)
                            pending_endpoint_frames = []
                        deliver_endpoint_frame(frame)
                    else:
                        pending_endpoint_frames.append(frame)
                next_send_time += frame_duration
                sleep_time = next_send_time - time.perf_counter()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                elif sleep_time < -0.5:
                    # Stall detected: resync rather than bursting the backlog.
                    next_send_time = time.perf_counter() - delivery_lead
            try:
                if desktop_sock is not None:
                    desktop_sock.close()
            except Exception:
                pass
            # Short clips can finish before modules signal ready — wait for
            # readiness (bounded) and flush the buffered frames so nothing is lost.
            if target_map and pending_endpoint_frames and not stopped:
                if not endpoints_ready:
                    remaining = endpoint_wait_deadline - time.perf_counter()
                    if remaining > 0:
                        endpoint_state.ready_event.wait(remaining)
                for queued_frame in pending_endpoint_frames:
                    deliver_endpoint_frame(queued_frame)
                pending_endpoint_frames = []
            recording_path = ""
            if recording is not None:
                try:
                    recording.close()
                    recording_path = str(recording.path)
                except Exception as exc:
                    log(f"broadcast recording close error broadcast={broadcast_id}: {exc}")
            if recording_path:
                try:
                    updated = dict(broadcast or {})
                    updated["runtime_recording"] = recording_path
                    updated["runtime_recording_codec"] = "wav"
                    updated["runtime_recording_sample_rate"] = 8000
                    put_active_broadcast(updated)
                except Exception as exc:
                    log(f"broadcast recording persist error broadcast={broadcast_id}: {exc}")
            if target_map:
                finish_stream(stream_id)
            return "stopped" if stopped else "sent"
        log(f"handle_broadcast audio_type_no_audio broadcast={broadcast_id} audio={audio_files}")
    dispatch("sendmsg", stream_id, broadcast_id, targets, metadata)
    return "stopped" if active_broadcast_stop_requested(broadcast_id) else "sent"


def finish_claimed_broadcast_delivery(stream_id, broadcast_id, source):
    try:
        try:
            sync_modules()
        except Exception as exc:
            log(f"{source} sync_modules error broadcast={broadcast_id}: {exc}")
        broadcast = fetch_broadcast(broadcast_id)
        status = deliver_broadcast(stream_id, broadcast_id)
        clear_active_broadcast_stop_request(broadcast_id)
        if status == "sent":
            mark_broadcast_history_delivery(broadcast_id, "sent")
            mark_active_broadcast_delivery(broadcast_id, "sent")
            log(f"{source} dispatched broadcast={broadcast_id} stream={stream_id}")
        elif status == "stopped":
            persistent = bool(broadcast) and not record_is_bell(broadcast) and not record_is_immediate(broadcast)
            mark_broadcast_history_delivery(broadcast_id, "stopped" if persistent else "cancelled")
            mark_active_broadcast_delivery(broadcast_id, "stopped")
            log(f"{source} stopped broadcast={broadcast_id} stream={stream_id} persistent={persistent}")
        else:
            mark_broadcast_history_delivery(broadcast_id, "failed")
            mark_active_broadcast_delivery(broadcast_id, "failed")
            log(f"{source} dispatch_failed broadcast={broadcast_id} stream={stream_id}")
    finally:
        with broadcast_delivery_lock:
            broadcast_delivery_ids.discard(broadcast_id)


def handle_broadcast(conn, parts):
    if len(parts) < 3:
        conn.sendall(b"ERROR\n")
        return
    stream_id = parts[1]
    broadcast_id = parts[2]
    with broadcast_delivery_lock:
        if broadcast_id in broadcast_delivery_ids:
            log(f"handle_broadcast already_in_progress broadcast={broadcast_id} stream={stream_id}")
            conn.sendall(b"DONE\n")
            return
        broadcast_delivery_ids.add(broadcast_id)
    if not claim_broadcast_delivery(broadcast_id, stream_id):
        with broadcast_delivery_lock:
            broadcast_delivery_ids.discard(broadcast_id)
        log(f"handle_broadcast claim_skipped broadcast={broadcast_id} stream={stream_id}")
        conn.sendall(b"DONE\n")
        return
    threading.Thread(
        target=finish_claimed_broadcast_delivery,
        args=(stream_id, broadcast_id, "handle_broadcast"),
        daemon=True,
    ).start()
    conn.sendall(b"DONE\n")


def handle_active_store(conn, parts):
    if len(parts) < 2:
        send_ipc_json(conn, {"ok": False, "error": "missing payload"})
        return
    try:
        record = decode_ipc_json_token(parts[1])
        if not isinstance(record, dict):
            raise ValueError("payload must be an object")
        record = hydrate_active_record_from_history(record)
        broadcast_id = put_active_broadcast(record)
        send_ipc_json(conn, {"ok": True, "id": broadcast_id})
    except Exception as exc:
        log(f"handle_active_store error: {exc}")
        send_ipc_json(conn, {"ok": False, "error": str(exc)})


def handle_active_expire_template_ids(conn, parts):
    if len(parts) < 2:
        send_ipc_json(conn, {"ok": False, "error": "missing payload"})
        return
    try:
        payload = decode_ipc_json_token(parts[1])
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        removed_ids = expire_active_broadcasts_by_template_ids(
            payload.get("template_ids") or [],
            exclude_broadcast_ids=payload.get("exclude_broadcast_ids") or [],
        )
        send_ipc_json(conn, {"ok": True, "removed_ids": removed_ids})
    except Exception as exc:
        log(f"handle_active_expire_template_ids error: {exc}")
        send_ipc_json(conn, {"ok": False, "error": str(exc)})


def handle_active_expire_triggered(conn, parts):
    if len(parts) < 2:
        send_ipc_json(conn, {"ok": False, "error": "missing payload"})
        return
    try:
        payload = decode_ipc_json_token(parts[1])
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        removed_ids = expire_active_broadcasts_triggered_by_template(payload.get("template_id"))
        send_ipc_json(conn, {"ok": True, "removed_ids": removed_ids})
    except Exception as exc:
        log(f"handle_active_expire_triggered error: {exc}")
        send_ipc_json(conn, {"ok": False, "error": str(exc)})


def deliver_pending_broadcast(broadcast_id):
    stream_id = uuid.uuid4().hex
    with broadcast_delivery_lock:
        if broadcast_id in broadcast_delivery_ids:
            log(f"broadcast_watcher already_in_progress broadcast={broadcast_id} stream={stream_id}")
            return
        broadcast_delivery_ids.add(broadcast_id)
    if not claim_broadcast_delivery(broadcast_id, stream_id):
        with broadcast_delivery_lock:
            broadcast_delivery_ids.discard(broadcast_id)
        log(f"broadcast_watcher claim_skipped broadcast={broadcast_id} stream={stream_id}")
        return
    finish_claimed_broadcast_delivery(stream_id, broadcast_id, "broadcast_watcher")


def broadcast_watcher_loop():
    log("broadcast_watcher polling interval=0.05s")
    while not broadcast_watcher_stop.is_set():
        try:
            for broadcast_id in fetch_pending_broadcast_ids():
                threading.Thread(
                    target=deliver_pending_broadcast,
                    args=(broadcast_id,),
                    daemon=True,
                ).start()
        except Exception as exc:
            log(f"broadcast_watcher error: {exc}")
        broadcast_watcher_stop.wait(0.05)


def handle_ready(conn, parts):
    if len(parts) >= 3:
        log(f"handle_ready module={parts[1]} stream={parts[2]}")
        mark_ready(parts[1], parts[2])
    conn.sendall(b"ACK\n")


def normalize_module_status_endpoints(module_info):
    endpoints = module_info.get("endpoints")
    if not isinstance(endpoints, list):
        endpoints = []
        module_info["endpoints"] = endpoints
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        direction = str(endpoint.get("direction") or endpoint.get("input_type") or "").lower()
        if "output" in direction:
            endpoint.setdefault("bell_capable", True)
            capabilities = endpoint.get("capabilities")
            if not isinstance(capabilities, list):
                capabilities = []
            if "bells" not in capabilities:
                capabilities.append("bells")
            endpoint["capabilities"] = capabilities
    module_info["count"] = len(endpoints)
    return module_info


def normalize_status_collection_error(exc):
    if isinstance(exc, json.JSONDecodeError):
        if exc.pos == 0:
            return "Module returned an empty or invalid JSON response"
        return f"Module returned invalid JSON: {exc}"
    return str(exc)


def cached_module_status_entry(module_name):
    with module_status_lock:
        entry = module_status_cache.get(module_name)
        if entry is None:
            return None
        return dict(entry)


def start_module_status_collection(module_name, builder):
    with module_status_lock:
        existing = module_status_tasks.get(module_name)
        if existing is not None and existing["thread"].is_alive():
            if (time.monotonic() - existing.get("started_at", 0.0)) <= MODULE_STATUS_TASK_STALE_SECONDS:
                return existing
            module_status_tasks.pop(module_name, None)
            log(f"module_status stale task discarded module={module_name}")
        state = {
            "module_name": module_name,
            "done": threading.Event(),
            "started_at": time.monotonic(),
        }
        module_status_tasks[module_name] = state

    def worker():
        payload = None
        error = None
        try:
            payload = builder()
            if not isinstance(payload, dict):
                payload = {}
        except Exception as exc:
            error = normalize_status_collection_error(exc)
        with module_status_lock:
            existing_cache = module_status_cache.get(module_name)
            cache_entry = {
                "payload": payload,
                "error": error,
                "warning": None,
                "started_at": state["started_at"],
                "updated_at": time.monotonic(),
            }
            if error and existing_cache and not existing_cache.get("error") and isinstance(existing_cache.get("payload"), dict):
                cache_entry["payload"] = dict(existing_cache["payload"])
                cache_entry["error"] = None
                cache_entry["warning"] = error
                log(f"module_status refresh failed module={module_name}: {error}; keeping last good payload")
            if existing_cache is None or existing_cache.get("started_at", 0.0) <= state["started_at"]:
                module_status_cache[module_name] = cache_entry
            current = module_status_tasks.get(module_name)
            if current is state:
                module_status_tasks.pop(module_name, None)
        state["done"].set()

    state["thread"] = threading.Thread(target=worker, daemon=True)
    state["thread"].start()
    return state


def resolve_module_status(module_name, display_name, timeout_seconds, **fallback_fields):
    cached = cached_module_status_entry(module_name)
    if cached is not None and (time.monotonic() - cached.get("updated_at", 0.0)) <= MODULE_STATUS_CACHE_TTL:
        return finalize_module_status(module_name, display_name, cached, **fallback_fields)
    return finish_module_status_collection(module_name, display_name, timeout_seconds, cached, **fallback_fields)


def finish_module_status_collection(module_name, display_name, timeout_seconds, cached_entry=None, **fallback_fields):
    with module_status_lock:
        state = module_status_tasks.get(module_name)
    if state is not None:
        state["done"].wait(max(0.0, timeout_seconds))
    cached = cached_module_status_entry(module_name)
    if cached is not None and (state is None or state["done"].is_set()):
        return finalize_module_status(module_name, display_name, cached, **fallback_fields)
    if cached_entry is not None:
        return finalize_module_status(module_name, display_name, cached_entry, **fallback_fields)
    if state is not None and not state["done"].is_set():
        timed_out = {
            "module": module_name,
            "display_name": display_name or module_name,
            "count": 0,
            "endpoints": [],
            "error": "Timed out while collecting endpoint status",
        }
        timed_out.update(fallback_fields)
        return timed_out
    failed = {
        "module": module_name,
        "display_name": display_name or module_name,
        "count": 0,
        "endpoints": [],
        "error": "Endpoint status unavailable",
    }
    failed.update(fallback_fields)
    return failed


def finalize_module_status(module_name, display_name, cached_entry, **fallback_fields):
    if cached_entry.get("error"):
        failed = {
            "module": module_name,
            "display_name": display_name or module_name,
            "count": 0,
            "endpoints": [],
            "error": cached_entry["error"],
        }
        failed.update(fallback_fields)
        return failed
    payload = cached_entry.get("payload") or {}
    payload["module"] = payload.get("module") or module_name
    payload["display_name"] = payload.get("display_name") or display_name or payload["module"]
    if cached_entry.get("warning") and not payload.get("warning"):
        payload["warning"] = cached_entry["warning"]
    for key, value in fallback_fields.items():
        payload.setdefault(key, value)
    return normalize_module_status_endpoints(payload)


def handle_list_endpoints(conn):
    sync_error = None
    try:
        sync_modules()
    except Exception as exc:
        sync_error = str(exc)
        log(f"list_endpoints sync error: {exc}")
    with loaded_modules_lock:
        modules_snapshot = list(loaded_modules.items())
        load_errors_snapshot = dict(module_load_errors)
    modules = []
    pending = []
    if not any(module_name == "siptrunks" for module_name, _mod in modules_snapshot):
        pending.append(
            (
                "siptrunks",
                "SIP Trunks",
                {
                    "system_builtin": True,
                    "enabled": True,
                    "loaded": True,
                    "trusted": True,
                    "can_load": True,
                },
            )
        )
        start_module_status_collection("siptrunks", get_siptrunks_endpoint_status)
    for module_name, mod in modules_snapshot:
        fallback = {
            "input_capable": module_is_input_capable(module_name),
            "output_capable": module_is_output_capable(module_name, mod),
        }
        if hasattr(mod, "get_endpoint_status"):
            pending.append((module_name, module_name, fallback))
            start_module_status_collection(module_name, mod.get_endpoint_status)
        else:
            module_info = {
                "module": module_name,
                "display_name": module_name,
                "count": 0,
                "endpoints": [],
                "error": "Module does not support endpoint status",
                **fallback,
            }
            modules.append(module_info)
    deadline = time.monotonic() + LIST_ENDPOINTS_STATUS_TIMEOUT
    for module_name, display_name, fallback in pending:
        remaining = deadline - time.monotonic()
        module_info = resolve_module_status(module_name, display_name, remaining, **fallback)
        if module_info.get("error"):
            log(f"get_endpoint_status error in {module_name}: {module_info['error']}")
        modules.append(module_info)
    for module_name, error in sorted(load_errors_snapshot.items()):
        modules.append(
            {
                "module": module_name,
                "display_name": module_name,
                "count": 0,
                "endpoints": [],
                "error": error,
            }
        )
    response = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "modules": modules,
    }
    if sync_error:
        response["warning"] = sync_error
    conn.sendall(json.dumps(response, default=str).encode("utf-8") + b"\n")


def default_module_info(module_name):
    return {
        "module": module_name,
        "name": module_name,
        "developer": "",
        "description": "",
        "input_type": "Output",
        "minimum_ops_version": OPS_VERSION,
        "requirements": [],
    }


def module_info_from_manifest(module_name, entry):
    info_path = Path(entry).parent.parent / "manifest.json"
    if not info_path.exists():
        return None
    try:
        manifest = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"manifest.json parse error in {module_name}: {exc}")
        return None
    info = {
        "module": module_name,
        "name": manifest.get("name") or module_name,
        "developer": manifest.get("developer") or manifest.get("author") or "",
        "description": manifest.get("description") or manifest.get("desp") or "",
        "input_type": manifest.get("input_type") or manifest.get("type") or "Output",
        "minimum_ops_version": manifest.get("minimum_ops_version") or OPS_VERSION,
        "requirements": manifest.get("requirements") or [],
    }
    version = manifest.get("version")
    updated = manifest.get("updated")
    if version:
        info["version"] = version
    if updated:
        info["updated"] = updated
    return info


def module_info_from_entry(module_name, entry):
    info = default_module_info(module_name)
    manifest_info = module_info_from_manifest(module_name, entry)
    if manifest_info is not None:
        info.update(manifest_info)
    info["module"] = module_name
    info["name"] = info.get("name") or module_name
    info["input_type"] = info.get("input_type") or "Output"
    return info


def handle_list_endpoint_modules(conn):
    sync_error = None
    try:
        sync_modules()
    except Exception as exc:
        sync_error = str(exc)
        log(f"list_endpoint_modules sync error: {exc}")
    packages = discover_endpoint_packages(extract_if_trusted=True)
    upsert_module_package_registry(packages)
    discovered = discover_modules()
    states = module_enabled_states(discovered)
    with loaded_modules_lock:
        loaded_names = sorted(loaded_modules.keys())
    modules = []
    for module_name in sorted(packages):
        package = packages[module_name]
        manifest = package.get("manifest") or {}
        verification = package.get("verification") or {}
        trusted = bool(package.get("trusted"))
        if module_name in discovered:
            info = module_info_from_entry(module_name, discovered[module_name])
            web_path = Path(package.get("web_path") or "")
        else:
            info = default_module_info(module_name)
            info.update(
                {
                    "name": manifest.get("name") or module_name,
                    "developer": manifest.get("developer") or manifest.get("author") or "",
                    "description": manifest.get("description") or "",
                    "input_type": manifest.get("input_type") or manifest.get("type") or "Output",
                    "version": manifest.get("version") or "",
                    "minimum_ops_version": manifest.get("minimum_ops_version") or OPS_VERSION,
                    "requirements": manifest.get("requirements") or [],
                }
            )
            web_path = Path()
        info["enabled"] = bool(states.get(module_name)) if trusted else False
        info["loaded"] = module_name in loaded_names
        info["trusted"] = trusted
        info["signature_state"] = verification.get("signature_state") or "unsigned"
        info["signature_label"] = verification.get("signature_label") or ""
        info["signer"] = verification.get("organization") or ""
        info["load_error"] = "" if trusted else package.get("load_error") or "This module is unsigned and cannot be verified"
        info["can_load"] = trusted
        info["input_capable"] = module_type_has_input(info.get("input_type"))
        info["output_capable"] = module_type_has_output(info.get("input_type"))
        web_mod = None
        if trusted and (web_path / "web.py").is_file():
            try:
                web_mod = load_endpoint_web_module(module_name, missing_ok=True)
            except Exception:
                web_mod = None
        info["has_forms"] = bool(getattr(web_mod, "forms", None))
        info["has_settings_page"] = bool(getattr(web_mod, "render_settings", None))
        modules.append(info)
    response = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "modules": modules,
    }
    if sync_error:
        response["warning"] = sync_error
    conn.sendall(json.dumps(response, default=str).encode("utf-8") + b"\n")


def handle_ipc_client(conn):
    try:
        line = recv_line(conn)
        if not line:
            return
        parts = line.decode("utf-8", errors="ignore").strip().split()
        if not parts:
            return
        command = parts[0]
        log(f"handle_ipc_client command={command} parts={parts}")
        if command == "PREPARELIVE":
            page_debug(f"ipc_preparelive_received parts={parts}")
        if command == "PREPARE":
            handle_prepare(conn, parts)
        elif command == "PREPARELIVE":
            handle_stream_prepare(conn, parts, "prepare_livepage")
        elif command == "SENDMSG":
            handle_sendmsg(conn, parts)
        elif command == "BROADCAST":
            handle_broadcast(conn, parts)
        elif command == "ACTIVE_STORE":
            handle_active_store(conn, parts)
        elif command == "ACTIVE_EXPIRE_TEMPLATE_IDS":
            handle_active_expire_template_ids(conn, parts)
        elif command == "ACTIVE_EXPIRE_TRIGGERED":
            handle_active_expire_triggered(conn, parts)
        elif command == "READY":
            handle_ready(conn, parts)
        elif command == "LIST_ENDPOINTS":
            handle_list_endpoints(conn)
        elif command == "LIST_ENDPOINT_MODULES":
            handle_list_endpoint_modules(conn)
        else:
            conn.sendall(b"ERROR\n")
    except Exception as exc:
        log(f"IPC connection handler error: {exc}")
    finally:
        conn.close()

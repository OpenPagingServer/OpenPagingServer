import base64
import hashlib
import html
import importlib.util
import ipaddress
import io
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
import uuid
import wave
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
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
    claim_active_broadcast_delivery,
    expire_active_broadcasts_by_template_ids,
    expire_active_broadcasts_triggered_by_template,
    fetch_active_broadcast,
    list_pending_active_broadcast_ids,
    mark_active_broadcast_delivery,
    put_active_broadcast,
)

try:
    from broadcasts import is_audio_type, message_expiration_is_immediate
except Exception:
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

loaded_modules = {}
module_load_errors = {}
loaded_modules_lock = threading.Lock()
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
        self.ready_event = threading.Event()

    def mark_ready(self, module_name):
        if module_name in self.pending_modules:
            self.ready_modules.add(module_name)
        if self.ready_modules >= self.pending_modules:
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
    return socket.create_connection(("127.0.0.1", 50000), timeout=timeout)


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
    manifest = json.loads(raw.decode("utf-8"))
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


def endpoint_package_info(bundle_path, extract_if_trusted=False):
    bundle_path = Path(bundle_path)
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
    return info


def discover_endpoint_packages(extract_if_trusted=False):
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
MULTICAST_RTP_CODECS = {"PCMU": 0, "PCMA": 8}
MULTICAST_RTP_DEFAULT_PACKET_MS = 20
MULTICAST_RTP_MIN_PACKET_MS = 20
MULTICAST_RTP_MAX_PACKET_MS = 200
MULTICAST_RTP_FRAME_MS = 20
MULTICAST_RTP_FRAME_SIZE = 160
MULTICAST_RTP_READY_SILENCE_FRAMES = 3
MULTICAST_RTP_IDLE_SECONDS = 1.0
MULTICAST_GATEWAY_HOST = os.getenv("OPS_MULTICAST_GATEWAY_HOST", "127.0.0.1")
MULTICAST_GATEWAY_PORT = int(os.getenv("OPS_MULTICAST_GATEWAY_PORT", "8710"))


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
    bias = 0x84
    clip = 32635
    sign = 0
    sample = int(sample)
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
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


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
        mixed[index] = linear_to_ulaw(total / frame_count)
    return bytes(mixed)


def multicast_priority_value(metadata):
    if not isinstance(metadata, dict):
        return "Normal"
    priority = str(metadata.get("priority") or "Normal").strip().title()
    return priority if priority in VALID_MESSAGE_PRIORITIES else "Normal"


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


def gateway_recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("gateway connection closed")
        data.extend(chunk)
    return bytes(data)


def gateway_frame_bytes(header, payload):
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_bytes = bytes(payload or b"")
    return struct.pack("!II", len(header_bytes), len(payload_bytes)) + header_bytes + payload_bytes


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
    sock = socket.create_connection((MULTICAST_GATEWAY_HOST, MULTICAST_GATEWAY_PORT), timeout=1.0)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.sendall(
        json.dumps(
            {"role": "source", "service": "endpoints", "pid": os.getpid()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
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
            sock.sendall(gateway_frame_bytes(header, data))
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
                forward_multicast_packet(data, host, port, family=sock.family, ttl=ttl)
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


def multicast_rtp_clean_port(value):
    try:
        port = int(str(value or "").strip())
    except ValueError as exc:
        raise ValueError("Enter a valid UDP port.") from exc
    if port < 1 or port > 65535:
        raise ValueError("Enter a valid UDP port.")
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


def multicast_rtp_clean_values(values):
    name = str(values.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required.")
    return {
        "name": name,
        "address": multicast_rtp_normalize_address(values.get("address")),
        "port": multicast_rtp_clean_port(values.get("port")),
        "codec": multicast_rtp_clean_codec(values.get("codec")),
        "packet_ms": multicast_rtp_clean_packet_ms(values.get("packet_ms")),
    }


def multicast_rtp_form_values(form, row=None):
    row = row or {}
    defaults = {
        "name": str(row.get("name") or ""),
        "address": str(row.get("address") or ""),
        "port": str(row.get("port") or ""),
        "codec": multicast_rtp_clean_codec(row.get("codec") or "PCMU"),
        "packet_ms": str(row.get("packet_ms") or MULTICAST_RTP_DEFAULT_PACKET_MS),
    }
    return {key: str(form.get(key, defaults[key]) if form is not None else defaults[key]).strip() for key in defaults}


def multicast_rtp_rows():
    ensure_multicast_rtp_schema()
    return sip_query_all(
        f"SELECT `id`, `name`, `address`, `port`, `codec`, `packet_ms` FROM `{MULTICAST_RTP_TABLE}` ORDER BY `name` ASC, `id` ASC"
    )


def multicast_rtp_row(row_id):
    ensure_multicast_rtp_schema()
    rows = sip_query_all(
        f"SELECT `id`, `name`, `address`, `port`, `codec`, `packet_ms` FROM `{MULTICAST_RTP_TABLE}` WHERE id=%s LIMIT 1",
        (row_id,),
    )
    return rows[0] if rows else None


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
    if (trunk_type == SIP_TRUNK_TYPE_IP or auth_type == "IP") and ipaddr:
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
        f"t.trunk_type AS trunk_type, t.connected_server AS trunk_connected_server "
        f"FROM `{SIP_OUTPUT_TABLE}` o "
        f"LEFT JOIN `{SIP_TRUNK_TABLE}` t ON t.id = o.trunk_id "
        f"ORDER BY o.name ASC, o.id ASC"
    )


def sip_fetch_output_row(row_id):
    rows = sip_query_all(
        f"SELECT o.*, t.name AS trunk_name, t.status AS trunk_status, t.auth AS trunk_auth, "
        f"t.trunk_type AS trunk_type, t.connected_server AS trunk_connected_server, "
        f"t.username AS trunk_username, t.password AS trunk_password, t.ipaddr AS trunk_ipaddr "
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
        "description": "Built-in SIP trunk, SIP dialplan, and SIP number endpoint management.",
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
    return [{"id": "0", "name": "All Recipients"}] + rows


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
        ".check{display:flex;gap:8px;align-items:center;font-weight:400}.check.disabled{opacity:.55}.switch-row{display:flex;align-items:center;gap:10px}.switch{position:relative;width:44px;height:24px}.switch input{opacity:0;width:0;height:0}.slider{position:absolute;cursor:pointer;inset:0;background:#9aa0a6;border-radius:999px;transition:.2s}.slider:before{content:\"\";position:absolute;height:18px;width:18px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s;box-shadow:0 1px 2px rgba(0,0,0,.25)}.switch input:checked + .slider{background:#1976D2}.switch input:checked + .slider:before{transform:translateX(20px)}.hint{color:#5f6368;font-size:.9em}"
        "@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.form-surface,.surface{background:#232323;border-color:#333;box-shadow:none}.control,input,select,.dropdown-checklist summary,.dropdown-panel{background:#171717;border-color:#3a3a3a;color:#eee}.notice{background:#332800;border-color:#5f4b00;color:#FFE8A3}.advanced{border-color:#333}.advanced-body{border-top-color:#333}button,.button{background:#BB86FC;color:#000}.danger{background:#EF9A9A}.meta,.hint{color:#aaa}.switch input:checked + .slider{background:#BB86FC}}</style>"
        + body
    )


def sip_dialplan_fields(values):
    selected_groups = set(str(values["group"] or "").split(".")) if values.get("group") else set()
    group_options = "".join(
        f"""<label class="check"><input type="checkbox" class="group-check" value="{h(row.get("id"))}" data-label="{h("All Recipients" if str(row.get("id")) == "0" else row.get("name") or row.get("id"))}"{" checked" if str(row.get("id")) in selected_groups else ""}> {h("All Recipients" if str(row.get("id")) == "0" else str(row.get("id")) + (" - " + str(row.get("name")) if row.get("name") else ""))}</label>"""
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
        if auth_type == "IP" or siptrunks_is_outbound_row(row):
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
<label class="check"><input type="checkbox" name="allow_cid_override" value="1"{" checked" if values.get("allow_cid_override") == "1" else ""}> Allow per-message CID Number override</label>
<label class="check"><input type="checkbox" name="allow_cnam_override" value="1"{" checked" if values.get("allow_cnam_override") == "1" else ""}> Allow per-message CNAM Caller ID Name override</label>
<div class="row"><label>Mode</label><select class="control short-control" name="mode" id="sipNumberMode"><option value="page"{" selected" if values.get("mode") == "page" else ""}>Page</option><option value="telephone"{" selected" if values.get("mode") == "telephone" else ""}>Telephone</option></select></div>
<div class="notice" id="sipModeNotice">Use page mode when the call is automatically picked up. Such as by a paging zone controller, PBX page group, etc. This endpoint will behave like a speaker where the broadcast will not start until all endpoints in a group including this one is ready to receive the page.<br><br>Use telephone mode if this is calling a person(s). Such as when calling a cellphone, telephone, ring group, etc. In this mode, broadcast audio is sent independently of all other endpoints so that, for example, a user picking up the phone a long time after a broadcast begins can hear the full broadcast while speakers inside of a building can freely play and finish the broadcast in the meantime.</div>
<div class="row"><label>Alert-Info Header</label><select class="control" name="alert_info_mode" id="alertInfoMode">{alert_options}</select><div class="hint">Most VoIP systems are a Back-to-Back User Agent (B2BUA) by design and may require a dialplan or configuration change to allow Alert-Info headers to pass through.</div></div>
<div class="row" id="alertInfoCustomRow"><label>Custom Alert-Info Value</label><input class="control" name="alert_info_value" id="alertInfoValue" value="{h(values.get("alert_info_value"))}"></div>
<div id="telephoneOnly">
<div class="row"><label>Answer Timeout (seconds)</label><input class="control short-control" type="number" min="0" max="3600" name="answer_timeout" id="answerTimeout" value="{h(values.get("answer_timeout"))}"><div class="hint">US: <span id="ringsUs"></span> rings | UK/AU: <span id="ringsUk"></span> rings | ETSI: <span id="ringsEtsi"></span> rings</div></div>
<div class="row"><label>Answer Timeout Retries</label><input class="control short-control" type="number" min="0" max="8" name="answer_timeout_retry_limit" value="{h(values.get("answer_timeout_retry_limit"))}"></div>
<div class="row"><label>Answer Timeout Retry Delay (seconds)</label><input class="control short-control" type="number" min="5" max="60" name="answer_timeout_retry_delay" value="{h(values.get("answer_timeout_retry_delay"))}"></div>
<label class="check"><input type="checkbox" name="amd_enabled" value="1" id="amdEnabled"{" checked" if values.get("amd_enabled") == "1" else ""}> Enable Answering Machine Detection</label>
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
            "dialplan": {"label": "SIP Dialplan Extension", "description": "Route a SIP extension to paging, messaging, test tone, or echo test."},
            "number": {"label": "SIP Number Endpoint", "description": "Dial a number over a SIP trunk and use it like an output endpoint."},
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
            body = f'<form method="post" class="grid form-surface" id="dialplanForm">{sip_dialplan_fields(values)}<button class="button" type="submit">Add SIP Dialplan Extension</button></form>'
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
                    error = "A dialplan entry already exists for that extension."
                else:
                    sip_execute(
                        f"UPDATE `{table}` SET name=%s, extension=%s, `group`=%s, trigger=%s, passcode=%s WHERE id=%s",
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
            body = f'<form method="post" class="grid form-surface" id="dialplanForm">{sip_dialplan_fields(values)}<button class="button" type="submit">Save SIP Dialplan Extension</button></form>'
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
        packet = struct.pack(
            "!BBHII",
            0x80,
            0x00,
            int(self.call.rtp_sequence) & 0xFFFF,
            int(self.call.rtp_timestamp) & 0xFFFFFFFF,
            int(self.call.rtp_ssrc) & 0xFFFFFFFF,
        ) + bytes(payload or SIP_OUTPUT_SILENCE_FRAME)
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
        self.call.rtp_timestamp = (int(self.call.rtp_timestamp) + SIP_OUTPUT_FRAME_BYTES) & 0xFFFFFFFF
        return True


class SipOutputSession:
    def __init__(self, row, metadata, recorder, on_ready, on_done):
        self.row = dict(row or {})
        self.metadata = dict(metadata or {})
        self.recorder = recorder
        self.on_ready = on_ready
        self.on_done = on_done
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
            payload = sip_parse_rtp_payload(packet)
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
        return sip_index.sip_server.place_outbound_call(
            self.row.get("trunk_id"),
            self.row.get("number"),
            caller_id_number=cid_number,
            caller_id_name=cnam_name,
            alert_info_value=sip_output_alert_value(self.row),
            custom_headers=sip_output_headers(self.row),
            answer_timeout=answer_timeout,
        )

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
            if session.mode == SIP_OUTPUT_MODE_PAGE:
                self.page_pending += 1

    def mark_nonblocking_ready(self):
        with self.lock:
            if not self.ready_marked and self.page_pending <= 0:
                self.ready_marked = True
        if self.ready_marked:
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
        with self.lock:
            self.sessions = [item for item in self.sessions if item is not session]
            empty = not self.sessions
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
        return [row for row in rows if f"number-{row.get('id')}" in wanted or str(row.get("id")) in wanted]

    def handle_dispatch(self, action, stream_id, msg_id, sub_targets, metadata=None):
        if action not in {"prepare_audio", "prepare_livepage"}:
            mark_ready("siptrunks", stream_id)
            return
        rows = self.resolve_output_rows(sub_targets)
        state = SipTrunksStreamState("siptrunks", stream_id, metadata or {}, self.remove_stream)
        with self.lock:
            self.streams[stream_id] = state
        if not rows:
            mark_ready("siptrunks", stream_id)
            return
        for row in rows:
            session = SipOutputSession(row, metadata or {}, state.recorder, state.session_ready, state.session_done)
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


def multicast_rtp_form_html(values, error, submit_label):
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    selected_codec = str(values.get("codec") or "PCMU").strip().upper()
    codec_options = "".join(
        f'<option value="{h(codec)}"{" selected" if selected_codec == codec else ""}>{h(codec)}</option>'
        for codec in ("PCMU", "PCMA")
    )
    return f"""{error_html}<form method="post" class="grid form-surface" id="multicastRtpForm">
<div class="notice">{h(MULTICAST_RTP_WARNING)}</div>
<div class="row"><label>Name</label><input class="control" name="name" value="{h(values.get("name"))}" required></div>
<div class="row"><label>Multicast Address</label><input class="control" name="address" id="multicastAddress" value="{h(values.get("address"))}" required></div>
<div class="row"><label>Port</label><input class="control short-control" type="number" name="port" id="multicastPort" value="{h(values.get("port"))}" min="1" max="65535" step="1" required></div>
<div class="row"><label>Codec</label><select class="control short-control" name="codec">{codec_options}</select></div>
<details class="advanced"><summary>Advanced options</summary><div class="advanced-body"><div class="row"><label>Packet Size (ms)</label><input class="control short-control" type="number" name="packet_ms" id="packetMs" value="{h(values.get("packet_ms") or MULTICAST_RTP_DEFAULT_PACKET_MS)}" min="{MULTICAST_RTP_MIN_PACKET_MS}" max="{MULTICAST_RTP_MAX_PACKET_MS}" step="20" required></div></div></details>
<div class="error" id="multicastClientError" style="display:none"></div>
<button class="button" id="saveMulticastRtp" type="submit">{h(submit_label)}</button>
</form>
<script>
const multicastForm = document.getElementById('multicastRtpForm');
const multicastAddress = document.getElementById('multicastAddress');
const multicastPort = document.getElementById('multicastPort');
const packetMs = document.getElementById('packetMs');
const saveMulticastRtp = document.getElementById('saveMulticastRtp');
const multicastClientError = document.getElementById('multicastClientError');
function isIpv4Multicast(value) {{
  const parts = value.trim().split('.');
  if (parts.length !== 4) return false;
  const octets = parts.map(part => Number(part));
  if (octets.some((octet, index) => !Number.isInteger(octet) || octet < 0 || octet > 255 || String(octet) !== parts[index])) return false;
  return octets[0] >= 224 && octets[0] <= 239;
}}
function isIpv6Multicast(value) {{
  const normalized = value.trim().toLowerCase();
  if (!normalized.includes(':') || !/^[0-9a-f:.]+$/.test(normalized)) return false;
  const first = normalized.split(':', 1)[0];
  return first.length > 0 && first.length <= 4 && first.startsWith('ff');
}}
function isMulticastAddress(value) {{
  return isIpv4Multicast(value) || isIpv6Multicast(value);
}}
function isValidPort(value) {{
  const port = Number(value);
  return Number.isInteger(port) && port >= 1 && port <= 65535;
}}
function isValidPacketMs(value) {{
  const ms = Number(value);
  return Number.isInteger(ms) && ms >= {MULTICAST_RTP_MIN_PACKET_MS} && ms <= {MULTICAST_RTP_MAX_PACKET_MS} && ms % 20 === 0;
}}
function syncMulticastForm() {{
  const errors = [];
  if (!isMulticastAddress(multicastAddress.value)) errors.push('Enter a multicast address.');
  if (!isValidPort(multicastPort.value)) errors.push('Enter a valid UDP port.');
  if (!isValidPacketMs(packetMs.value)) errors.push('Packet size must be a 20 ms increment between 20 and 200 ms.');
  multicastAddress.setCustomValidity(errors.some(error => error.includes('multicast')) ? 'Enter a multicast address.' : '');
  multicastPort.setCustomValidity(errors.some(error => error.includes('port')) ? 'Enter a valid UDP port.' : '');
  packetMs.setCustomValidity(errors.some(error => error.includes('Packet size')) ? 'Packet size must be a 20 ms increment between 20 and 200 ms.' : '');
  multicastClientError.textContent = errors.join(' ');
  multicastClientError.style.display = errors.length ? 'block' : 'none';
  saveMulticastRtp.disabled = errors.length > 0;
  return errors.length === 0;
}}
[multicastAddress, multicastPort, packetMs].forEach(input => input.addEventListener('input', syncMulticastForm));
multicastForm.addEventListener('submit', event => {{
  if (!syncMulticastForm()) {{
    event.preventDefault();
    multicastForm.reportValidity();
  }}
}});
syncMulticastForm();
</script>"""


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
                    f"INSERT INTO `{MULTICAST_RTP_TABLE}` (`name`, `address`, `port`, `codec`, `packet_ms`) VALUES (%s,%s,%s,%s,%s)",
                    (clean["name"], clean["address"], clean["port"], clean["codec"], clean["packet_ms"]),
                )
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
                    f"UPDATE `{MULTICAST_RTP_TABLE}` SET `name`=%s, `address`=%s, `port`=%s, `codec`=%s, `packet_ms`=%s WHERE id=%s",
                    (clean["name"], clean["address"], clean["port"], clean["codec"], clean["packet_ms"], row_id),
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
            body = multicast_rtp_form_html(multicast_rtp_form_values(None, row), error, "Save Multicast RTP")
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
        if self.codec == "PCMA":
            return payload.translate(ULAW_TO_ALAW_TABLE)
        return payload

    def send_frame(self, payload):
        frame = self.encode_frame(payload)
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
        self.timestamp = (self.timestamp + (self.pending_frames * MULTICAST_RTP_FRAME_SIZE)) & 0xFFFFFFFF
        self.pending_payload.clear()
        self.pending_frames = 0

    def close(self):
        try:
            self.flush()
        finally:
            self.sock.close()


class MulticastRTPSource:
    def __init__(self, priority="Normal"):
        self.lock = threading.Lock()
        self.partial_frame = bytearray()
        self.closed = False
        self.preroll_frames = 0
        self.ready_sent = False
        self.priority = priority if priority in VALID_MESSAGE_PRIORITIES else "Normal"

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
                if discard:
                    return None, False
                return frame, False
            if self.closed and self.partial_frame:
                frame = bytes(self.partial_frame).ljust(MULTICAST_RTP_FRAME_SIZE, b"\xff")
                self.partial_frame.clear()
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
            self.preroll_frames += 1
            if self.preroll_frames >= MULTICAST_RTP_READY_SILENCE_FRAMES:
                self.ready_sent = True
                return True
            return False

    def close(self):
        with self.lock:
            self.closed = True


class MulticastRTPEndpointChannel:
    def __init__(self, row, on_idle):
        self.key = str(row.get("id") or "")
        self.lock = threading.Lock()
        self.sender = MulticastRTPSender(row)
        self.sources = {}
        self.stop_event = threading.Event()
        self.idle_since = None
        self.on_idle = on_idle
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def attach_source(self, stream_id, priority="Normal"):
        source = MulticastRTPSource(priority=priority)
        with self.lock:
            previous = self.sources.pop(stream_id, None)
            self.sources[stream_id] = source
            self.idle_since = None
        if previous is not None:
            previous.close()
        return source

    def stop_source(self, stream_id):
        with self.lock:
            source = self.sources.get(stream_id)
        if source is not None:
            source.close()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            sources = list(self.sources.values())
        for source in sources:
            source.close()

    def run(self):
        next_send = time.monotonic()
        try:
            while not self.stop_event.is_set():
                ready_sources = []
                finished_stream_ids = []
                frames = []
                now = time.monotonic()
                with self.lock:
                    source_items = list(self.sources.items())
                    idle_since = self.idle_since
                if not source_items:
                    if idle_since is None:
                        with self.lock:
                            if self.idle_since is None:
                                self.idle_since = now
                    elif now - idle_since >= MULTICAST_RTP_IDLE_SECONDS:
                        break
                    mixed_frame = MULTICAST_RTP_SILENCE_FRAME
                else:
                    with self.lock:
                        self.idle_since = None
                    emergency_active = any(source.is_emergency() for _stream_id, source in source_items)
                    for stream_id, source in source_items:
                        discard = emergency_active and not source.is_emergency()
                        frame, finished = source.next_frame(discard=discard)
                        if frame is not None:
                            frames.append(frame)
                        if source.note_preroll_frame():
                            ready_sources.append(stream_id)
                        if finished:
                            finished_stream_ids.append(stream_id)
                    mixed_frame = mix_ulaw_frames(frames)
                self.sender.send_frame(mixed_frame)
                for stream_id in ready_sources:
                    self.on_idle("ready", self.key, stream_id, self)
                if finished_stream_ids:
                    with self.lock:
                        for stream_id in finished_stream_ids:
                            source = self.sources.get(stream_id)
                            if source is not None and source.closed:
                                self.sources.pop(stream_id, None)
                        if not self.sources and self.idle_since is None:
                            self.idle_since = time.monotonic()
                next_send += MULTICAST_RTP_FRAME_MS / 1000.0
                sleep_for = next_send - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_send = time.monotonic()
        finally:
            try:
                self.sender.close()
            finally:
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

    def get_endpoint_status(self):
        return get_multicast_rtp_endpoint_status()

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
            if channel is None:
                channel = MulticastRTPEndpointChannel(row, self.handle_channel_event)
                self.channels[channel_key] = channel
            return channel

    def handle_dispatch(self, action, stream_id, msg_id, sub_targets, metadata=None):
        if action not in {"prepare_audio", "prepare_livepage"}:
            return
        rows = multicast_rtp_rows_for_targets(sub_targets)
        state = MulticastRTPStreamState(stream_id)
        priority = multicast_priority_value(metadata)
        for row in rows:
            channel = self.ensure_channel(row)
            state.add_source(channel.key, channel.attach_source(stream_id, priority=priority))
        with self.lock:
            previous = self.streams.pop(stream_id, None)
            self.streams[stream_id] = state
        if previous is not None:
            try:
                previous.close()
            except Exception as exc:
                log(f"multicast rtp stream replace error stream={stream_id}: {exc}")
        if not rows:
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


def ensure_builtin_modules_loaded():
    global siptrunks_runtime, multicast_rtp_runtime
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


def load_endpoint_web_module(module, missing_ok=False):
    if module == "siptrunks":
        return BuiltinSipTrunksWeb()
    if module == MULTICAST_RTP_MODULE:
        return BuiltinMulticastRTPWeb()
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
    package = discover_endpoint_packages(extract_if_trusted=True).get(module)
    if not package or not package.get("trusted"):
        return None
    return Path(package.get("web_path") or "")


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
    for audio_file in str(audio_files_str or "").split(":"):
        audio_file = audio_file.strip()
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
            target_list = set()
            if str(group_id) == "0":
                for module_name in output_module_names():
                    target_list.add(f"{module_name}/all")
            else:
                for gid in str(group_id or "").split("."):
                    gid = gid.strip()
                    if not gid:
                        continue
                    cur.execute("SELECT members FROM groups WHERE id = %s", (gid,))
                    group_row = cur.fetchone()
                    if group_row and group_row[0]:
                        for member in group_row[0].replace(",", " ").split():
                            target_list.add(member)
            return sorted(target_list)
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
    if not module_type_has_output(module_type):
        return False
    if mod is None:
        with loaded_modules_lock:
            mod = loaded_modules.get(module_name)
    if mod is not None and hasattr(mod, "get_endpoint_status"):
        try:
            status_info = mod.get_endpoint_status()
            if isinstance(status_info, dict):
                if status_info.get("output_capable") is False:
                    return False
                for endpoint in status_info.get("endpoints") or []:
                    if endpoint_is_output_capable(endpoint):
                        return True
        except Exception as exc:
            log(f"module_is_output_capable status error module={module_name}: {exc}")
    return True


def output_module_names():
    with loaded_modules_lock:
        modules_snapshot = list(loaded_modules.items())
    names = []
    for module_name, mod in modules_snapshot:
        if module_name == MULTICAST_RTP_MODULE and multicast_rtp_endpoint_count() <= 0:
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
            cur.execute("SHOW COLUMNS FROM broadcasts")
            broadcast_columns = {row[0] for row in cur.fetchall()}
            if "vendor_specific" not in broadcast_columns:
                cur.execute("ALTER TABLE broadcasts ADD COLUMN vendor_specific TEXT DEFAULT NULL")
            else:
                cur.execute("ALTER TABLE broadcasts MODIFY COLUMN vendor_specific TEXT DEFAULT NULL")
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
            expire_message_rule_broadcasts(cur, expires_rule, [broadcast_id])
            expire_broadcasts_triggered_by_template(cur, message_id, [broadcast_id])
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
            expire_message_rule_broadcasts(cur, expires_rule, [broadcast_id])
            expire_any_message_rule_broadcasts(cur, [broadcast_id])
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
    log(f"load_module {module_dir}")


def mark_module_load_error(module_dir, exc):
    with loaded_modules_lock:
        module_load_errors[module_dir] = str(exc)
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
        if module_dir in {"siptrunks", MULTICAST_RTP_MODULE}:
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
    for target in targets:
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
        mark_ready(module_name, stream_id)


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


def recv_line(conn):
    data = bytearray()
    while True:
        chunk = conn.recv(1)
        if not chunk:
            break
        if chunk == b"\n":
            break
        data.extend(chunk)
    return bytes(data)


def send_ipc_json(conn, payload):
    conn.sendall(json.dumps(payload, default=str).encode("utf-8") + b"\n")


def decode_ipc_json_token(token):
    raw = base64.b64decode(str(token or "").encode("ascii"), validate=True)
    return json.loads(raw.decode("utf-8"))


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
        threading.Thread(target=handle_ipc_client, args=(conn,), daemon=True).start()


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
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        log(f"handle_prepare audio_chunk stream={stream_id} bytes={len(chunk)} modules={list(target_map.keys())}")
        for module_name in target_map:
            with loaded_modules_lock:
                mod = loaded_modules.get(module_name)
            if mod and hasattr(mod, "receive_audio"):
                try:
                    mod.receive_audio(chunk, stream_id)
                except Exception as exc:
                    log(f"receive_audio error in {module_name}: {exc}")
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
        f"ready_modules={sorted(state.ready_modules)} pending={sorted(state.pending_modules)}"
    )
    if not ready:
        pop_stream_state(stream_id)
        page_debug(f"handle_stream_prepare_timeout action={action_name} stream={stream_id}")
        conn.sendall(b"ERROR\n")
        return
    conn.sendall(b"OK\n")
    page_debug(f"handle_stream_prepare_ok action={action_name} stream={stream_id}")
    chunk_count = 0
    byte_count = 0
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunk_count += 1
        byte_count += len(chunk)
        if chunk_count == 1 or chunk_count % 50 == 0:
            page_debug(
                f"handle_stream_prepare_audio action={action_name} stream={stream_id} "
                f"chunks={chunk_count} bytes={byte_count} last_chunk={len(chunk)} modules={list(target_map.keys())}"
            )
        log(f"handle_stream_prepare action={action_name} audio_chunk stream={stream_id} bytes={len(chunk)} modules={list(target_map.keys())}")
        for module_name in target_map:
            with loaded_modules_lock:
                mod = loaded_modules.get(module_name)
            if mod and hasattr(mod, "receive_audio"):
                try:
                    mod.receive_audio(chunk, stream_id)
                except Exception as exc:
                    log(f"receive_audio error in {module_name}: {exc}")
                    page_debug(f"receive_audio_error module={module_name} stream={stream_id} error={exc.__class__.__name__}: {exc}")
    page_debug(f"handle_stream_prepare_end action={action_name} stream={stream_id} chunks={chunk_count} bytes={byte_count}")
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
        return False
    targets = resolve_group_targets(broadcast.get("groups"))
    if not targets:
        log(f"handle_broadcast no_targets stream={stream_id} broadcast={broadcast_id} groups={broadcast.get('groups')}")
        return False
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
            desktop_sock = None
            try:
                desktop_sock, desktop_result = start_desktop_broadcast_stream(broadcast_id, codec="mulaw", sample_rate=8000)
            except Exception as exc:
                desktop_sock = None
                desktop_result = {}
                log(f"desktop broadcast start error broadcast={broadcast_id}: {exc}")
            desktop_matched = int(desktop_result.get("matched") or 0)
            if not target_map and desktop_matched <= 0:
                log(f"handle_broadcast no_target_modules stream={stream_id} broadcast={broadcast_id} targets={targets}")
                return False
            if target_map:
                state = create_stream_state(stream_id, target_map)
                dispatch("prepare_audio", stream_id, broadcast_id, targets, metadata)
                ready = state.ready_event.wait(10.0)
                log(f"handle_broadcast waited stream={stream_id} ready={ready}")
            frame_duration = 160 / 8000
            next_send_time = time.perf_counter()
            for frame in [first_frame]:
                for module_name in target_map:
                    with loaded_modules_lock:
                        mod = loaded_modules.get(module_name)
                    if mod and hasattr(mod, "receive_audio"):
                        try:
                            mod.receive_audio(frame, stream_id)
                        except Exception as exc:
                            log(f"receive_audio error in {module_name}: {exc}")
                try:
                    if desktop_sock is not None:
                        send_stream_frame(desktop_sock, frame)
                except Exception as exc:
                    log(f"desktop broadcast frame error broadcast={broadcast_id}: {exc}")
            for frame in gen:
                next_send_time += frame_duration
                sleep_time = next_send_time - time.perf_counter()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    next_send_time = time.perf_counter()
                for module_name in target_map:
                    with loaded_modules_lock:
                        mod = loaded_modules.get(module_name)
                    if mod and hasattr(mod, "receive_audio"):
                        try:
                            mod.receive_audio(frame, stream_id)
                        except Exception as exc:
                            log(f"receive_audio error in {module_name}: {exc}")
                try:
                    if desktop_sock is not None:
                        send_stream_frame(desktop_sock, frame)
                except Exception as exc:
                    log(f"desktop broadcast frame error broadcast={broadcast_id}: {exc}")
            try:
                if desktop_sock is not None:
                    desktop_sock.close()
            except Exception:
                pass
            if target_map:
                finish_stream(stream_id)
            return True
        log(f"handle_broadcast audio_type_no_audio broadcast={broadcast_id} audio={audio_files}")
    dispatch("sendmsg", stream_id, broadcast_id, targets, metadata)
    return True


def finish_claimed_broadcast_delivery(stream_id, broadcast_id, source):
    try:
        try:
            sync_modules()
        except Exception as exc:
            log(f"{source} sync_modules error broadcast={broadcast_id}: {exc}")
        if deliver_broadcast(stream_id, broadcast_id):
            mark_broadcast_history_delivery(broadcast_id, "sent")
            mark_active_broadcast_delivery(broadcast_id, "sent")
            log(f"{source} dispatched broadcast={broadcast_id} stream={stream_id}")
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
    if not any(module_name == "siptrunks" for module_name, _mod in modules_snapshot):
        try:
            sip_info = get_siptrunks_endpoint_status()
            sip_info["count"] = len(sip_info.get("endpoints") or [])
            modules.append(sip_info)
        except Exception as exc:
            modules.append(
                {
                    "module": "siptrunks",
                    "display_name": "SIP Trunks",
                    "count": 0,
                    "endpoints": [],
                    "error": str(exc),
                    "system_builtin": True,
                }
            )
    for module_name, mod in modules_snapshot:
        module_info = {
            "module": module_name,
            "display_name": module_name,
            "count": 0,
            "endpoints": [],
            "input_capable": module_is_input_capable(module_name),
            "output_capable": module_is_output_capable(module_name, mod),
        }
        try:
            if hasattr(mod, "get_endpoint_status"):
                status_info = mod.get_endpoint_status()
                if isinstance(status_info, dict):
                    module_info.update(status_info)
            else:
                module_info["error"] = "Module does not support endpoint status"
        except Exception as exc:
            module_info["error"] = str(exc)
            log(f"get_endpoint_status error in {module_name}: {exc}")
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
        module_info["module"] = module_info.get("module") or module_name
        module_info["display_name"] = module_info.get("display_name") or module_info["module"]
        module_info["count"] = len(endpoints)
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


import base64
import hashlib
import html
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import wave
import xml.etree.ElementTree as ET
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
    from broadcasts import is_audio_type
except Exception:
    def is_audio_type(value):
        return str(value or "").strip() in ("audio", "text+audio", "liveaudio", "liveaudio+text", "AudioMessage", "Text+AudioMessage", "Page")

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
LOG_FILE = MODULE_LOG_DIR / "endpoint_dispatch.log"

loaded_modules = {}
module_load_errors = {}
loaded_modules_lock = threading.Lock()
stream_states = {}
stream_states_lock = threading.Lock()
broadcast_watcher_stop = threading.Event()
broadcast_delivery_ids = set()
broadcast_delivery_lock = threading.Lock()
core = None
server_socket = None


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


def init(core_obj):
    global core
    core = core_obj
    try:
        ensure_siptrunks_schema()
    except Exception as exc:
        log(f"siptrunks schema init error: {exc}")
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


def safe_module_name(value):
    return re.fullmatch(r"^[A-Za-z0-9_-]+$", str(value or "")) is not None


def package_module_name(value):
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-_")
    return normalized or "module"


def supports_unix_sockets():
    return hasattr(socket, "AF_UNIX") and os.name != "nt"


def connect_endpoint_ipc(timeout=2):
    if supports_unix_sockets() and Path("/run/openpagingserver/endpointmodules.sock").exists():
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(Path("/run/openpagingserver/endpointmodules.sock")))
            return sock
        except Exception:
            sock.close()
            raise
    return socket.create_connection(("127.0.0.1", 50000), timeout=timeout)


def create_endpoint_ipc_server_socket():
    if supports_unix_sockets():
        Path("/run/openpagingserver/endpointmodules.sock").parent.mkdir(parents=True, exist_ok=True)
        try:
            if Path("/run/openpagingserver/endpointmodules.sock").exists() or Path("/run/openpagingserver/endpointmodules.sock").is_socket():
                Path("/run/openpagingserver/endpointmodules.sock").unlink()
        except OSError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(Path("/run/openpagingserver/endpointmodules.sock")))
        os.chmod(Path("/run/openpagingserver/endpointmodules.sock"), 0o600)
        return sock, f"unix:{Path('/run/openpagingserver/endpointmodules.sock')}"
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


def ensure_siptrunks_schema():
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{SIP_TRUNK_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`status` VARCHAR(255) NOT NULL DEFAULT 'Offline', "
                "`auth` VARCHAR(32) NOT NULL DEFAULT 'IP', "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`username` VARCHAR(255) DEFAULT NULL, "
                "`password` VARCHAR(255) DEFAULT NULL, "
                "`ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0', "
                "`holdbehavior` VARCHAR(32) NOT NULL DEFAULT 'passrtp', "
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
            trunk_columns = sip_table_columns(cur, SIP_TRUNK_TABLE)
            trunk_additions = {
                "status": "`status` VARCHAR(255) NOT NULL DEFAULT 'Offline'",
                "auth": "`auth` VARCHAR(32) NOT NULL DEFAULT 'IP'",
                "name": "`name` VARCHAR(255) NOT NULL DEFAULT ''",
                "username": "`username` VARCHAR(255) DEFAULT NULL",
                "password": "`password` VARCHAR(255) DEFAULT NULL",
                "ipaddr": "`ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0'",
                "holdbehavior": "`holdbehavior` VARCHAR(32) NOT NULL DEFAULT 'passrtp'",
            }
            for column, sql in trunk_additions.items():
                if column not in trunk_columns:
                    cur.execute(f"ALTER TABLE `{SIP_TRUNK_TABLE}` ADD COLUMN {sql}")
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
        conn.commit()
    finally:
        conn.close()


def siptrunks_status_label(row):
    raw = str(row.get("status") or "").strip()
    if not raw:
        return "Offline"
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
    auth_type = str(row.get("auth") or "").upper()
    return "Authenticated SIP Trunk" if auth_type == "USERPASS" else "IP SIP Trunk"


def siptrunks_row_name(row):
    name = str(row.get("name") or row.get("username") or row.get("ipaddr") or f"SIP Trunk {row.get('id')}")
    auth_type = str(row.get("auth") or "").upper()
    ipaddr = str(row.get("ipaddr") or "").strip()
    if auth_type == "IP" and ipaddr:
        return f"{name} ({ipaddr})"
    return name


def siptrunks_dialplan_row_name(row):
    name = str(row.get("name") or row.get("extension") or f"SIP Extension {row.get('id')}")
    extension = str(row.get("extension") or "").strip()
    return f"{name} ({extension})" if extension else name


def get_siptrunks_endpoint_status():
    ensure_siptrunks_schema()
    endpoints = []
    conn = get_dict_db_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    f"SELECT `id`, `name`, `auth`, `username`, `ipaddr`, `status` "
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
    finally:
        conn.close()
    for row in rows:
        endpoints.append(
            {
                "id": f"trunk-{row.get('id')}",
                "name": siptrunks_row_name(row),
                "address": "",
                "model": "",
                "status": siptrunks_status_label(row),
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
    return {
        "module": "siptrunks",
        "display_name": "SIP Trunks",
        "name": "SIP Trunks",
        "description": "Built-in SIP trunk and SIP dialplan endpoint management.",
        "system_builtin": True,
        "enabled": True,
        "loaded": True,
        "trusted": True,
        "can_load": True,
        "output_capable": False,
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
        "<style>body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}"
        ".grid{display:grid;gap:12px}.row{display:grid;gap:6px}.surface{max-width:760px;border:1px solid #e6e8eb;border-radius:8px;padding:18px;background:#fff}"
        "label{font-weight:500}.control,input,select{padding:10px 11px;border:1px solid #ccd1d5;border-radius:6px;font:inherit;box-sizing:border-box;width:100%;background:#fff;color:#202124}.short{max-width:190px}"
        "button,.button{background:#1976D2;color:#fff;border:0;border-radius:6px;padding:10px 14px;font:inherit;cursor:pointer;justify-self:start;text-decoration:none}.danger{background:#C62828}"
        ".error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}.meta{color:#5f6368;margin:0 0 12px}.hint{color:#5f6368;font-size:.9em}"
        "@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.surface{background:#232323;border-color:#333}.control,input,select{background:#171717;border-color:#3a3a3a;color:#eee}button,.button{background:#BB86FC;color:#000}.danger{background:#EF9A9A}.meta,.hint{color:#aaa}}</style>"
        + body
    )


def sip_group_options(selected):
    selected_groups = set(str(selected or "").split(".")) if selected else set()
    return "".join(
        f'<label><input type="checkbox" name="group_item" value="{h(row.get("id"))}"{" checked" if str(row.get("id")) in selected_groups else ""}> '
        f'{h("All Recipients" if str(row.get("id")) == "0" else str(row.get("id")) + (" - " + str(row.get("name")) if row.get("name") else ""))}</label>'
        for row in sip_fetch_groups()
    )


def sip_dialplan_fields(values):
    trigger_options = "".join(
        f'<option value="{h(value)}"{" selected" if value == values["trigger_type"] else ""}>{h(label)}</option>'
        for value, label in (("page", "Paging"), ("message", "Send Message"), ("#testtone", "Milliwatt Test Tone"), ("#echotest", "Echo Test"))
    )
    message_options = "".join(
        f'<option value="{h(row.get("id"))}"{" selected" if str(row.get("id")) == values["message_id"] else ""}>{h(row.get("id"))} - {h(row.get("name") or "")}</option>'
        for row in sip_fetch_messages()
    )
    return f"""<div class="row"><label>Name</label><input class="control" name="name" value="{h(values["name"])}" required></div>
<div class="row"><label>Extension</label><input class="control short" name="extension" value="{h(values["extension"])}" required pattern="[0-9*#]*" inputmode="tel"></div>
<div class="row"><label>Trigger</label><select class="control" name="trigger_type">{trigger_options}</select></div>
<div class="row"><label>Message</label><select class="control" name="message_id"><option value="">Choose a message</option>{message_options}</select></div>
<div class="row"><label>Groups</label><div class="grid">{sip_group_options(values["group"])}</div><p class="hint">Choose All Recipients or one or more groups.</p></div>
<div class="row"><label>Passcode</label><input class="control short" name="passcode" value="{h(values["passcode"])}" pattern="[0-9A-Da-d]*" inputmode="text"></div>"""


class BuiltinSipTrunksWeb:
    def forms(self):
        return {
            "ip": {"label": "IP SIP Trunk", "description": "Trust SIP requests from a specific trunk IP address."},
            "auth": {"label": "Authenticated SIP Trunk", "description": "Authenticate SIP requests with a username and password."},
            "dialplan": {"label": "SIP Dialplan Extension", "description": "Route a SIP extension to paging, messaging, test tone, or echo test."},
        }

    def render_form(self, form_type, request, conn_factory, page, user):
        ensure_siptrunks_schema()
        if form_type not in self.forms():
            return page("Endpoint Form", "<h1>Endpoint form not found</h1>", "endpoints", user, status=404)
        error = ""
        values = {
            "ip": {"name": "", "ipaddr": ""},
            "auth": {"name": "", "username": "", "password": "", "ipaddr": "0.0.0.0"},
            "dialplan": {"name": "", "extension": "", "group": "", "trigger_type": "page", "message_id": "", "passcode": ""},
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
                        f"INSERT INTO `{SIP_TRUNK_TABLE}` (name, auth, username, password, ipaddr, status) VALUES (%s,'IP',NULL,NULL,%s,'Offline')",
                        (values["name"], values["ipaddr"]),
                    )
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
                        f"INSERT INTO `{SIP_TRUNK_TABLE}` (name, auth, username, password, ipaddr, status) VALUES (%s,'USERPASS',%s,%s,%s,'Offline')",
                        (values["name"], values["username"], values["password"], values["ipaddr"] or "0.0.0.0"),
                    )
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            else:
                for key in values:
                    values[key] = request.form.get(key, values[key]).strip()
                values["group"] = sip_clean_groups(request.form.getlist("group_item") or request.form.get("group", ""))
                values["passcode"] = values["passcode"].upper()
                trigger = sip_dialplan_trigger(values["trigger_type"], values["message_id"])
                if values["trigger_type"] not in {"page", "message"}:
                    values["group"] = ""
                if not values["name"] or not values["extension"]:
                    error = "Name and extension are required."
                elif not re.fullmatch(r"[0-9*#]+", values["extension"]):
                    error = "Extension can only contain 0-9, *, and #."
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
        error_html = f'<div class="error">{h(error)}</div>' if error else ""
        if form_type == "ip":
            body = f"""{error_html}<form method="post" class="grid surface">
<div class="row"><label>Name</label><input class="control" name="name" value="{h(values["name"])}" required></div>
<div class="row"><label>IP Address</label><input class="control" name="ipaddr" value="{h(values["ipaddr"])}" required></div>
<button type="submit">Add IP SIP Trunk</button></form>"""
        elif form_type == "auth":
            body = f"""{error_html}<form method="post" class="grid surface">
<div class="row"><label>Name</label><input class="control" name="name" value="{h(values["name"])}" required></div>
<div class="row"><label>Username</label><input class="control" name="username" value="{h(values["username"])}" required></div>
<div class="row"><label>Password</label><input class="control" type="password" name="password" value="{h(values["password"])}" required></div>
<div class="row"><label>IP Restriction</label><input class="control" name="ipaddr" value="{h(values["ipaddr"])}" required></div>
<button type="submit">Add Authenticated SIP Trunk</button></form>"""
        else:
            body = f'{error_html}<form method="post" class="grid surface">{sip_dialplan_fields(values)}<button type="submit">Add SIP Dialplan Extension</button></form>'
        return page(self.forms()[form_type]["label"], sip_form_frame(body), "endpoints", user)

    def render_action(self, action, endpoint_id, request, conn_factory, page, user):
        ensure_siptrunks_schema()
        kind, _, row_id = str(endpoint_id or "").partition("-")
        if action not in {"edit", "delete"} or kind not in {"trunk", "dialplan"} or not row_id.isdigit():
            return page("Endpoint Action", "<h1>Invalid endpoint action</h1>", "endpoints", user, status=400)
        table = SIP_TRUNK_TABLE if kind == "trunk" else SIP_DIALPLAN_TABLE
        rows = sip_query_all(f"SELECT * FROM `{table}` WHERE id=%s LIMIT 1", (row_id,))
        if not rows:
            return page("Endpoint Action", "<h1>Endpoint not found</h1>", "endpoints", user, status=404)
        row = rows[0]
        error = ""
        if request.method == "POST":
            if action == "delete":
                sip_execute(f"DELETE FROM `{table}` WHERE id=%s", (row_id,))
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
            if kind == "trunk":
                name = request.form.get("name", "").strip()
                ipaddr = request.form.get("ipaddr", "").strip()
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "").strip()
                holdbehavior = request.form.get("holdbehavior", "passrtp").strip().lower()
                auth_type = str(row.get("auth") or "IP").upper()
                if not name:
                    error = "Name is required."
                elif holdbehavior not in {"passrtp", "pausertp", "endcall"}:
                    error = "Choose a valid hold behavior."
                elif auth_type == "IP":
                    if not ipaddr or not sip_valid_ip(ipaddr):
                        error = "Enter a valid IP address."
                    else:
                        sip_execute(
                            f"UPDATE `{table}` SET name=%s, username=NULL, password=NULL, ipaddr=%s, holdbehavior=%s WHERE id=%s",
                            (name, ipaddr, holdbehavior, row_id),
                        )
                        return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
                elif not username or not password:
                    error = "Username and password are required."
                elif not sip_valid_ip_or_network(ipaddr or "0.0.0.0"):
                    error = "Enter a valid IP restriction."
                else:
                    sip_execute(
                        f"UPDATE `{table}` SET name=%s, username=%s, password=%s, ipaddr=%s, holdbehavior=%s WHERE id=%s",
                        (name, username, password, ipaddr or "0.0.0.0", holdbehavior, row_id),
                    )
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
                row.update({"name": name, "ipaddr": ipaddr, "username": username, "password": password, "holdbehavior": holdbehavior})
            else:
                name = request.form.get("name", "").strip()
                extension = request.form.get("extension", "").strip()
                trigger_type = request.form.get("trigger_type", "page").strip()
                message_id = request.form.get("message_id", "").strip()
                group = sip_clean_groups(request.form.getlist("group_item") or request.form.get("group", ""))
                passcode = request.form.get("passcode", "").strip().upper()
                trigger = sip_dialplan_trigger(trigger_type, message_id)
                if trigger_type not in {"page", "message"}:
                    group = ""
                if not name or not extension:
                    error = "Enter a name and extension."
                elif not re.fullmatch(r"[0-9*#]+", extension):
                    error = "Extension can only contain 0-9, *, and #."
                elif trigger_type == "message" and not message_id:
                    error = "Choose a message."
                elif trigger_type in {"page", "message"} and not group:
                    error = "Choose at least one group."
                elif passcode and not re.fullmatch(r"[0-9A-D]+", passcode):
                    error = "Passcode can only contain 0-9 and A-D."
                else:
                    sip_execute(
                        f"UPDATE `{table}` SET name=%s, extension=%s, `group`=%s, trigger=%s, passcode=%s WHERE id=%s",
                        (name, extension, group or None, trigger, passcode or None, row_id),
                    )
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p>Endpoint saved.</p>", "endpoints", user)
                row.update({"name": name, "extension": extension, "group": group, "trigger": trigger, "passcode": passcode})
        error_html = f'<div class="error">{h(error)}</div>' if error else ""
        if action == "delete":
            body = f"""{error_html}<form method="post" class="grid surface">
<p class="meta">Delete {h(row.get("name") or endpoint_id)}?</p>
<button class="danger" type="submit">Delete Endpoint</button></form>"""
        elif kind == "trunk":
            auth_type = str(row.get("auth") or "IP").upper()
            hold_value = str(row.get("holdbehavior") or "passrtp").lower()
            options = "".join(
                f'<option value="{h(value)}"{" selected" if hold_value == value else ""}>{h(label)}</option>'
                for value, label in (("passrtp", "Pass RTP"), ("pausertp", "Pause RTP"), ("endcall", "End Call"))
            )
            auth_fields = (
                f'<div class="row"><label>IP Address</label><input class="control" name="ipaddr" value="{h(row.get("ipaddr"))}" required></div>'
                if auth_type == "IP"
                else f'<div class="row"><label>Username</label><input class="control" name="username" value="{h(row.get("username"))}" required></div><div class="row"><label>Password</label><input class="control" type="password" name="password" value="{h(row.get("password"))}" required></div><div class="row"><label>IP Restriction</label><input class="control" name="ipaddr" value="{h(row.get("ipaddr") or "0.0.0.0")}" required></div>'
            )
            body = f"""{error_html}<form method="post" class="grid surface">
<p class="meta">Current status: {h(row.get("status") or "Offline")}</p>
<div class="row"><label>Name</label><input class="control" name="name" value="{h(row.get("name"))}" required></div>
{auth_fields}<div class="row"><label>Hold Behavior</label><select class="control" name="holdbehavior">{options}</select></div>
<button type="submit">Save SIP Trunk</button></form>"""
        else:
            trigger_type, message_id = sip_split_dialplan_trigger(row.get("trigger"))
            values = {
                "name": str(row.get("name") or ""),
                "extension": str(row.get("extension") or ""),
                "group": str(row.get("group") or ""),
                "trigger_type": trigger_type,
                "message_id": message_id,
                "passcode": str(row.get("passcode") or ""),
            }
            body = f'{error_html}<form method="post" class="grid surface">{sip_dialplan_fields(values)}<button type="submit">Save SIP Dialplan Extension</button></form>'
        return page("Endpoint Action", sip_form_frame(body), "endpoints", user)

    def render_settings(self, request, conn_factory, page, user):
        return page("SIP Trunk Settings", "<p>No additional settings are required for SIP trunks.</p>", "endpoints", user)


def load_endpoint_web_module(module, missing_ok=False):
    if module == "siptrunks":
        return BuiltinSipTrunksWeb()
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
    search_roots = [Path("/var/lib/openpagingserver/assets/"), BASE_DIR / "assets", BASE_DIR / "sip" / "audio"]
    for root in search_roots:
        path = root / audio_file
        if path.exists():
            return str(path)
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
    discovered = discover_modules()
    entry = discovered.get(module_name)
    if entry is None:
        return ""
    info = module_info_from_entry(module_name, entry)
    return str(info.get("input_type") or "")


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
    if "output" not in module_type and "management" in module_type:
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
    return "output" in module_type


def output_module_names():
    with loaded_modules_lock:
        modules_snapshot = list(loaded_modules.items())
    return [
        module_name
        for module_name, mod in modules_snapshot
        if module_is_output_capable(module_name, mod)
    ]


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
        if module_dir not in enabled:
            try:
                unload_module(module_dir)
            except Exception as exc:
                log(f"unload_module error {module_dir}: {exc}")


def shutdown_all():
    global server_socket
    broadcast_watcher_stop.set()
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
            if Path("/run/openpagingserver/endpointmodules.sock").exists() or Path("/run/openpagingserver/endpointmodules.sock").is_socket():
                Path("/run/openpagingserver/endpointmodules.sock").unlink()
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
            if not target_map:
                log(f"handle_broadcast no_target_modules stream={stream_id} broadcast={broadcast_id} targets={targets}")
                return False
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

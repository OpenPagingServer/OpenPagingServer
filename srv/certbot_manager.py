import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

CERTBOT_JOB_ROOT = Path(os.getenv("OPS_CERTBOT_JOB_PATH", "/var/lib/openpagingserver/certbot-jobs"))
CERTBOT_JOBS = {}
CERTBOT_JOBS_LOCK = threading.Lock()
CERTBOT_RUNTIME_LOCK = threading.Lock()
CERTBOT_RUNTIME_CACHE = None
CERTBOT_RUNTIME_CACHE_SECONDS = 60
CERTBOT_OPTIONS_LOCK = threading.Lock()
CERTBOT_OPTIONS_CACHE = {}
CERTBOT_ACCOUNT_LOCK = threading.Lock()
CERTBOT_LOCK_RETRY_SECONDS = 15
CERTBOT_ACCOUNT_JOBS = {}
CERTBOT_ACCOUNT_JOBS_LOCK = threading.Lock()
CERTBOT_CONFIG_ROOT = Path(os.getenv("OPS_CERTBOT_CONFIG_PATH", "/etc/letsencrypt"))
HOSTNAME_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
EMAIL_RE = re.compile(r"^[^\s@,]+@[^\s@,]+\.[^\s@,]+$")


def certbot_environment(extra=None, binary=None):
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    system_dist_packages = Path("/usr/lib/python3/dist-packages")
    if binary and os.path.realpath(str(binary)) == "/usr/bin/certbot" and system_dist_packages.is_dir():
        env["PYTHONPATH"] = str(system_dist_packages)
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def certbot_supported_options(binary):
    cache_key = os.path.normcase(os.path.realpath(str(binary)))
    with CERTBOT_OPTIONS_LOCK:
        cached = CERTBOT_OPTIONS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            [binary, "--help", "all"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=certbot_environment(binary=binary),
        )
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).lower()
        options = frozenset(re.findall(r"--[a-z0-9][a-z0-9-]*", output))
    except (OSError, subprocess.TimeoutExpired):
        options = frozenset()
    with CERTBOT_OPTIONS_LOCK:
        CERTBOT_OPTIONS_CACHE[cache_key] = options
    return options


def certbot_auth_hook_path():
    return Path(__file__).resolve().parent / "web" / "certbot_dns_auth.py"


def find_account_emails(value):
    emails = []
    if isinstance(value, dict):
        for item in value.values():
            emails.extend(find_account_emails(item))
    elif isinstance(value, list):
        for item in value:
            emails.extend(find_account_emails(item))
    elif isinstance(value, str) and value.lower().startswith("mailto:"):
        email = value[7:].strip()
        if EMAIL_RE.fullmatch(email) and email not in emails:
            emails.append(email)
    return emails


def certbot_account_status(config_root=None, binary=None):
    accounts_root = Path(config_root or CERTBOT_CONFIG_ROOT) / "accounts"
    try:
        registration_files = list(accounts_root.glob("**/regr.json"))
    except OSError:
        registration_files = []
    registered = bool(registration_files)
    emails = []
    for registration_file in registration_files:
        try:
            payload = json.loads(registration_file.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        for email in find_account_emails(payload):
            if email not in emails:
                emails.append(email)
    if not emails and binary:
        try:
            result = subprocess.run(
                [binary, "show_account", "--non-interactive"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                env=certbot_environment(binary=binary),
            )
            output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
            if result.returncode == 0:
                registered = True
                match = re.search(r"(?im)^\s*Email contact:\s*(.*?)\s*$", output)
                if match:
                    for candidate in match.group(1).split(","):
                        email = candidate.strip()
                        if EMAIL_RE.fullmatch(email) and email not in emails:
                            emails.append(email)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "registered": registered,
        "email": emails[0] if emails else "",
        "ready": bool(registered and emails),
    }


def normalize_certbot_email(value):
    email = str(value or "").strip()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise ValueError("Enter a valid email address.")
    return email


def certbot_instance_locked(output):
    return "another instance of certbot is already running" in str(output or "").lower()


def run_certbot_account_command(command, binary):
    deadline = time.monotonic() + CERTBOT_LOCK_RETRY_SECONDS
    while True:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=certbot_environment(binary=binary),
        )
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        if result.returncode == 0:
            return output
        if certbot_instance_locked(output) and time.monotonic() < deadline:
            time.sleep(1)
            continue
        if certbot_instance_locked(output):
            raise RuntimeError(
                "Another Certbot operation is still running. Wait for it to finish or cancel the existing "
                "certificate request, then click Continue again."
            )
        raise RuntimeError(output[-2000:] or "Certbot could not configure the Let's Encrypt account.")


def ensure_certbot_account(email_value, terms_agreed, marketing_opt_in=False):
    runtime = certbot_runtime_status()
    binary = runtime.get("binary") if runtime.get("available") else None
    if not binary:
        raise RuntimeError(runtime.get("error") or "Certbot is not installed on this server.")
    if not terms_agreed:
        raise ValueError("You must agree to the Let's Encrypt Subscriber Agreement.")
    email = normalize_certbot_email(email_value)
    with CERTBOT_ACCOUNT_LOCK:
        account = certbot_account_status(binary=binary)
        command = [
            binary,
            "update_account" if account["registered"] else "register",
            "--email",
            email,
            "--non-interactive",
        ]
        if not account["registered"]:
            command.append("--agree-tos")
        options = certbot_supported_options(binary)
        if marketing_opt_in and "--eff-email" in options:
            command.append("--eff-email")
        elif "--no-eff-email" in options:
            command.append("--no-eff-email")
        run_certbot_account_command(command, binary)
        account = certbot_account_status(binary=binary)
    return {
        "registered": True,
        "email": account.get("email") or email,
        "ready": True,
    }


def public_certbot_account_job(job):
    if not job:
        return None
    return {
        "id": job["id"],
        "status": job.get("status", "working"),
        "error": job.get("error", ""),
    }


def run_certbot_account_job(job_id, email, terms_agreed, marketing_opt_in):
    try:
        ensure_certbot_account(email, terms_agreed, marketing_opt_in)
    except Exception as exc:
        with CERTBOT_ACCOUNT_JOBS_LOCK:
            job = CERTBOT_ACCOUNT_JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = str(exc) or "Certbot could not configure the Let's Encrypt account."
        return
    with CERTBOT_ACCOUNT_JOBS_LOCK:
        job = CERTBOT_ACCOUNT_JOBS.get(job_id)
        if job:
            job["status"] = "success"
            job["error"] = ""


def start_certbot_account_job(email_value, terms_agreed, marketing_opt_in=False):
    if not certbot_candidates():
        raise RuntimeError("Certbot is not installed on this server.")
    email = normalize_certbot_email(email_value)
    if not terms_agreed:
        raise ValueError("You must agree to the Let's Encrypt Subscriber Agreement.")
    job_id = uuid.uuid4().hex
    job = {"id": job_id, "status": "working", "error": ""}
    with CERTBOT_ACCOUNT_JOBS_LOCK:
        CERTBOT_ACCOUNT_JOBS[job_id] = job
    threading.Thread(
        target=run_certbot_account_job,
        args=(job_id, email, True, bool(marketing_opt_in)),
        daemon=True,
    ).start()
    return public_certbot_account_job(job)


def get_certbot_account_job(job_id):
    with CERTBOT_ACCOUNT_JOBS_LOCK:
        return public_certbot_account_job(CERTBOT_ACCOUNT_JOBS.get(str(job_id or "").strip()))


def parse_certbot_certificate_paths(output):
    paths = {}
    labels = {
        "certificate path": "certificate_path",
        "private key path": "private_key_path",
    }
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        label, separator, value = line.partition(":")
        key = labels.get(label.strip().lower())
        if separator and key and value.strip():
            paths[key] = value.strip()
    return paths.get("certificate_path", ""), paths.get("private_key_path", "")


def certbot_certificate_paths(certbot_name, binary=None):
    name = str(certbot_name or "").strip()
    if not name or "/" in name or "\\" in name:
        raise ValueError("Invalid Certbot certificate name.")
    selected_binary = binary or certbot_binary()
    if not selected_binary:
        raise RuntimeError("Certbot is not installed on this server.")
    result = subprocess.run(
        [selected_binary, "certificates", "--cert-name", name],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=certbot_environment(binary=selected_binary),
    )
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise RuntimeError(output[-2000:] or f"Certbot could not inspect certificate {name}.")
    certificate_path, private_key_path = parse_certbot_certificate_paths(output)
    if not certificate_path or not private_key_path:
        raise RuntimeError(f"Certbot did not report certificate paths for {name}.")
    missing = [path for path in (certificate_path, private_key_path) if not Path(path).is_file()]
    if missing:
        raise RuntimeError("Certbot reported certificate files that are not readable: " + ", ".join(missing))
    return certificate_path, private_key_path


def build_certbot_command(binary, hook_command, certbot_name, hostnames, force_renewal=False, supported_options=None):
    options = certbot_supported_options(binary) if supported_options is None else frozenset(supported_options)
    command = [
        binary,
        "certonly",
        "--manual",
        "--preferred-challenges",
        "dns",
        "--manual-auth-hook",
        hook_command,
        "--non-interactive",
        "--cert-name",
        certbot_name,
    ]
    if "--manual-public-ip-logging-ok" in options:
        command.append("--manual-public-ip-logging-ok")
    if "--no-autorenew" in options:
        command.append("--no-autorenew")
    if force_renewal:
        command.append("--force-renewal")
    for hostname in hostnames:
        command.extend(["-d", hostname])
    return command


def certbot_candidates():
    configured = str(os.getenv("OPS_CERTBOT_BINARY", "") or "").strip()
    if configured:
        resolved = shutil.which(configured) or configured
        return [resolved] if Path(resolved).is_file() else []
    candidates = [
        "/snap/bin/certbot",
        "/usr/local/bin/certbot",
        "/opt/certbot/bin/certbot",
        shutil.which("certbot"),
        "/usr/bin/certbot",
    ]
    available = []
    seen = set()
    for value in candidates:
        path = str(value or "").strip()
        if not path or not Path(path).is_file():
            continue
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        available.append(path)
    return available


def certbot_runtime_hint():
    with CERTBOT_RUNTIME_LOCK:
        cached = dict(CERTBOT_RUNTIME_CACHE) if CERTBOT_RUNTIME_CACHE else None
    if cached:
        return cached
    candidates = certbot_candidates()
    if candidates:
        return {
            "available": True,
            "installed": True,
            "binary": candidates[0],
            "version": "Certbot",
            "error": "",
            "checked_at": 0,
            "verified": False,
        }
    return {
        "available": False,
        "installed": False,
        "binary": "",
        "version": "",
        "error": "Certbot is not installed on this server.",
        "checked_at": 0,
        "verified": False,
    }


def certbot_startup_error(binary, output):
    message = str(output or "").strip()
    if "X509_V_FLAG_NOTIFY_POLICY" in message or ("OpenSSL" in message and "AttributeError" in message):
        return (
            f"Certbot at {binary} cannot start because its Python OpenSSL packages are incompatible. "
            "Repair or reinstall Certbot using an isolated supported package (the official Snap package is recommended), "
            "then reload this page."
        )
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    detail = lines[-1] if lines else "The startup check failed."
    return f"Certbot at {binary} cannot start: {detail[:700]}"


def certbot_runtime_status(refresh=False):
    global CERTBOT_RUNTIME_CACHE
    now = time.monotonic()
    with CERTBOT_RUNTIME_LOCK:
        if (
            not refresh
            and CERTBOT_RUNTIME_CACHE
            and now - CERTBOT_RUNTIME_CACHE["checked_at"] < CERTBOT_RUNTIME_CACHE_SECONDS
        ):
            return dict(CERTBOT_RUNTIME_CACHE)
        candidates = certbot_candidates()
        failures = []
        for binary in candidates:
            try:
                result = subprocess.run(
                    [binary, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                    env=certbot_environment(binary=binary),
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                if result.returncode == 0:
                    CERTBOT_RUNTIME_CACHE = {
                        "available": True,
                        "installed": True,
                        "binary": binary,
                        "version": output.splitlines()[0] if output else "Certbot",
                        "error": "",
                        "checked_at": now,
                    }
                    return dict(CERTBOT_RUNTIME_CACHE)
                failures.append(certbot_startup_error(binary, output))
            except (OSError, subprocess.TimeoutExpired) as exc:
                failures.append(certbot_startup_error(binary, str(exc)))
        CERTBOT_RUNTIME_CACHE = {
            "available": False,
            "installed": bool(candidates),
            "binary": candidates[0] if candidates else "",
            "version": "",
            "error": failures[0] if failures else "Certbot is not installed on this server.",
            "checked_at": now,
        }
        return dict(CERTBOT_RUNTIME_CACHE)


def certbot_binary(refresh=False):
    runtime = certbot_runtime_status(refresh=refresh)
    return runtime["binary"] if runtime["available"] else None


def certbot_available():
    return bool(certbot_binary())


def normalize_hostname(value):
    raw = str(value or "").strip().lower().rstrip(".")
    wildcard = raw.startswith("*.")
    domain = raw[2:] if wildcard else raw
    if not domain or "://" in domain or "/" in domain or "@" in domain:
        raise ValueError(f"Invalid hostname: {value}")
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"Invalid hostname: {value}") from exc
    labels = ascii_domain.split(".")
    if len(labels) < 2 or len(ascii_domain) > 253 or any(not HOSTNAME_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError(f"Invalid hostname: {value}")
    return ("*." if wildcard else "") + ascii_domain


def normalize_hostnames(values):
    hostnames = []
    for value in values or []:
        hostname = normalize_hostname(value)
        if hostname not in hostnames:
            hostnames.append(hostname)
    if not hostnames:
        raise ValueError("Enter at least one hostname.")
    if len(hostnames) > 100:
        raise ValueError("A certificate may contain at most 100 hostnames.")
    return hostnames


def certbot_certificate_name(hostnames):
    first = str(hostnames[0]).replace("*.", "wildcard-")
    slug = re.sub(r"[^a-z0-9.-]+", "-", first.lower()).strip("-.")[:180] or "certificate"
    return f"openpagingserver-{slug}-{uuid.uuid4().hex[:8]}"


def public_job(job):
    if not job:
        return None
    return {
        "id": job["id"],
        "status": job.get("status", "starting"),
        "hostnames": list(job.get("hostnames") or []),
        "certbot_name": job.get("certbot_name", ""),
        "certificate_path": job.get("certificate_path", ""),
        "private_key_path": job.get("private_key_path", ""),
        "certificate_id": job.get("certificate_id"),
        "renew_certificate_id": job.get("renew_certificate_id"),
        "error": job.get("error", ""),
        "status_message": job.get("status_message", ""),
        "dns_name": job.get("dns_name", ""),
        "dns_value": job.get("dns_value", ""),
        "challenges": list(job.get("challenges") or []),
        "challenge_number": job.get("challenge_number", 1),
        "challenge_total": job.get("challenge_total", len(job.get("hostnames") or []) or 1),
        "external_observations": dict(job.get("external_observations") or {}),
        "ready_resolvers": list(job.get("ready_resolvers") or []),
        "next_check_at": job.get("next_check_at"),
        "created_at": job.get("created_at"),
    }


def read_challenge_state(job):
    state_path = Path(job["job_dir"]) / "challenge.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return
    with CERTBOT_JOBS_LOCK:
        current = CERTBOT_JOBS.get(job["id"])
        if not current or current.get("status") in {"success", "cancelled"}:
            return
        for key in (
            "status",
            "status_message",
            "error",
            "dns_name",
            "dns_value",
            "challenges",
            "challenge_number",
            "challenge_total",
            "external_observations",
            "ready_resolvers",
            "next_check_at",
        ):
            if key in state:
                current[key] = state[key]


def log_tail(path, limit=4000):
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace").strip()


def watch_certbot_job(job_id):
    with CERTBOT_JOBS_LOCK:
        job = CERTBOT_JOBS.get(job_id)
    if not job:
        return
    process = job["process"]
    while process.poll() is None:
        read_challenge_state(job)
        time.sleep(1)
    return_code = process.returncode
    try:
        job["log_handle"].close()
    except Exception:
        pass
    read_challenge_state(job)
    certificate_paths = None
    certificate_path_error = ""
    if return_code == 0:
        try:
            certificate_paths = certbot_certificate_paths(job["certbot_name"], binary=job["binary"])
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            certificate_path_error = str(exc)
    with CERTBOT_JOBS_LOCK:
        current = CERTBOT_JOBS.get(job_id)
        if not current:
            return
        if current.get("status") == "cancelled":
            return
        if return_code == 0:
            if certificate_paths:
                certificate_path, private_key_path = certificate_paths
                current["status"] = "success"
                current["certificate_path"] = certificate_path
                current["private_key_path"] = private_key_path
                current["error"] = ""
                return
            current["error"] = certificate_path_error or "Certbot completed but its certificate files could not be found."
        elif current.get("status") != "error":
            current["error"] = log_tail(current["log_path"]) or "Certbot failed to issue the certificate."
        current["status"] = "error"


def start_certbot_job(hostname_values, existing_certbot_name=None, renew_certificate_id=None):
    runtime = certbot_runtime_status()
    binary = runtime.get("binary") if runtime.get("available") else None
    if not binary:
        raise RuntimeError(runtime.get("error") or "Certbot is not installed on this server.")
    if not certbot_account_status(binary=binary).get("ready"):
        raise RuntimeError("Complete the Let's Encrypt account setup before requesting a certificate.")
    hostnames = normalize_hostnames(hostname_values)
    certbot_name = str(existing_certbot_name or "").strip()
    if certbot_name:
        if "/" in certbot_name or "\\" in certbot_name or len(certbot_name) > 255:
            raise ValueError("Invalid Certbot certificate name.")
    else:
        certbot_name = certbot_certificate_name(hostnames)
    job_id = uuid.uuid4().hex
    CERTBOT_JOB_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(CERTBOT_JOB_ROOT, 0o700)
    except OSError:
        pass
    job_dir = CERTBOT_JOB_ROOT / job_id
    job_dir.mkdir(mode=0o700)
    log_path = job_dir / "certbot.log"
    log_handle = log_path.open("ab")
    hook_path = certbot_auth_hook_path()
    if not hook_path.is_file():
        log_handle.close()
        raise RuntimeError("The OpenPagingServer Certbot DNS hook is missing.")
    hook_arguments = [sys.executable, str(hook_path)]
    hook_command = subprocess.list2cmdline(hook_arguments) if os.name == "nt" else shlex.join(hook_arguments)
    command = build_certbot_command(
        binary,
        hook_command,
        certbot_name,
        hostnames,
        force_renewal=bool(renew_certificate_id),
    )
    env = certbot_environment({"OPS_CERTBOT_JOB_DIR": str(job_dir)}, binary=binary)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            close_fds=True,
            start_new_session=os.name != "nt",
        )
    except Exception:
        log_handle.close()
        raise
    job = {
        "id": job_id,
        "status": "starting",
        "hostnames": hostnames,
        "certbot_name": certbot_name,
        "job_dir": str(job_dir),
        "log_path": str(log_path),
        "log_handle": log_handle,
        "process": process,
        "binary": binary,
        "certificate_path": "",
        "private_key_path": "",
        "certificate_id": int(renew_certificate_id) if renew_certificate_id else None,
        "renew_certificate_id": int(renew_certificate_id) if renew_certificate_id else None,
        "error": "",
        "status_message": "This may take several minutes...",
        "created_at": int(time.time()),
    }
    with CERTBOT_JOBS_LOCK:
        CERTBOT_JOBS[job_id] = job
    threading.Thread(target=watch_certbot_job, args=(job_id,), daemon=True).start()
    return public_job(job)


def get_certbot_job(job_id):
    wanted = str(job_id or "").strip()
    with CERTBOT_JOBS_LOCK:
        job = CERTBOT_JOBS.get(wanted)
    if not job:
        return None
    read_challenge_state(job)
    with CERTBOT_JOBS_LOCK:
        return public_job(CERTBOT_JOBS.get(wanted))


def cancel_certbot_job(job_id):
    wanted = str(job_id or "").strip()
    with CERTBOT_JOBS_LOCK:
        job = CERTBOT_JOBS.get(wanted)
        if not job:
            return False
        if job.get("status") in {"success", "cancelled"}:
            return True
        job["status"] = "cancelled"
        process = job.get("process")
        job_dir = Path(job["job_dir"])
    try:
        (job_dir / "cancel").touch()
    except OSError:
        pass
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
    return True


def set_certbot_job_certificate_id(job_id, certificate_id):
    with CERTBOT_JOBS_LOCK:
        job = CERTBOT_JOBS.get(str(job_id or "").strip())
        if job:
            job["certificate_id"] = int(certificate_id)
            return public_job(job)
    return None


def delete_certbot_certificate(certbot_name):
    runtime = certbot_runtime_status(refresh=True)
    binary = runtime.get("binary") if runtime.get("available") else None
    if not binary:
        raise RuntimeError(runtime.get("error") or "Certbot is not installed on this server.")
    name = str(certbot_name or "").strip()
    if not name or "/" in name or "\\" in name:
        raise ValueError("Invalid Certbot certificate name.")
    result = subprocess.run(
        [binary, "delete", "--cert-name", name, "--non-interactive"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=certbot_environment(binary=binary),
    )
    if result.returncode != 0:
        message = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
        raise RuntimeError(message[-2000:] or "Certbot could not delete the certificate.")
    return True

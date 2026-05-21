#!/usr/bin/env python3

import atexit
import argparse
import ipaddress
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

token = None
last_token_renewal = 0.0
stopping = False


def db():
    if not DB_NAME:
        raise RuntimeError(".env not set!")
    return pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME)


def log(message):
    print(f"[analyticsd] {message}", flush=True)


def load_server_id():
    return load_local_setting("analytics_server_id")


def load_server_secret():
    return load_local_setting("analytics_server_secret")


def load_local_setting(parameter):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM systemsettings WHERE parameter = %s LIMIT 1", (parameter,))
            row = cur.fetchone()
            if row and str(row[0]).strip():
                return str(row[0]).strip()
            return None
    finally:
        conn.close()


def save_server_id(server_id):
    save_local_setting(
        "analytics_server_id",
        server_id,
        "Analytics identifier. Reference this to Open Paging Server Project staff when requested.",
    )


def save_server_secret(server_secret):
    save_local_setting(
        "analytics_server_secret",
        server_secret,
        "Analytics secret. DO NOT SHARE.",
    )


def save_local_setting(parameter, value, description):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO systemsettings (parameter, value, description)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE value = VALUES(value), description = VALUES(description)
                """,
                (parameter, value, description),
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT value FROM systemsettings WHERE parameter = %s LIMIT 1", (parameter,))
            row = cur.fetchone()
            stored_id = str(row[0]).strip() if row and row[0] is not None else ""
            if stored_id != value:
                raise RuntimeError(f"{parameter} was not saved to the local database")
    finally:
        conn.close()


def xml_bytes(root_name, values):
    root = ET.Element(root_name)
    for key, value in values.items():
        child = ET.SubElement(root, key)
        child.text = "" if value is None else str(value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def request(endpoint, body=None, auth_token=None):
    headers = {
        "User-Agent": "OpenPagingServer",
        "Content-Type": "application/xml",
        "Accept": "*/*",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = urllib.request.Request(
        f"https://analytics.openpagingserver.org/{endpoint.strip('/')}/",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        return urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        return exc


def register_token(server_id):
    global token, last_token_renewal
    server_secret = load_server_secret()
    if server_id and not server_secret:
        log("equesting a new secured server identity")
        server_id = None
    response = request(
        "token/create",
        xml_bytes("token_request", {"server_id": server_id, "server_secret": server_secret}),
    )
    if response.code == 204:
        new_token = response.headers.get("X-Auth-Token")
        if not new_token:
            raise RuntimeError("analytics token response did not include X-Auth-Token")
        new_server_id = response.headers.get("X-Server-Id") or server_id
        if not new_server_id:
            raise RuntimeError("analytics token response did not include X-Server-Id")
        new_server_secret = response.headers.get("X-Server-Secret")
        if not server_secret and not new_server_secret:
            raise RuntimeError("analytics token response did not include X-Server-Secret")
        save_server_id(new_server_id)
        if new_server_secret:
            save_server_secret(new_server_secret)
        token = new_token
        last_token_renewal = time.time()
        log(f"registered analytics token for server id {new_server_id[:12]}...")
        return new_server_id
    if response.code == 403:
        log("token registration was refused; if this install has an old analytics_server_id without a secret, clear analytics_server_id and analytics_server_secret once")
        return False
    raise RuntimeError(f"token registration failed with HTTP {response.code}")


def renew_token(server_id):
    global token, last_token_renewal
    if not token:
        return register_token(server_id)
    response = request("token/renew", xml_bytes("token_request", {"server_id": server_id}), token)
    if response.code == 204:
        new_token = response.headers.get("X-Auth-Token")
        if new_token:
            token = new_token
        last_token_renewal = time.time()
        log("renewed analytics token")
        return server_id
    if response.code in (401, 403):
        token = None
        return register_token(server_id)
    raise RuntimeError(f"token renewal failed with HTTP {response.code}")


def delete_token():
    global token
    if not token:
        return
    try:
        request("token/delete", None, token)
    except Exception as exc:
        log(f"token delete failed: {exc}")
    token = None


def command_output(args):
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def read_first(paths):
    for path in paths:
        try:
            value = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def distro_name():
    if sys.platform.startswith("linux"):
        try:
            lines = Path("/etc/os-release").read_text(encoding="utf-8", errors="ignore").splitlines()
            data = {}
            for line in lines:
                if "=" in line:
                    key, value = line.split("=", 1)
                    data[key] = value.strip().strip('"')
            return data.get("PRETTY_NAME") or data.get("NAME") or platform.platform()
        except OSError:
            return platform.platform()
    if sys.platform == "darwin":
        version = command_output(["sw_vers", "-productVersion"])
        return f"macOS {version}".strip()
    if os.name == "nt":
        return platform.platform()
    return platform.platform()


def host_model():
    if sys.platform.startswith("linux"):
        vendor = read_first(["/sys/class/dmi/id/sys_vendor", "/sys/devices/virtual/dmi/id/sys_vendor"])
        product = read_first(["/sys/class/dmi/id/product_name", "/sys/devices/virtual/dmi/id/product_name"])
        return " ".join(part for part in [vendor, product] if part).strip() or platform.machine()
    if sys.platform == "darwin":
        return command_output(["sysctl", "-n", "hw.model"]) or platform.machine()
    if os.name == "nt":
        value = command_output(["wmic", "computersystem", "get", "manufacturer,model", "/value"])
        parts = []
        for line in value.splitlines():
            if "=" in line:
                item = line.split("=", 1)[1].strip()
                if item:
                    parts.append(item)
        return " ".join(parts) or platform.machine()
    return platform.machine()


def cpu_name():
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    if sys.platform == "darwin":
        return command_output(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
    if os.name == "nt":
        return platform.processor()
    return platform.processor() or platform.machine()


def ram_bytes():
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        except Exception:
            return ""
    if sys.platform == "darwin":
        return command_output(["sysctl", "-n", "hw.memsize"])
    if os.name == "nt":
        value = command_output(["wmic", "computersystem", "get", "TotalPhysicalMemory", "/value"])
        for line in value.splitlines():
            if line.startswith("TotalPhysicalMemory="):
                return line.split("=", 1)[1].strip()
    return ""


def disk_usage():
    usage = shutil.disk_usage(str(BASE_DIR.anchor or BASE_DIR))
    return usage.total, usage.used


def package_count():
    commands = [
        (["dpkg-query", "-f", "${binary:Package}\n", "-W"], None),
        (["rpm", "-qa"], None),
        (["pacman", "-Qq"], None),
        (["brew", "list", "--formula"], None),
        (["pkgutil", "--pkgs"], None),
        (["winget", "list", "--disable-interactivity"], 2),
    ]
    for args, skip_lines in commands:
        if not shutil.which(args[0]):
            continue
        output = command_output(args)
        if output:
            lines = [line for line in output.splitlines() if line.strip()]
            if skip_lines:
                lines = lines[skip_lines:]
            return len(lines)
    try:
        import importlib.metadata

        return len(list(importlib.metadata.distributions()))
    except Exception:
        return ""


def ops_version():
    try:
        for line in (BASE_DIR / "pyproject.toml").read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def server_uptime_seconds():
    if sys.platform.startswith("linux"):
        try:
            return int(float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0]))
        except Exception:
            return ""
    if sys.platform == "darwin":
        boot = command_output(["sysctl", "-n", "kern.boottime"])
        if "sec =" in boot:
            try:
                sec = int(boot.split("sec =", 1)[1].split(",", 1)[0].strip())
                return max(0, int(time.time()) - sec)
            except Exception:
                return ""
    if os.name == "nt":
        ticks = command_output(["wmic", "os", "get", "LastBootUpTime", "/value"])
        for line in ticks.splitlines():
            if line.startswith("LastBootUpTime="):
                stamp = line.split("=", 1)[1].split(".", 1)[0]
                try:
                    boot_dt = datetime.strptime(stamp, "%Y%m%d%H%M%S")
                    return max(0, int((datetime.now() - boot_dt).total_seconds()))
                except Exception:
                    return ""
    return ""


def ip_mix():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addresses.add(info[4][0])
    except Exception:
        pass
    private_count = 0
    public_count = 0
    for value in addresses:
        try:
            ip = ipaddress.ip_address(value.split("%", 1)[0])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            continue
        if ip.is_private:
            private_count += 1
        elif ip.is_global:
            public_count += 1
    if private_count and public_count:
        ip_type = "mixed"
    elif public_count:
        ip_type = "public"
    else:
        ip_type = "private"
    return ip_type, private_count, public_count


def collect_report(server_id):
    disk_total, disk_used = disk_usage()
    ip_type, private_count, public_count = ip_mix()
    return {
        "server_id": server_id,
        "linux_kernel": platform.release(),
        "distro": distro_name(),
        "host": host_model(),
        "cpu": cpu_name(),
        "ram_bytes": ram_bytes() or 0,
        "disk_total_bytes": disk_total,
        "disk_used_bytes": disk_used,
        "ops_version": ops_version(),
        "server_uptime_seconds": server_uptime_seconds() or 0,
        "package_count": package_count() or 0,
        "ip_type": ip_type,
        "private_ip_count": private_count,
        "public_ip_count": public_count,
    }


def seconds_until_next_report():
    now = datetime.now()
    target = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


def send_report(server_id):
    if not token:
        return False
    response = request("report", xml_bytes("analytics_report", collect_report(server_id)), token)
    if response.code == 204:
        message = response.headers.get("X-Message")
        if message:
            log(message)
        log("sent analytics report")
        return True
    if response.code in (401, 403):
        log(f"report refused with HTTP {response.code}")
        return False
    raise RuntimeError(f"report failed with HTTP {response.code}")


def upload_once():
    server_id = load_server_id()
    registered_server_id = register_token(server_id)
    if not registered_server_id:
        return 1
    server_id = registered_server_id
    try:
        return 0 if send_report(server_id) else 1
    finally:
        delete_token()


def handle_signal(signum, frame):
    global stopping
    stopping = True


def main():
    server_id = load_server_id()
    next_register_attempt = 0.0
    next_report_at = time.time() + seconds_until_next_report()

    while not stopping:
        now = time.time()
        try:
            if not token and now >= next_register_attempt:
                registered_server_id = register_token(server_id)
                if registered_server_id:
                    server_id = registered_server_id
                else:
                    next_register_attempt = now + 24 * 60 * 60

            if token and server_id and now - last_token_renewal >= 7 * 24 * 60 * 60:
                renewed_server_id = renew_token(server_id)
                if renewed_server_id:
                    server_id = renewed_server_id

            if token and server_id and now >= next_report_at:
                send_report(server_id)
                next_report_at = time.time() + seconds_until_next_report()
        except Exception as exc:
            log(f"loop error: {exc}")

        time.sleep(30)

    delete_token()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Open Paging Server analytics daemon")
    parser.add_argument("-u", "--upload-now", action="store_true", help="upload one analytics report immediately")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    atexit.register(delete_token)
    if args.upload_now:
        raise SystemExit(upload_once())
    main()

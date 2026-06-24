#!/usr/bin/env python3

import ipaddress
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime
import importlib.util
from pathlib import Path

import endpoints

try:
    import pymysql
except Exception:
    pymysql = None

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return False

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

MODULE_LOADER_PATH = BASE_DIR / "endpoints.py"
MODULES_DIR = endpoints.MODULE_STORE_DIR

loaded_modules = {}
messaged_proc = None
livepaged_proc = None
belld_proc = None
analytics_proc = None
webd_proc = None
multicastgateway_proc = None
endpoint_manager = None
sip_server = None


class Core:
    def log(self, msg):
        print(msg)


core = Core()


def get_db_connection():
    if pymysql is None:
        raise RuntimeError("PyMySQL is not installed")
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )


def analytics_enabled():
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM systemsettings WHERE parameter = 'analytics' LIMIT 1")
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        core.log(f"analytics setting read error: {exc}")
        return False

    if not row:
        return False

    return str(row[0]).strip().lower() in {"1", "true", "yes", "on"}


def start_analytics():
    global analytics_proc
    analytics_path = BASE_DIR / "analyticsd.py"
    if analytics_proc and analytics_proc.poll() is None:
        return
    if not analytics_path.exists():
        return

    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "WINDIR": os.environ.get("WINDIR", ""),
        "DB_HOST": DB_HOST or "",
        "DB_USER": DB_USER or "",
        "DB_PASS": DB_PASS or "",
        "DB_NAME": DB_NAME or "",
        "ANALYTICS_URL": os.environ.get("ANALYTICS_URL", "https://analytics.openpagingserver.org"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    popen_kwargs = {
        "cwd": BASE_DIR,
        "env": env,
        "close_fds": True,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    analytics_proc = subprocess.Popen([sys.executable, str(analytics_path)], **popen_kwargs)
    core.log(f"analytics worker started pid={analytics_proc.pid}")


def stop_analytics():
    global analytics_proc
    if not analytics_proc:
        return
    if analytics_proc.poll() is None:
        analytics_proc.terminate()
        try:
            analytics_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            analytics_proc.kill()
            analytics_proc.wait(timeout=5)
    core.log("analytics worker stopped")
    analytics_proc = None


def sync_analytics():
    if analytics_enabled():
        start_analytics()
    else:
        stop_analytics()


def db_enabled_modules():
    return set()


def discover_modules():
    found = {}
    if not MODULES_DIR.exists():
        return found

    for module_dir in MODULES_DIR.iterdir():
        if not module_dir.is_dir():
            continue

        manifest = module_dir / "manifest.json"
        if not manifest.exists():
            continue

        data = json.loads(manifest.read_text(encoding="utf-8"))
        found[data["id"]] = module_dir / data["entry"]

    return found


def load_module(mid, entry):
    spec = importlib.util.spec_from_file_location(mid, entry)
    if spec is None or spec.loader is None:
        return

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if hasattr(mod, "init"):
        mod.init(core)

    loaded_modules[mid] = mod
    core.log(f"loaded module {mid}")


def unload_module(mid):
    mod = loaded_modules.get(mid)
    if mod is None:
        return

    if hasattr(mod, "shutdown"):
        mod.shutdown()

    del loaded_modules[mid]
    core.log(f"unloaded module {mid}")


def sync_modules():
    enabled = db_enabled_modules()
    discovered = discover_modules()

    for mid in enabled:
        if mid not in loaded_modules and mid in discovered:
            load_module(mid, discovered[mid])

    for mid in list(loaded_modules.keys()):
        if mid not in enabled:
            unload_module(mid)


def load_endpoint_manager():
    spec = importlib.util.spec_from_file_location("endpoint_manager", MODULE_LOADER_PATH)
    if spec is None or spec.loader is None:
        return None

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def command_output(args, timeout=10):
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def bool_text(value):
    return "Yes" if value else "No"


def format_bytes(value):
    try:
        value = int(value)
    except Exception:
        return "Unknown"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    number = float(value)
    unit = units[0]
    for unit in units:
        if number < 1024.0 or unit == units[-1]:
            break
        number /= 1024.0
    if unit in {"B", "KB"}:
        return f"{number:.0f} {unit}"
    return f"{number:.2f} {unit}"


def format_uptime(seconds):
    try:
        total = int(seconds)
    except Exception:
        return "Unknown"

    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def read_pyproject_version():
    pyproject_path = BASE_DIR / "pyproject.toml"
    if not pyproject_path.exists():
        return "Unknown"
    try:
        for line in pyproject_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return "Unknown"
    return "Unknown"


def is_admin_user():
    if os.name == "nt":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    return geteuid() == 0


def candidate_project_pythons():
    candidates = []
    venv_names = [".venv", "venv", "env"]
    for venv_name in venv_names:
        if os.name == "nt":
            candidates.append(BASE_DIR / venv_name / "Scripts" / "python.exe")
        else:
            candidates.append(BASE_DIR / venv_name / "bin" / "python")
    return [path for path in candidates if path.exists()]


def project_dotvenv_python():
    if os.name == "nt":
        candidates = [
            BASE_DIR / ".venv" / "Scripts" / "python.exe",
            BASE_DIR / ".venv" / "Scripts" / "python",
        ]
    else:
        candidates = [
            BASE_DIR / ".venv" / "bin" / "python",
            BASE_DIR / ".venv" / "bin" / "python3",
        ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def same_python_path(left, right):
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return os.path.abspath(str(left)) == os.path.abspath(str(right))


def maybe_rerun_debug_report_in_dotvenv():
    venv_python = project_dotvenv_python()
    if venv_python is None:
        return
    os.environ.setdefault("OPS_DEBUG_REPORT_PYTHON", str(venv_python))
    if os.environ.get("OPS_DEBUG_REPORT_VENV_REEXEC") == "1":
        return
    if same_python_path(venv_python, sys.executable):
        return

    env = os.environ.copy()
    env["OPS_DEBUG_REPORT_VENV_REEXEC"] = "1"
    env["OPS_DEBUG_REPORT_PYTHON"] = str(venv_python)
    env["VIRTUAL_ENV"] = str(BASE_DIR / ".venv")
    env["PATH"] = str(venv_python.parent) + os.pathsep + env.get("PATH", "")
    completed = subprocess.run(
        [str(venv_python), str((BASE_DIR / "index.py").resolve()), *sys.argv[1:]],
        cwd=BASE_DIR,
        env=env,
    )
    raise SystemExit(completed.returncode)


def _windows_running_python():
    script_path = str((BASE_DIR / "index.py").resolve()).replace("\\", "\\\\")
    ps_script = rf"""
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {{
    $_.ProcessId -ne {os.getpid()} -and $_.CommandLine -and $_.CommandLine -like '*index.py*'
}} | Select-Object ProcessId, ExecutablePath, CommandLine
$procs | ConvertTo-Json -Depth 4 -Compress
"""
    output = command_output(["powershell", "-NoProfile", "-Command", ps_script], timeout=15)
    if not output:
        return None
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return None

    rows = parsed if isinstance(parsed, list) else [parsed]
    for row in rows:
        command_line = str(row.get("CommandLine") or "")
        executable = str(row.get("ExecutablePath") or "")
        if str(os.getpid()) == str(row.get("ProcessId")):
            continue
        if script_path.lower() in command_line.lower() or "index.py" in command_line.lower():
            if executable:
                return executable
    return None


def _posix_running_python():
    output = command_output(["ps", "-eo", "pid=,args="], timeout=15)
    if not output:
        return None
    target_path = str((BASE_DIR / "index.py").resolve())
    for line in output.splitlines():
        match = re.match(r"^\s*(\d+)\s+(.*)$", line)
        if not match:
            continue
        pid = int(match.group(1))
        if pid == os.getpid():
            continue
        command_line = match.group(2)
        if target_path not in command_line and "index.py" not in command_line:
            continue
        try:
            parts = shlex.split(command_line)
        except ValueError:
            continue
        if not parts:
            continue
        return parts[0]
    return None


def detect_running_python():
    if os.name == "nt":
        return _windows_running_python()
    return _posix_running_python()


def already_running_message():
    return "Open Paging Server is already running...\n\nCLI interface is coming soon!"


def refuse_second_server_launch():
    if not detect_running_python():
        return False
    print(already_running_message())
    return True


def gather_python_environment_from_interpreter(interpreter):
    if Path(interpreter).resolve() == Path(sys.executable).resolve():
        try:
            import importlib.metadata as metadata

            packages = []
            for dist in metadata.distributions():
                name = dist.metadata.get("Name") or dist.metadata.get("Summary") or dist.name
                packages.append(f"{name}=={dist.version}")
            packages.sort(key=str.lower)
            in_venv = (
                hasattr(sys, "real_prefix")
                or sys.prefix != getattr(sys, "base_prefix", sys.prefix)
                or bool(os.environ.get("VIRTUAL_ENV"))
            )
            return {
                "python_version": platform.python_version(),
                "in_venv": in_venv,
                "packages": packages,
            }
        except Exception:
            return {
                "python_version": platform.python_version(),
                "in_venv": False,
                "packages": [],
            }

    inspector = (
        "import importlib.metadata as m, json, os, platform, sys;"
        "pkgs=[];"
        "[(pkgs.append(f\"{(d.metadata.get('Name') or d.name)}=={d.version}\")) for d in m.distributions()];"
        "pkgs.sort(key=str.lower);"
        "print(json.dumps({"
        "'python_version': platform.python_version(),"
        "'in_venv': bool(getattr(sys, 'real_prefix', None)) or sys.prefix != getattr(sys, 'base_prefix', sys.prefix) or bool(os.environ.get('VIRTUAL_ENV')),"
        "'packages': pkgs"
        "}))"
    )
    result = command_output([interpreter, "-c", inspector], timeout=30)
    if not result:
        raise RuntimeError(f"Unable to inspect Python interpreter: {interpreter}")
    payload = json.loads(result)
    payload["packages"] = sorted(payload.get("packages") or [], key=str.lower)
    return payload


def choose_python_environment():
    preferred_python = os.getenv("OPS_DEBUG_REPORT_PYTHON", "").strip()
    if preferred_python and Path(preferred_python).exists():
        info = gather_python_environment_from_interpreter(preferred_python)
        return preferred_python, info

    running_python = detect_running_python()
    if running_python:
        info = gather_python_environment_from_interpreter(running_python)
        return running_python, info

    for candidate in candidate_project_pythons():
        info = gather_python_environment_from_interpreter(str(candidate))
        return str(candidate), info

    info = gather_python_environment_from_interpreter(sys.executable)
    return sys.executable, info


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


def cpu_name():
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            return platform.processor() or platform.machine()
    if sys.platform == "darwin":
        return command_output(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
    if os.name == "nt":
        powershell_value = command_output(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)"],
            timeout=10,
        )
        return powershell_value or platform.processor() or platform.machine()
    return platform.processor() or platform.machine()


def ram_bytes():
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        except Exception:
            return None
    if sys.platform == "darwin":
        value = command_output(["sysctl", "-n", "hw.memsize"])
        return int(value) if value.isdigit() else None
    if os.name == "nt":
        value = command_output(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"],
            timeout=10,
        )
        return int(value) if value.isdigit() else None
    return None


def disk_usage_details():
    disk_root = Path(BASE_DIR.anchor or os.path.sep)
    usage = shutil.disk_usage(str(disk_root))
    return str(disk_root), usage.total, usage.used


def package_count():
    commands = [
        (["dpkg-query", "-f", "${binary:Package}\n", "-W"], 0),
        (["rpm", "-qa"], 0),
        (["pacman", "-Qq"], 0),
        (["brew", "list", "--formula"], 0),
        (["pkgutil", "--pkgs"], 0),
        (["winget", "list", "--disable-interactivity"], 2),
    ]
    for args, skip_lines in commands:
        if not shutil.which(args[0]):
            continue
        output = command_output(args, timeout=20)
        if not output:
            continue
        lines = [line for line in output.splitlines() if line.strip()]
        if skip_lines:
            lines = lines[skip_lines:]
        if lines:
            return len(lines)
    try:
        import importlib.metadata as metadata

        return len(list(metadata.distributions()))
    except Exception:
        return "Unknown"


def server_uptime_seconds():
    if sys.platform.startswith("linux"):
        try:
            return int(float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0]))
        except Exception:
            return None
    if sys.platform == "darwin":
        boot = command_output(["sysctl", "-n", "kern.boottime"])
        if "sec =" in boot:
            try:
                sec = int(boot.split("sec =", 1)[1].split(",", 1)[0].strip())
                return max(0, int(time.time()) - sec)
            except Exception:
                return None
        return None
    if os.name == "nt":
        value = command_output(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')"],
            timeout=10,
        )
        if not value:
            return None
        try:
            boot_dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return max(0, int((datetime.now(boot_dt.tzinfo) - boot_dt).total_seconds()))
        except Exception:
            return None
    return None


def prefix_to_netmask(prefix_length):
    try:
        prefix_length = int(prefix_length)
    except Exception:
        return "Unknown"
    network = ipaddress.ip_network(f"0.0.0.0/{prefix_length}", strict=False)
    return str(network.netmask)


def is_privateish(ip_obj):
    if ip_obj.version == 4 and ip_obj in ipaddress.ip_network("100.64.0.0/10"):
        return True
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
    )


def classify_address(ip_text):
    try:
        ip_obj = ipaddress.ip_address(ip_text.split("%", 1)[0])
    except ValueError:
        return None
    if ip_obj.is_loopback or ip_obj.is_multicast or ip_obj.is_unspecified:
        return None
    if is_privateish(ip_obj):
        return "PRIVATE IP/NAT"
    if ip_obj.is_global:
        return "PUBLIC IP"
    return "UNKNOWN"


def select_best_interface_address(addresses):
    scored = []
    for address in addresses:
        addr = str(address.get("address") or "")
        if not addr:
            continue
        classification = classify_address(addr)
        if not classification:
            continue
        score = 2 if classification == "PUBLIC IP" else 1
        scored.append((score, address))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def route_for_address(address, prefix):
    try:
        network = ipaddress.ip_network(f"{address}/{int(prefix)}", strict=False)
    except Exception:
        return "Unknown"
    return str(network)


def windows_network_info():
    ps_script = r"""
$items = Get-NetIPConfiguration -Detailed -ErrorAction SilentlyContinue | ForEach-Object {
    [pscustomobject]@{
        name = $_.InterfaceAlias
        state = [string]$_.NetAdapter.Status
        dhcp4 = if ($_.NetIPv4Interface) { [string]$_.NetIPv4Interface.Dhcp } else { "" }
        dhcp6 = if ($_.NetIPv6Interface) { [string]$_.NetIPv6Interface.Dhcp } else { "" }
        ipv4 = @($_.IPv4Address | ForEach-Object { [pscustomobject]@{ address = $_.IPAddress; prefix = $_.PrefixLength } })
        ipv6 = @($_.IPv6Address | ForEach-Object { [pscustomobject]@{ address = $_.IPAddress; prefix = $_.PrefixLength } })
    }
}
$items | ConvertTo-Json -Depth 6 -Compress
"""
    output = command_output(["powershell", "-NoProfile", "-Command", ps_script], timeout=20)
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return []

    rows = parsed if isinstance(parsed, list) else [parsed]
    adapters = []
    for row in rows:
        addresses = []
        for item in row.get("ipv4") or []:
            addresses.append({"family": "ipv4", "address": item.get("address"), "prefix": item.get("prefix")})
        for item in row.get("ipv6") or []:
            addresses.append({"family": "ipv6", "address": item.get("address"), "prefix": item.get("prefix")})
        adapters.append(
            {
                "name": row.get("name") or "Unknown",
                "state": "UP" if str(row.get("state") or "").lower() == "up" else "DOWN",
                "dhcp": "Yes" if "Enabled" in {str(row.get("dhcp4") or ""), str(row.get("dhcp6") or "")} else "No",
                "multicast": "No" if re.search(r"loopback|isatap|teredo|tunnel", str(row.get("name") or ""), re.I) else "Yes",
                "addresses": addresses,
            }
        )
    return adapters


def linux_network_info():
    output = command_output(["ip", "-json", "addr", "show"], timeout=20)
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return []

    adapters = []
    for row in parsed:
        addresses = []
        for item in row.get("addr_info") or []:
            addresses.append(
                {
                    "family": item.get("family"),
                    "address": item.get("local"),
                    "prefix": item.get("prefixlen"),
                    "dynamic": bool(item.get("dynamic")),
                }
            )
        adapters.append(
            {
                "name": row.get("ifname") or "Unknown",
                "state": "UP" if str(row.get("operstate") or "").upper() == "UP" else "DOWN",
                "dhcp": "Yes" if any(item.get("dynamic") for item in addresses) else "No",
                "multicast": "Yes" if "MULTICAST" in (row.get("flags") or []) else "No",
                "addresses": addresses,
            }
        )
    return adapters


def macos_network_info():
    adapters = []
    try:
        interface_names = [name for _, name in socket.if_nameindex()]
    except OSError:
        return adapters

    for name in interface_names:
        ifconfig_output = command_output(["ifconfig", name], timeout=10)
        if not ifconfig_output:
            continue
        state = "UP" if "status: active" in ifconfig_output.lower() else "DOWN"
        multicast = "Yes" if "MULTICAST" in ifconfig_output else "No"
        dhcp = "Yes" if bool(command_output(["ipconfig", "getpacket", name], timeout=5)) else "No"
        addresses = []
        for line in ifconfig_output.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                parts = line.split()
                if len(parts) >= 2:
                    address = parts[1]
                    netmask = ""
                    if "netmask" in parts:
                        netmask = parts[parts.index("netmask") + 1]
                    prefix = 24
                    if netmask:
                        try:
                            prefix = ipaddress.ip_network(f"0.0.0.0/{netmask}", strict=False).prefixlen
                        except Exception:
                            prefix = 24
                    addresses.append({"family": "ipv4", "address": address, "prefix": prefix})
            elif line.startswith("inet6 "):
                parts = line.split()
                if len(parts) >= 2:
                    address = parts[1].split("%", 1)[0]
                    prefix = 64
                    if "prefixlen" in parts:
                        try:
                            prefix = int(parts[parts.index("prefixlen") + 1])
                        except Exception:
                            prefix = 64
                    addresses.append({"family": "ipv6", "address": address, "prefix": prefix})
        adapters.append(
            {
                "name": name,
                "state": state,
                "dhcp": dhcp,
                "multicast": multicast,
                "addresses": addresses,
            }
        )
    return adapters


def collect_network_adapters():
    if os.name == "nt":
        return windows_network_info()
    if sys.platform.startswith("linux"):
        return linux_network_info()
    if sys.platform == "darwin":
        return macos_network_info()
    return []


def read_system_settings():
    settings = {}
    if not all([DB_HOST, DB_USER, DB_NAME]):
        return settings
    try:
        conn = get_db_connection()
    except Exception:
        return settings
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT parameter, value FROM systemsettings")
            for parameter, value in cur.fetchall():
                settings[str(parameter)] = "" if value is None else str(value)
    except Exception:
        return settings
    finally:
        conn.close()
    return settings


def truthy_setting(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def read_endpoint_modules():
    discovered = {}
    for module_name, package in endpoints.discover_endpoint_packages(extract_if_trusted=False).items():
        manifest = package.get("manifest") or {}
        discovered[module_name] = {
            "module": module_name,
            "developer": manifest.get("developer") or manifest.get("author") or "Unknown",
            "version": manifest.get("version") or "Unknown",
            "enabled": "Disabled",
        }

    settings_from_db = {}
    if all([DB_HOST, DB_USER, DB_NAME]):
        try:
            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT `dir`, enabled FROM endpointmodulesloaded")
                    for module_dir, enabled in cur.fetchall():
                        settings_from_db[str(module_dir)] = str(enabled or "")
            finally:
                conn.close()
        except Exception:
            settings_from_db = {}

    for module_name, module_info in discovered.items():
        module_info["enabled"] = "Enabled" if settings_from_db.get(module_name, "true").strip().lower() == "true" else "Disabled"

    for module_name, enabled in settings_from_db.items():
        if module_name not in discovered:
            discovered[module_name] = {
                "module": module_name,
                "developer": "Unknown",
                "version": "Unknown",
                "enabled": "Enabled" if enabled.strip().lower() == "true" else "Disabled",
            }

    return [discovered[name] for name in sorted(discovered)]


def analytics_lines(settings):
    enabled = truthy_setting(settings.get("analytics"))
    lines = [f"Analytics Enabled: {bool_text(enabled)}"]
    if not enabled:
        return lines

    analytics_id = settings.get("analytics_server_id", "").strip()
    analytics_secret = settings.get("analytics_server_secret", "").strip()
    last_success = settings.get("analytics_last_successful_report", "").strip() or "Never recorded"
    registered = bool(analytics_id and analytics_secret)
    lines.extend(
        [
            f"Analytics Registered: {bool_text(registered)}",
            f"Last Successful Analytics Report: {last_success}",
            f"Analytics Identifier: {analytics_id or 'Not registered'}",
        ]
    )
    return lines


def software_lines(settings):
    return [
        f"Kernel: {platform.release() or 'Unknown'}",
        f"Opreating System: {distro_name()}",
        f"Uptime: {format_uptime(server_uptime_seconds())}",
        f"Package Count: {package_count()}",
    ]


def sip_lines(settings):
    sip_enabled = truthy_setting(settings.get("enable_insecure_sip"))
    sip_port = settings.get("insecure_sip_port", "5060") or "5060"
    return [
        f"SIP ENABLED: {bool_text(sip_enabled)}",
        f"UDP/TCP SIP PORT: {sip_port}",
    ]


def web_lines(settings):
    http_port = settings.get("webserver_http_port", "80") or "80"
    web_enabled = truthy_setting(settings.get("webserver_enable", "1"))
    return [
        f"WEB ENABLED: {bool_text(web_enabled)}",
        f"HTTP WEB PORT: {http_port}",
    ]


def render_network_sections(adapters):
    sections = []
    for adapter in adapters:
        name = adapter.get("name") or "Unknown"
        lines = [f"--NETWORK ({name})--"]
        lines.append(f"State: {adapter.get('state', 'Unknown')}")
        lines.append(f"DHCP: {adapter.get('dhcp', 'No')}")
        selected = select_best_interface_address(adapter.get("addresses") or [])
        if selected is None:
            lines.append("IP Type: UNKNOWN")
            lines.append("Subnet Mask: Unknown")
        else:
            ip_type = classify_address(selected.get("address") or "") or "UNKNOWN"
            lines.append(f"IP Type: {ip_type}")
            prefix = selected.get("prefix")
            family = str(selected.get("family") or "").lower()
            if ip_type == "PRIVATE IP/NAT":
                lines.append(f"Subnet: {route_for_address(selected.get('address'), prefix)}")
            if family == "ipv4":
                lines.append(f"Subnet Mask: {prefix_to_netmask(prefix)}")
            elif family == "ipv6":
                lines.append(f"Subnet Mask: /{prefix}")
            else:
                lines.append("Subnet Mask: Unknown")
        lines.append(f"Multicast Capable: {adapter.get('multicast', 'Unknown')}")
        sections.append("\n".join(lines))
    return sections


def build_debug_report():
    now = datetime.now().astimezone()
    hostname = socket.gethostname()
    timezone_name = now.tzname() or "UTC"
    python_path, python_info = choose_python_environment()
    settings = read_system_settings()
    disk_root, disk_total, disk_used = disk_usage_details()
    modules = read_endpoint_modules()

    lines = [
        "OPEN PAGING SERVER DEBUG REPORT",
        f"Generated at {now.strftime('%H:%M:%S')} {now.strftime('%m/%d/%Y')} {timezone_name} on {hostname}",
        "",
        f"Open Paging Server Verison {read_pyproject_version()}",
        f"Python Verison {python_info.get('python_version') or platform.python_version()}",
        "Running in a venv" if python_info.get("in_venv") else "Running outside a venv",
        f"Python Executable: {python_path}",
        "Installed Python Packages:",
    ]

    packages = python_info.get("packages") or []
    if packages:
        lines.extend(packages)
    else:
        lines.append("No packages found")

    lines.extend(
        [
            "",
            "--HARDWARE--",
            f"CPU: {cpu_name() or 'Unknown'}",
            f"RAM (in MB or GB): {format_bytes(ram_bytes())}",
            f"DISK ({disk_root}): ({format_bytes(disk_used)} of {format_bytes(disk_total)} used)",
            "",
        ]
    )

    network_sections = render_network_sections(collect_network_adapters())
    if network_sections:
        lines.extend(network_sections)
    else:
        lines.extend(
            [
                "--NETWORK (Unknown)--",
                "State: Unknown",
                "DHCP: Unknown",
                "IP Type: UNKNOWN",
                "Subnet Mask: Unknown",
                "Multicast Capable: Unknown",
            ]
        )

    lines.extend(["", "-- SOFTWARE --", *software_lines(settings), "", "-- ANALYTICS --", *analytics_lines(settings), "", "-- ENDPOINT MODULES --"])
    if modules:
        for module in modules:
            lines.append(
                f"{module['module']} | Developer: {module['developer']} | Version: {module['version']} | {module['enabled']}"
            )
    else:
        lines.append("No endpoint modules found")

    lines.extend(["", "-- SIP --", *sip_lines(settings), "", "--WEB--", *web_lines(settings), "", "---- END OF DEBUG REPORT ----"])
    return "\n".join(lines) + "\n"


def debug_log_dir():
    return Path.home() / "openpagingserver_debuglogs"


def debug_log_path(now):
    filename = now.strftime("%H-%M-%S %m-%d-%y.txt") if os.name == "nt" else now.strftime("%H:%M:%S %m-%d-%y.txt")
    return debug_log_dir() / filename


def write_debug_log(contents, now):
    target_dir = debug_log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = debug_log_path(now)
    target_path.write_text(contents, encoding="utf-8")
    return target_path


def print_failure(message):
    red = "\033[31m"
    reset = "\033[0m"
    print(f"{red}{message}{reset}")


def handle_debug_report():
    if not is_admin_user():
        print("Debug reports require root/admin")
        return 1

    now = datetime.now().astimezone()
    log_path = None
    try:
        report = build_debug_report()
        log_path = write_debug_log(report, now)
        sys.stdout.write(report)
        print("-------------------------------")
        print(f"Saved to {log_path}")
        return 0
    except Exception:
        debug_trace = traceback.format_exc()
        try:
            failure_contents = "Failed to generate debug report\n\n" + debug_trace
            log_path = write_debug_log(failure_contents, now)
        except Exception:
            log_path = None
        print_failure("Failed to generate debug report")
        sys.stdout.write(debug_trace)
        print("-------------------------------")
        if log_path is not None:
            print(f"Saved to {log_path}")
        return 1


def shutdown(sig, frame):
    global messaged_proc, livepaged_proc, belld_proc, webd_proc, multicastgateway_proc, endpoint_manager
    if endpoint_manager and hasattr(endpoint_manager, "shutdown_all"):
        endpoint_manager.shutdown_all()

    for mid in list(loaded_modules.keys()):
        unload_module(mid)

    if messaged_proc:
        messaged_proc.terminate()

    if livepaged_proc:
        livepaged_proc.terminate()

    if belld_proc:
        belld_proc.terminate()

    if webd_proc:
        webd_proc.terminate()

    if multicastgateway_proc:
        multicastgateway_proc.terminate()

    stop_analytics()

    if sip_server is not None and hasattr(sip_server, "shutdown"):
        try:
            sip_server.shutdown()
        except Exception:
            pass

    sys.exit(0)


def main():
    global messaged_proc, livepaged_proc, belld_proc, webd_proc, multicastgateway_proc, endpoint_manager, sip_server
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    import sip.index as sip_module

    sip_server = sip_module

    endpoint_manager = load_endpoint_manager()
    if endpoint_manager and hasattr(endpoint_manager, "init"):
        endpoint_manager.init(core)

    messaged_path = BASE_DIR / "messaged.py"
    if messaged_path.exists():
        messaged_proc = subprocess.Popen([sys.executable, str(messaged_path)], cwd=BASE_DIR)
        core.log(f"message worker started pid={messaged_proc.pid}")

    livepaged_path = BASE_DIR / "livepaged.py"
    if livepaged_path.exists():
        livepaged_proc = subprocess.Popen([sys.executable, str(livepaged_path)], cwd=BASE_DIR)
        core.log(f"live paging websocket worker started pid={livepaged_proc.pid}")

    belld_path = BASE_DIR / "belld.py"
    if belld_path.exists():
        belld_proc = subprocess.Popen([sys.executable, str(belld_path)], cwd=BASE_DIR)
        core.log(f"bell scheduler worker started pid={belld_proc.pid}")

    webd_path = BASE_DIR / "webd.py"
    if webd_path.exists():
        webd_proc = subprocess.Popen([sys.executable, str(webd_path)], cwd=BASE_DIR)
        core.log(f"web worker started pid={webd_proc.pid}")

    multicastgateway_path = BASE_DIR / "multicastgatewayd.py"
    if multicastgateway_path.exists():
        multicastgateway_proc = subprocess.Popen([sys.executable, str(multicastgateway_path)], cwd=BASE_DIR)
        core.log(f"multicast gateway worker started pid={multicastgateway_proc.pid}")

    sip_server.start()
    core.log("SIP server started")

    while True:
        try:
            sync_modules()
            sync_analytics()
            if endpoint_manager and hasattr(endpoint_manager, "sync_modules"):
                endpoint_manager.sync_modules()
        except Exception as exc:
            core.log(f"module sync error: {exc}")
        time.sleep(5)


if __name__ == "__main__":
    if "--debug-report" in sys.argv or "-Dr" in sys.argv:
        maybe_rerun_debug_report_in_dotvenv()
        raise SystemExit(handle_debug_report())
    if refuse_second_server_launch():
        raise SystemExit(0)
    main()

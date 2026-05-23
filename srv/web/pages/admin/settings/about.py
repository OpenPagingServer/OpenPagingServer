import os
import platform
import subprocess
import urllib.request

from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


def run_text(command):
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=2).strip()
    except Exception:
        return ""


def is_private_ipv4(value):
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(part) for part in parts]
    except ValueError:
        return False
    if nums[0] == 10 or nums[0] == 127:
        return True
    if nums[0] == 172 and 16 <= nums[1] <= 31:
        return True
    if nums[0] == 192 and nums[1] == 168:
        return True
    if nums[0] == 169 and nums[1] == 254:
        return True
    return False


def detected_public_ipv4():
    try:
        with urllib.request.urlopen("https://analytics.openpagingserver.org/ipaddr/", timeout=2) as response:
            value = response.read(64).decode("utf-8", errors="ignore").strip()
        parts = value.split(".")
        if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts) and not is_private_ipv4(value):
            return value
    except Exception:
        pass
    return "Unknown"


def ipv4_addresses():
    values = []
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = item[4][0]
            if ip not in values and not ip.startswith("127."):
                values.append(ip)
    except Exception:
        pass
    if values:
        return values
    if os.name == "nt":
        output = run_text(["ipconfig"])
        for match in re.findall(r"IPv4 Address[.\s]*:\s*([0-9.]+)", output):
            if match not in values and not match.startswith("127."):
                values.append(match)
    else:
        output = run_text(["ip", "-4", "addr", "show"])
        for match in re.findall(r"inet\s+([0-9.]+)/", output):
            if match not in values and not match.startswith("127."):
                values.append(match)
    return values


def ipv6_addresses():
    values = []
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6):
            ip = item[4][0].split("%", 1)[0]
            if ip not in values and ip != "::1":
                values.append(ip)
    except Exception:
        pass
    return values


def dns_servers():
    values = []
    if os.name == "nt":
        output = run_text(["ipconfig", "/all"])
        for line in output.splitlines():
            stripped = line.strip()
            if "DNS Servers" in stripped and ":" in stripped:
                value = stripped.split(":", 1)[1].strip()
                if value and value not in values:
                    values.append(value)
            elif values and re.match(r"^[0-9a-fA-F:.]+$", stripped) and stripped not in values:
                values.append(stripped)
    else:
        try:
            with open("/etc/resolv.conf", "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == "nameserver" and parts[1] not in values:
                        values.append(parts[1])
        except OSError:
            pass
    return values


def gateway():
    if os.name == "nt":
        output = run_text(["ipconfig"])
        matches = re.findall(r"Default Gateway[.\s]*:\s*([0-9.]+)", output)
        for value in matches:
            if value:
                return value
        return "Unknown"
    value = run_text(["sh", "-c", "ip route show default 2>/dev/null | awk '{print $3; exit}'"])
    return value or "Unknown"


def system_uptime():
    if os.name == "nt":
        boot = run_text(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime"])
        return boot or "Information unavailable on Windows"
    value = run_text(["uptime", "-p"])
    return value or "Unknown"


def total_memory():
    if os.name == "nt":
        value = run_text(["powershell", "-NoProfile", "-Command", "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB)"])
        return f"{value} MB" if value else ""
    output = run_text(["free", "-m"])
    match = re.search(r"^Mem:\s+(\d+)", output, re.MULTILINE)
    return f"{match.group(1)} MB" if match else ""


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    version = read_version()
    hostname = socket.gethostname()
    os_name = f"{platform.system()} {platform.release()}".strip()
    processor = platform.processor() or platform.machine() or "Unknown"
    memory = total_memory()
    private_ipv4 = []
    public_ipv4 = []
    for ip in ipv4_addresses():
        if is_private_ipv4(ip):
            private_ipv4.append(ip)
        else:
            public_ipv4.append(ip)
    ipv6 = ipv6_addresses()
    dns = dns_servers()
    network_rows = ""
    if private_ipv4:
        network_rows += f'<div class="info-row"><span class="info-label">Private IPv4</span><span>{h(", ".join(private_ipv4))}</span></div>'
        network_rows += f'<div class="info-row"><span class="info-label">Public IPv4 (Detected)</span><span>{h(detected_public_ipv4())}</span></div>'
    else:
        network_rows += f'<div class="info-row"><span class="info-label">Public IPv4</span><span>{h(", ".join(public_ipv4) or "Unknown")}</span></div>'
    network_rows += f'<div class="info-row"><span class="info-label">Gateway</span><span>{h(gateway())}</span></div>'
    if dns:
        network_rows += f'<div class="info-row"><span class="info-label">DNS Servers</span><span>{h(", ".join(dns))}</span></div>'
    if ipv6:
        network_rows += f'<div class="info-row"><span class="info-label">IPv6 Addresses</span><span>{h(", ".join(ipv6))}</span></div>'
    memory_row = f'<div class="info-row"><span class="info-label">Total Memory</span><span>{h(memory)}</span></div>' if memory else ""
    body = f"""
    <div id="about" class="tab-content active">
        <picture>
            <source srcset="/assets/OPENPAGINGSERVER-768x576-DARKMODE.png" media="(prefers-color-scheme: dark)">
            <img src="/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png" class="server-image" alt="Open Paging Server">
        </picture>
        <p>Open Paging Server{(" " + h(version)) if version else ""}</p>
        <p>Open Paging Server is licensed under the GNU General Public License v2.0. Third-party components, modules, and software used by Open Paging Server are subject to their own licenses.</p>
        <p>Open Paging Server is provided "as is" without any warranties, express or implied, including but not limited to fitness for a particular purpose or non-infringement.</p>
        <div class="info-card">
            <h2>Hardware & OS</h2>
            <div class="info-row"><span class="info-label">Hostname</span><span>{h(hostname)}</span></div>
            <div class="info-row"><span class="info-label">Operating System</span><span>{h(os_name)}</span></div>
            <div class="info-row"><span class="info-label">Processor</span><span>{h(processor)}</span></div>
            {memory_row}
            <div class="info-row"><span class="info-label">System Uptime</span><span>{h(system_uptime())}</span></div>
        </div>
        <div class="info-card">
            <h2>Networking</h2>
            {network_rows}
        </div>
    </div>"""
    return settings_page("About", ctx, "about", body)

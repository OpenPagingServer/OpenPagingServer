import getpass
import os
import random
import re
import socket
import string
import subprocess
import sys
import time
from pathlib import Path
import shutil

try:
    import mysql.connector
except ModuleNotFoundError:
    print("Missing dependency: mysql-connector-python")
    print("Install it with: python3 -m pip install mysql-connector-python")
    sys.exit(1)

DATABASE_NAME = "openpagingserver"
DATABASE_USER = "openpagingserver"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENDPOINT_MODULES_DIR = Path("/var/lib/openpagingserver/endpointmodules")
TRUSTED_CA_DIR = Path("/etc/openpagingserver/trustedca")
PROJECT_CA_URL = "https://install.openpagingserver.org/rootca.crt"
PROJECT_CA_PATH = TRUSTED_CA_DIR / "OpenPagingServerProject.crt"
TRUSTED_CA_README_URL = "https://install.openpagingserver.org/trustedca-dir.md"
TRUSTED_CA_README_PATH = TRUSTED_CA_DIR / "README.md"
DEFAULT_WEB_PORT = 80
WEB_PORT_FALLBACKS = [81, 82, 83, 84, 85, 8080, 7000, 7001, 7010, 7100]
DEFAULT_SIP_PORT = 5060
SIP_PORT_FALLBACKS = [5065, 5160, 5162, 5260, 17777, 17778, 18888, 18887]
RANDOM_PORT_MIN = 1024
RANDOM_PORT_MAX = 60999
RANDOM_PORT_MAX_ATTEMPTS = 1000


def random_password(length=32):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def can_bind(port, socket_type, family):
    address = "0.0.0.0" if family == socket.AF_INET else "::"

    try:
        sock = socket.socket(family, socket_type)
    except OSError:
        return True

    try:
        sock.bind((address, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def tcp_port_available(port):
    return can_bind(port, socket.SOCK_STREAM, socket.AF_INET) and can_bind(port, socket.SOCK_STREAM, socket.AF_INET6)


def udp_port_available(port):
    return can_bind(port, socket.SOCK_DGRAM, socket.AF_INET) and can_bind(port, socket.SOCK_DGRAM, socket.AF_INET6)


def web_port_available(port):
    return tcp_port_available(port)


def sip_port_available(port):
    return tcp_port_available(port) and udp_port_available(port)


def extract_process_from_output(output):
    match = re.search(r'users:\(\(\"([^\"]+)\"', output)
    if match:
        return match.group(1)

    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) >= 2:
        parts = lines[1].split()
        if parts:
            return parts[0]

    return ""


def find_port_service_with_ss(port, udp=False):
    if shutil.which("ss") is None:
        return ""

    command = ["ss", "-H", "-lunp" if udp else "-ltnp", f"sport = :{port}"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return ""

    return extract_process_from_output(result.stdout)


def find_port_service_with_lsof(port, udp=False):
    if shutil.which("lsof") is None:
        return ""

    if udp:
        command = ["lsof", "-nP", f"-iUDP:{port}"]
    else:
        command = ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return ""

    return extract_process_from_output(result.stdout)


def find_port_service(port, protocols):
    for protocol in protocols:
        udp = protocol == "udp"
        service = find_port_service_with_ss(port, udp=udp)
        if service:
            return service

        service = find_port_service_with_lsof(port, udp=udp)
        if service:
            return service

    return "unknown service"


def choose_random_port(port_available, blocked_ports):
    blocked_ports = set(blocked_ports)

    for _ in range(RANDOM_PORT_MAX_ATTEMPTS):
        port = random.randint(RANDOM_PORT_MIN, RANDOM_PORT_MAX)
        if port in blocked_ports:
            continue
        if port_available(port):
            return port

    for port in range(RANDOM_PORT_MIN, RANDOM_PORT_MAX + 1):
        if port in blocked_ports:
            continue
        if port_available(port):
            return port

    raise RuntimeError("No available ports found")


def select_port(default_port, fallback_ports, port_available, protocols, label):
    if port_available(default_port):
        return default_port

    selected_port = None
    for fallback_port in fallback_ports:
        if port_available(fallback_port):
            selected_port = fallback_port
            break

    if selected_port is None:
        selected_port = choose_random_port(port_available, [default_port, *fallback_ports])

    service = find_port_service(default_port, protocols)
    print(f"Port {default_port} is already in use by {service}. {label} port will be set to {selected_port}")

    return selected_port


def sql_string(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def safe_endpoint_module_package(path):
    return (
        path.is_file()
        and path.suffix == ".opsepm"
        and all(char.isalnum() or char in "-_." for char in path.name)
    )


def discover_endpoint_module_packages():
    if not ENDPOINT_MODULES_DIR.is_dir():
        return []
    return sorted(path.stem for path in ENDPOINT_MODULES_DIR.iterdir() if safe_endpoint_module_package(path))


def systemctl_available():
    return shutil.which("systemctl") is not None


def systemd_unit_exists(unit):
    if not systemctl_available():
        return False

    checks = [
        ["systemctl", "list-unit-files", unit],
        ["systemctl", "list-units", "--all", unit],
    ]

    for check in checks:
        result = subprocess.run(check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        if result.returncode == 0:
            return True

    return False


def run_systemctl(action):
    unit = "openpagingserver.service"

    if not systemctl_available():
        return

    if not systemd_unit_exists(unit):
        return

    subprocess.run(["systemctl", action, unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)


def download_file(url, destination):
    if shutil.which("wget") is None:
        print(f"Skipping download because wget is not installed: {url}")
        return False

    try:
        subprocess.run(
            ["wget", "-q", "-O", str(destination), url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        print(f"Warning: failed to download {url}")
        return False


def install_project_root_ca():
    TRUSTED_CA_DIR.mkdir(parents=True, exist_ok=True)
    download_file(PROJECT_CA_URL, PROJECT_CA_PATH)
    download_file(TRUSTED_CA_README_URL, TRUSTED_CA_README_PATH)


def connect_as_admin():
    try:
        return mysql.connector.connect(
            user="root",
            unix_socket="/var/run/mysqld/mysqld.sock",
        )
    except mysql.connector.Error:
        print("Root socket auth failed, enter database admin credentials:")
        user = input("Username: ")
        passwd = getpass.getpass("Password: ")
        try:
            return mysql.connector.connect(user=user, password=passwd)
        except mysql.connector.Error as exc:
            print("Connection failed:", exc)
            sys.exit(1)


def execute_schema(cursor):
    schema_statements = [
        """
        CREATE TABLE IF NOT EXISTS messages (
            type ENUM('liveaudio','liveaudio+text','text','text+audio','audio','record','record+text','text+audio+live') DEFAULT NULL,
            messageid INT DEFAULT NULL,
            name VARCHAR(255) DEFAULT NULL,
            shortmessage TEXT DEFAULT NULL,
            longmessage TEXT DEFAULT NULL,
            audio TEXT DEFAULT NULL,
            image VARCHAR(255) DEFAULT '',
            color VARCHAR(7) DEFAULT NULL,
            icon VARCHAR(255) DEFAULT '',
            expires VARCHAR(100) DEFAULT NULL,
            vendor_specific TEXT DEFAULT NULL,
            owner_user_id INT DEFAULT NULL,
            priority ENUM('Low','Normal','High','Emergency') DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            email VARCHAR(255) UNIQUE,
            password VARCHAR(64) NOT NULL,
            salt VARCHAR(64) NOT NULL,
            role ENUM('admin','tempadmin','user','tempuser','receiver','tempreceiver') NOT NULL,
            auth_provider VARCHAR(32) NOT NULL DEFAULT 'local',
            external_id VARCHAR(255) DEFAULT NULL,
            display_name VARCHAR(255) DEFAULT NULL,
            ldap_groups LONGTEXT DEFAULT NULL,
            identity_recipient_groups LONGTEXT DEFAULT NULL,
            loginsleft INT DEFAULT 0,
            logincount INT DEFAULT 0,
            lastlogin DATETIME DEFAULT NULL,
            accountexpire DATETIME DEFAULT NULL,
            accountcreated DATE DEFAULT CURRENT_DATE,
            adminperm LONGTEXT DEFAULT NULL,
            msgsendperm LONGTEXT DEFAULT NULL,
            userperm LONGTEXT DEFAULT NULL,
            restrict_groups TINYINT(1) NOT NULL DEFAULT 0,
            restrict_messages TINYINT(1) NOT NULL DEFAULT 0,
            restrict_bell_schedules TINYINT(1) NOT NULL DEFAULT 0,
            require_password_change TINYINT(1) NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ip VARCHAR(45) DEFAULT NULL,
            username VARCHAR(255) DEFAULT NULL,
            success TINYINT(1) DEFAULT NULL,
            attempt_time DATETIME DEFAULT NULL,
            user_agent TEXT DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS endpointmodulesloaded (
            `dir` VARCHAR(100) NOT NULL,
            enabled ENUM('true','false') DEFAULT 'true',
            `tables` TEXT DEFAULT NULL,
            package_path TEXT DEFAULT NULL,
            trusted VARCHAR(10) NOT NULL DEFAULT 'false',
            signature_state VARCHAR(32) NOT NULL DEFAULT 'unsigned',
            signer VARCHAR(255) DEFAULT NULL,
            load_error TEXT DEFAULT NULL,
            manifest_json LONGTEXT DEFAULT NULL,
            PRIMARY KEY (`dir`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS groups (
            id VARCHAR(100) DEFAULT NULL,
            name VARCHAR(100) DEFAULT NULL,
            members TEXT DEFAULT NULL,
            monitor_members TEXT DEFAULT NULL,
            monitor_categories VARCHAR(64) DEFAULT NULL,
            page_pre_tone TEXT DEFAULT NULL,
            page_post_tone TEXT DEFAULT NULL,
            owner_user_id INT DEFAULT NULL,
            suspend_bells_on_emergency TINYINT(1) NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS broadcasts (
            id VARCHAR(100) DEFAULT NULL,
            shortmessage VARCHAR(100) DEFAULT NULL,
            longmessage TEXT DEFAULT NULL,
            icon VARCHAR(100) DEFAULT NULL,
            color VARCHAR(100) DEFAULT NULL,
            vendor_specific TEXT DEFAULT NULL,
            type ENUM('Page','AudioMessage','TextMessage','Text+AudioMessage') DEFAULT NULL,
            expires DATETIME DEFAULT NULL,
            issued DATETIME DEFAULT NULL,
            `groups` TEXT DEFAULT NULL,
            image VARCHAR(100) DEFAULT NULL,
            audio TEXT DEFAULT NULL,
            sender VARCHAR(100) DEFAULT NULL,
            priority ENUM('Low','Normal','High','Emergency') DEFAULT NULL,
            delivery VARCHAR(100) DEFAULT NULL,
            name VARCHAR(100) DEFAULT NULL,
            template_id VARCHAR(64) DEFAULT NULL,
            expires_rule VARCHAR(64) DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS bell_schedules (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            enabled TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            timezone VARCHAR(64) NOT NULL DEFAULT 'server'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS bell_lists (
            id INT AUTO_INCREMENT PRIMARY KEY,
            schedule_id INT NOT NULL DEFAULT 0,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            KEY schedule_id_idx (schedule_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS bell_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            list_id INT NOT NULL,
            fire_time TIME NOT NULL,
            audio TEXT NOT NULL,
            days_of_week VARCHAR(32) NOT NULL DEFAULT '0,1,2,3,4,5,6',
            KEY list_id_idx (list_id),
            KEY fire_time_idx (fire_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS bell_schedule_groups (
            schedule_id INT NOT NULL,
            group_id VARCHAR(100) NOT NULL,
            PRIMARY KEY (schedule_id, group_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS bell_calendar (
            schedule_id INT NOT NULL,
            bell_date DATE NOT NULL,
            list_id INT DEFAULT NULL,
            PRIMARY KEY (schedule_id, bell_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS bell_calendar_lists (
            schedule_id INT NOT NULL,
            bell_date DATE NOT NULL,
            list_id INT NOT NULL,
            PRIMARY KEY (schedule_id, bell_date, list_id),
            KEY bell_date_idx (bell_date),
            KEY list_id_idx (list_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS history (
            entryid INT NOT NULL AUTO_INCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            actor VARCHAR(255) DEFAULT NULL,
            action VARCHAR(255) DEFAULT NULL,
            target VARCHAR(255) DEFAULT NULL,
            message TEXT NOT NULL,
            icon VARCHAR(50) DEFAULT NULL,
            PRIMARY KEY (entryid)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS systemsettings (
            parameter VARCHAR(128) NOT NULL,
            value TEXT NOT NULL,
            description TEXT NOT NULL,
            PRIMARY KEY (parameter)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS api_tokens (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            token_hash VARCHAR(64) NOT NULL,
            token_prefix VARCHAR(24) NOT NULL,
            expires_at DATETIME DEFAULT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at DATETIME DEFAULT NULL,
            UNIQUE KEY api_tokens_hash_unique (token_hash),
            KEY api_tokens_user_idx (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS user_group_access (
            user_id INT NOT NULL,
            group_id VARCHAR(100) NOT NULL,
            PRIMARY KEY (user_id, group_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS user_message_access (
            user_id INT NOT NULL,
            message_id INT NOT NULL,
            PRIMARY KEY (user_id, message_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS user_bell_schedule_access (
            user_id INT NOT NULL,
            schedule_id INT NOT NULL,
            PRIMARY KEY (user_id, schedule_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS loginhistory (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT DEFAULT NULL,
            username VARCHAR(255) DEFAULT NULL,
            auth_provider VARCHAR(32) NOT NULL DEFAULT 'local',
            session_id VARCHAR(64) DEFAULT NULL,
            session_type VARCHAR(16) NOT NULL DEFAULT 'web',
            ip VARCHAR(64) DEFAULT NULL,
            user_agent TEXT DEFAULT NULL,
            login_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY loginhistory_user_idx (user_id),
            KEY loginhistory_session_idx (session_id),
            KEY loginhistory_time_idx (login_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            session_id VARCHAR(64) NOT NULL PRIMARY KEY,
            user_id INT NOT NULL,
            session_type VARCHAR(16) NOT NULL DEFAULT 'web',
            auth_provider VARCHAR(32) NOT NULL DEFAULT 'local',
            username VARCHAR(255) DEFAULT NULL,
            ip VARCHAR(64) DEFAULT NULL,
            user_agent TEXT DEFAULT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME DEFAULT NULL,
            revoked_at DATETIME DEFAULT NULL,
            KEY user_sessions_user_idx (user_id),
            KEY user_sessions_type_idx (session_type),
            KEY user_sessions_revoked_idx (revoked_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
    ]

    for statement in schema_statements:
        cursor.execute(statement)


def seed_defaults(cursor, webserver_http_port, insecure_sip_port):
    endpoint_module_dirs = [(module_dir, "true") for module_dir in discover_endpoint_module_packages()]
    if endpoint_module_dirs:
        cursor.executemany(
            """
            INSERT INTO endpointmodulesloaded (`dir`, enabled)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE enabled = VALUES(enabled)
            """,
            endpoint_module_dirs,
        )

    cursor.execute(
        "INSERT IGNORE INTO bell_schedules (id, name, enabled, timezone) VALUES (1, 'Default Bell Schedule', 1, 'server')"
    )
    cursor.execute(
        "INSERT IGNORE INTO bell_lists (id, schedule_id, name) VALUES (1, 1, 'Regular Day')"
    )

    messages = [
        (
            "text+audio",
            5,
            "TEST Message",
            "This is a test of ${productname}",
            "This is a test of the ${productname} MNS system. No action is required.",
            "OPS-900HZ-SlowPulse.wav:OPS-TESTING.wav",
            "",
            None,
            "",
            "15m",
            None,
            None,
        ),
    ]

    cursor.executemany(
        """
        INSERT IGNORE INTO messages (`type`, messageid, name, shortmessage, longmessage, audio, image, color, icon, expires, vendor_specific, priority)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        messages,
    )

    systemsettings = [
        ("enable_insecure_sip", "1", "Enable SIP over UDP and TCP (0/1)"),
        ("enable_login_logo", "1", "Enable the logo on login page"),
        (
            "enable_secure_sip",
            "0",
            "Enable SIP over TLS (0 = NO, 1 = Yes with same cert as web server, 2 = Yes with independent cert)",
        ),
        (
            "analytics",
            "0",
            "Send optional analytics to the Open Paging Server project. Privacy Policy: https://www.openpagingserver.org/privacypolicy/analytics",
        ),
        (
            "analytics_server_id",
            "",
            "Analytics identifier. Reference this to Open Paging Server Project staff or in bug reports when requested.",
        ),
        (
            "analytics_server_secret",
            "",
            "Analytics secret. DO NOT SHARE.",
        ),
        ("favicon", "/assets/favicon.svg", "Browser Favicon. Path to file within web server."),
        ("insecure_sip_port", str(insecure_sip_port), "Port for UDP/TCP SIP"),
        ("sip_nat_support", "1", "Enable NAT support for SIP (0/1)"),
        ("sip_external_ipv4_mode", "auto", "SIP external IPv4 mode (auto/manual)"),
        ("sip_external_ipv4", "", "Manual SIP external IPv4 address"),
        ("sip_rtp_port_start", "40000", "SIP RTP port range start"),
        ("sip_rtp_port_end", "50000", "SIP RTP port range end"),
        ("sip_intrusion_prevention", "1", "WARNING!!! Disabling this setting WILL compromise the security of this server, especially if the SIP port is exposed to WAN. There's usually no reason to disable this in production. The Open Paging Server project is NOT responsible for any financial loss caused by abuse of telephone service by malicious bots. CONTINUE AT YOUR OWN RISK!!!"),
        ("sip_block_scanners", "1", "WARNING!!! Disabling this setting WILL compromise the security of this server, especially if the SIP port is exposed to WAN. There's usually no reason to disable this in production. The Open Paging Server project is NOT responsible for any financial loss caused by abuse of telephone service by malicious bots. CONTINUE AT YOUR OWN RISK!!!"),
        ("login_banner_enabled", "1", "Enable or disable the login page banner (0/1)"),
        (
            "login_banner_message",
            "OPS is currently in early devlopment stages, and is not yet suitable for production use. Visit our website at https://www.openpagingserver.org to learn how to contribute and to join our Discord. Thank you for installing the Open Paging Server beta!",
            "Message text for the login page banner",
        ),
        ("login_banner_title", "Welcome to Open Paging Server Beta!!!", "Optional title for the login page banner"),
        ("login_captcha_external_only", "1", "Require login CAPTCHA only for external IP addresses (0/1)"),
        (
            "login_logo_dark",
            "/assets/OPENPAGINGSERVER-768x576-DARKMODE.png",
            "Dark mode logo. Path to file within web server.",
        ),
        (
            "login_logo_light",
            "/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png",
            "Light mode logo. Path to file within web server.",
        ),
        ("product_name", "Open Paging Server", "Name of this server."),
        ("secure_sip_cert", "", "If enable_secure_sip is 2, this cert will be used. Path to file"),
        ("secure_sip_port", "5061", "Port for TLS SIP"),
        ("secure_sip_privkey", "", "If enable_secure_sip is 2, this private key will be used. Path to file"),
        (
            "separate_dark_logo",
            "1",
            "Use a separate logo for dark mode. When disabled, uses only logo_light. (0/1)",
        ),
        ("show_online_docs", "1", "Show GUI links to docs.openpagingserver.org (0/1)"),
        ("allow_multicast_gateway", "1", "Allow Multicast Gateway connections to this server (0/1)"),
        ("use_logo_in_sidebar", "1", "Use a logo in the sidebar, if disabled the product name will show"),
        ("sidebar_logo_light", "/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png", "Light mode logo for the sidebar"),
        ("sidebar_logo_dark", "/assets/OPENPAGINGSERVER-768x576-DARKMODE.png", "Dark mode logo for the sidebar"),
        ("webserver_enable", "1", "Enable access to Open Paging Server via a web browser (0/1)"),
        ("webserver_https_enable", "0", "HTTPs Enable (0/1)"),
        ("webserver_https_port", "443", "HTTPs Server Port (Default: 443)"),
        ("webserver_https_privkey", "", "HTTPS private key path on the server. Must start with /"),
        ("webserver_https_cert", "", "HTTPS certificate path on the server. Must start with /"),
        ("webserver_http_to_https", "0", "Automatically redirect HTTP requests to HTTPS (0/1)"),
        ("webserver_hsts", "0", "Send HSTS headers over HTTPS (0/1)"),
        ("webserver_http_port", str(webserver_http_port), "HTTP Server Port (Default: 80)"),
        ("api_http_enable", "0", "Enable REST API over HTTP (0/1)"),
        ("api_http_port", "8088", "REST API HTTP port"),
        ("identity_provider", "local", ""),
        ("identity_redirect_auto", "1", ""),
        ("identity_allow_local_login", "0", ""),
        ("ldap_enabled", "0", ""),
        ("ldap_template", "generic", ""),
        ("ldap_server_address", "", ""),
        ("ldap_server_port", "389", ""),
        ("ldap_secure", "0", ""),
        ("ldap_ca_certificate", "", ""),
        ("ldap_base_dn", "", ""),
        ("ldap_bind_username", "", ""),
        ("ldap_bind_password", "", ""),
        ("ldap_password_change_url", "", ""),
        ("ldap_login_field", "uid", ""),
        ("ldap_user_search_filter", "({field}={username})", ""),
        ("ldap_display_name_field", "cn", ""),
        ("ldap_email_field", "mail", ""),
        ("ldap_required_group", "", ""),
        ("ldap_admin_group", "", ""),
        ("ldap_auto_create_users", "0", ""),
        ("ldap_local_login_fallback", "1", ""),
        ("ldap_connection_timeout", "5", ""),
        ("ldap_failure_behavior", "deny", ""),
        ("ldap_group_sync", "1", ""),
        ("ldap_default_role", "receiver", ""),
        ("ldap_role_mappings", "[]", ""),
        ("oidc_discovery_url", "", ""),
        ("oidc_client_id", "", ""),
        ("oidc_client_secret", "", ""),
        ("oidc_password_change_url", "", ""),
        ("oidc_scim_enabled", "0", ""),
        ("oidc_scim_base_url", "", ""),
        ("oidc_scim_bearer_token", "", ""),
        ("oidc_scim_timeout", "5", ""),
        ("oidc_scim_sync_groups", "1", ""),
        ("oidc_scope", "openid profile email", ""),
        ("oidc_username_claim", "preferred_username", ""),
        ("oidc_display_name_claim", "name", ""),
        ("oidc_email_claim", "email", ""),
        ("oidc_groups_claim", "groups", ""),
        ("oidc_required_group", "", ""),
        ("oidc_admin_group", "", ""),
        ("oidc_auto_create_users", "0", ""),
        ("oidc_group_sync", "1", ""),
        ("oidc_default_role", "receiver", ""),
        ("oidc_role_mappings", "[]", ""),
        ("saml_idp_entity_id", "", ""),
        ("saml_sso_url", "", ""),
        ("saml_x509_certificate", "", ""),
        ("saml_password_change_url", "", ""),
        ("saml_scim_enabled", "0", ""),
        ("saml_scim_base_url", "", ""),
        ("saml_scim_bearer_token", "", ""),
        ("saml_scim_timeout", "5", ""),
        ("saml_scim_sync_groups", "1", ""),
        ("saml_username_attribute", "uid", ""),
        ("saml_display_name_attribute", "displayName", ""),
        ("saml_email_attribute", "mail", ""),
        ("saml_groups_attribute", "groups", ""),
        ("saml_required_group", "", ""),
        ("saml_admin_group", "", ""),
        ("saml_auto_create_users", "0", ""),
        ("saml_group_sync", "1", ""),
        ("saml_default_role", "receiver", ""),
        ("saml_role_mappings", "[]", ""),
        ("notify_users_about_account_expiration", "1", ""),
    ]
    cursor.executemany(
        """
        INSERT INTO systemsettings (parameter, value, description)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            value = IF(parameter IN ('analytics_server_id', 'analytics_server_secret') AND value <> '', value, VALUES(value)),
            description = VALUES(description)
        """,
        systemsettings,
    )


def write_config(db_password):
    env_file = f"""DB_HOST='127.0.0.1'
DB_USER='{DATABASE_USER}'
DB_PASS={sql_string(db_password)}
DB_NAME='{DATABASE_NAME}'
DEBUG=false
WEB_REVERSE_PROXY_ALLOWED=127.0.0.1
API_REVERSE_PROXY_ALLOWED=127.0.0.1
DEMO_MODE=false

"""

    os.makedirs("/var/lib/openpagingserver/assets", exist_ok=True)
    with open(PROJECT_ROOT / ".env", "w", encoding="utf-8") as env_config_file:
        env_config_file.write(env_file)
    with open(PROJECT_ROOT / ".oobe", "w", encoding="utf-8"):
        pass


def recreate_database_user(cursor, db_password):
    hosts = ["localhost", "127.0.0.1"]

    for host in hosts:
        cursor.execute(f"DROP USER IF EXISTS '{DATABASE_USER}'@'{host}'")
        cursor.execute(f"CREATE USER '{DATABASE_USER}'@'{host}' IDENTIFIED BY {sql_string(db_password)}")
        cursor.execute(f"GRANT ALL PRIVILEGES ON `{DATABASE_NAME}`.* TO '{DATABASE_USER}'@'{host}'")

    cursor.execute("FLUSH PRIVILEGES")


def main():
    conn = None
    cursor = None

    try:
        conn = connect_as_admin()
        cursor = conn.cursor()

        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DATABASE_NAME}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
        cursor.execute(f"USE `{DATABASE_NAME}`")

        db_password = random_password()
        recreate_database_user(cursor, db_password)

        execute_schema(cursor)
        webserver_http_port = select_port(DEFAULT_WEB_PORT, WEB_PORT_FALLBACKS, web_port_available, ["tcp"], "Web")
        insecure_sip_port = select_port(DEFAULT_SIP_PORT, SIP_PORT_FALLBACKS, sip_port_available, ["tcp", "udp"], "SIP")
        seed_defaults(cursor, webserver_http_port, insecure_sip_port)

        conn.commit()
        write_config(db_password)
        install_project_root_ca()
        print("Database initialized successfully")
    except mysql.connector.Error as exc:
        if conn:
            conn.rollback()
        print("Database setup failed:", exc)
        sys.exit(1)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def wrapped_main():
    unit = "openpagingserver.service"
    service_exists = systemctl_available() and systemd_unit_exists(unit)

    if service_exists:
        run_systemctl("stop")
        time.sleep(5)

    try:
        main()
    finally:
        if service_exists:
            time.sleep(5)
            run_systemctl("start")


if __name__ == "__main__":
    wrapped_main()

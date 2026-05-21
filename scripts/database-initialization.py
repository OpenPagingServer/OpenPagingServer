import getpass
import os
import random
import string
import subprocess
import sys
import time
from pathlib import Path
import shutil

import mysql.connector


DATABASE_NAME = "openpagingserver"
DATABASE_USER = "openpagingserver"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENDPOINT_MODULES_DIR = PROJECT_ROOT / "endpoint-modules"


def random_password(length=32):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def sql_string(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def php_string(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def safe_endpoint_module_dir(path):
    return (
        path.is_dir()
        and path.name not in (".", "..", "__pycache__")
        and all(char.isalnum() or char in "-_" for char in path.name)
        and (path / "index.py").exists()
    )


def discover_endpoint_module_dirs():
    if not ENDPOINT_MODULES_DIR.is_dir():
        return []
    return sorted(path.name for path in ENDPOINT_MODULES_DIR.iterdir() if safe_endpoint_module_dir(path))


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
        CREATE TABLE messages (
            type ENUM('liveaudio','liveaudio+text','text','text+audio','audio','record','record+text','text+audio+live') DEFAULT NULL,
            messageid INT DEFAULT NULL,
            name VARCHAR(255) DEFAULT NULL,
            shortmessage TEXT DEFAULT NULL,
            longmessage TEXT DEFAULT NULL,
            audio VARCHAR(255) DEFAULT NULL,
            image VARCHAR(255) DEFAULT '',
            color VARCHAR(7) DEFAULT NULL,
            icon VARCHAR(255) DEFAULT '',
            expires VARCHAR(100) DEFAULT NULL,
            vendor_specific TEXT DEFAULT NULL,
            priority ENUM('Low','Normal','High','Emergency') DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            email VARCHAR(255) UNIQUE,
            password VARCHAR(64) NOT NULL,
            salt VARCHAR(64) NOT NULL,
            role ENUM('admin','tempadmin','user','tempuser','receiver','tempreceiver') NOT NULL,
            loginsleft INT DEFAULT 0,
            logincount INT DEFAULT 0,
            lastlogin DATETIME DEFAULT NULL,
            accountexpire DATE DEFAULT NULL,
            accountcreated DATE DEFAULT (CURRENT_DATE),
            adminperm LONGTEXT DEFAULT NULL,
            msgsendperm LONGTEXT DEFAULT NULL,
            userperm LONGTEXT DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE login_attempts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ip VARCHAR(45) DEFAULT NULL,
            username VARCHAR(255) DEFAULT NULL,
            success TINYINT(1) DEFAULT NULL,
            attempt_time DATETIME DEFAULT NULL,
            user_agent TEXT DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE endpointmodulesloaded (
            `dir` VARCHAR(100) NOT NULL,
            enabled ENUM('true','false') DEFAULT 'true',
            PRIMARY KEY (`dir`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE groups (
            id VARCHAR(100) DEFAULT NULL,
            name VARCHAR(100) DEFAULT NULL,
            members TEXT DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE broadcasts (
            id VARCHAR(100) DEFAULT NULL,
            shortmessage VARCHAR(100) DEFAULT NULL,
            longmessage TEXT DEFAULT NULL,
            icon VARCHAR(100) DEFAULT NULL,
            color VARCHAR(100) DEFAULT NULL,
            vendor_specific VARCHAR(100) DEFAULT NULL,
            type ENUM('Page','AudioMessage','TextMessage','Text+AudioMessage') DEFAULT NULL,
            expires DATETIME DEFAULT NULL,
            issued DATETIME DEFAULT NULL,
            `groups` TEXT DEFAULT NULL,
            image VARCHAR(100) DEFAULT NULL,
            audio VARCHAR(10000) DEFAULT NULL,
            sender VARCHAR(100) DEFAULT NULL,
            priority ENUM('Low','Normal','High','Emergency') DEFAULT NULL,
            delivery VARCHAR(100) DEFAULT NULL,
            name VARCHAR(100) DEFAULT NULL,
            template_id VARCHAR(64) DEFAULT NULL,
            expires_rule VARCHAR(64) DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE bell_schedules (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            enabled TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            timezone VARCHAR(64) NOT NULL DEFAULT 'server'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE bell_lists (
            id INT AUTO_INCREMENT PRIMARY KEY,
            schedule_id INT NOT NULL DEFAULT 0,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            KEY schedule_id_idx (schedule_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE bell_events (
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
        CREATE TABLE bell_schedule_groups (
            schedule_id INT NOT NULL,
            group_id VARCHAR(100) NOT NULL,
            PRIMARY KEY (schedule_id, group_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE bell_calendar (
            schedule_id INT NOT NULL,
            bell_date DATE NOT NULL,
            list_id INT DEFAULT NULL,
            PRIMARY KEY (schedule_id, bell_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE bell_calendar_lists (
            schedule_id INT NOT NULL,
            bell_date DATE NOT NULL,
            list_id INT NOT NULL,
            PRIMARY KEY (schedule_id, bell_date, list_id),
            KEY bell_date_idx (bell_date),
            KEY list_id_idx (list_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
        """
        CREATE TABLE history (
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
        CREATE TABLE systemsettings (
            parameter VARCHAR(128) NOT NULL,
            value TEXT NOT NULL,
            description TEXT NOT NULL,
            PRIMARY KEY (parameter)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """,
    ]

    for statement in schema_statements:
        cursor.execute(statement)


def seed_defaults(cursor):
    endpoint_module_dirs = [(module_dir, "true") for module_dir in discover_endpoint_module_dirs()]
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
        "INSERT INTO bell_schedules (name, enabled, timezone) VALUES ('Default Bell Schedule', 1, 'server')"
    )
    default_bell_schedule_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO bell_lists (schedule_id, name) VALUES (%s, 'Regular Day')",
        (default_bell_schedule_id,),
    )

    messages = [
        (
            "text+audio",
            5,
            "TEST Message",
            "This is a test of Open Paging Server",
            "This is a test of the Open Paging Server MNS system. No action is required.",
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
        INSERT INTO messages (`type`, messageid, name, shortmessage, longmessage, audio, image, color, icon, expires, vendor_specific, priority)
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
            "Permanent anonymous analytics server identifier. Requested from the analytics server when analytics is first enabled.",
        ),
        (
            "analytics_server_secret",
            "",
            "Permanent analytics server secret. Keep private.",
        ),
        ("favicon", "/assets/favicon.svg", "Browser Favicon. Path to file within web server."),
        ("insecure_sip_port", "5060", "Port for UDP/TCP SIP"),
        ("login_banner_enabled", "1", "Enable or disable the login page banner (0/1)"),
        (
            "login_banner_message",
            "OPS is currently in early devlopment stages, and is not yet suitable for production use.  Visit our website at https://www.openpagingserver.org to learn how to contribute and to join our Discord. Thank you for installing the Open Paging Server beta!",
            "Message text for the login page banner",
        ),
        ("login_banner_title", "Welcome to Open Paging Server Beta!!!", "Optional title for the login page banner"),
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
        ("use_logo_in_sidebar", "1", "Use a logo in the sidebar, if disabled the product name will show"),
        ("sidebar_logo_light", "/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png", "Light mode logo for the sidebar"),
        ("sidebar_logo_dark", "/assets/OPENPAGINGSERVER-768x576-DARKMODE.png", "Dark mode logo for the sidebar"),
        ("webserver_https_enable", "0", "HTTPs Enable (0/1)"),
        ("webserver_https_port", "443", "HTTPs Server Port (Default: 443)"),
        ("webserver_http_port", "80", "HTTP Server Port (Default: 80)"),
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
    config_php = f"""<?php
$host = 'localhost';
$db   = '{DATABASE_NAME}';
$user = '{DATABASE_USER}';
$pass = {php_string(db_password)};
$charset = 'utf8mb4';

$dsn = "mysql:host=$host;dbname=$db;charset=$charset";
$options = [
    PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES   => false,
];

try {{
    $pdo = new PDO($dsn, $user, $pass, $options);
}} catch (\\PDOException $e) {{
    throw new \\PDOException($e->getMessage(), (int)$e->getCode());
}}
"""

    env_file = f"""DB_HOST='127.0.0.1'
DB_USER='{DATABASE_USER}'
DB_PASS={sql_string(db_password)}
DB_NAME='{DATABASE_NAME}'
DEBUG=false
"""

    os.makedirs("/opt/OpenPagingServer/web", exist_ok=True)
    os.makedirs("/var/lib/openpagingserver/assets", exist_ok=True)
    with open("/opt/OpenPagingServer/web/config.php", "w", encoding="utf-8") as config_file:
        config_file.write(config_php)
    with open(PROJECT_ROOT / ".env", "w", encoding="utf-8") as env_config_file:
        env_config_file.write(env_file)
    with open(PROJECT_ROOT / ".oobe", "w", encoding="utf-8"):
        pass


def main():
    conn = connect_as_admin()
    cursor = conn.cursor()

    cursor.execute(f"SHOW DATABASES LIKE {sql_string(DATABASE_NAME)}")
    if cursor.fetchone():
        overwrite = input("Database exists. Overwrite? (y/n): ")
        if overwrite.lower() != "y":
            print("Exiting.")
            cursor.close()
            conn.close()
            sys.exit(0)
        cursor.execute(f"DROP DATABASE `{DATABASE_NAME}`")

    cursor.execute(f"CREATE DATABASE `{DATABASE_NAME}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
    cursor.execute(f"USE `{DATABASE_NAME}`")

    db_password = random_password()
    cursor.execute(f"DROP USER IF EXISTS '{DATABASE_USER}'@'localhost'")
    cursor.execute(f"CREATE USER '{DATABASE_USER}'@'localhost' IDENTIFIED BY {sql_string(db_password)}")
    cursor.execute(f"GRANT ALL PRIVILEGES ON `{DATABASE_NAME}`.* TO '{DATABASE_USER}'@'localhost'")
    cursor.execute("FLUSH PRIVILEGES")

    execute_schema(cursor)
    seed_defaults(cursor)

    conn.commit()
    cursor.close()
    conn.close()

    write_config(db_password)
    print("Database initialized successfully")


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

import subprocess
import sys
import random
import string
import mysql.connector
import os

def random_password(length=32):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

try:
    conn = mysql.connector.connect(
        user="root",
        unix_socket="/var/run/mysqld/mysqld.sock"
    )
except mysql.connector.Error:
    print("Root socket auth failed, enter credentials:")
    user = input("Username: ")
    passwd = input("Password: ")
    try:
        conn = mysql.connector.connect(user=user, password=passwd)
    except mysql.connector.Error as e:
        print("Connection failed:", e)
        sys.exit(1)

cursor = conn.cursor()
cursor.execute("SHOW DATABASES LIKE 'openpagingserver'")
if cursor.fetchone():
    overwrite = input("Database exists. Overwrite? (y/n): ")
    if overwrite.lower() != "y":
        print("Exiting.")
        sys.exit(0)
    cursor.execute("DROP DATABASE openpagingserver")

cursor.execute("CREATE DATABASE openpagingserver")
cursor.execute("USE openpagingserver")

db_password = random_password()
cursor.execute("DROP USER IF EXISTS 'openpagingserver'@'localhost'")
cursor.execute(f"CREATE USER 'openpagingserver'@'localhost' IDENTIFIED BY '{db_password}'")
cursor.execute("GRANT ALL PRIVILEGES ON openpagingserver.* TO 'openpagingserver'@'localhost'")
cursor.execute("FLUSH PRIVILEGES")

cursor.execute("""
CREATE TABLE messages (
    type ENUM('liveaudio','liveaudio+text','text','text+audio','audio','record','record+text','text+audio+live'),
    messageid INT,
    name VARCHAR(255),
    shortmessage TEXT,
    longmessage TEXT,
    audio VARCHAR(255),
    image VARCHAR(255) DEFAULT '',
    color VARCHAR(7),
    icon VARCHAR(255) DEFAULT ''
)
""")

cursor.execute("""
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    password VARCHAR(64) NOT NULL, 
    salt VARCHAR(64) NOT NULL,
    role ENUM('admin','tempadmin','user','tempuser','receiver','tempreceiver') NOT NULL,
    loginsleft INT DEFAULT 0,
    logincount INT DEFAULT 0,
    lastlogin DATETIME,
    accountexpire DATE,
    accountcreated DATE DEFAULT CURRENT_DATE,
    adminperm LONGTEXT,
    msgsendperm LONGTEXT,
    userperm LONGTEXT
)
""")

cursor.execute("""
CREATE TABLE login_attempts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ip VARCHAR(45),
    username VARCHAR(255),
    success TINYINT(1),
    attempt_time DATETIME,
    user_agent TEXT
) ENGINE=InnoDB
""")

cursor.execute("""
CREATE TABLE enabledmodules (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    path VARCHAR(255) NOT NULL,
    status TINYINT(1) NOT NULL DEFAULT 1,
    webpath VARCHAR(255) NOT NULL,
    webroles VARCHAR(255) NOT NULL DEFAULT 'user',
    webinterface TINYINT(1) NOT NULL DEFAULT 1,
    webname VARCHAR(255) NOT NULL DEFAULT '',
    webicon VARCHAR(50) NOT NULL DEFAULT 'fa-circle',
    PRIMARY KEY (id),
    UNIQUE KEY path_unique (path)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
""")

cursor.execute("""
CREATE TABLE `endpoints-input-sip` (
    name VARCHAR(100) DEFAULT NULL,
    extension VARCHAR(100) DEFAULT NULL,
    `group` VARCHAR(100) DEFAULT NULL,
    `trigger` VARCHAR(100) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
""")

cursor.execute("""
CREATE TABLE groups (
    id VARCHAR(100) DEFAULT NULL,
    name VARCHAR(100) DEFAULT NULL,
    members VARCHAR(100) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
""")

cursor.execute("""
CREATE TABLE systemsettings (
    parameter VARCHAR(128) NOT NULL,
    value TEXT NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (parameter)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
""")

cursor.execute("INSERT INTO messages VALUES ('text+audio',1,'SRP HOLD','HOLD! In your area.','Hold! In your room or area. Clear the halls.','srphold.wav','',NULL,'')")
cursor.execute("INSERT INTO messages VALUES ('text+audio',2,'SRP SECURE','SECURE!','Secure! Get Inside. Lock outside doors.','srpsecure.wav','',NULL,'')")
cursor.execute("INSERT INTO messages VALUES ('text+audio',3,'SRP LOCKDOWN','LOCKDOWN! Locks, Lights, Out of Sight.','LOCKDOWN! Locks, Lights, Out of Sight.','srplockdown.wav','',NULL,'')")

cursor.execute("INSERT INTO enabledmodules VALUES (3,'modules/bells',1,'/bells.php','user,admin,tempadmin',1,'Bell Schedules','fa-bell')")
cursor.execute("INSERT INTO enabledmodules VALUES (4,'modules/wakeup',1,'/wakeup.php','user,admin,tempadmin',1,'Wake Up Calls','fa-bed')")

cursor.execute("INSERT INTO `endpoints-input-sip` VALUES ('Paging','#10104','Test','livepaging')")

cursor.execute("INSERT INTO systemsettings VALUES ('enable_insecure_sip','1','Enable SIP over UDP and TCP (0/1)')")
cursor.execute("INSERT INTO systemsettings VALUES ('enable_login_logo','1','Enable the logo on login page')")
cursor.execute("INSERT INTO systemsettings VALUES ('enable_secure_sip','0','Enable SIP over TLS (0 = NO, 1 = Yes with same cert as web server, 2 = Yes with independent cert)')")
cursor.execute("INSERT INTO systemsettings VALUES ('favicon','/assets/favicon.svg','Browser Favicon. Path to file within web server.')")
cursor.execute("INSERT INTO systemsettings VALUES ('insecure_sip_port','5060','Port for UDP/TCP SIP')")
cursor.execute("INSERT INTO systemsettings VALUES ('login_banner_enabled','1','Enable or disable the login page banner (0/1)')")
cursor.execute("INSERT INTO systemsettings VALUES ('login_banner_message','','Message text for the login page banner')")
cursor.execute("INSERT INTO systemsettings VALUES ('login_banner_title','Thank you for installing the Open Paging Server beta!','Optional title for the login page banner')")
cursor.execute("INSERT INTO systemsettings VALUES ('login_logo_dark','/assets/OPENPAGINGSERVER-768x576-DARKMODE.png','Dark mode logo. Path to file within web server.')")
cursor.execute("INSERT INTO systemsettings VALUES ('login_logo_light','/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png','Light mode logo. Path to file within web server.')")
cursor.execute("INSERT INTO systemsettings VALUES ('product_name','Open Paging Server','Name of this server.')")
cursor.execute("INSERT INTO systemsettings VALUES ('secure_sip_cert','','If enable_secure_sip is 2, this cert will be used. Path to file')")
cursor.execute("INSERT INTO systemsettings VALUES ('secure_sip_port','5061','Port for TLS SIP')")
cursor.execute("INSERT INTO systemsettings VALUES ('secure_sip_privkey','','If enable_secure_sip is 2, this private key will be used. Path to file')")
cursor.execute("INSERT INTO systemsettings VALUES ('separate_dark_logo','1','Use a separate logo for dark mode. When disabled, uses only logo_light. (0/1)')")
cursor.execute("INSERT INTO systemsettings VALUES ('show_online_docs','1','Show GUI links to docs.openpagingserver.org (0/1)')")
cursor.execute("INSERT INTO systemsettings VALUES ('webserver_https_enable','0','HTTPs Enable (0/1)')")
cursor.execute("INSERT INTO systemsettings VALUES ('webserver_https_port','443','HTTPs Server Port (Default: 443)')")
cursor.execute("INSERT INTO systemsettings VALUES ('webserver_http_port','80','HTTP Server Port (Default: 80)')")

conn.commit()
conn.close()

config_php = """<?php
$host = 'localhost';
$db   = 'openpagingserver';
$user = 'openpagingserver';
$pass = '""" + db_password + """';
$charset = 'utf8mb4';

$dsn = "mysql:host=$host;dbname=$db;charset=$charset";
$options = [
    PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES   => false,
];

try {
    $pdo = new PDO($dsn, $user, $pass, $options);
} catch (\\PDOException $e) {
    throw new \\PDOException($e->getMessage(), (int)$e->getCode());
}
"""

os.makedirs("/var/www/html", exist_ok=True)
with open("/var/www/html/config.php", "w") as f:
    f.write(config_php)

print("Done.")

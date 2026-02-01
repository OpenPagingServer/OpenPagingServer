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
    email VARCHAR(255) UNIQUE NOT NULL,
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

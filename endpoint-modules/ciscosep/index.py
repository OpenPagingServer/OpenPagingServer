#!/usr/bin/env python3

import os
import sys
import time
import threading
import subprocess
import requests
import pymysql
import re
import html
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

import message_send

ENV_PATH = BASE_DIR.parent.parent / ".env"
load_dotenv(ENV_PATH)

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

core = None
running = False
thread = None
INTERVAL = 60

imggen_proc = None
authserver_proc = None

def init(core_obj):
    global core, running, thread, imggen_proc, authserver_proc
    core = core_obj
    running = True

    imggen_path = BASE_DIR / "imggen.py"
    authserver_path = BASE_DIR / "authserver.py"

    if imggen_path.exists():
        imggen_proc = subprocess.Popen([sys.executable, str(imggen_path)], cwd=BASE_DIR)
    
    if authserver_path.exists():
        authserver_proc = subprocess.Popen([sys.executable, str(authserver_path)], cwd=BASE_DIR)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

def log(msg):
    if core and hasattr(core, "log"):
        core.log(msg)
    else:
        print(msg)

def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )

def fetch_endpoints():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ipv4, status FROM `endpoints-output-ciscosep`")
            return cur.fetchall()
    finally:
        conn.close()

def update_status(ip, status):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE `endpoints-output-ciscosep` SET status=%s WHERE ipv4=%s",
                (status, ip),
            )
        conn.commit()
    finally:
        conn.close()

def check_phone(ip):
    url = f"http://{ip}/CGI/Java/Serviceability?adapter=device.statistics.configuration"
    try:
        r = requests.get(url, timeout=3)
        if r.status_code != 200:
            return "Offline"

        text = r.text
        match = re.search(
            r"Authentication URL</B></TD><td[^>]*></TD><TD><B>(.*?)</B>",
            text,
            re.IGNORECASE | re.DOTALL
        )

        if not match:
            return "NoAuthURL"

        value = html.unescape(match.group(1)).strip()

        if value == "":
            return "NoAuthURL"

        return "Online"
    except:
        return "Offline"

def loop():
    while running:
        try:
            endpoints = fetch_endpoints()
            for ip, status in endpoints:
                if status == "Unchecked":
                    continue

                result = check_phone(ip)
                if result != status:
                    update_status(ip, result)
                    log(f"{ip} -> {result}")
        except Exception as e:
            log(f"ciscosep error: {e}")

        time.sleep(INTERVAL)

def shutdown():
    global running, imggen_proc, authserver_proc
    running = False
    if imggen_proc:
        imggen_proc.terminate()
    if authserver_proc:
        authserver_proc.terminate()

def api_endpoint(command_string):
    message_send.handle_api(command_string)

def receive_audio(chunk, stream_id):
    message_send.receive_audio(chunk, stream_id)
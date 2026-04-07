#!/usr/bin/env python3

import json
import os
import time
import signal
import sys
import pymysql
import importlib.util
from pathlib import Path
from dotenv import load_dotenv

import sip.index as sip_server

load_dotenv("/opt/openpagingserver/.env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

MODULES_DIR = Path("/opt/openpagingserver/endpoint-modules")
loaded_modules = {}

class Core:
    def log(self, msg):
        print(msg)

core = Core()

def db_enabled_modules():
    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME)
    with conn.cursor() as cur:
        cur.execute("SELECT plugin_id FROM enabledmodules WHERE enabled = 1")
        rows = cur.fetchall()
    conn.close()
    return {r[0] for r in rows}

def discover_modules():
    found = {}
    for d in MODULES_DIR.iterdir():
        manifest = d / "manifest.json"
        if manifest.exists():
            data = json.loads(manifest.read_text())
            found[data["id"]] = d / data["entry"]
    return found

def load_module(mid, entry):
    spec = importlib.util.spec_from_file_location(mid, entry)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.init(core)
    loaded_modules[mid] = mod
    core.log(f"loaded module {mid}")

def unload_module(mid):
    if mid in loaded_modules:
        if hasattr(loaded_modules[mid], "shutdown"):
            loaded_modules[mid].shutdown()
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

def shutdown(sig, frame):
    for mid in list(loaded_modules.keys()):
        unload_module(mid)
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

while True:
    sync_modules()
    time.sleep(5)

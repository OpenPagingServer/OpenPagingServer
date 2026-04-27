#!/usr/bin/env python3

import json
import os
import signal
import sys
import time
import importlib.util
from pathlib import Path

import pymysql
from dotenv import load_dotenv

import sip.index as sip_server

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

MODULE_LOADER_PATH = BASE_DIR / "endpoint-modules" / "index.py"
MODULES_DIR = BASE_DIR / "endpoint-modules"

loaded_modules = {}


class Core:
    def log(self, msg):
        print(msg)


core = Core()


def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )


def db_enabled_modules():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM enabledmodules WHERE status = 1")
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


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

        data = json.loads(manifest.read_text())
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


endpoint_manager = load_endpoint_manager()

if endpoint_manager and hasattr(endpoint_manager, "init"):
    endpoint_manager.init(core)


def shutdown(sig, frame):
    if endpoint_manager and hasattr(endpoint_manager, "shutdown_all"):
        endpoint_manager.shutdown_all()

    for mid in list(loaded_modules.keys()):
        unload_module(mid)

    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    sip_server.start()
    core.log("SIP server started")

    while True:
        try:
            sync_modules()
            if endpoint_manager and hasattr(endpoint_manager, "sync_modules"):
                endpoint_manager.sync_modules()
        except Exception as exc:
            core.log(f"module sync error: {exc}")
        time.sleep(5)


if __name__ == "__main__":
    main()

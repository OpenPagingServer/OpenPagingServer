#!/usr/bin/env python3

import json
import os
import signal
import subprocess
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
messaged_proc = None
livepaged_proc = None
belld_proc = None


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
    global messaged_proc, livepaged_proc, belld_proc
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

    sys.exit(0)


def main():
    global messaged_proc, livepaged_proc, belld_proc
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

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

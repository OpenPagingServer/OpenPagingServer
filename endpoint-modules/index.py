#!/usr/bin/env python3

import importlib.util
import os
import socket
import threading
import time
from pathlib import Path
import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

loaded_modules = {}
core = None
IPC_PORT = 50000

audio_ready_flags = {}

def init(core_obj):
    global core
    core = core_obj
    threading.Thread(target=start_ipc_server, daemon=True).start()

def log(msg):
    if core is not None and hasattr(core, "log"):
        core.log(msg)
    else:
        print(msg)

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )

def enabled_module_dirs():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT `dir` FROM endpointmodulesloaded WHERE enabled = 'true'")
            return {row[0] for row in cur.fetchall() if row and row[0]}
    finally:
        conn.close()

def discover_modules():
    discovered = {}
    if not BASE_DIR.exists():
        return discovered

    for module_dir in BASE_DIR.iterdir():
        if not module_dir.is_dir():
            continue

        entry = module_dir / "index.py"
        if entry.exists():
            discovered[module_dir.name] = entry

    return discovered

def load_module(module_dir, entry):
    spec_name = f"endpoint_module_{module_dir.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(spec_name, entry)
    if spec is None or spec.loader is None:
        return

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if hasattr(mod, "init"):
        mod.init(core)

    loaded_modules[module_dir] = mod

def unload_module(module_dir):
    mod = loaded_modules.get(module_dir)
    if mod is None:
        return

    if hasattr(mod, "shutdown"):
        mod.shutdown()

    del loaded_modules[module_dir]

def sync_modules():
    enabled = enabled_module_dirs()
    discovered = discover_modules()

    for module_dir in enabled:
        if module_dir not in loaded_modules and module_dir in discovered:
            load_module(module_dir, discovered[module_dir])

    for module_dir in list(loaded_modules.keys()):
        if module_dir not in enabled:
            unload_module(module_dir)

def shutdown_all():
    for module_dir in list(loaded_modules.keys()):
        unload_module(module_dir)

def api_receive(action, stream_id, msg_id, targets):
    for target in targets:
        if "/" in target:
            module_name, sub_command = target.split("/", 1)
            mod = loaded_modules.get(module_name)
            if mod and hasattr(mod, "api_endpoint"):
                mod.api_endpoint(f"{action} {sub_command} {stream_id} {msg_id}")
        else:
            for mod_name, mod in loaded_modules.items():
                if hasattr(mod, "api_endpoint"):
                    mod.api_endpoint(f"{action} {target} {stream_id} {msg_id}")

def report_ready(module_name, stream_id):
    if stream_id in audio_ready_flags:
        if module_name in audio_ready_flags[stream_id]:
            audio_ready_flags[stream_id][module_name] = True

def start_ipc_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('127.0.0.1', IPC_PORT))
    server.listen(5)
    
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_ipc_client, args=(conn,), daemon=True).start()

def handle_ipc_client(conn):
    try:
        data = conn.recv(4096).decode('utf-8')
        if not data:
            return
        
        parts = data.strip().split()
        command = parts[0]
        
        if command == "PREPARE":
            stream_id = parts[1]
            msg_id = parts[2]
            targets = parts[3:]
            local_targets = []
            
            if stream_id not in audio_ready_flags:
                audio_ready_flags[stream_id] = {}
            
            for target in targets:
                if "/" in target:
                    module_name = target.split("/", 1)[0]
                    if module_name not in local_targets:
                        local_targets.append(module_name)
                        audio_ready_flags[stream_id][module_name] = False
                else:
                    for mod_name in loaded_modules:
                        if mod_name not in local_targets:
                            local_targets.append(mod_name)
                            audio_ready_flags[stream_id][mod_name] = False
            
            api_receive("prepare_audio", stream_id, msg_id, targets)
            
            start_time = time.time()
            while time.time() - start_time < 5:
                flags = audio_ready_flags.get(stream_id, {})
                if flags and all(flags.values()):
                    break
                time.sleep(0.1)
            
            conn.sendall(b"OK")
            
            while True:
                try:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    for mod_name in local_targets:
                        mod = loaded_modules.get(mod_name)
                        if mod and hasattr(mod, "receive_audio"):
                            try:
                                mod.receive_audio(chunk, stream_id)
                            except Exception as e:
                                log(f"Error in {mod_name}.receive_audio: {e}")
                except Exception as e:
                    log(f"Error reading chunk from connection: {e}")
                    break
            
            if stream_id in audio_ready_flags:
                del audio_ready_flags[stream_id]
                        
        elif command == "SENDMSG":
            stream_id = parts[1]
            msg_id = parts[2]
            targets = parts[3:]
            api_receive("sendmsg", stream_id, msg_id, targets)
            conn.sendall(b"DONE")
            
        elif command == "READY":
            if len(parts) > 2:
                report_ready(parts[1], parts[2])
            conn.sendall(b"ACK")
            
    except Exception as e:
        log(f"IPC connection handler error: {e}")
    finally:
        conn.close()

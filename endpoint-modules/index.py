#!/usr/bin/env python3

import importlib.util
import os
import socket
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
LOG_FILE = BASE_DIR / "endpoint_dispatch_debug.log"

IPC_HOST = "127.0.0.1"
IPC_PORT = 50000
READY_TIMEOUT = 10.0

loaded_modules = {}
loaded_modules_lock = threading.Lock()
stream_states = {}
stream_states_lock = threading.Lock()
core = None
server_socket = None


class StreamState:
    def __init__(self, stream_id, target_map):
        self.stream_id = stream_id
        self.target_map = target_map
        self.pending_modules = {name for name, targets in target_map.items() if targets}
        self.ready_modules = set()
        self.ready_event = threading.Event()

    def mark_ready(self, module_name):
        if module_name in self.pending_modules:
            self.ready_modules.add(module_name)
        if self.ready_modules >= self.pending_modules:
            self.ready_event.set()


def init(core_obj):
    global core
    core = core_obj
    threading.Thread(target=start_ipc_server, daemon=True).start()


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {msg}\n")
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
            discovered = discover_modules()
            return {resolve_module_name(row[0], discovered) for row in cur.fetchall() if row and row[0]}
    finally:
        conn.close()


def normalize_module_name(value):
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def resolve_module_name(module_name, discovered=None):
    if discovered is None:
        discovered = discover_modules()
    if module_name in discovered:
        return module_name
    wanted = normalize_module_name(module_name)
    for candidate in discovered:
        normalized = normalize_module_name(candidate)
        if normalized == wanted or wanted.startswith(normalized) or normalized.startswith(wanted):
            return candidate
    return module_name


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
    with loaded_modules_lock:
        loaded_modules[module_dir] = mod
    log(f"load_module {module_dir}")


def unload_module(module_dir):
    with loaded_modules_lock:
        mod = loaded_modules.get(module_dir)
    if mod is None:
        return
    if hasattr(mod, "shutdown"):
        mod.shutdown()
    with loaded_modules_lock:
        loaded_modules.pop(module_dir, None)
    log(f"unload_module {module_dir}")


def sync_modules():
    enabled = enabled_module_dirs()
    discovered = discover_modules()
    log(f"sync_modules enabled={sorted(enabled)} discovered={sorted(discovered)}")
    for module_dir in enabled:
        with loaded_modules_lock:
            already_loaded = module_dir in loaded_modules
        if not already_loaded and module_dir in discovered:
            load_module(module_dir, discovered[module_dir])
    with loaded_modules_lock:
        loaded_names = list(loaded_modules.keys())
    for module_dir in loaded_names:
        if module_dir not in enabled:
            unload_module(module_dir)


def shutdown_all():
    global server_socket
    for module_dir in list(loaded_modules.keys()):
        unload_module(module_dir)
    if server_socket is not None:
        try:
            server_socket.close()
        except OSError:
            pass
        server_socket = None


def normalize_targets(targets):
    target_map = {}
    with loaded_modules_lock:
        module_names = list(loaded_modules.keys())
    discovered = discover_modules()
    for target in targets:
        target = target.strip()
        if not target:
            continue
        if "/" in target:
            module_name, sub_target = target.split("/", 1)
            module_name = resolve_module_name(module_name, discovered)
            if module_name in loaded_modules:
                target_map.setdefault(module_name, [])
                if sub_target not in target_map[module_name]:
                    target_map[module_name].append(sub_target)
            continue
        for module_name in module_names:
            target_map.setdefault(module_name, [])
            if target not in target_map[module_name]:
                target_map[module_name].append(target)
    log(f"normalize_targets raw={targets} mapped={target_map}")
    return target_map


def dispatch_to_module(module_name, action, stream_id, msg_id, sub_targets, metadata=None):
    with loaded_modules_lock:
        mod = loaded_modules.get(module_name)
    if mod is None:
        log(f"dispatch_to_module missing module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        return
    try:
        log(f"dispatch_to_module start module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        if hasattr(mod, "handle_dispatch"):
            mod.handle_dispatch(action, stream_id, msg_id, list(sub_targets), metadata)
        elif hasattr(mod, "api_endpoint"):
            for sub_target in sub_targets:
                mod.api_endpoint(f"{action} {sub_target} {stream_id} {msg_id}")
        log(f"dispatch_to_module done module={module_name} action={action} stream={stream_id}")
    except Exception as exc:
        log(f"dispatch error in {module_name}: {exc}")
        mark_ready(module_name, stream_id)


def dispatch(action, stream_id, msg_id, targets, metadata=None):
    target_map = normalize_targets(targets)
    if not target_map:
        log(f"dispatch no_targets action={action} stream={stream_id} msg={msg_id}")
        return {}
    log(f"dispatch action={action} stream={stream_id} msg={msg_id} target_map={target_map}")
    for module_name, sub_targets in target_map.items():
        threading.Thread(
            target=dispatch_to_module,
            args=(module_name, action, stream_id, msg_id, tuple(sub_targets), metadata),
            daemon=True,
        ).start()
    return target_map


def create_stream_state(stream_id, target_map):
    state = StreamState(stream_id, target_map)
    if not state.pending_modules:
        state.ready_event.set()
    with stream_states_lock:
        stream_states[stream_id] = state
    log(f"create_stream_state stream={stream_id} pending={sorted(state.pending_modules)}")
    return state


def pop_stream_state(stream_id):
    with stream_states_lock:
        state = stream_states.pop(stream_id, None)
    log(f"pop_stream_state stream={stream_id} found={state is not None}")
    return state


def mark_ready(module_name, stream_id):
    with stream_states_lock:
        state = stream_states.get(stream_id)
    if state is None:
        log(f"mark_ready missing_state module={module_name} stream={stream_id}")
        return
    state.mark_ready(module_name)
    log(f"mark_ready module={module_name} stream={stream_id} ready={sorted(state.ready_modules)} pending={sorted(state.pending_modules)}")


def finish_stream(stream_id):
    with loaded_modules_lock:
        modules_snapshot = list(loaded_modules.items())
    log(f"finish_stream stream={stream_id} modules={[name for name, _ in modules_snapshot]}")
    for module_name, mod in modules_snapshot:
        if hasattr(mod, "end_stream"):
            try:
                mod.end_stream(stream_id)
            except Exception as exc:
                log(f"end_stream error in {module_name}: {exc}")
    pop_stream_state(stream_id)


def recv_line(conn):
    data = bytearray()
    while True:
        chunk = conn.recv(1)
        if not chunk:
            break
        if chunk == b"\n":
            break
        data.extend(chunk)
    return bytes(data)


def start_ipc_server():
    global server_socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((IPC_HOST, IPC_PORT))
    server_socket.listen()
    log(f"ipc_server listening host={IPC_HOST} port={IPC_PORT}")
    while True:
        try:
            conn, _ = server_socket.accept()
        except OSError:
            break
        threading.Thread(target=handle_ipc_client, args=(conn,), daemon=True).start()


def handle_prepare(conn, parts):
    if len(parts) < 4:
        conn.sendall(b"ERROR\n")
        return
    stream_id = parts[1]
    msg_id = parts[2]
    targets = parts[3:]
    target_map = normalize_targets(targets)
    state = create_stream_state(stream_id, target_map)
    dispatch("prepare_audio", stream_id, msg_id, targets)
    ready = state.ready_event.wait(READY_TIMEOUT)
    log(f"handle_prepare waited stream={stream_id} ready={ready}")
    conn.sendall(b"OK\n")
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        log(f"handle_prepare audio_chunk stream={stream_id} bytes={len(chunk)} modules={list(target_map.keys())}")
        for module_name in target_map:
            with loaded_modules_lock:
                mod = loaded_modules.get(module_name)
            if mod and hasattr(mod, "receive_audio"):
                try:
                    mod.receive_audio(chunk, stream_id)
                except Exception as exc:
                    log(f"receive_audio error in {module_name}: {exc}")
    finish_stream(stream_id)


def handle_stream_prepare(conn, parts, action_name):
    if len(parts) < 4:
        conn.sendall(b"ERROR\n")
        return
    stream_id = parts[1]
    msg_id = parts[2]
    sender = ""
    if len(parts) >= 5 and "/" not in parts[3]:
        sender = urllib.parse.unquote(parts[3])
        targets = parts[4:]
    else:
        targets = parts[3:]
    target_map = normalize_targets(targets)
    state = create_stream_state(stream_id, target_map)
    dispatch(action_name, stream_id, msg_id, targets, {"sender": sender})
    ready = state.ready_event.wait(READY_TIMEOUT)
    log(f"handle_stream_prepare action={action_name} waited stream={stream_id} ready={ready}")
    conn.sendall(b"OK\n")
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        log(f"handle_stream_prepare action={action_name} audio_chunk stream={stream_id} bytes={len(chunk)} modules={list(target_map.keys())}")
        for module_name in target_map:
            with loaded_modules_lock:
                mod = loaded_modules.get(module_name)
            if mod and hasattr(mod, "receive_audio"):
                try:
                    mod.receive_audio(chunk, stream_id)
                except Exception as exc:
                    log(f"receive_audio error in {module_name}: {exc}")
    finish_stream(stream_id)


def handle_sendmsg(conn, parts):
    if len(parts) < 4:
        conn.sendall(b"ERROR\n")
        return
    stream_id = parts[1]
    msg_id = parts[2]
    targets = parts[3:]
    log(f"handle_sendmsg stream={stream_id} msg={msg_id} targets={targets}")
    dispatch("sendmsg", stream_id, msg_id, targets)
    conn.sendall(b"DONE\n")


def handle_ready(conn, parts):
    if len(parts) >= 3:
        log(f"handle_ready module={parts[1]} stream={parts[2]}")
        mark_ready(parts[1], parts[2])
    conn.sendall(b"ACK\n")


def handle_ipc_client(conn):
    try:
        line = recv_line(conn)
        if not line:
            return
        parts = line.decode("utf-8", errors="ignore").strip().split()
        if not parts:
            return
        command = parts[0]
        log(f"handle_ipc_client command={command} parts={parts}")
        if command == "PREPARE":
            handle_prepare(conn, parts)
        elif command == "PREPARELIVE":
            handle_stream_prepare(conn, parts, "prepare_livepage")
        elif command == "SENDMSG":
            handle_sendmsg(conn, parts)
        elif command == "READY":
            handle_ready(conn, parts)
        else:
            conn.sendall(b"ERROR\n")
    except Exception as exc:
        log(f"IPC connection handler error: {exc}")
    finally:
        conn.close()

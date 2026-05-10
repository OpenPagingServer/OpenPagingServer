#!/usr/bin/env python3

import base64
import importlib.util
import json
import os
import socket
import subprocess
import threading
import time
import uuid
import wave
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from active_broadcast_store import (
    claim_active_broadcast_delivery,
    expire_active_broadcasts_by_template_ids,
    expire_active_broadcasts_triggered_by_template,
    fetch_active_broadcast,
    list_pending_active_broadcast_ids,
    mark_active_broadcast_delivery,
    put_active_broadcast,
)

try:
    from broadcasts import is_audio_type
except Exception:
    def is_audio_type(value):
        return str(value or "").strip() in ("audio", "text+audio", "liveaudio", "liveaudio+text", "AudioMessage", "Text+AudioMessage", "Page")

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
LOG_FILE = BASE_DIR / "endpoint_dispatch_debug.log"
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"

IPC_HOST = "127.0.0.1"
IPC_PORT = 50000
READY_TIMEOUT = 10.0
BROADCAST_POLL_INTERVAL = max(0.01, float(os.getenv("BROADCAST_POLL_INTERVAL", "0.05")))
ASSET_PATH = os.getenv("ASSET_PATH", "/var/lib/openpagingserver/assets/")
SAMPLE_RATE = 8000
FRAME_SIZE = 160
FALLBACK_ASSET_DIRS = [
    BASE_DIR.parent / "assets",
    BASE_DIR.parent / "sip" / "audio",
]

loaded_modules = {}
module_load_errors = {}
loaded_modules_lock = threading.Lock()
stream_states = {}
stream_states_lock = threading.Lock()
broadcast_watcher_stop = threading.Event()
broadcast_delivery_ids = set()
broadcast_delivery_lock = threading.Lock()
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
    threading.Thread(target=broadcast_watcher_loop, daemon=True).start()


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if DEBUG:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {msg}\n")
    if core is not None and hasattr(core, "log"):
        core.log(msg)
    elif DEBUG:
        print(msg)


def page_debug(msg):
    if DEBUG:
        log(f"DEBUG {msg}")


def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )


def is_8k_ulaw(file_path):
    try:
        with wave.open(file_path, "rb") as wav_file:
            n_channels, _sample_width, framerate, _n_frames, compression, _ = wav_file.getparams()
            return framerate == SAMPLE_RATE and compression == "ULAW" and n_channels == 1
    except Exception:
        return False


def resolve_audio_file(audio_file):
    candidate = Path(audio_file)
    if candidate.is_file():
        return str(candidate)
    search_roots = [Path(ASSET_PATH), *FALLBACK_ASSET_DIRS]
    for root in search_roots:
        path = root / audio_file
        if path.exists():
            return str(path)
    return None


def audio_frames(audio_files_str):
    for audio_file in str(audio_files_str or "").split(":"):
        audio_file = audio_file.strip()
        if not audio_file:
            continue
        if audio_file.startswith("%silence(") and audio_file.endswith(")"):
            try:
                duration = float(audio_file[9:-1])
            except ValueError:
                continue
            for _ in range(int(duration * SAMPLE_RATE / FRAME_SIZE)):
                yield b"\xff" * FRAME_SIZE
            continue
        file_path = resolve_audio_file(audio_file)
        if not file_path:
            continue
        if is_8k_ulaw(file_path):
            with open(file_path, "rb") as handle:
                while True:
                    chunk = handle.read(FRAME_SIZE)
                    if not chunk:
                        break
                    yield chunk.ljust(FRAME_SIZE, b"\xff")
            continue
        ffmpeg = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "quiet",
                "-i",
                file_path,
                "-ar",
                str(SAMPLE_RATE),
                "-ac",
                "1",
                "-f",
                "mulaw",
                "-flush_packets",
                "1",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        while True:
            chunk = ffmpeg.stdout.read(FRAME_SIZE)
            if not chunk:
                break
            yield chunk.ljust(FRAME_SIZE, b"\xff")
        ffmpeg.stdout.close()
        ffmpeg.wait()


def fetch_broadcast(broadcast_id):
    return fetch_active_broadcast(broadcast_id)


def hydrate_active_record_from_history(record):
    hydrated = dict(record or {})
    broadcast_id = str(hydrated.get("id") or "").strip()
    if not broadcast_id:
        return hydrated
    conn = pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM broadcasts")
            columns = {row["Field"] for row in cur.fetchall() if row.get("Field")}
            wanted = [
                "id",
                "name",
                "shortmessage",
                "longmessage",
                "icon",
                "color",
                "vendor_specific",
                "type",
                "expires",
                "issued",
                "groups",
                "image",
                "audio",
                "sender",
                "priority",
                "delivery",
                "template_id",
                "expires_rule",
            ]
            selected = [column for column in wanted if column in columns]
            if not selected:
                return hydrated
            select_sql = ", ".join(f"`{column}`" for column in selected)
            cur.execute(f"SELECT {select_sql} FROM broadcasts WHERE id=%s LIMIT 1", (broadcast_id,))
            history_row = cur.fetchone()
            if not history_row:
                return hydrated
            for key, value in history_row.items():
                if value is not None:
                    hydrated[key] = value
            return hydrated
    finally:
        conn.close()


def fetch_pending_broadcast_ids(limit=20):
    return list_pending_active_broadcast_ids(limit=limit, exclude_sender="sendmsgd")


def claim_broadcast_delivery(broadcast_id, stream_id):
    return claim_active_broadcast_delivery(broadcast_id, stream_id)


def mark_broadcast_history_delivery(broadcast_id, status):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE broadcasts SET delivery=%s WHERE id=%s", (status, broadcast_id))
        conn.commit()
    finally:
        conn.close()


def resolve_group_targets(group_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            target_list = set()
            if str(group_id) == "0":
                for module_name in output_module_names():
                    target_list.add(f"{module_name}/all")
            else:
                for gid in str(group_id or "").split("."):
                    gid = gid.strip()
                    if not gid:
                        continue
                    cur.execute("SELECT members FROM groups WHERE id = %s", (gid,))
                    group_row = cur.fetchone()
                    if group_row and group_row[0]:
                        for member in group_row[0].replace(",", " ").split():
                            target_list.add(member)
            return sorted(target_list)
    finally:
        conn.close()


def enabled_module_dirs():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            discovered = discover_modules()
            try:
                cur.execute("SELECT `dir` FROM endpointmodulesloaded WHERE enabled = 'true'")
            except Exception as exc:
                log(f"enabled_module_dirs falling back to discovered modules: {exc}")
                return set(discovered.keys())
            enabled = {resolve_module_name(row[0], discovered) for row in cur.fetchall() if row and row[0]}
            try:
                cur.execute("SELECT `dir` FROM endpointmodulesloaded")
                configured = {resolve_module_name(row[0], discovered) for row in cur.fetchall() if row and row[0]}
            except Exception:
                configured = set(enabled)
            for builtin in ("siptrunks",):
                if builtin in discovered and builtin not in configured:
                    enabled.add(builtin)
            return enabled
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


def module_info_type(module_name):
    discovered = discover_modules()
    entry = discovered.get(module_name)
    if entry is None:
        return ""
    info = module_info_from_entry(module_name, entry)
    return str(info.get("input_type") or "")


def endpoint_is_output_capable(endpoint):
    if not isinstance(endpoint, dict):
        return False
    if endpoint.get("output_capable") is False:
        return False
    direction = str(endpoint.get("direction") or endpoint.get("input_type") or "").lower()
    if "output" in direction:
        return True
    capabilities = endpoint.get("capabilities")
    if isinstance(capabilities, list):
        lowered = {str(item).strip().lower() for item in capabilities}
        if "output" in lowered or "bells" in lowered:
            return True
    return bool(endpoint.get("bell_capable"))


def module_is_output_capable(module_name, mod=None):
    module_type = module_info_type(module_name).lower()
    if "output" not in module_type and "management" in module_type:
        return False
    if mod is None:
        with loaded_modules_lock:
            mod = loaded_modules.get(module_name)
    if mod is not None and hasattr(mod, "get_endpoint_status"):
        try:
            status_info = mod.get_endpoint_status()
            if isinstance(status_info, dict):
                if status_info.get("output_capable") is False:
                    return False
                for endpoint in status_info.get("endpoints") or []:
                    if endpoint_is_output_capable(endpoint):
                        return True
        except Exception as exc:
            log(f"module_is_output_capable status error module={module_name}: {exc}")
    return "output" in module_type


def output_module_names():
    with loaded_modules_lock:
        modules_snapshot = list(loaded_modules.items())
    return [
        module_name
        for module_name, mod in modules_snapshot
        if module_is_output_capable(module_name, mod)
    ]


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
        module_load_errors.pop(module_dir, None)
    log(f"load_module {module_dir}")


def mark_module_load_error(module_dir, exc):
    with loaded_modules_lock:
        module_load_errors[module_dir] = str(exc)
    log(f"load_module error {module_dir}: {exc}")


def unload_module(module_dir):
    with loaded_modules_lock:
        mod = loaded_modules.get(module_dir)
    if mod is None:
        return
    if hasattr(mod, "shutdown"):
        mod.shutdown()
    with loaded_modules_lock:
        loaded_modules.pop(module_dir, None)
        module_load_errors.pop(module_dir, None)
    log(f"unload_module {module_dir}")


def sync_modules():
    enabled = enabled_module_dirs()
    discovered = discover_modules()
    log(f"sync_modules enabled={sorted(enabled)} discovered={sorted(discovered)}")
    for module_dir in enabled:
        with loaded_modules_lock:
            already_loaded = module_dir in loaded_modules
        if not already_loaded and module_dir in discovered:
            try:
                load_module(module_dir, discovered[module_dir])
            except Exception as exc:
                mark_module_load_error(module_dir, exc)
                continue
    with loaded_modules_lock:
        loaded_names = list(loaded_modules.keys())
    for module_dir in loaded_names:
        if module_dir not in enabled:
            try:
                unload_module(module_dir)
            except Exception as exc:
                log(f"unload_module error {module_dir}: {exc}")


def shutdown_all():
    global server_socket
    broadcast_watcher_stop.set()
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
    page_debug(
        f"normalize_targets_start raw={targets} loaded={module_names} discovered={sorted(discovered.keys())}"
    )
    for target in targets:
        target = target.strip()
        if not target:
            continue
        if "/" in target:
            module_name, sub_target = target.split("/", 1)
            module_name = resolve_module_name(module_name, discovered)
            if module_name in loaded_modules and module_is_output_capable(module_name):
                target_map.setdefault(module_name, [])
                if sub_target not in target_map[module_name]:
                    target_map[module_name].append(sub_target)
            continue
        for module_name in output_module_names():
            target_map.setdefault(module_name, [])
            if target not in target_map[module_name]:
                target_map[module_name].append(target)
    log(f"normalize_targets raw={targets} mapped={target_map}")
    page_debug(f"normalize_targets_done raw={targets} mapped={target_map}")
    return target_map


def dispatch_to_module(module_name, action, stream_id, msg_id, sub_targets, metadata=None):
    with loaded_modules_lock:
        mod = loaded_modules.get(module_name)
    if mod is None:
        log(f"dispatch_to_module missing module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        page_debug(f"dispatch_to_module_missing module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        return
    try:
        log(f"dispatch_to_module start module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        page_debug(f"dispatch_to_module_start module={module_name} action={action} stream={stream_id} msg={msg_id} targets={sub_targets}")
        if hasattr(mod, "handle_dispatch"):
            mod.handle_dispatch(action, stream_id, msg_id, list(sub_targets), metadata)
        elif hasattr(mod, "api_endpoint"):
            for sub_target in sub_targets:
                mod.api_endpoint(f"{action} {sub_target} {stream_id} {msg_id}")
        else:
            mark_ready(module_name, stream_id)
        log(f"dispatch_to_module done module={module_name} action={action} stream={stream_id}")
        page_debug(f"dispatch_to_module_done module={module_name} action={action} stream={stream_id}")
    except Exception as exc:
        log(f"dispatch error in {module_name}: {exc}")
        page_debug(f"dispatch_to_module_error module={module_name} action={action} stream={stream_id} error={exc.__class__.__name__}: {exc}")
        mark_ready(module_name, stream_id)


def dispatch(action, stream_id, msg_id, targets, metadata=None):
    target_map = normalize_targets(targets)
    if not target_map:
        log(f"dispatch no_targets action={action} stream={stream_id} msg={msg_id}")
        page_debug(f"dispatch_no_targets action={action} stream={stream_id} msg={msg_id} targets={targets}")
        return {}
    log(f"dispatch action={action} stream={stream_id} msg={msg_id} target_map={target_map}")
    page_debug(f"dispatch_start action={action} stream={stream_id} msg={msg_id} target_map={target_map}")
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
    page_debug(f"create_stream_state stream={stream_id} pending={sorted(state.pending_modules)} target_map={target_map}")
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
        page_debug(f"mark_ready_missing_state module={module_name} stream={stream_id}")
        return
    state.mark_ready(module_name)
    log(f"mark_ready module={module_name} stream={stream_id} ready={sorted(state.ready_modules)} pending={sorted(state.pending_modules)}")
    page_debug(f"mark_ready module={module_name} stream={stream_id} ready={sorted(state.ready_modules)} pending={sorted(state.pending_modules)}")


def finish_stream(stream_id):
    with loaded_modules_lock:
        modules_snapshot = list(loaded_modules.items())
    log(f"finish_stream stream={stream_id} modules={[name for name, _ in modules_snapshot]}")
    page_debug(f"finish_stream stream={stream_id} modules={[name for name, _ in modules_snapshot]}")
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


def send_ipc_json(conn, payload):
    conn.sendall(json.dumps(payload, default=str).encode("utf-8") + b"\n")


def decode_ipc_json_token(token):
    raw = base64.b64decode(str(token or "").encode("ascii"), validate=True)
    return json.loads(raw.decode("utf-8"))


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
    page_debug(f"handle_stream_prepare_start action={action_name} parts={parts}")
    if len(parts) < 4:
        page_debug(f"handle_stream_prepare_bad_parts action={action_name} parts={parts}")
        conn.sendall(b"ERROR\n")
        return
    stream_id = parts[1]
    msg_id = parts[2]
    targets = parts[3:]
    try:
        sync_modules()
    except Exception as exc:
        log(f"handle_stream_prepare sync_modules error stream={stream_id}: {exc}")
        page_debug(f"handle_stream_prepare_sync_error stream={stream_id} error={exc.__class__.__name__}: {exc}")
    target_map = normalize_targets(targets)
    if not target_map:
        log(f"handle_stream_prepare no_target_modules action={action_name} stream={stream_id} msg={msg_id} targets={targets}")
        page_debug(f"handle_stream_prepare_no_target_modules action={action_name} stream={stream_id} msg={msg_id} targets={targets}")
        conn.sendall(b"ERROR\n")
        return
    state = create_stream_state(stream_id, target_map)
    dispatch(action_name, stream_id, msg_id, targets)
    ready = state.ready_event.wait(READY_TIMEOUT)
    log(f"handle_stream_prepare action={action_name} waited stream={stream_id} ready={ready}")
    page_debug(
        f"handle_stream_prepare_ready action={action_name} stream={stream_id} ready={ready} "
        f"ready_modules={sorted(state.ready_modules)} pending={sorted(state.pending_modules)}"
    )
    if not ready:
        pop_stream_state(stream_id)
        page_debug(f"handle_stream_prepare_timeout action={action_name} stream={stream_id}")
        conn.sendall(b"ERROR\n")
        return
    conn.sendall(b"OK\n")
    page_debug(f"handle_stream_prepare_ok action={action_name} stream={stream_id}")
    chunk_count = 0
    byte_count = 0
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunk_count += 1
        byte_count += len(chunk)
        if chunk_count == 1 or chunk_count % 50 == 0:
            page_debug(
                f"handle_stream_prepare_audio action={action_name} stream={stream_id} "
                f"chunks={chunk_count} bytes={byte_count} last_chunk={len(chunk)} modules={list(target_map.keys())}"
            )
        log(f"handle_stream_prepare action={action_name} audio_chunk stream={stream_id} bytes={len(chunk)} modules={list(target_map.keys())}")
        for module_name in target_map:
            with loaded_modules_lock:
                mod = loaded_modules.get(module_name)
            if mod and hasattr(mod, "receive_audio"):
                try:
                    mod.receive_audio(chunk, stream_id)
                except Exception as exc:
                    log(f"receive_audio error in {module_name}: {exc}")
                    page_debug(f"receive_audio_error module={module_name} stream={stream_id} error={exc.__class__.__name__}: {exc}")
    page_debug(f"handle_stream_prepare_end action={action_name} stream={stream_id} chunks={chunk_count} bytes={byte_count}")
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


def deliver_broadcast(stream_id, broadcast_id):
    broadcast = fetch_broadcast(broadcast_id)
    if not broadcast:
        log(f"handle_broadcast missing broadcast={broadcast_id}")
        return False
    targets = resolve_group_targets(broadcast.get("groups"))
    if not targets:
        log(f"handle_broadcast no_targets stream={stream_id} broadcast={broadcast_id} groups={broadcast.get('groups')}")
        return False
    msg_type = broadcast.get("type")
    audio_files = broadcast.get("audio") or ""
    metadata = {
        "broadcast_id": broadcast_id,
        "groups": str(broadcast.get("groups") or ""),
        "type": msg_type,
        "sender": broadcast.get("sender") or "",
        "priority": broadcast.get("priority") or "",
        "template_id": broadcast.get("template_id") or "",
    }
    if is_audio_type(msg_type):
        gen = audio_frames(audio_files)
        try:
            first_frame = next(gen)
            has_audio = True
        except StopIteration:
            has_audio = False
        if has_audio:
            target_map = normalize_targets(targets)
            if not target_map:
                log(f"handle_broadcast no_target_modules stream={stream_id} broadcast={broadcast_id} targets={targets}")
                return False
            state = create_stream_state(stream_id, target_map)
            dispatch("prepare_audio", stream_id, broadcast_id, targets, metadata)
            ready = state.ready_event.wait(READY_TIMEOUT)
            log(f"handle_broadcast waited stream={stream_id} ready={ready}")
            frame_duration = FRAME_SIZE / SAMPLE_RATE
            next_send_time = time.perf_counter()
            for frame in [first_frame]:
                for module_name in target_map:
                    with loaded_modules_lock:
                        mod = loaded_modules.get(module_name)
                    if mod and hasattr(mod, "receive_audio"):
                        try:
                            mod.receive_audio(frame, stream_id)
                        except Exception as exc:
                            log(f"receive_audio error in {module_name}: {exc}")
            for frame in gen:
                next_send_time += frame_duration
                sleep_time = next_send_time - time.perf_counter()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    next_send_time = time.perf_counter()
                for module_name in target_map:
                    with loaded_modules_lock:
                        mod = loaded_modules.get(module_name)
                    if mod and hasattr(mod, "receive_audio"):
                        try:
                            mod.receive_audio(frame, stream_id)
                        except Exception as exc:
                            log(f"receive_audio error in {module_name}: {exc}")
            finish_stream(stream_id)
            return True
        log(f"handle_broadcast audio_type_no_audio broadcast={broadcast_id} audio={audio_files}")
    dispatch("sendmsg", stream_id, broadcast_id, targets, metadata)
    return True


def finish_claimed_broadcast_delivery(stream_id, broadcast_id, source):
    try:
        try:
            sync_modules()
        except Exception as exc:
            log(f"{source} sync_modules error broadcast={broadcast_id}: {exc}")
        if deliver_broadcast(stream_id, broadcast_id):
            mark_broadcast_history_delivery(broadcast_id, "sent")
            mark_active_broadcast_delivery(broadcast_id, "sent")
            log(f"{source} dispatched broadcast={broadcast_id} stream={stream_id}")
        else:
            mark_broadcast_history_delivery(broadcast_id, "failed")
            mark_active_broadcast_delivery(broadcast_id, "failed")
            log(f"{source} dispatch_failed broadcast={broadcast_id} stream={stream_id}")
    finally:
        with broadcast_delivery_lock:
            broadcast_delivery_ids.discard(broadcast_id)


def handle_broadcast(conn, parts):
    if len(parts) < 3:
        conn.sendall(b"ERROR\n")
        return
    stream_id = parts[1]
    broadcast_id = parts[2]
    with broadcast_delivery_lock:
        if broadcast_id in broadcast_delivery_ids:
            log(f"handle_broadcast already_in_progress broadcast={broadcast_id} stream={stream_id}")
            conn.sendall(b"DONE\n")
            return
        broadcast_delivery_ids.add(broadcast_id)
    if not claim_broadcast_delivery(broadcast_id, stream_id):
        with broadcast_delivery_lock:
            broadcast_delivery_ids.discard(broadcast_id)
        log(f"handle_broadcast claim_skipped broadcast={broadcast_id} stream={stream_id}")
        conn.sendall(b"DONE\n")
        return
    threading.Thread(
        target=finish_claimed_broadcast_delivery,
        args=(stream_id, broadcast_id, "handle_broadcast"),
        daemon=True,
    ).start()
    conn.sendall(b"DONE\n")


def handle_active_store(conn, parts):
    if len(parts) < 2:
        send_ipc_json(conn, {"ok": False, "error": "missing payload"})
        return
    try:
        record = decode_ipc_json_token(parts[1])
        if not isinstance(record, dict):
            raise ValueError("payload must be an object")
        record = hydrate_active_record_from_history(record)
        broadcast_id = put_active_broadcast(record)
        send_ipc_json(conn, {"ok": True, "id": broadcast_id})
    except Exception as exc:
        log(f"handle_active_store error: {exc}")
        send_ipc_json(conn, {"ok": False, "error": str(exc)})


def handle_active_expire_template_ids(conn, parts):
    if len(parts) < 2:
        send_ipc_json(conn, {"ok": False, "error": "missing payload"})
        return
    try:
        payload = decode_ipc_json_token(parts[1])
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        removed_ids = expire_active_broadcasts_by_template_ids(
            payload.get("template_ids") or [],
            exclude_broadcast_ids=payload.get("exclude_broadcast_ids") or [],
        )
        send_ipc_json(conn, {"ok": True, "removed_ids": removed_ids})
    except Exception as exc:
        log(f"handle_active_expire_template_ids error: {exc}")
        send_ipc_json(conn, {"ok": False, "error": str(exc)})


def handle_active_expire_triggered(conn, parts):
    if len(parts) < 2:
        send_ipc_json(conn, {"ok": False, "error": "missing payload"})
        return
    try:
        payload = decode_ipc_json_token(parts[1])
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        removed_ids = expire_active_broadcasts_triggered_by_template(payload.get("template_id"))
        send_ipc_json(conn, {"ok": True, "removed_ids": removed_ids})
    except Exception as exc:
        log(f"handle_active_expire_triggered error: {exc}")
        send_ipc_json(conn, {"ok": False, "error": str(exc)})


def deliver_pending_broadcast(broadcast_id):
    stream_id = uuid.uuid4().hex
    with broadcast_delivery_lock:
        if broadcast_id in broadcast_delivery_ids:
            log(f"broadcast_watcher already_in_progress broadcast={broadcast_id} stream={stream_id}")
            return
        broadcast_delivery_ids.add(broadcast_id)
    if not claim_broadcast_delivery(broadcast_id, stream_id):
        with broadcast_delivery_lock:
            broadcast_delivery_ids.discard(broadcast_id)
        log(f"broadcast_watcher claim_skipped broadcast={broadcast_id} stream={stream_id}")
        return
    finish_claimed_broadcast_delivery(stream_id, broadcast_id, "broadcast_watcher")


def broadcast_watcher_loop():
    log(f"broadcast_watcher polling interval={BROADCAST_POLL_INTERVAL}s")
    while not broadcast_watcher_stop.is_set():
        try:
            for broadcast_id in fetch_pending_broadcast_ids():
                threading.Thread(
                    target=deliver_pending_broadcast,
                    args=(broadcast_id,),
                    daemon=True,
                ).start()
        except Exception as exc:
            log(f"broadcast_watcher error: {exc}")
        broadcast_watcher_stop.wait(BROADCAST_POLL_INTERVAL)


def handle_ready(conn, parts):
    if len(parts) >= 3:
        log(f"handle_ready module={parts[1]} stream={parts[2]}")
        mark_ready(parts[1], parts[2])
    conn.sendall(b"ACK\n")


def handle_list_endpoints(conn):
    sync_error = None
    try:
        sync_modules()
    except Exception as exc:
        sync_error = str(exc)
        log(f"list_endpoints sync error: {exc}")
    with loaded_modules_lock:
        modules_snapshot = list(loaded_modules.items())
        load_errors_snapshot = dict(module_load_errors)
    modules = []
    for module_name, mod in modules_snapshot:
        module_info = {
            "module": module_name,
            "display_name": module_name,
            "count": 0,
            "endpoints": [],
        }
        try:
            if hasattr(mod, "get_endpoint_status"):
                status_info = mod.get_endpoint_status()
                if isinstance(status_info, dict):
                    module_info.update(status_info)
            else:
                module_info["error"] = "Module does not support endpoint status"
        except Exception as exc:
            module_info["error"] = str(exc)
            log(f"get_endpoint_status error in {module_name}: {exc}")
        endpoints = module_info.get("endpoints")
        if not isinstance(endpoints, list):
            endpoints = []
            module_info["endpoints"] = endpoints
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                continue
            direction = str(endpoint.get("direction") or endpoint.get("input_type") or "").lower()
            if "output" in direction:
                endpoint.setdefault("bell_capable", True)
                capabilities = endpoint.get("capabilities")
                if not isinstance(capabilities, list):
                    capabilities = []
                if "bells" not in capabilities:
                    capabilities.append("bells")
                endpoint["capabilities"] = capabilities
        module_info["module"] = module_info.get("module") or module_name
        module_info["display_name"] = module_info.get("display_name") or module_info["module"]
        module_info["count"] = len(endpoints)
        modules.append(module_info)
    for module_name, error in sorted(load_errors_snapshot.items()):
        modules.append(
            {
                "module": module_name,
                "display_name": module_name,
                "count": 0,
                "endpoints": [],
                "error": error,
            }
        )
    response = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "modules": modules,
    }
    if sync_error:
        response["warning"] = sync_error
    conn.sendall(json.dumps(response, default=str).encode("utf-8") + b"\n")


def default_module_info(module_name):
    return {
        "module": module_name,
        "name": module_name,
        "author": "",
        "description": "",
        "input_type": "Output",
    }


def module_info_from_xml(module_name, entry):
    info_path = entry.parent / "info.xml"
    if not info_path.exists():
        return None
    try:
        root = ET.parse(info_path).getroot()
    except Exception as exc:
        log(f"info.xml parse error in {module_name}: {exc}")
        return None

    def text_for(tag):
        node = root.find(tag)
        return node.text.strip() if node is not None and node.text else ""

    info = {
        "module": module_name,
        "name": text_for("name") or module_name,
        "author": text_for("author"),
        "description": text_for("desp") or text_for("description"),
        "input_type": text_for("type") or "Output",
    }
    version = text_for("version")
    updated = text_for("updated")
    if version:
        info["version"] = version
    if updated:
        info["updated"] = updated
    return info


def module_info_from_entry(module_name, entry):
    info = default_module_info(module_name)
    xml_info = module_info_from_xml(module_name, entry)
    if xml_info is not None:
        info.update(xml_info)
    info["module"] = module_name
    info["name"] = info.get("name") or module_name
    info["input_type"] = info.get("input_type") or "Output"
    return info


def handle_list_endpoint_modules(conn):
    sync_error = None
    try:
        sync_modules()
    except Exception as exc:
        sync_error = str(exc)
        log(f"list_endpoint_modules sync error: {exc}")
    discovered = discover_modules()
    with loaded_modules_lock:
        loaded_names = sorted(loaded_modules.keys())
    modules = [
        module_info_from_entry(module_name, discovered[module_name])
        for module_name in loaded_names
        if module_name in discovered
    ]
    response = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "modules": modules,
    }
    if sync_error:
        response["warning"] = sync_error
    conn.sendall(json.dumps(response, default=str).encode("utf-8") + b"\n")


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
        if command == "PREPARELIVE":
            page_debug(f"ipc_preparelive_received parts={parts}")
        if command == "PREPARE":
            handle_prepare(conn, parts)
        elif command == "PREPARELIVE":
            handle_stream_prepare(conn, parts, "prepare_livepage")
        elif command == "SENDMSG":
            handle_sendmsg(conn, parts)
        elif command == "BROADCAST":
            handle_broadcast(conn, parts)
        elif command == "ACTIVE_STORE":
            handle_active_store(conn, parts)
        elif command == "ACTIVE_EXPIRE_TEMPLATE_IDS":
            handle_active_expire_template_ids(conn, parts)
        elif command == "ACTIVE_EXPIRE_TRIGGERED":
            handle_active_expire_triggered(conn, parts)
        elif command == "READY":
            handle_ready(conn, parts)
        elif command == "LIST_ENDPOINTS":
            handle_list_endpoints(conn)
        elif command == "LIST_ENDPOINT_MODULES":
            handle_list_endpoint_modules(conn)
        else:
            conn.sendall(b"ERROR\n")
    except Exception as exc:
        log(f"IPC connection handler error: {exc}")
    finally:
        conn.close()

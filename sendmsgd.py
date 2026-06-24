#!/usr/bin/env python3

import os
import re
import socket
import sys
import time
import wave
import subprocess
import uuid
from pathlib import Path
import xml.etree.ElementTree as ET
import pymysql
from dotenv import load_dotenv
from active_broadcast_store import fetch_active_broadcast, mark_active_broadcast_delivery
from broadcasts import (
    create_broadcast_from_template,
    expire_message_rule_broadcasts,
    expire_broadcasts_triggered_by_template,
    fetch_template,
    is_audio_type,
)
from endpoints import (
    MULTICAST_RTP_MODULE,
    connect_endpoint_ipc,
    discover_endpoint_packages,
    module_type_has_output,
    multicast_rtp_endpoint_count,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"

LOG_FILE = BASE_DIR / "sendmsgd_debug.log"
ASSET_PATH = os.getenv("ASSET_PATH", "/var/lib/openpagingserver/assets/")
SAMPLE_RATE = 8000
FRAME_SIZE = 160
FALLBACK_ASSET_DIRS = [
    BASE_DIR / "assets",
    BASE_DIR / "sip" / "audio",
]

def module_is_output_capable(module_name):
    module_name = str(module_name or "").strip()
    if not module_name or module_name == "siptrunks":
        return False
    if module_name == MULTICAST_RTP_MODULE:
        return multicast_rtp_endpoint_count() > 0
    package = discover_endpoint_packages(extract_if_trusted=False).get(module_name)
    if not package or not package.get("trusted"):
        return False
    manifest = package.get("manifest") or {}
    return module_type_has_output(manifest.get("input_type") or manifest.get("type") or "Output")

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )

def debug_log(message):
    if not DEBUG:
        return
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass

def connect_ipc():
    return connect_endpoint_ipc(timeout=10)

def fetch_broadcast(broadcast_id):
    return fetch_active_broadcast(broadcast_id)


def mark_history_delivery(broadcast_id, status):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE broadcasts SET delivery = %s WHERE id = %s", (status, broadcast_id))
        conn.commit()
    finally:
        conn.close()

def resolve_group_targets(group_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            target_list = set()
            if str(group_id) == "0":
                try:
                    cur.execute("SELECT `dir` FROM endpointmodulesloaded WHERE enabled = 'true' AND trusted = 'true'")
                    rows = cur.fetchall()
                except Exception:
                    rows = []
                for row in rows:
                    if row and row[0] and module_is_output_capable(row[0]):
                        target_list.add(f"{row[0]}/all")
                if module_is_output_capable(MULTICAST_RTP_MODULE):
                    target_list.add(f"{MULTICAST_RTP_MODULE}/all")
            else:
                for gid in str(group_id or "").split("."):
                    gid = gid.strip()
                    if not gid:
                        continue
                    cur.execute("SELECT members FROM groups WHERE id = %s", (gid,))
                    group_row = cur.fetchone()
                    if group_row and group_row[0]:
                        for member in str(group_row[0]).replace(",", " ").split():
                            if member:
                                target_list.add(member)
            return sorted(target_list)
    finally:
        conn.close()

def is_8k_ulaw(file_path):
    try:
        with wave.open(file_path, 'rb') as wav_file:
            n_channels, sample_width, framerate, n_frames, compression, _ = wav_file.getparams()
            return framerate == 8000 and compression == 'ULAW' and n_channels == 1
    except:
        return False

def resolve_audio_file(audio_file):
    candidate = Path(audio_file)
    if candidate.is_file():
        return str(candidate)
    raw = str(audio_file or "").replace("\0", "").replace("\\", "/").split("/")[-1].strip()
    candidates = []
    if raw:
        candidates.append(raw)
        secure = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._")
        if secure and secure not in candidates:
            candidates.append(secure)
    search_roots = [Path(ASSET_PATH), *FALLBACK_ASSET_DIRS]
    for root in search_roots:
        for name in candidates:
            path = root / name
            if path.exists():
                return str(path)
        lowered = {name.lower() for name in candidates}
        try:
            for path in root.iterdir():
                if path.is_file() and path.name.lower() in lowered:
                    return str(path)
        except OSError:
            continue
    return None

def get_all_audio_frames(audio_files_str):
    yielded = False
    audio_files = audio_files_str.split(":")
    for audio_file in audio_files:
        audio_file = audio_file.strip()
        if not audio_file:
            continue

        if audio_file.startswith("%silence(") and audio_file.endswith(")"):
            duration_str = audio_file[9:-1]
            try:
                duration = float(duration_str)
                silence_frames = int(duration * SAMPLE_RATE / FRAME_SIZE)
                silence_payload = b'\xff' * FRAME_SIZE
                for _ in range(silence_frames):
                    yielded = True
                    yield silence_payload
            except ValueError:
                continue
        else:
            file_path = resolve_audio_file(audio_file)
            if not file_path:
                continue

            if is_8k_ulaw(file_path):
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(FRAME_SIZE)
                        if not chunk:
                            break
                        if len(chunk) < FRAME_SIZE:
                            chunk = chunk.ljust(FRAME_SIZE, b'\xff')
                        yielded = True
                        yield chunk
            else:
                ffmpeg = subprocess.Popen([
                    "ffmpeg", "-v", "quiet", "-i", file_path,
                    "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "mulaw", "-flush_packets", "1", "pipe:1"
                ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                while True:
                    chunk = ffmpeg.stdout.read(FRAME_SIZE)
                    if not chunk:
                        break
                    if len(chunk) < FRAME_SIZE:
                        chunk = chunk.ljust(FRAME_SIZE, b'\xff')
                    yielded = True
                    yield chunk
                ffmpeg.stdout.close()
                ffmpeg.wait()
    return yielded

def send_broadcast_ipc(stream_id, broadcast_id):
    sock = None
    try:
        sock = connect_ipc()
        command = f"BROADCAST {stream_id} {broadcast_id}\n"
        sock.sendall(command.encode("utf-8"))
        response = sock.recv(1024)
        debug_log(f"BROADCAST stream={stream_id} broadcast={broadcast_id} response={response!r}")
        return b"DONE" in response
    except Exception as exc:
        debug_log(f"BROADCAST stream={stream_id} broadcast={broadcast_id} error={exc}")
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

def send_legacy_ipc(stream_id, broadcast, dispatch_message_id=None):
    broadcast_id = broadcast.get("id")
    msg_id = dispatch_message_id or broadcast_id
    targets = resolve_group_targets(broadcast.get("groups"))
    if not targets:
        debug_log(f"fallback no_targets stream={stream_id} broadcast={broadcast_id} groups={broadcast.get('groups')}")
        return False

    msg_type = broadcast.get("type")
    audio_files = broadcast.get("audio") or ""
    if is_audio_type(msg_type):
        frames = get_all_audio_frames(audio_files)
        try:
            first_frame = next(frames)
        except StopIteration:
            first_frame = None
        if first_frame is not None:
            sock = None
            try:
                sock = connect_ipc()
                command = f"PREPARE {stream_id} {msg_id} {' '.join(targets)}\n"
                sock.sendall(command.encode("utf-8"))
                response = sock.recv(1024)
                debug_log(f"fallback PREPARE stream={stream_id} broadcast={broadcast_id} msg={msg_id} response={response!r} targets={targets}")
                if b"OK" not in response:
                    return False
                frame_duration = FRAME_SIZE / SAMPLE_RATE
                next_send_time = time.perf_counter()
                sock.sendall(first_frame)
                for frame in frames:
                    next_send_time += frame_duration
                    sleep_time = next_send_time - time.perf_counter()
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    else:
                        next_send_time = time.perf_counter()
                    sock.sendall(frame)
                return True
            except Exception as exc:
                debug_log(f"fallback PREPARE error stream={stream_id} broadcast={broadcast_id} error={exc}")
                return False
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        debug_log(f"fallback audio_type_no_audio stream={stream_id} broadcast={broadcast_id} audio={audio_files!r}")

    sock = None
    try:
        sock = connect_ipc()
        command = f"SENDMSG {stream_id} {msg_id} {' '.join(targets)}\n"
        sock.sendall(command.encode("utf-8"))
        response = sock.recv(1024)
        debug_log(f"fallback SENDMSG stream={stream_id} broadcast={broadcast_id} msg={msg_id} response={response!r} targets={targets}")
        return b"DONE" in response
    except Exception as exc:
        debug_log(f"fallback SENDMSG error stream={stream_id} broadcast={broadcast_id} error={exc}")
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

def main():
    if len(sys.argv) < 3:
        print("sendmsgd.py requires group_id and message_id", file=sys.stderr)
        sys.exit(1)

    group_id = sys.argv[1]
    message_id = sys.argv[2]
    stream_id = uuid.uuid4().hex

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            template = fetch_template(cur, message_id)
            if not template:
                print(f"Message '{message_id}' was not found", file=sys.stderr)
                sys.exit(1)
            broadcast_id, expires_rule = create_broadcast_from_template(cur, template, group_id, sender="sendmsgd")
            expire_message_rule_broadcasts(cur, expires_rule, exclude_broadcast_ids=[broadcast_id])
            expire_broadcasts_triggered_by_template(cur, message_id, exclude_broadcast_ids=[broadcast_id])
        conn.commit()
    finally:
        conn.close()

    debug_log(f"created broadcast={broadcast_id} template={message_id} group={group_id} expires_rule={expires_rule!r}")

    if send_broadcast_ipc(stream_id, broadcast_id):
        return

    broadcast = fetch_broadcast(broadcast_id)
    if not broadcast:
        debug_log(f"fallback missing_broadcast broadcast={broadcast_id}")
        print(f"Broadcast '{broadcast_id}' was not found after insert", file=sys.stderr)
        sys.exit(1)

    debug_log(f"using legacy fallback stream={stream_id} broadcast={broadcast_id}")
    if not send_legacy_ipc(stream_id, broadcast, broadcast_id):
        mark_history_delivery(broadcast_id, "failed")
        mark_active_broadcast_delivery(broadcast_id, "failed")
        print(f"IPC send failed for broadcast '{broadcast_id}'", file=sys.stderr)
        sys.exit(1)
    mark_history_delivery(broadcast_id, "sent")
    mark_active_broadcast_delivery(broadcast_id, "sent")

if __name__ == "__main__":
    main()

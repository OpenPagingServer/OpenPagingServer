#!/usr/bin/env python3

import os
import socket
import sys
import time
import struct
import wave
import subprocess
import itertools
import uuid
from pathlib import Path
import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

IPC_PORT = 50000
ASSET_PATH = "/var/lib/openpagingserver/assets/"
SAMPLE_RATE = 8000
FRAME_SIZE = 160

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )

def is_8k_ulaw(file_path):
    try:
        with wave.open(file_path, 'rb') as wav_file:
            n_channels, sample_width, framerate, n_frames, compression, _ = wav_file.getparams()
            return framerate == 8000 and compression == 'ULAW' and n_channels == 1
    except:
        return False

def get_all_audio_frames(audio_files_str):
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
                    yield silence_payload
            except ValueError:
                continue
        else:
            file_path = ASSET_PATH + audio_file
            if not os.path.exists(file_path):
                continue

            if is_8k_ulaw(file_path):
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(FRAME_SIZE)
                        if not chunk:
                            break
                        if len(chunk) < FRAME_SIZE:
                            chunk = chunk.ljust(FRAME_SIZE, b'\xff')
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
                    yield chunk
                ffmpeg.stdout.close()
                ffmpeg.wait()

def main():
    if len(sys.argv) < 3:
        sys.exit(1)

    group_id = sys.argv[1]
    message_id = sys.argv[2]
    stream_id = uuid.uuid4().hex

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            target_list = set()
            
            if group_id == "0":
                cur.execute("SELECT `dir` FROM endpointmodulesloaded WHERE enabled = 'true'")
                for row in cur.fetchall():
                    if row and row[0]:
                        target_list.add(f"{row[0]}/all")
            else:
                group_ids = group_id.split('.')
                for gid in group_ids:
                    gid = gid.strip()
                    if not gid:
                        continue
                    cur.execute("SELECT members FROM groups WHERE id = %s", (gid,))
                    group_row = cur.fetchone()
                    if group_row and group_row[0]:
                        for member in group_row[0].replace(",", " ").split():
                            target_list.add(member)

            if not target_list:
                sys.exit(1)

            members = " ".join(target_list)

            cur.execute("SELECT type, audio FROM messages WHERE messageid = %s", (message_id,))
            msg_row = cur.fetchone()
            if not msg_row:
                sys.exit(1)
            msg_type, audio_files_str = msg_row
    finally:
        conn.close()

    if not audio_files_str:
        audio_files_str = ""

    audio_gen = get_all_audio_frames(audio_files_str)
    try:
        first_frame = next(audio_gen)
        has_audio = True
    except StopIteration:
        has_audio = False

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        sock.connect(('127.0.0.1', IPC_PORT))
    except Exception:
        sys.exit(1)

    if msg_type in ("text+audio", "audio") and has_audio:
        command = f"PREPARE {stream_id} {message_id} {members}"
        sock.sendall(command.encode('utf-8'))
        
        response = sock.recv(1024)
        if b"OK" in response:
            frame_duration = FRAME_SIZE / SAMPLE_RATE
            next_send_time = time.perf_counter()
            
            for frame in itertools.chain([first_frame], audio_gen):
                try:
                    sock.sendall(frame)
                except BrokenPipeError:
                    break
                
                next_send_time += frame_duration
                sleep_time = next_send_time - time.perf_counter()
                
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    next_send_time = time.perf_counter()
    else:
        command = f"SENDMSG {stream_id} {message_id} {members}"
        sock.sendall(command.encode('utf-8'))
        sock.recv(1024)

    sock.close()

if __name__ == "__main__":
    main()
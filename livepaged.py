#!/usr/bin/env python3

import os
import socket
import threading
import time
import urllib.parse
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

IPC_HOST = "127.0.0.1"
IPC_PORT = 50000


def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )


def parse_rtp_payload(packet):
    if len(packet) < 12:
        return b""
    cc = packet[0] & 0x0F
    ext = (packet[0] & 0x10) >> 4
    payload_type = packet[1] & 0x7F
    if payload_type != 0:
        return b""
    offset = 12 + cc * 4
    if ext:
        if len(packet) < offset + 4:
            return b""
        ext_len = int.from_bytes(packet[offset + 2:offset + 4], "big")
        offset += 4 + ext_len * 4
    if offset >= len(packet):
        return b""
    return packet[offset:]


def resolve_targets(group_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            target_list = set()
            if str(group_id) == "0":
                cur.execute("SELECT `dir` FROM endpointmodulesloaded WHERE enabled = 'true'")
                for row in cur.fetchall():
                    if row and row[0]:
                        target_list.add(f"{row[0]}/all")
            else:
                for gid in str(group_id).split("."):
                    gid = gid.strip()
                    if not gid:
                        continue
                    cur.execute("SELECT members FROM groups WHERE id = %s", (gid,))
                    row = cur.fetchone()
                    if row and row[0]:
                        for member in row[0].replace(",", " ").split():
                            target_list.add(member)
            return sorted(target_list)
    finally:
        conn.close()


class LivePageSession:
    def __init__(self, remote_ip, remote_port, group_id, generator=None, on_finish=None, sender=None):
        self.remote_ip = remote_ip
        self.remote_port = remote_port
        self.group_id = str(group_id) if group_id is not None else ""
        self.generator = generator
        self.on_finish = on_finish
        self.sender = "" if sender is None else str(sender)
        self.local_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.local_sock.bind(("0.0.0.0", 0))
        self.local_sock.settimeout(0.5)
        self.local_port = self.local_sock.getsockname()[1]
        self.control_sock = None
        self.stream_id = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.thread = None
        self.targets = []
        self.cleanup_lock = threading.Lock()
        self.cleaned_up = False

    def preflight(self):
        self.targets = resolve_targets(self.group_id)
        if not self.targets:
            raise RuntimeError("503 Service Unavailable")
        self.control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.control_sock.settimeout(10)
        self.control_sock.connect((IPC_HOST, IPC_PORT))
        encoded_sender = urllib.parse.quote(self.sender, safe="")
        command = f"PREPARELIVE {self.stream_id} {self.group_id} {encoded_sender} {' '.join(self.targets)}\n"
        self.control_sock.sendall(command.encode("utf-8"))
        response = self.control_sock.recv(1024)
        if b"OK" not in response:
            raise RuntimeError("503 Service Unavailable")

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    packet, _ = self.local_sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                payload = parse_rtp_payload(packet)
                if not payload:
                    continue
                try:
                    if self.control_sock is not None:
                        self.control_sock.sendall(payload)
                except OSError:
                    break
        finally:
            self.cleanup()

    def stop(self):
        self.stop_event.set()
        self.cleanup()

    def cleanup(self):
        with self.cleanup_lock:
            if self.cleaned_up:
                return
            self.cleaned_up = True
        try:
            if self.local_sock is not None:
                self.local_sock.close()
        except OSError:
            pass
        self.local_sock = None
        try:
            if self.control_sock is not None:
                self.control_sock.close()
        except OSError:
            pass
        self.control_sock = None
        if self.on_finish is not None:
            try:
                self.on_finish()
            except Exception:
                pass

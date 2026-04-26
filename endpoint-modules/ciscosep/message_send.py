#!/usr/bin/env python3

import os
import random
import socket
import struct
import threading
import time
import requests
import pymysql
from requests.auth import HTTPBasicAuth
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent.parent / ".env"
load_dotenv(ENV_PATH)

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

USERNAME = "admin"
PASSWORD = "admin"
PAYLOAD_TYPE = 0
IPC_PORT = 50000
STREAM_IDLE_TIMEOUT = 0.6
WATCHDOG_INTERVAL = 0.1

rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
rtp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
rtp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
try:
    rtp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0xB8)
except OSError:
    pass

active_streams = {}
phone_active_msgs = {}
streams_lock = threading.Lock()

def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor
    )

def send_phone(ip, xml, results, idx):
    try:
        r = requests.post(
            f"http://{ip}/CGI/Execute",
            data={"XML": xml},
            auth=HTTPBasicAuth(USERNAME, PASSWORD),
            timeout=5
        )
        results[idx] = (r.status_code == 200)
    except requests.exceptions.RequestException:
        results[idx] = False

def send_parallel_and_wait(ips, xml):
    threads = []
    results = [False] * len(ips)

    for i, ip in enumerate(ips):
        t = threading.Thread(target=send_phone, args=(ip, xml, results, i))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return all(results)

def send_ready_signal(module_name, stream_id):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", IPC_PORT))
        s.sendall(f"READY {module_name} {stream_id}".encode("utf-8"))
        s.close()
    except Exception:
        pass

def stop_stream(stream_id):
    phones_to_stop = []
    with streams_lock:
        stream = active_streams.pop(stream_id, None)
        if not stream:
            return

        for ip, streams in list(phone_active_msgs.items()):
            if stream_id in streams:
                streams.remove(stream_id)
                if not streams:
                    phones_to_stop.append(ip)
                    del phone_active_msgs[ip]

    if phones_to_stop:
        stop_xml = """<CiscoIPPhoneExecute>
<ExecuteItem Priority="0" URL="RTPMRx:Stop"/>
</CiscoIPPhoneExecute>"""
        send_parallel_and_wait(phones_to_stop, stop_xml)

def stream_watchdog(stream_id):
    while True:
        time.sleep(WATCHDOG_INTERVAL)
        with streams_lock:
            stream = active_streams.get(stream_id)
            if not stream:
                break
            if time.time() - stream["last_seen"] > STREAM_IDLE_TIMEOUT:
                pass
            else:
                continue
        stop_stream(stream_id)
        break

def handle_api(command_string):
    parts = command_string.strip().split()
    if len(parts) < 4:
        return

    action = parts[0]
    mac_addr = parts[1]
    stream_id = parts[2]
    msg_id = parts[3]

    conn = db()
    endpoints = []
    try:
        with conn.cursor() as cur:
            if mac_addr == "all":
                cur.execute("SELECT ipv4, status, audio FROM `endpoints-output-ciscosep` WHERE status IN ('Unchecked', 'Online')")
                endpoints = cur.fetchall()
            else:
                cur.execute("SELECT ipv4, status, audio FROM `endpoints-output-ciscosep` WHERE macaddr=%s", (mac_addr,))
                row = cur.fetchone()
                if row:
                    endpoints = [row]

            cur.execute("SELECT name, longmessage, type FROM messages WHERE messageid=%s", (msg_id,))
            msg = cur.fetchone()
            if not msg:
                return

            msg_type = msg.get("type", "text+audio")
            name = msg.get("name", "")
            longmessage = msg.get("longmessage", "")
    finally:
        conn.close()

    if not endpoints:
        return

    text_ips = [ep["ipv4"] for ep in endpoints if ep["ipv4"] and ep["status"] in ("Unchecked", "Online")]
    if msg_type in ("text", "text+audio") and text_ips:
        text_xml = f"""<CiscoIPPhoneText>
<Title>{name}</Title>
<Prompt>Select an Action</Prompt>
<Text>{longmessage}</Text>
</CiscoIPPhoneText>"""
        send_parallel_and_wait(text_ips, text_xml)

    audio_ips = [ep["ipv4"] for ep in endpoints if ep["ipv4"] and ep["status"] in ("Unchecked", "Online") and ep["audio"] == "Multicast"]
    if msg_type in ("audio", "text+audio") and audio_ips:
        if action == "prepare_audio":
            with streams_lock:
                if stream_id not in active_streams:
                    active_streams[stream_id] = {
                        "seq": 0,
                        "ts": 0,
                        "ssrc": random.randint(1000, 99999),
                        "last_seen": time.time(),
                        "mcast_ip": f"239.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}",
                        "mcast_port": random.randrange(20480, 32768, 2)
                    }
                    threading.Thread(target=stream_watchdog, args=(stream_id,), daemon=True).start()

            mcast_ip = active_streams[stream_id]["mcast_ip"]
            mcast_port = active_streams[stream_id]["mcast_port"]
            start_xml = f"""<CiscoIPPhoneExecute>
<ExecuteItem Priority="0" URL="RTPMRx:{mcast_ip}:{mcast_port}"/>
</CiscoIPPhoneExecute>"""

            needs_start_ips = []
            with streams_lock:
                for ip in audio_ips:
                    if ip not in phone_active_msgs:
                        phone_active_msgs[ip] = set()
                        needs_start_ips.append(ip)
                    phone_active_msgs[ip].add(stream_id)

            if needs_start_ips:
                success = send_parallel_and_wait(needs_start_ips, start_xml)
            else:
                success = True

            if success:
                send_ready_signal("ciscosep", stream_id)

def receive_audio(chunk, stream_id):
    with streams_lock:
        if stream_id and stream_id in active_streams:
            stream_info = active_streams[stream_id].copy()
        else:
            return 

    seq = stream_info["seq"]
    ts = stream_info["ts"]
    ssrc = stream_info["ssrc"]
    mcast_ip = stream_info["mcast_ip"]
    mcast_port = stream_info["mcast_port"]

    offset = 0
    while offset < len(chunk):
        frame = chunk[offset:offset + 160]
        if len(frame) < 160:
            frame = frame.ljust(160, b"\xff")

        rtp_header = struct.pack("!BBHII", 0x80, PAYLOAD_TYPE, seq, ts, ssrc)
        try:
            rtp_sock.sendto(rtp_header + frame, (mcast_ip, mcast_port))
        except Exception:
            pass

        seq = (seq + 1) % 65536
        ts = (ts + 160) % 4294967296
        offset += 160

    with streams_lock:
        if stream_id in active_streams:
            active_streams[stream_id]["seq"] = seq
            active_streams[stream_id]["ts"] = ts
            active_streams[stream_id]["last_seen"] = time.time()
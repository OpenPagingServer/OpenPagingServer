#!/usr/bin/env python3
import sys
import random
import socket
import struct
import time
import requests
import pymysql
import subprocess
import wave
import itertools
import threading
from requests.auth import HTTPBasicAuth

VERBOSE = "-v" in sys.argv
MESSAGE_ID = next((a for a in sys.argv[1:] if a != "-v"), None)

if not MESSAGE_ID:
    sys.exit(1)

def log(*x):
    if VERBOSE:
        print(*x)

DB_HOST = "localhost"
DB_USER = "root"
DB_PASS = ""
DB_NAME = "openpagingserver"

PHONES = ["10.50.10.114"]

USERNAME = "admin"
PASSWORD = "admin"

ASSET_PATH = "/var/lib/openpagingserver/assets/"
SAMPLE_RATE = 8000
FRAME_SIZE = 160
PAYLOAD_TYPE = 0
SSRC = random.randint(1000, 99999)

MULTICAST_IP = f"239.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}"
MULTICAST_PORT = random.randrange(20480, 32768, 2)

log("Multicast:", MULTICAST_IP, MULTICAST_PORT)

conn = pymysql.connect(
    host=DB_HOST,
    user=DB_USER,
    password=DB_PASS,
    database=DB_NAME,
    cursorclass=pymysql.cursors.DictCursor
)

with conn.cursor() as cursor:
    cursor.execute(
        "SELECT name, longmessage, audio, type FROM messages WHERE messageid=%s",
        (MESSAGE_ID,)
    )
    row = cursor.fetchone()

if not row:
    sys.exit(1)

NAME = row["name"]
LONGMESSAGE = row["longmessage"]
AUDIO_FILES = row["audio"].split(":")
MESSAGE_TYPE = row.get("type", "text+audio")

def send_phone(ip, xml):
    try:
        r = requests.post(
            f"http://{ip}/CGI/Execute",
            data={"XML": xml},
            auth=HTTPBasicAuth(USERNAME, PASSWORD),
            timeout=5
        )
        log("PHONE:", ip, r.status_code, r.text)
    except requests.exceptions.RequestException as e:
        log("FAILED:", ip, e)

def post_xml(xml):
    log("POST:", xml)
    threads = []
    for ip in PHONES:
        t = threading.Thread(target=send_phone, args=(ip, xml))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return True

def is_8k_ulaw(file_path):
    try:
        with wave.open(file_path, 'rb') as wav_file:
            n_channels, sample_width, framerate, n_frames, compression, _ = wav_file.getparams()
            return framerate == 8000 and compression == 'ULAW' and n_channels == 1
    except:
        return False

def get_all_audio_frames():
    for audio_file in AUDIO_FILES:
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

audio_gen = get_all_audio_frames()

try:
    first_frame = next(audio_gen)
    has_audio = True
except StopIteration:
    has_audio = False

if MESSAGE_TYPE != "audio":
    text_xml = f"""<CiscoIPPhoneText>
<Title>{NAME}</Title>
<Prompt>Select an Action</Prompt>
<Text>{LONGMESSAGE}</Text>
</CiscoIPPhoneText>"""
    post_xml(text_xml)

start_xml = f"""<CiscoIPPhoneExecute>
<ExecuteItem Priority="0" URL="RTPMRx:{MULTICAST_IP}:{MULTICAST_PORT}"/>
</CiscoIPPhoneExecute>"""

post_xml(start_xml)
time.sleep(0.1)

if has_audio:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0xB8)
    except OSError:
        pass

    SEQ = 0
    TS = 0

    frame_duration = FRAME_SIZE / SAMPLE_RATE
    next_send_time = time.perf_counter()

    for frame in itertools.chain([first_frame], audio_gen):
        rtp_header = struct.pack("!BBHII", 0x80, PAYLOAD_TYPE, SEQ, TS, SSRC)
        sock.sendto(rtp_header + frame, (MULTICAST_IP, MULTICAST_PORT))

        SEQ = (SEQ + 1) % 65536
        TS += FRAME_SIZE

        next_send_time += frame_duration
        sleep_time = next_send_time - time.perf_counter()

        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            next_send_time = time.perf_counter()

    sock.close()

stop_xml = """<CiscoIPPhoneExecute>
<ExecuteItem Priority="0" URL="RTPMRx:Stop"/>
</CiscoIPPhoneExecute>"""

post_xml(stop_xml)
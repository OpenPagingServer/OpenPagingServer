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

PHONE_IP = "10.50.10.170"
USERNAME = "admin"
PASSWORD = "admin"

ASSET_PATH = "/opt/openpagingserver/assets/"
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

def post_xml(xml):
    log("POST:", xml)
    r = requests.post(
        f"http://{PHONE_IP}/CGI/Execute",
        data={"XML": xml},
        auth=HTTPBasicAuth(USERNAME, PASSWORD),
        timeout=5
    )
    log("HTTP:", r.status_code)
    log("RESP:", r.text)
    return r

if MESSAGE_TYPE != "audio":
    text_xml = f"""<CiscoIPPhoneText>
<Title>{NAME}</Title>
<Prompt>Select an Action</Prompt>
<Text>{LONGMESSAGE}</Text>
</CiscoIPPhoneText>"""
    post_xml(text_xml)
    time.sleep(1)

start_xml = f"""<CiscoIPPhoneExecute>
<ExecuteItem Priority="0" URL="RTPMRx:{MULTICAST_IP}:{MULTICAST_PORT}"/>
</CiscoIPPhoneExecute>"""

post_xml(start_xml)
time.sleep(0.5)

def generate_silence(duration):
    silence_frames = int(duration * SAMPLE_RATE / FRAME_SIZE)
    return b'\x00' * (silence_frames * FRAME_SIZE)

def is_8k_ulaw(file_path):
    try:
        with wave.open(file_path, 'rb') as wav_file:
            n_channels, sample_width, framerate, n_frames, compression, _ = wav_file.getparams()
            return framerate == 8000 and compression == 'ULAW' and n_channels == 1
    except:
        return False

def process_audio_file_real_time(file_path):
    log("Streaming WAV to μ-law in real time:", file_path)
    ffmpeg = subprocess.Popen([
        "ffmpeg",
        "-i", file_path,
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-f", "mulaw",
        "pipe:1"
    ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    while True:
        chunk = ffmpeg.stdout.read(FRAME_SIZE)
        if not chunk:
            break
        yield chunk
    ffmpeg.stdout.close()
    ffmpeg.wait()

def stream_raw_ulaw(file_path):
    log("Streaming raw 8k µ-law:", file_path)
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(FRAME_SIZE)
            if not chunk:
                break
            yield chunk

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

SEQ = 0
TS = 0

log("Streaming RTP")

for audio_file in AUDIO_FILES:
    audio_file = audio_file.strip()
    
    if audio_file.startswith("%silence(") and audio_file.endswith(")"):
        duration_str = audio_file[9:-1]
        try:
            duration = float(duration_str)
            log(f"Generating silence for {duration} seconds")
            ulaw_data = generate_silence(duration)
            offset = 0
            frame_bytes = FRAME_SIZE
            while offset < len(ulaw_data):
                frame = ulaw_data[offset:offset+frame_bytes]
                if not frame:
                    break
                rtp_header = struct.pack("!BBHII", 0x80, PAYLOAD_TYPE, SEQ, TS, SSRC)
                sock.sendto(rtp_header + frame, (MULTICAST_IP, MULTICAST_PORT))
                log("RTP", SEQ, TS)
                SEQ = (SEQ + 1) % 65536
                TS += FRAME_SIZE
                offset += frame_bytes
                time.sleep(FRAME_SIZE / SAMPLE_RATE)
        except ValueError:
            log(f"Invalid silence duration: {duration_str}")
            continue
    else:
        file_path = ASSET_PATH + audio_file
        if is_8k_ulaw(file_path):
            audio_generator = stream_raw_ulaw(file_path)
        else:
            audio_generator = process_audio_file_real_time(file_path)
        
        for frame in audio_generator:
            rtp_header = struct.pack("!BBHII", 0x80, PAYLOAD_TYPE, SEQ, TS, SSRC)
            sock.sendto(rtp_header + frame, (MULTICAST_IP, MULTICAST_PORT))
            log("RTP", SEQ, TS)
            SEQ = (SEQ + 1) % 65536
            TS += FRAME_SIZE
            time.sleep(FRAME_SIZE / SAMPLE_RATE)

sock.close()

stop_xml = """<CiscoIPPhoneExecute>
<ExecuteItem Priority="0" URL="RTPMRx:Stop"/>
</CiscoIPPhoneExecute>"""

post_xml(stop_xml)
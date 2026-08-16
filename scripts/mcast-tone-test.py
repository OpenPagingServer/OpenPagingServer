"""Standalone multicast RTP test-tone sender (PCMU, G722, G722.1/G722.1C).

Usage (run on the server, from the repo root):
    python3 scripts/mcast-tone-test.py 232.9.10.11 23456 pcmu
    python3 scripts/mcast-tone-test.py 232.9.10.11 23456 g722
    python3 scripts/mcast-tone-test.py 232.9.10.11 23456 g7221 [rate] [bitrate] [pt] [ts_clock]

g7221 options (positional, all optional):
    rate     16000 (Siren7 / G.722.1) or 32000 (Siren14 / G.722.1C). Default 32000.
    bitrate  24000, 32000 or 48000 (48000 only valid at 32000 rate). Default 24000.
    pt       RTP payload type. Default 121.
    ts_clock RTP timestamp clock. Default = rate.

Examples for sweeping what a phone accepts:
    python3 scripts/mcast-tone-test.py 232.9.10.11 23456 g7221 32000 24000 121
    python3 scripts/mcast-tone-test.py 232.9.10.11 23456 g7221 32000 48000 121
    python3 scripts/mcast-tone-test.py 232.9.10.11 23456 g7221 16000 24000 121
    python3 scripts/mcast-tone-test.py 232.9.10.11 23456 g7221 32000 24000 98

Sends 5 seconds of 440 Hz tone. PCMU is the control: if pcmu plays but the
codec under test doesn't, the phone is rejecting that codec/parameters.
"""

import math
import random
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sip.codec import SipCodecEncoder, _linear_to_ulaw  # noqa: E402


def pick_interface():
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
        probe.close()
        return ip
    except Exception:
        return ""


def open_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    iface = pick_interface()
    if iface:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
        except OSError:
            pass
    return sock, iface


def tone_pcm16(rate, frame_index, samples):
    base = frame_index * samples
    return b"".join(
        int(12000 * math.sin(2 * math.pi * 440 * (base + n) / rate)).to_bytes(2, "little", signed=True)
        for n in range(samples)
    )


def send_stream(sock, addr, port, pt, ts_step, frames, payload_for_frame, label):
    seq = random.randrange(0, 65536)
    ts = random.randrange(0, 1 << 32)
    ssrc = random.randrange(0, 1 << 32)
    print(f"sending {label} to {addr}:{port} pt={pt} ts_step={ts_step}")
    sent = 0
    next_send = time.monotonic()
    for i in range(frames):
        payload = payload_for_frame(i)
        if payload:
            pkt = struct.pack("!BBHII", 0x80, pt, seq, ts, ssrc) + payload
            sock.sendto(pkt, (addr, port))
            sent += 1
            seq = (seq + 1) & 0xFFFF
            ts = (ts + ts_step) & 0xFFFFFFFF
        next_send += 0.02
        wait = next_send - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    print(f"done sent={sent} packets")


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else "232.9.10.11"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 23456
    codec = (sys.argv[3] if len(sys.argv) > 3 else "g722").upper()
    sock, iface = open_socket()
    print(f"iface={iface or 'default'}")

    if codec in ("G7221", "G7221C", "G722.1", "G722.1C"):
        from sip.g7221 import G7221Encoder

        rate = int(sys.argv[4]) if len(sys.argv) > 4 else 32000
        bitrate = int(sys.argv[5]) if len(sys.argv) > 5 else 24000
        pt = int(sys.argv[6]) if len(sys.argv) > 6 else 121
        ts_clock = int(sys.argv[7]) if len(sys.argv) > 7 else rate
        enc = G7221Encoder(bit_rate=bitrate, sample_rate=rate)
        samples = rate // 50

        def payload_for_frame(i):
            return enc.encode(tone_pcm16(rate, i, samples))

        send_stream(sock, addr, port, pt, ts_clock // 50, 250,
                    payload_for_frame, f"G722.1 rate={rate} bitrate={bitrate}")
        return

    if codec in ("G726", "G726-32", "G72632"):
        from sip.g726 import G726Encoder
        import sip.g726 as g726mod

        nibble = (sys.argv[4] if len(sys.argv) > 4 else "msb").lower()
        g726mod.G726_NIBBLE_LSB = nibble in ("lsb", "little", "aal2")
        pt = int(sys.argv[5]) if len(sys.argv) > 5 else 2
        frames_per_packet = int(sys.argv[6]) if len(sys.argv) > 6 else 1
        enc = G726Encoder()
        packet_samples = 160 * frames_per_packet

        def payload_for_frame(i):
            return enc.encode(tone_pcm16(8000, i, packet_samples))

        # Each "frame" here is one whole packet (frames_per_packet * 20 ms).
        seq = random.randrange(0, 65536)
        ts = random.randrange(0, 1 << 32)
        ssrc = random.randrange(0, 1 << 32)
        label = f"G726-32 nibble={'lsb' if g726mod.G726_NIBBLE_LSB else 'msb'} fpp={frames_per_packet}"
        print(f"sending {label} to {addr}:{port} pt={pt} ts_step={packet_samples}")
        next_send = time.monotonic()
        for i in range(250 // frames_per_packet):
            payload = payload_for_frame(i)
            pkt = struct.pack("!BBHII", 0x80, pt, seq, ts, ssrc) + payload
            sock.sendto(pkt, (addr, port))
            seq = (seq + 1) & 0xFFFF
            ts = (ts + packet_samples) & 0xFFFFFFFF
            next_send += 0.02 * frames_per_packet
            wait = next_send - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        print("done")
        return

    ts_clock = int(sys.argv[4]) if len(sys.argv) > 4 else (16000 if codec == "G722" else 8000)
    pt = 9 if codec == "G722" else 0
    enc = SipCodecEncoder(codec if codec == "G722" else "PCMU")

    def payload_for_frame(i):
        frame = bytes(
            _linear_to_ulaw(int(12000 * math.sin(2 * math.pi * 440 * ((i * 160) + n) / 8000)))
            for n in range(160)
        )
        return enc.encode(frame)

    send_stream(sock, addr, port, pt, ts_clock // 50, 250, payload_for_frame, codec)


if __name__ == "__main__":
    main()

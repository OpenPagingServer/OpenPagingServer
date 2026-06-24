import random
import socket
import struct
import threading
import time
import select
import os

DTMF_MAP = {
    0: "0", 1: "1", 2: "2", 3: "3",
    4: "4", 5: "5", 6: "6", 7: "7",
    8: "8", 9: "9", 10: "*", 11: "#",
    12: "A", 13: "B", 14: "C", 15: "D"
}

def rtp_debug(message):
    if os.getenv("DEBUG", "").strip().lower() == "true":
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] DEBUG sip.rtp {message}", flush=True)

def rtp_sockname(sock):
    try:
        host, port = sock.getsockname()[:2]
        return f"{host}:{port}"
    except Exception:
        return "unknown"

class RTPSession:
    def __init__(self, target_ip, target_port, payload_generator=None, on_finish=None, rtp_socket=None):
        self.target_ip = target_ip
        self.target_port = target_port
        self.payload_generator = payload_generator
        self.on_finish = on_finish
        self.stop_event = threading.Event()
        self.thread = None
        self.socket = rtp_socket if rtp_socket is not None else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if rtp_socket is None:
            self.socket.bind(("0.0.0.0", 0))
        self.local_port = self.socket.getsockname()[1]
        self.digits = ""
        self.rtp_paused = False
        self.last_dtmf_time = 0
        self.active_dtmf_event = None
        self.active_dtmf_event_time = 0
        self.active_dtmf_event_timestamp = None
        self.finished_dtmf_event = None
        self.finished_dtmf_event_time = 0
        self.finished_dtmf_event_timestamp = None
        self._last_rtp_timestamp = None
        self.initial_silence_frames = 0
        self.lock = threading.Lock()
        self.sent_packets = 0
        self.received_packets = 0

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        try:
            self.socket.close()
        except:
            pass

    def get_digits(self):
        with self.lock:
            return self.digits

    def clear_digits(self):
        with self.lock:
            self.digits = ""
            self.active_dtmf_event = None
            self.active_dtmf_event_time = 0
            self.active_dtmf_event_timestamp = None
            self.finished_dtmf_event = None
            self.finished_dtmf_event_time = 0
            self.finished_dtmf_event_timestamp = None

    def _append_digit(self, d):
        now = time.time()
        if now - self.last_dtmf_time < 0.15:
            return
        self.last_dtmf_time = now
        with self.lock:
            self.digits += d

    def _learn_packet_source(self, addr, data=b""):
        if not getattr(self, "rtp_latching_enabled", False):
            return
        if not addr or len(addr) < 2:
            return
        source_ip = str(addr[0] or "").strip()
        try:
            source_port = int(addr[1] or 0)
        except Exception:
            source_port = 0
        current_port = int(getattr(self, "target_port", 0) or 0)
        packet_type = data[1] if len(data) > 1 else 0
        if (
            not source_ip
            or source_port <= 0
            or 192 <= packet_type <= 223
            or ((data[:1] or b"\x00")[0] >> 6) != 2
            or (current_port > 0 and current_port % 2 == 0 and source_port == current_port + 1 and source_port % 2 == 1)
        ):
            return
        old_ip = str(getattr(self, "target_ip", "") or "")
        old_port = int(getattr(self, "target_port", 0) or 0)
        self.target_ip = source_ip
        self.target_port = source_port
        if (old_ip, old_port) != (source_ip, source_port):
            rtp_debug(f"learned source local={rtp_sockname(self.socket)} old={old_ip}:{old_port} new={source_ip}:{source_port}")

    def _parse_rtp(self, data):
        if len(data) < 12:
            return None, None

        pt = data[1] & 0x7F
        cc = data[0] & 0x0F
        ext = (data[0] & 0x10) >> 4
        self._last_rtp_timestamp = struct.unpack(">I", data[4:8])[0]

        offset = 12 + cc * 4

        if ext:
            if len(data) < offset + 4:
                return None, None
            ext_len = struct.unpack(">H", data[offset+2:offset+4])[0]
            offset += 4 + ext_len * 4

        payload = data[offset:]
        return pt, payload

    def _handle_dtmf(self, pt, payload):
        if 96 <= pt <= 127:
            if len(payload) >= 2:
                event = payload[0]
                if event in DTMF_MAP:
                    now = time.time()
                    end_event = (payload[1] & 0x80) != 0
                    rtp_timestamp = self._last_rtp_timestamp
                    append_event = False
                    with self.lock:
                        stale_event = now - self.active_dtmf_event_time > 0.5
                        same_active_event = (
                            self.active_dtmf_event == event
                            and self.active_dtmf_event_timestamp == rtp_timestamp
                        )
                        recently_finished = (
                            self.finished_dtmf_event == event
                            and self.finished_dtmf_event_timestamp == rtp_timestamp
                            and now - self.finished_dtmf_event_time <= 0.5
                        )
                        if not recently_finished and (not same_active_event or stale_event):
                            append_event = True
                            self.active_dtmf_event = event
                            self.active_dtmf_event_timestamp = rtp_timestamp
                        self.active_dtmf_event_time = now
                        if end_event:
                            self.active_dtmf_event = None
                            self.active_dtmf_event_timestamp = None
                            self.finished_dtmf_event = event
                            self.finished_dtmf_event_time = now
                            self.finished_dtmf_event_timestamp = rtp_timestamp
                    if append_event:
                        self._append_digit(DTMF_MAP[event])

    def build_packet(self, sequence, timestamp, ssrc, payload):
        return struct.pack("!BBHII", 0x80, 0x00, sequence, timestamp, ssrc) + payload

    def run(self):
        sequence = random.randrange(0, 65536)
        timestamp = random.randrange(0, 4294967296)
        ssrc = random.randrange(0, 4294967296)
        next_send = time.monotonic()
        gen = self.payload_generator
        finish_when_generator_exhausted = gen is not None

        try:
            while not self.stop_event.is_set():
                payload = None
                r, _, _ = select.select([self.socket], [], [], 0.01)
                if r:
                    try:
                        data, addr = self.socket.recvfrom(4096)
                        self._learn_packet_source(addr, data)
                        pt, received_payload = self._parse_rtp(data)
                        if pt is not None:
                            self.received_packets += 1
                            if self.received_packets <= 3 or self.received_packets % 50 == 0:
                                rtp_debug(f"recv packet={self.received_packets} local={rtp_sockname(self.socket)} remote={addr[0]}:{addr[1]} bytes={len(data)}")
                            self._handle_dtmf(pt, received_payload)
                    except:
                        pass

                if getattr(self, "initial_silence_frames", 0) and not getattr(self, "rtp_paused", False):
                    payload = b"\xff" * 160
                    try:
                        self.initial_silence_frames = max(0, int(self.initial_silence_frames) - 1)
                    except Exception:
                        self.initial_silence_frames = 0
                elif gen and getattr(self, "rtp_paused", False):
                    payload = None
                elif gen:
                    try:
                        payload = next(gen)
                    except StopIteration:
                        gen = None
                        payload = None
                        if finish_when_generator_exhausted:
                            break

                if payload:
                    packet = self.build_packet(sequence, timestamp, ssrc, payload)
                    try:
                        self.socket.sendto(packet, (self.target_ip, self.target_port))
                    except:
                        break
                    self.sent_packets += 1
                    if self.sent_packets <= 3 or self.sent_packets % 50 == 0:
                        rtp_debug(f"send packet={self.sent_packets} local={rtp_sockname(self.socket)} remote={self.target_ip}:{self.target_port} bytes={len(packet)}")
                    sequence = (sequence + 1) & 0xFFFF
                    timestamp = (timestamp + 160) & 0xFFFFFFFF

                next_send += 0.02
                sleep_for = next_send - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)

        finally:
            try:
                self.socket.close()
            except:
                pass
            if self.on_finish:
                try:
                    self.on_finish()
                except:
                    pass

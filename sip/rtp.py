import random
import socket
import struct
import threading
import time
import select

DTMF_MAP = {
    0: "0", 1: "1", 2: "2", 3: "3",
    4: "4", 5: "5", 6: "6", 7: "7",
    8: "8", 9: "9", 10: "*", 11: "#",
    12: "A", 13: "B", 14: "C", 15: "D"
}

class RTPSession:
    def __init__(self, target_ip, target_port, payload_generator=None, on_finish=None):
        self.target_ip = target_ip
        self.target_port = target_port
        self.payload_generator = payload_generator
        self.on_finish = on_finish
        self.stop_event = threading.Event()
        self.thread = None
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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
        self.lock = threading.Lock()

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
                r, _, _ = select.select([self.socket], [], [], 0.01)
                if r:
                    try:
                        data, _ = self.socket.recvfrom(4096)
                        pt, payload = self._parse_rtp(data)
                        if pt is not None:
                            self._handle_dtmf(pt, payload)
                    except:
                        pass

                if gen and getattr(self, "rtp_paused", False):
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

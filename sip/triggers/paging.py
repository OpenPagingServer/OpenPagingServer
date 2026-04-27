import importlib.util
import math
import struct
import threading
import time
from pathlib import Path

trigger_name = "page"

BASE_DIR = Path(__file__).resolve().parents[2]


def load_livepaged():
    module_path = BASE_DIR / "livepaged.py"
    spec = importlib.util.spec_from_file_location("openpaging_livepaged", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


livepaged = load_livepaged()


def lin2ulaw(sample):
    maximum = 32767
    bias = 132
    sign = 0 if sample >= 0 else 0x80
    sample = abs(sample)
    sample = min(sample, maximum)
    sample += bias
    exponent = 7
    mask = 0x4000
    while (sample & mask) == 0 and exponent > 0:
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


BEEP_CYCLE = bytearray()
for i in range(8):
    value = int(math.sin(2 * math.pi * i / 8) * 16000)
    BEEP_CYCLE.append(lin2ulaw(value))
BEEP_FRAME = bytes(BEEP_CYCLE * 20)


def get_ringback_frame(seq):
    time_sec = (seq * 160) / 8000.0
    if (time_sec % 6.0) > 2.0:
        return bytes([0xFF] * 160)
    frame = bytearray()
    for i in range(160):
        t = ((seq * 160) + i) / 8000.0
        value = int(((math.sin(2 * math.pi * 440 * t) + math.sin(2 * math.pi * 480 * t)) / 2) * 16000)
        frame.append(lin2ulaw(value))
    return bytes(frame)


def make_rtp_packet(seq, ts, payload, pt=0):
    header = bytearray(12)
    header[0] = 0x80
    header[1] = pt & 0x7F
    struct.pack_into("!H", header, 2, seq)
    struct.pack_into("!I", header, 4, ts)
    struct.pack_into("!I", header, 8, 0x12345678)
    return bytes(header) + payload


class SipLivePageSession(livepaged.LivePageSession):
    def __init__(self, remote_ip, remote_port, group_id, generator=None, on_finish=None, sender=None):
        super().__init__(remote_ip, remote_port, group_id=group_id, generator=generator, on_finish=on_finish, sender=sender)
        self.seq = 1000
        self.ts = 1000

    def send_rtp(self, payload):
        packet = make_rtp_packet(self.seq, self.ts, payload, pt=0)
        try:
            if self.local_sock is not None:
                self.local_sock.sendto(packet, (self.remote_ip, self.remote_port))
        except Exception:
            pass
        self.seq = (self.seq + 1) & 0xFFFF
        self.ts = (self.ts + 160) & 0xFFFFFFFF

    def preflight(self):
        setup_done = threading.Event()
        setup_error = []

        def prepare():
            try:
                super(SipLivePageSession, self).preflight()
            except Exception as exc:
                setup_error.append(exc)
            finally:
                setup_done.set()

        threading.Thread(target=prepare, daemon=True).start()
        loops = 0
        while not setup_done.is_set():
            self.send_rtp(get_ringback_frame(loops))
            loops += 1
            time.sleep(0.02)
        if setup_error:
            raise setup_error[0]

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        try:
            for _ in range(25):
                if self.stop_event.is_set():
                    return
                start_time = time.time()
                self.send_rtp(BEEP_FRAME)
                elapsed = time.time() - start_time
                if elapsed < 0.02:
                    time.sleep(0.02 - elapsed)
            time.sleep(0.5)
            if self.local_sock is not None:
                self.local_sock.setblocking(False)
                try:
                    while True:
                        self.local_sock.recvfrom(4096)
                except Exception:
                    pass
                self.local_sock.setblocking(True)
                self.local_sock.settimeout(0.5)
            super().run()
        finally:
            self.cleanup()


def handle(arg, group=None, sender=None):
    class BoundSipLivePageSession(SipLivePageSession):
        def __init__(self, remote_ip, remote_port, generator=None, on_finish=None):
            super().__init__(remote_ip, remote_port, group_id=group, generator=generator, on_finish=on_finish, sender=sender)

    return {
        "session_class": BoundSipLivePageSession,
    }

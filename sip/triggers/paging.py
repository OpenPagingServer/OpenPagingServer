import importlib
import math
import os
import struct
import sys
import threading
import time
from pathlib import Path

trigger_name = "page"

BASE_DIR = Path(__file__).resolve().parents[2]
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"


def page_debug(message):
    if DEBUG:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] sip.triggers.paging {message}")


def load_livepaged():
    base_dir_text = str(BASE_DIR)
    if base_dir_text not in sys.path:
        sys.path.insert(0, base_dir_text)
    return importlib.import_module("livepaged")


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
SILENCE_FRAME = bytes([0xFF] * 160)


def get_dual_tone_frame(seq, frequencies, volume=16000):
    frame = bytearray()
    for i in range(160):
        t = ((seq * 160) + i) / 8000.0
        value = 0.0
        for frequency in frequencies:
            value += math.sin(2 * math.pi * frequency * t)
        value = int((value / len(frequencies)) * volume)
        frame.append(lin2ulaw(value))
    return bytes(frame)


def get_cadence_frame(seq, frequencies, tone_seconds, silence_seconds, volume=16000):
    time_sec = (seq * 160) / 8000.0
    if (time_sec % (tone_seconds + silence_seconds)) >= tone_seconds:
        return SILENCE_FRAME
    return get_dual_tone_frame(seq, frequencies, volume=volume)


def get_ringback_frame(seq):
    return get_cadence_frame(seq, (440, 480), 2.0, 3.0)


def get_busy_frame(seq):
    return get_cadence_frame(seq, (480, 620), 0.5, 0.5)


def get_reorder_frame(seq):
    return get_cadence_frame(seq, (480, 620), 0.25, 0.25)


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
        page_debug(
            f"session_init stream={self.stream_id} remote={remote_ip}:{remote_port} "
            f"group={self.group_id!r} sender={self.sender!r} local_port={self.local_port}"
        )

    def send_rtp(self, payload):
        if getattr(self, "rtp_paused", False):
            return
        packet = make_rtp_packet(self.seq, self.ts, payload, pt=0)
        try:
            if self.local_sock is not None:
                self.local_sock.sendto(packet, (self.remote_ip, self.remote_port))
        except Exception:
            pass
        self.seq = (self.seq + 1) & 0xFFFF
        self.ts = (self.ts + 160) & 0xFFFFFFFF

    def play_progress_tone(self, tone="reorder", duration=4.0):
        frame_factory = get_busy_frame if str(tone).lower() == "busy" else get_reorder_frame
        frames = max(1, int(float(duration) * 50))
        page_debug(f"progress_tone_start stream={self.stream_id} tone={tone} frames={frames}")
        next_send = time.monotonic()
        for frame_seq in range(frames):
            if self.stop_event.is_set():
                break
            self.send_rtp(frame_factory(frame_seq))
            next_send += 0.02
            sleep_for = next_send - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
        page_debug(f"progress_tone_done stream={self.stream_id} tone={tone}")

    def preflight(self):
        page_debug(f"preflight_start stream={self.stream_id} group={self.group_id!r}")
        setup_done = threading.Event()
        setup_error = []

        def prepare():
            try:
                super(SipLivePageSession, self).preflight()
                page_debug(
                    f"preflight_prepare_done stream={self.stream_id} group={self.group_id!r} "
                    f"targets={self.targets}"
                )
            except Exception as exc:
                setup_error.append(exc)
                page_debug(
                    f"preflight_prepare_error stream={self.stream_id} group={self.group_id!r} "
                    f"error={exc.__class__.__name__}: {exc}"
                )
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
        page_debug(f"preflight_ok stream={self.stream_id} ringback_frames={loops}")

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            page_debug(f"start stream={self.stream_id} group={self.group_id!r}")
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        try:
            page_debug(f"run_beep_start stream={self.stream_id}")
            for _ in range(25):
                if self.stop_event.is_set():
                    page_debug(f"run_stopped_during_beep stream={self.stream_id}")
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
            page_debug(f"run_live_audio_start stream={self.stream_id}")
            super().run()
        finally:
            page_debug(f"run_cleanup stream={self.stream_id}")
            self.cleanup()


def handle(arg, group=None, sender=None):
    page_group = group if group not in (None, "") else arg
    page_debug(f"handle arg={arg!r} group={group!r} resolved_group={page_group!r} sender={sender!r}")

    class BoundSipLivePageSession(SipLivePageSession):
        def __init__(self, remote_ip, remote_port, generator=None, on_finish=None):
            super().__init__(remote_ip, remote_port, group_id=page_group, generator=generator, on_finish=on_finish, sender=sender)

    return {
        "session_class": BoundSipLivePageSession,
    }

import importlib
import math
import os
import select
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


def socket_name(sock):
    try:
        host, port = sock.getsockname()[:2]
        return f"{host}:{port}"
    except Exception:
        return "unknown"


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
    defers_preflight = True

    def __init__(self, remote_ip, remote_port, group_id, generator=None, on_finish=None, sender=None):
        super().__init__(remote_ip, remote_port, group_id=group_id, generator=generator, on_finish=on_finish, sender=sender)
        self.seq = 1000
        self.ts = 1000
        self.rtp_packets_sent = 0
        self.rtp_send_errors = 0
        self.rtp_keepalive_payload = None
        self.setup_failed = False
        self._preflight_done = False
        page_debug(
            f"session_init stream={self.stream_id} remote={remote_ip}:{remote_port} "
            f"group={self.group_id!r} sender={self.sender!r} local_port={self.local_port}"
        )

    def poll_rtp_source(self, max_packets=4):
        if self.local_sock is None:
            return
        for _ in range(max(1, int(max_packets or 1))):
            try:
                ready, _, _ = select.select([self.local_sock], [], [], 0)
            except Exception:
                return
            if not ready:
                return
            try:
                packet, addr = self.local_sock.recvfrom(4096)
            except Exception:
                return
            self.rtp_packets_received = int(getattr(self, "rtp_packets_received", 0) or 0) + 1
            if self.rtp_packets_received <= 3 or self.rtp_packets_received % 50 == 0:
                page_debug(
                    f"rtp_poll_recv stream={self.stream_id} packet={self.rtp_packets_received} "
                    f"local={socket_name(self.local_sock)} remote={addr[0]}:{addr[1]} bytes={len(packet)}"
                )
            try:
                self.learn_rtp_source(addr, packet)
            except Exception:
                pass

    def send_rtp(self, payload, poll_source=True):
        if getattr(self, "rtp_paused", False):
            return
        packet = make_rtp_packet(self.seq, self.ts, payload, pt=0)
        try:
            if self.local_sock is not None:
                if poll_source:
                    self.poll_rtp_source()
                self.local_sock.sendto(packet, (self.remote_ip, self.remote_port))
                self.rtp_packets_sent += 1
                if self.rtp_packets_sent <= 3 or self.rtp_packets_sent % 50 == 0:
                    page_debug(
                        f"rtp_send stream={self.stream_id} packet={self.rtp_packets_sent} "
                        f"local={socket_name(self.local_sock)} remote={self.remote_ip}:{self.remote_port} bytes={len(packet)}"
                    )
            else:
                if self.rtp_send_errors == 0:
                    page_debug(f"rtp_send_no_socket stream={self.stream_id} remote={self.remote_ip}:{self.remote_port}")
                self.rtp_send_errors += 1
        except Exception as exc:
            self.rtp_send_errors += 1
            if self.rtp_send_errors <= 3 or self.rtp_send_errors % 50 == 0:
                page_debug(
                    f"rtp_send_error stream={self.stream_id} errors={self.rtp_send_errors} "
                    f"local={socket_name(self.local_sock)} remote={self.remote_ip}:{self.remote_port} "
                    f"error={exc.__class__.__name__}: {exc}"
                )
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
        if self._preflight_done:
            return
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
        self._preflight_done = True
        page_debug(f"preflight_ok stream={self.stream_id} ringback_frames={loops}")

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            page_debug(f"start stream={self.stream_id} group={self.group_id!r}")
            self.cleanup_after_run = False
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def play_pre_tones_with_silence(self):
        if not self.pre_tones:
            self.pre_tone_completed = True
            return
        from endpoints import audio_frames, mix_ulaw_frames

        self.pre_tone_active = True
        frame_duration = 0.02
        next_send = time.monotonic()
        try:
            for tone in self.pre_tones:
                for frame in audio_frames(tone):
                    if self.stop_event.is_set() and not self.end_requested.is_set():
                        return
                    if self.local_sock is not None:
                        self.send_rtp(SILENCE_FRAME, poll_source=False)
                    next_send += frame_duration
                    live_payload = self.recv_pre_tone_payload_until(next_send)
                    output_frame = self.normalize_audio_frame(frame)
                    if live_payload:
                        output_frame = mix_ulaw_frames([output_frame, self.normalize_audio_frame(live_payload)])
                    else:
                        sleep_for = next_send - time.monotonic()
                        if sleep_for > 0:
                            time.sleep(sleep_for)
                        else:
                            next_send = time.monotonic()
                    self.forward_payload(output_frame, ignore_pause=True, ignore_stop=True)
        finally:
            self.pre_tone_active = False
            self.pre_tone_completed = True
            if self.end_requested.is_set():
                self.stop_event.set()

    def recv_pre_tone_payload_until(self, deadline):
        payload = None
        while not self.stop_event.is_set():
            local_sock = self.local_sock
            if local_sock is None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ready, _, _ = select.select([local_sock], [], [], remaining)
            except Exception:
                break
            if not ready:
                break
            try:
                packet, addr = local_sock.recvfrom(4096)
            except Exception:
                break
            self.rtp_packets_received = int(getattr(self, "rtp_packets_received", 0) or 0) + 1
            if self.rtp_packets_received <= 3 or self.rtp_packets_received % 50 == 0:
                page_debug(
                    f"pretone_rtp_recv stream={self.stream_id} packet={self.rtp_packets_received} "
                    f"local={socket_name(self.local_sock)} remote={addr[0]}:{addr[1]} bytes={len(packet)}"
                )
            try:
                self.learn_rtp_source(addr, packet)
            except Exception:
                pass
            current = livepaged.parse_rtp_payload(packet)
            if current:
                payload = current
        return payload

    def forward_rtp_payloads_until(self, deadline, debug_label="beep_rtp_recv"):
        while not self.stop_event.is_set():
            local_sock = self.local_sock
            if local_sock is None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ready, _, _ = select.select([local_sock], [], [], remaining)
            except Exception:
                break
            if not ready:
                break
            try:
                packet, addr = local_sock.recvfrom(4096)
            except Exception:
                break
            self.rtp_packets_received = int(getattr(self, "rtp_packets_received", 0) or 0) + 1
            if self.rtp_packets_received <= 3 or self.rtp_packets_received % 50 == 0:
                page_debug(
                    f"{debug_label} stream={self.stream_id} packet={self.rtp_packets_received} "
                    f"local={socket_name(self.local_sock)} remote={addr[0]}:{addr[1]} bytes={len(packet)}"
                )
            try:
                self.learn_rtp_source(addr, packet)
            except Exception:
                pass
            payload = livepaged.parse_rtp_payload(packet)
            if payload:
                self.forward_live_payload(payload)

    def run(self):
        live_audio_started = False
        try:
            try:
                self.preflight()
            except Exception as exc:
                page_debug(f"run_preflight_failed stream={self.stream_id} error={exc.__class__.__name__}: {exc}")
                self.setup_failed = True
                self.play_progress_tone(tone="reorder", duration=4.0)
                return
            threading.Thread(target=self.enable_livepage_tracking, daemon=True).start()
            self.play_pre_tones_with_silence()
            if self.end_requested.is_set():
                page_debug(f"run_end_during_pretone stream={self.stream_id}")
                return
            page_debug(f"run_beep_start stream={self.stream_id}")
            next_send = time.monotonic()
            for _ in range(25):
                if self.stop_event.is_set():
                    page_debug(f"run_stopped_during_beep stream={self.stream_id}")
                    return
                self.send_rtp(BEEP_FRAME, poll_source=False)
                next_send += 0.02
                self.forward_rtp_payloads_until(next_send)
            self.rtp_keepalive_payload = SILENCE_FRAME
            live_audio_started = True
            page_debug(f"run_live_audio_start stream={self.stream_id} keepalive=20ms")
            super().run()
        finally:
            if live_audio_started and self.post_tones:
                try:
                    self.play_post_tones()
                except Exception as exc:
                    page_debug(f"posttone_error stream={self.stream_id} error={exc.__class__.__name__}: {exc}")
            page_debug(f"run_cleanup stream={self.stream_id}")
            self.cleanup()

    def stop(self):
        self.request_end()
        thread = self.thread
        if thread is not None and thread.is_alive():
            return
        if self.pre_tone_active:
            while self.pre_tone_active and not self.cleaned_up:
                time.sleep(0.05)
            if not self.cleaned_up:
                self.cleanup()
            return
        if not self.cleaned_up:
            if self.pre_tone_completed and self.post_tones:
                try:
                    self.play_post_tones()
                except Exception as exc:
                    page_debug(f"posttone_stop_error stream={self.stream_id} error={exc.__class__.__name__}: {exc}")
            self.cleanup()

    def handle_external_stop_request(self):
        self.skip_post_tones = True
        self.end_requested.set()
        self.stop_event.set()
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

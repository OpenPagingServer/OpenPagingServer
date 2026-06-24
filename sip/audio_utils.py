import math
import os
import struct
import time
import socket
import select
import wave

ULAW_TO_LINEAR = []
for i in range(256):
    u_val = ~i & 0xFF
    sign = u_val & 0x80
    exponent = (u_val >> 4) & 0x07
    mantissa = u_val & 0x0F
    sample = (mantissa << 3) + 0x84
    sample <<= exponent
    sample -= 0x84
    ULAW_TO_LINEAR.append(-sample if sign else sample)

def detect_dtmf_hash_inband(payload):
    return False

def linear2ulaw(sample):
    bias = 0x84
    clip = 32635
    sign = 0
    if sample < 0:
        sample = -sample
        sign = 0x80
    if sample > clip:
        sample = clip
    sample += bias
    exponent = 7
    exp_mask = 0x4000
    while exponent > 0 and not (sample & exp_mask):
        exponent -= 1
        exp_mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF

def generate_tone(frequency, duration):
    phase = 0.0
    step = 2.0 * math.pi * frequency / 8000.0
    num_frames = int(duration * 50)
    for _ in range(num_frames):
        payload = bytearray()
        for _ in range(160):
            pcm = int(12000 * math.sin(phase))
            payload.append(linear2ulaw(pcm))
            phase += step
        yield bytes(payload)

def generate_wav(filepath):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.normpath(os.path.join(script_dir, filepath) if not os.path.isabs(filepath) else filepath)
    if not os.path.exists(full_path):
        print(f"[Warning] Audio file not found: {full_path}")
        return

    try:
        with wave.open(full_path, 'rb') as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()

            chunk_size = int(framerate * 0.02)

            while True:
                raw_frames = wf.readframes(chunk_size)
                if not raw_frames:
                    break

                actual_frames = len(raw_frames) // (sampwidth * channels)
                payload = bytearray()

                for i in range(160):
                    if actual_frames > 0:
                        orig_idx = int(i * actual_frames / 160)
                        if orig_idx >= actual_frames:
                            orig_idx = actual_frames - 1

                        byte_idx = orig_idx * sampwidth * channels

                        if sampwidth == 2:
                            sample = struct.unpack('<h', raw_frames[byte_idx:byte_idx+2])[0]
                        elif sampwidth == 1:
                            sample = (raw_frames[byte_idx] - 128) * 256
                        else:
                            sample = 0
                    else:
                        sample = 0

                    payload.append(linear2ulaw(sample))

                yield bytes(payload)
    except Exception as e:
        print(f"[Warning] Error reading WAV {full_path}: {e}")

def chain_generators(*generators):
    for gen in generators:
        if gen:
            yield from gen

class EchoRTPSession:
    def __init__(self, remote_ip, remote_port, generator=None, on_finish=None):
        self.remote_ip = remote_ip
        self.remote_port = remote_port
        self.on_finish = on_finish
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.local_port = self.sock.getsockname()[1]
        self.stopped = False
        self.seq = 0
        self.ts = 0
        self.ssrc = 0x12345678
        self.dtmf_event_flag = False
        self.last_dtmf_time = 0.0

    def start(self):
        import threading
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stopped = True
        try:
            self.sock.close()
        except:
            pass

    def _send_rtp(self, payload, pt=0):
        header = bytearray(12)
        header[0] = 0x80
        header[1] = pt & 0x7F
        struct.pack_into(">H", header, 2, self.seq)
        struct.pack_into(">I", header, 4, self.ts)
        struct.pack_into(">I", header, 8, self.ssrc)
        self.seq = (self.seq + 1) & 0xFFFF
        try:
            self.sock.sendto(header + payload, (self.remote_ip, self.remote_port))
        except:
            pass

    def _learn_source(self, addr, data=b""):
        if not getattr(self, "rtp_latching_enabled", False):
            return
        if not addr or len(addr) < 2:
            return
        source_ip = str(addr[0] or "").strip()
        try:
            source_port = int(addr[1] or 0)
        except Exception:
            source_port = 0
        packet_type = data[1] if len(data) > 1 else 0
        if (
            not source_ip
            or source_port <= 0
            or 192 <= packet_type <= 223
            or ((data[:1] or b"\x00")[0] >> 6) != 2
            or (self.remote_port > 0 and self.remote_port % 2 == 0 and source_port == self.remote_port + 1 and source_port % 2 == 1)
        ):
            return
        self.remote_ip = source_ip
        self.remote_port = source_port

    def _check_dtmf(self, data=None):
        is_dtmf = False
        if self.dtmf_event_flag:
            self.dtmf_event_flag = False
            is_dtmf = True

        if not is_dtmf and data:
            try:
                if len(data) >= 12:
                    pt = data[1] & 0x7F
                    if 96 <= pt <= 127:
                        cc = data[0] & 0x0F
                        ext = (data[0] & 0x10) >> 4
                        offset = 12 + cc * 4
                        if ext and len(data) >= offset + 4:
                            ext_len = struct.unpack_into(">H", data, offset + 2)[0]
                            offset += 4 + ext_len * 4
                        if len(data) >= offset + 1:
                            event = data[offset]
                            if event == 11:
                                is_dtmf = True
                    elif pt == 0:
                        cc = data[0] & 0x0F
                        ext = (data[0] & 0x10) >> 4
                        offset = 12 + cc * 4
                        if ext and len(data) >= offset + 4:
                            ext_len = struct.unpack_into(">H", data, offset + 2)[0]
                            offset += 4 + ext_len * 4
                        payload = data[offset:]
                        if detect_dtmf_hash_inband(payload):
                            is_dtmf = True
            except:
                pass

        if is_dtmf:
            now = time.time()
            if now - self.last_dtmf_time > 0.5:
                self.last_dtmf_time = now
                print("[EchoRTPSession] DTMF '#' detected, ending session.")
                return True
            self.last_dtmf_time = now
        return False

    def _play_intro(self):
        gen = generate_wav("./audio/echotest.wav")
        next_time = time.time()
        for chunk in gen:
            if self.stopped:
                return
            if self._check_dtmf():
                return

            self._send_rtp(chunk, pt=0)
            self.ts = (self.ts + len(chunk)) & 0xFFFFFFFF
            next_time += 0.02

            while not self.stopped:
                now = time.time()
                timeout = next_time - now
                if timeout <= 0:
                    break
                try:
                    r, _, _ = select.select([self.sock], [], [], timeout)
                    if r:
                        data, addr = self.sock.recvfrom(4096)
                        self._learn_source(addr, data)
                        if self._check_dtmf(data):
                            return
                except:
                    break

    def _echo_loop(self):
        while not self.stopped:
            if self._check_dtmf():
                return
            try:
                r, _, _ = select.select([self.sock], [], [], 0.05)
                if r:
                    data, addr = self.sock.recvfrom(4096)
                    self._learn_source(addr, data)
                    if self._check_dtmf(data):
                        return
                    pt = data[1] & 0x7F
                    if pt == 0:
                        cc = data[0] & 0x0F
                        ext = (data[0] & 0x10) >> 4
                        offset = 12 + cc * 4
                        if ext and len(data) >= offset + 4:
                            ext_len = struct.unpack_into(">H", data, offset + 2)[0]
                            offset += 4 + ext_len * 4
                        payload = data[offset:]
                        self._send_rtp(payload, pt=0)
                        self.ts = (self.ts + len(payload)) & 0xFFFFFFFF
            except Exception:
                pass

    def _play_outro(self):
        gen = generate_wav("./audio/echotestdone.wav")
        next_time = time.time()
        for chunk in gen:
            if self.stopped:
                return

            self._send_rtp(chunk, pt=0)
            self.ts = (self.ts + len(chunk)) & 0xFFFFFFFF
            next_time += 0.02

            while not self.stopped:
                now = time.time()
                timeout = next_time - now
                if timeout <= 0:
                    break
                try:
                    r, _, _ = select.select([self.sock], [], [], timeout)
                    if r:
                        data, addr = self.sock.recvfrom(4096)
                        if self._check_dtmf(data):
                            return
                except:
                    break

    def _run(self):
        self._play_intro()

        if not self.stopped:
            self._echo_loop()

        if not self.stopped:
            self._play_outro()

        if self.on_finish and not self.stopped:
            self.on_finish()

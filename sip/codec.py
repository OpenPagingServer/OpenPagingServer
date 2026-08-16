"""Synchronous, in-process SIP audio codec encoding.

Input is always 20 ms G.711 u-law frames (160 bytes @ 8 kHz mono). PCMU is a
pass-through, PCMA is a table translate, and G722/OPUS are encoded in-process
with PyAV (libav) so no subprocesses or reader threads are involved and
encode() returns output with minimal latency.
"""

import os

# G.722.1C (Siren14) bit rate: 24000, 32000 or 48000. Multicast listeners have
# no SDP, so this must match the phones' configured G722.1C bit rate
# (Yealink defaults to 24000).
try:
    G7221C_BIT_RATE = int(os.getenv("OPS_G7221C_BITRATE", "24000") or 24000)
except ValueError:
    G7221C_BIT_RATE = 24000
if G7221C_BIT_RATE not in (24000, 32000, 48000):
    G7221C_BIT_RATE = 24000
G7221C_SAMPLE_RATE = 32000
G7221C_FRAME_BYTES = G7221C_BIT_RATE // 50 // 8  # bytes / 20 ms

SIP_CODEC_PAYLOADS = {
    "PCMU": {"payload_type": 0, "sample_rate": 8000, "rtpmap": "PCMU/8000", "samples_per_frame": 160},
    "PCMA": {"payload_type": 8, "sample_rate": 8000, "rtpmap": "PCMA/8000", "samples_per_frame": 160},
    "G722": {"payload_type": 9, "sample_rate": 8000, "rtpmap": "G722/8000", "samples_per_frame": 160, "sample_rate_wire": 16000},
    "OPUS": {"payload_type": 111, "sample_rate": 48000, "rtpmap": "opus/48000/2", "samples_per_frame": 960, "frame_ms": 20},
    "G7221C": {"payload_type": 121, "sample_rate": 32000, "rtpmap": "G7221/32000", "samples_per_frame": 640, "sample_rate_wire": 32000, "fmtp": f"bitrate={G7221C_BIT_RATE}"},
    "G726-32": {"payload_type": 2, "sample_rate": 8000, "rtpmap": "G726-32/8000", "samples_per_frame": 160},
}

# Bytes of encoded audio produced per 20 ms input frame for constant-bitrate codecs.
G722_FRAME_BYTES = 160  # 64 kbit/s * 0.02 s / 8


def normalize_sip_codec_name(value):
    token = str(value or "").strip().upper()
    if token not in SIP_CODEC_PAYLOADS:
        token = token.replace(".", "")  # accept "G722.1C" for G7221C
    if token in ("G726", "G72632"):
        token = "G726-32"
    return token if token in SIP_CODEC_PAYLOADS else ""


def _build_ulaw_tables():
    """Build u-law -> PCM16 and u-law -> A-law lookup tables locally."""
    pcm = [0] * 256
    for i in range(256):
        u = ~i & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        magnitude = ((mantissa << 3) + 0x84) << exponent
        sample = magnitude - 0x84
        pcm[i] = -sample if sign else sample

    def _linear_to_alaw(sample):
        sign = 0x80 if sample >= 0 else 0
        if sample < 0:
            sample = -sample - 1
        if sample > 32767:
            sample = 32767
        if sample >= 256:
            exponent = 7
            mask = 0x4000
            while exponent > 1 and not (sample & mask):
                exponent -= 1
                mask >>= 1
            mantissa = (sample >> (exponent + 3)) & 0x0F
            alaw = (exponent << 4) | mantissa
        else:
            alaw = sample >> 4
        return (alaw | sign) ^ 0x55

    alaw_table = bytes(_linear_to_alaw(pcm[i]) for i in range(256))
    pcm_bytes = [int(v).to_bytes(2, "little", signed=True) for v in pcm]
    return pcm_bytes, alaw_table


_ULAW_TO_PCM16, ULAW_TO_ALAW_TABLE = _build_ulaw_tables()


def _linear_to_ulaw(sample):
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
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _build_alaw_to_ulaw_table():
    values = []
    for i in range(256):
        a = i ^ 0x55
        sign = a & 0x80
        exponent = (a >> 4) & 0x07
        mantissa = a & 0x0F
        if exponent:
            sample = ((mantissa << 4) + 0x108) << (exponent - 1)
        else:
            sample = (mantissa << 4) + 8
        if sign:
            sample = -sample
        values.append(_linear_to_ulaw(sample))
    return bytes(values)


ALAW_TO_ULAW_TABLE = _build_alaw_to_ulaw_table()


def _pcm16_to_ulaw_bytes(pcm):
    out = bytearray(len(pcm) // 2)
    for i in range(len(out)):
        sample = int.from_bytes(pcm[2 * i:2 * i + 2], "little", signed=True)
        out[i] = _linear_to_ulaw(sample)
    return bytes(out)


def _ulaw_to_pcm16_bytes(ulaw_frame):
    table = _ULAW_TO_PCM16
    return b"".join(table[b] for b in ulaw_frame)


class _AvEncoder:
    """Shared PyAV encoder plumbing: resample 8 kHz s16 mono to codec rate."""

    def __init__(self, av_codec_name, rate, options=None, bit_rate=None):
        import av
        from av.audio.fifo import AudioFifo
        from av.audio.resampler import AudioResampler

        self._av = av
        ctx = av.CodecContext.create(av_codec_name, "w")
        ctx.format = "s16"
        ctx.layout = "mono"
        ctx.sample_rate = rate
        if bit_rate:
            ctx.bit_rate = bit_rate
        if options:
            ctx.options = dict(options)
        ctx.open()
        self.ctx = ctx
        self.rate = rate
        self.resampler = AudioResampler(format="s16", layout="mono", rate=rate)
        self.fifo = AudioFifo()
        self._pts = 0
        # Codecs like Opus require fixed frame sizes; g722 accepts any.
        self.frame_size = int(ctx.frame_size or 0)

    def feed(self, pcm16_8k):
        """Feed 8 kHz s16 mono PCM bytes; return list of encoded packets (bytes)."""
        av = self._av
        samples = len(pcm16_8k) // 2
        frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
        frame.sample_rate = 8000
        frame.pts = self._pts
        self._pts += samples
        frame.planes[0].update(pcm16_8k)

        out = []
        for resampled in self.resampler.resample(frame):
            self.fifo.write(resampled)
        chunk = self.frame_size or self.fifo.samples
        while self.fifo.samples >= chunk and chunk:
            piece = self.fifo.read(chunk)
            for packet in self.ctx.encode(piece):
                out.append(bytes(packet))
        return out

    def close(self):
        try:
            self.ctx.close()
        except Exception:
            pass


class _G7221CEncoder:
    """Resample 8 kHz PCM to 32 kHz and encode with libg722_1 (Siren14)."""

    def __init__(self):
        import av
        from av.audio.fifo import AudioFifo
        from av.audio.resampler import AudioResampler
        from sip.g7221 import G7221Encoder

        self._av = av
        self.enc = G7221Encoder(bit_rate=G7221C_BIT_RATE, sample_rate=G7221C_SAMPLE_RATE)
        self.resampler = AudioResampler(format="s16", layout="mono", rate=G7221C_SAMPLE_RATE)
        self.fifo = AudioFifo()
        self._pts = 0
        self.frame_samples = G7221C_SAMPLE_RATE // 50  # 640

    def feed(self, pcm16_8k):
        av = self._av
        samples = len(pcm16_8k) // 2
        frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
        frame.sample_rate = 8000
        frame.pts = self._pts
        self._pts += samples
        frame.planes[0].update(pcm16_8k)
        for resampled in self.resampler.resample(frame):
            self.fifo.write(resampled)
        out = []
        while self.fifo.samples >= self.frame_samples:
            piece = self.fifo.read(self.frame_samples)
            pcm = bytes(piece.planes[0])[: piece.samples * 2]
            packet = self.enc.encode(pcm)
            if packet:
                out.append(packet)
        return out

    def close(self):
        try:
            self.enc.close()
        except Exception:
            pass


class _G726EncoderShim:
    """Adapt sip.g726.G726Encoder to the feed() -> [packets] interface."""

    def __init__(self):
        from sip.g726 import G726Encoder
        self.enc = G726Encoder()

    def feed(self, pcm16_8k):
        packet = self.enc.encode(pcm16_8k)
        return [packet] if packet else []

    def close(self):
        self.enc.close()


class SipCodecEncoder:
    def __init__(self, codec_name):
        self.codec_name = normalize_sip_codec_name(codec_name) or "PCMU"
        self.failed = False
        self._encoder = None
        self._g722_buffer = bytearray()
        self._opus_packets = []

        if self.codec_name in ("PCMU", "PCMA"):
            return
        try:
            if self.codec_name == "G722":
                self._encoder = _AvEncoder("adpcm_g722", 16000)
            elif self.codec_name == "OPUS":
                self._encoder = _AvEncoder(
                    "libopus", 48000, bit_rate=64000,
                    options={"application": "voip", "frame_duration": "20"},
                )
            elif self.codec_name == "G7221C":
                self._encoder = _G7221CEncoder()
            elif self.codec_name == "G726-32":
                self._encoder = _G726EncoderShim()
            else:
                self.failed = True
        except Exception as exc:
            print(f"sip codec encoder init failed codec={self.codec_name}: {exc}", flush=True)
            self.failed = True
            self._encoder = None

    def encode(self, ulaw_frame):
        frame = bytes(ulaw_frame or b"")[:160].ljust(160, b"\xff")
        if self.codec_name == "PCMU":
            return frame
        if self.codec_name == "PCMA":
            return frame.translate(ULAW_TO_ALAW_TABLE)
        if self.failed or self._encoder is None:
            return b""
        try:
            packets = self._encoder.feed(_ulaw_to_pcm16_bytes(frame))
        except Exception as exc:
            print(f"sip codec encode failed codec={self.codec_name}: {exc}", flush=True)
            self.failed = True
            return b""
        if self.codec_name == "G722":
            for packet in packets:
                self._g722_buffer.extend(packet)
            if len(self._g722_buffer) >= G722_FRAME_BYTES:
                out = bytes(self._g722_buffer[:G722_FRAME_BYTES])
                del self._g722_buffer[:G722_FRAME_BYTES]
                return out
            return b""
        # OPUS/G7221C: one packet per 20 ms frame once the resampler primes.
        self._opus_packets.extend(packets)
        if self._opus_packets:
            return self._opus_packets.pop(0)
        return b""

    def close(self):
        if self._encoder is not None:
            self._encoder.close()
            self._encoder = None


def encode_sip_rtp_payload(codec_name, payload, encoder_state=None):
    codec = normalize_sip_codec_name(codec_name) or "PCMU"
    encoder = encoder_state
    if encoder is None or getattr(encoder, "codec_name", "") != codec:
        if encoder is not None:
            try:
                encoder.close()
            except Exception:
                pass
        encoder = SipCodecEncoder(codec)
    return encoder.encode(payload), encoder


class _AvDecoder:
    """PyAV decoder: codec payload -> 8 kHz s16 mono PCM."""

    def __init__(self, av_codec_name, rate, channels=1):
        import av
        from av.audio.resampler import AudioResampler

        self._av = av
        ctx = av.CodecContext.create(av_codec_name, "r")
        ctx.format = "s16"
        ctx.layout = "mono" if channels == 1 else "stereo"
        ctx.sample_rate = rate
        self.ctx = ctx
        self.resampler = AudioResampler(format="s16", layout="mono", rate=8000)

    def feed(self, payload):
        av = self._av
        packet = av.Packet(bytes(payload))
        pcm = bytearray()
        for frame in self.ctx.decode(packet):
            for resampled in self.resampler.resample(frame):
                pcm.extend(bytes(resampled.planes[0])[: resampled.samples * 2])
        return bytes(pcm)

    def close(self):
        try:
            self.ctx.close()
        except Exception:
            pass


class _G7221CDecoder:
    """Decode Siren14 with libg722_1 and resample 32 kHz -> 8 kHz PCM."""

    def __init__(self):
        import av
        from av.audio.resampler import AudioResampler
        from sip.g7221 import G7221Decoder

        self._av = av
        self.dec = G7221Decoder(bit_rate=G7221C_BIT_RATE, sample_rate=G7221C_SAMPLE_RATE)
        self.resampler = AudioResampler(format="s16", layout="mono", rate=8000)
        self._pts = 0

    def feed(self, payload):
        av = self._av
        pcm32 = self.dec.decode(payload)
        if not pcm32:
            return b""
        samples = len(pcm32) // 2
        frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
        frame.sample_rate = G7221C_SAMPLE_RATE
        frame.pts = self._pts
        self._pts += samples
        frame.planes[0].update(pcm32)
        out = bytearray()
        for resampled in self.resampler.resample(frame):
            out.extend(bytes(resampled.planes[0])[: resampled.samples * 2])
        return bytes(out)

    def close(self):
        try:
            self.dec.close()
        except Exception:
            pass


class _G726DecoderShim:
    """Adapt sip.g726.G726Decoder to the feed() -> pcm16 interface."""

    def __init__(self):
        from sip.g726 import G726Decoder
        self.dec = G726Decoder()

    def feed(self, payload):
        return self.dec.decode(payload)

    def close(self):
        self.dec.close()


class SipCodecDecoder:
    """Decode inbound RTP payloads to 8 kHz u-law byte stream."""

    def __init__(self, codec_name):
        self.codec_name = normalize_sip_codec_name(codec_name) or "PCMU"
        self.failed = False
        self._decoder = None
        if self.codec_name in ("PCMU", "PCMA"):
            return
        try:
            if self.codec_name == "G722":
                self._decoder = _AvDecoder("adpcm_g722", 16000)
            elif self.codec_name == "OPUS":
                self._decoder = _AvDecoder("libopus", 48000, channels=2)
            elif self.codec_name == "G7221C":
                self._decoder = _G7221CDecoder()
            elif self.codec_name == "G726-32":
                self._decoder = _G726DecoderShim()
            else:
                self.failed = True
        except Exception as exc:
            print(f"sip codec decoder init failed codec={self.codec_name}: {exc}", flush=True)
            self.failed = True
            self._decoder = None

    def decode(self, payload):
        data = bytes(payload or b"")
        if not data:
            return b""
        if self.codec_name == "PCMU":
            return data
        if self.codec_name == "PCMA":
            return data.translate(ALAW_TO_ULAW_TABLE)
        if self.failed or self._decoder is None:
            return b""
        try:
            pcm = self._decoder.feed(data)
        except Exception:
            return b""
        if not pcm:
            return b""
        return _pcm16_to_ulaw_bytes(pcm)

    def close(self):
        if self._decoder is not None:
            self._decoder.close()
            self._decoder = None


def decode_sip_rtp_payload(codec_name, payload, decoder_state=None):
    codec = normalize_sip_codec_name(codec_name) or "PCMU"
    decoder = decoder_state
    if decoder is None or getattr(decoder, "codec_name", "") != codec:
        if decoder is not None:
            try:
                decoder.close()
            except Exception:
                pass
        decoder = SipCodecDecoder(codec)
    return decoder.decode(payload), decoder

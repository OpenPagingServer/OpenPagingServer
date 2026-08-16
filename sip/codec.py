import ctypes
import ctypes.util
import os

try:
    G7221C_BIT_RATE = int(os.getenv("OPS_G7221C_BITRATE", "24000") or 24000)
except ValueError:
    G7221C_BIT_RATE = 24000
if G7221C_BIT_RATE not in (24000, 32000, 48000):
    G7221C_BIT_RATE = 24000
G7221C_SAMPLE_RATE = 32000
G7221C_FRAME_BYTES = G7221C_BIT_RATE // 50 // 8

SIP_CODEC_PAYLOADS = {
    "PCMU": {"payload_type": 0, "sample_rate": 8000, "rtpmap": "PCMU/8000", "samples_per_frame": 160},
    "PCMA": {"payload_type": 8, "sample_rate": 8000, "rtpmap": "PCMA/8000", "samples_per_frame": 160},
    "G722": {"payload_type": 9, "sample_rate": 8000, "rtpmap": "G722/8000", "samples_per_frame": 160, "sample_rate_wire": 16000},
    "OPUS": {"payload_type": 111, "sample_rate": 48000, "rtpmap": "opus/48000/2", "samples_per_frame": 960, "frame_ms": 20},
    "G7221C": {"payload_type": 121, "sample_rate": 32000, "rtpmap": "G7221/32000", "samples_per_frame": 640, "sample_rate_wire": 32000, "fmtp": f"bitrate={G7221C_BIT_RATE}"},
    "G726-32": {"payload_type": 2, "sample_rate": 8000, "rtpmap": "G726-32/8000", "samples_per_frame": 160},
}

G722_FRAME_BYTES = 160


def normalize_sip_codec_name(value):
    token = str(value or "").strip().upper()
    if token not in SIP_CODEC_PAYLOADS:
        token = token.replace(".", "")
    if token in ("G726", "G72632"):
        token = "G726-32"
    return token if token in SIP_CODEC_PAYLOADS else ""


def _build_ulaw_tables():
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
        self.frame_size = int(ctx.frame_size or 0)

    def feed(self, pcm16_8k):
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

    def __init__(self):
        import av
        from av.audio.fifo import AudioFifo
        from av.audio.resampler import AudioResampler

        self._av = av
        self.enc = G7221Encoder(bit_rate=G7221C_BIT_RATE, sample_rate=G7221C_SAMPLE_RATE)
        self.resampler = AudioResampler(format="s16", layout="mono", rate=G7221C_SAMPLE_RATE)
        self.fifo = AudioFifo()
        self._pts = 0
        self.frame_samples = G7221C_SAMPLE_RATE // 50

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

    def __init__(self):
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

    def __init__(self):
        import av
        from av.audio.resampler import AudioResampler

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

    def __init__(self):
        self.dec = G726Decoder()

    def feed(self, payload):
        return self.dec.decode(payload)

    def close(self):
        self.dec.close()


class SipCodecDecoder:

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


G726_NIBBLE_LSB = str(os.getenv("OPS_G726_NIBBLE", "") or "").strip().lower() in ("lsb", "little", "aal2")

_QTAB = (-124, 80, 178, 246, 300, 349, 400)
_DQLNTAB = (-2048, 4, 135, 213, 273, 323, 373, 425,
            425, 373, 323, 273, 213, 135, 4, -2048)
_WITAB = (-12, 18, 41, 64, 112, 198, 355, 1122,
          1122, 355, 198, 112, 64, 41, 18, -12)
_FITAB = (0, 0, 0, 0x200, 0x200, 0x200, 0x600, 0xE00,
          0xE00, 0x600, 0x200, 0x200, 0x200, 0, 0, 0)


def _log2plus1(val):
    return val.bit_length()


def _to_short(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _fmult(an, srn):
    anmag = an if an > 0 else (-an) & 0x1FFF
    anexp = _log2plus1(anmag) - 6
    if anmag == 0:
        anmant = 32
    elif anexp >= 0:
        anmant = anmag >> anexp
    else:
        anmant = anmag << -anexp
    wanexp = anexp + ((srn >> 6) & 0xF) - 13
    wanmant = (anmant * (srn & 0o77) + 0x30) >> 4
    if wanexp >= 0:
        retval = (wanmant << wanexp) & 0x7FFF
    else:
        retval = wanmant >> -wanexp
    return -retval if (an ^ srn) < 0 else retval


class G726State:
    __slots__ = ("yl", "yu", "dms", "dml", "ap", "a", "b", "pk", "dq", "sr", "td")

    def __init__(self):
        self.yl = 34816
        self.yu = 544
        self.dms = 0
        self.dml = 0
        self.ap = 0
        self.a = [0, 0]
        self.pk = [0, 0]
        self.sr = [32, 32]
        self.b = [0] * 6
        self.dq = [32] * 6
        self.td = 0

    def _predictor_zero(self):
        b = self.b
        dq = self.dq
        sezi = 0
        for i in range(6):
            sezi += _fmult(_to_short(b[i]) >> 2, dq[i])
        return sezi

    def _predictor_pole(self):
        return (_fmult(_to_short(self.a[1]) >> 2, self.sr[1]) +
                _fmult(_to_short(self.a[0]) >> 2, self.sr[0]))

    def _step_size(self):
        if self.ap >= 256:
            return self.yu
        y = self.yl >> 6
        dif = self.yu - y
        al = self.ap >> 2
        if dif > 0:
            y += (dif * al) >> 6
        elif dif < 0:
            y += (dif * al + 0x3F) >> 6
        return y

    def _update(self, y, wi, fi, dq, sr, dqsez):
        pk0 = 1 if dqsez < 0 else 0
        mag = dq & 0x7FFF
        ylint = self.yl >> 15
        ylfrac = (self.yl >> 10) & 0x1F
        thr1 = (32 + ylfrac) << ylint
        thr2 = (31 << 10) if ylint > 9 else thr1
        dqthr = (thr2 + (thr2 >> 1)) >> 1
        if self.td == 0 or mag <= dqthr:
            tr = 0
        else:
            tr = 1

        self.yu = y + ((wi - y) >> 5)
        if self.yu < 544:
            self.yu = 544
        elif self.yu > 5120:
            self.yu = 5120
        self.yl += self.yu + ((-self.yl) >> 6)

        a2p = 0
        if tr == 1:
            self.a[0] = self.a[1] = 0
            for i in range(6):
                self.b[i] = 0
        else:
            pks1 = pk0 ^ self.pk[0]
            a2p = self.a[1] - (self.a[1] >> 7)
            if dqsez != 0:
                fa1 = self.a[0] if pks1 else -self.a[0]
                if fa1 < -8191:
                    a2p -= 0x100
                elif fa1 > 8191:
                    a2p += 0xFF
                else:
                    a2p += fa1 >> 5
                if pk0 ^ self.pk[1]:
                    if a2p <= -12160:
                        a2p = -12288
                    elif a2p >= 12416:
                        a2p = 12288
                    else:
                        a2p -= 0x80
                elif a2p <= -12416:
                    a2p = -12288
                elif a2p >= 12160:
                    a2p = 12288
                else:
                    a2p += 0x80
            self.a[1] = a2p
            self.a[0] -= self.a[0] >> 8
            if dqsez != 0:
                if pks1 == 0:
                    self.a[0] += 192
                else:
                    self.a[0] -= 192
            a1ul = 15360 - a2p
            if self.a[0] < -a1ul:
                self.a[0] = -a1ul
            elif self.a[0] > a1ul:
                self.a[0] = a1ul
            for i in range(6):
                self.b[i] -= self.b[i] >> 8
                if dq & 0x7FFF:
                    if (dq ^ self.dq[i]) >= 0:
                        self.b[i] += 128
                    else:
                        self.b[i] -= 128

        for i in range(5, 0, -1):
            self.dq[i] = self.dq[i - 1]
        if mag == 0:
            self.dq[0] = 0x20 if dq >= 0 else _to_short(0xFC20)
        else:
            exp = _log2plus1(mag)
            if dq >= 0:
                self.dq[0] = (exp << 6) + ((mag << 6) >> exp)
            else:
                self.dq[0] = (exp << 6) + ((mag << 6) >> exp) - 0x400

        self.sr[1] = self.sr[0]
        if sr == 0:
            self.sr[0] = 0x20
        elif sr > 0:
            exp = _log2plus1(sr)
            self.sr[0] = (exp << 6) + ((sr << 6) >> exp)
        elif sr > -32768:
            mag2 = -sr
            exp = _log2plus1(mag2)
            self.sr[0] = (exp << 6) + ((mag2 << 6) >> exp) - 0x400
        else:
            self.sr[0] = _to_short(0xFC20)

        self.pk[1] = self.pk[0]
        self.pk[0] = pk0

        if tr == 1:
            self.td = 0
        elif a2p < -11776:
            self.td = 1
        else:
            self.td = 0

        self.dms += (fi - self.dms) >> 5
        self.dml += ((fi << 2) - self.dml) >> 7
        if tr == 1:
            self.ap = 256
        elif y < 1536 or self.td == 1:
            self.ap += (0x200 - self.ap) >> 4
        elif abs((self.dms << 2) - self.dml) >= (self.dml >> 3):
            self.ap += (0x200 - self.ap) >> 4
        else:
            self.ap += (-self.ap) >> 4


def _quantize(d, y):
    dqm = abs(d)
    exp = _log2plus1(dqm >> 1)
    mant = ((dqm << 7) >> exp) & 0x7F if exp < 21 else 0x7F
    dl = (exp << 7) + mant
    dln = dl - (y >> 2)
    i = 0
    for threshold in _QTAB:
        if dln < threshold:
            break
        i += 1
    if d < 0:
        return 15 - i
    if i == 0:
        return 15
    return i


def _reconstruct(sign, dqln, y):
    dql = dqln + (y >> 2)
    if dql < 0:
        return -0x8000 if sign else 0
    dex = (dql >> 7) & 15
    dqt = 128 + (dql & 127)
    dq = (dqt << 7) >> (14 - dex)
    return dq - 0x8000 if sign else dq


def g726_encode_sample(state, sl):
    sl >>= 2
    sezi = state._predictor_zero()
    sez = sezi >> 1
    se = (sezi + state._predictor_pole()) >> 1
    d = sl - se
    y = state._step_size()
    i = _quantize(d, y)
    dq = _reconstruct(i & 8, _DQLNTAB[i], y)
    sr = se - (dq & 0x3FFF) if dq < 0 else se + dq
    dqsez = sr + sez - se
    state._update(y, _WITAB[i] << 5, _FITAB[i], dq, sr, dqsez)
    return i


def g726_decode_sample(state, code):
    i = code & 0x0F
    sezi = state._predictor_zero()
    sez = sezi >> 1
    se = (sezi + state._predictor_pole()) >> 1
    y = state._step_size()
    dq = _reconstruct(i & 8, _DQLNTAB[i], y)
    sr = se - (dq & 0x3FFF) if dq < 0 else se + dq
    dqsez = sr - se + sez
    state._update(y, _WITAB[i] << 5, _FITAB[i], dq, sr, dqsez)
    sample = sr << 2
    if sample > 32767:
        sample = 32767
    elif sample < -32768:
        sample = -32768
    return sample


class G726Encoder:

    def __init__(self):
        self.state = G726State()

    def encode(self, pcm16):
        samples = len(pcm16) // 2
        state = self.state
        out = bytearray((samples + 1) // 2)
        for n in range(samples):
            v = int.from_bytes(pcm16[2 * n:2 * n + 2], "little", signed=True)
            code = g726_encode_sample(state, v)
            if G726_NIBBLE_LSB:
                out[n >> 1] |= code << 4 if n & 1 else code
            else:
                out[n >> 1] |= code if n & 1 else code << 4
        return bytes(out)

    def close(self):
        pass


class G726Decoder:

    def __init__(self):
        self.state = G726State()

    def decode(self, data):
        data = bytes(data or b"")
        state = self.state
        out = bytearray(len(data) * 4)
        pos = 0
        for byte in data:
            if G726_NIBBLE_LSB:
                codes = (byte & 0x0F, byte >> 4)
            else:
                codes = (byte >> 4, byte & 0x0F)
            for code in codes:
                sample = g726_decode_sample(state, code)
                out[pos:pos + 2] = sample.to_bytes(2, "little", signed=True)
                pos += 2
        return bytes(out)

    def close(self):
        pass


_G7221_LIB_CANDIDATES = (
    os.environ.get("OPS_G7221_LIB", ""),
    "libg722_1.so.0",
    "libg722_1.so",
    "/usr/local/lib/libg722_1.so",
    "libg722_1.dll",
    "g722_1.dll",
)

_g7221_lib = None
_g7221_lib_error = None


def _load_g7221():
    global _g7221_lib, _g7221_lib_error
    if _g7221_lib is not None or _g7221_lib_error is not None:
        return _g7221_lib
    last_error = None
    names = [name for name in _G7221_LIB_CANDIDATES if name]
    found = ctypes.util.find_library("g722_1")
    if found:
        names.append(found)
    for name in names:
        try:
            lib = ctypes.CDLL(name)
        except OSError as exc:
            last_error = exc
            continue
        try:
            lib.g722_1_encode_init.restype = ctypes.c_void_p
            lib.g722_1_encode_init.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int)
            lib.g722_1_encode.restype = ctypes.c_int
            lib.g722_1_encode.argtypes = (
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8),
                ctypes.POINTER(ctypes.c_int16), ctypes.c_int,
            )
            lib.g722_1_decode_init.restype = ctypes.c_void_p
            lib.g722_1_decode_init.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int)
            lib.g722_1_decode.restype = ctypes.c_int
            lib.g722_1_decode.argtypes = (
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16),
                ctypes.POINTER(ctypes.c_uint8), ctypes.c_int,
            )
            for release in ("g722_1_encode_release", "g722_1_decode_release",
                            "g722_1_encode_free", "g722_1_decode_free"):
                if hasattr(lib, release):
                    getattr(lib, release).restype = ctypes.c_int
                    getattr(lib, release).argtypes = (ctypes.c_void_p,)
        except AttributeError as exc:
            last_error = exc
            continue
        _g7221_lib = lib
        return _g7221_lib
    _g7221_lib_error = last_error or OSError("libg722_1 not found")
    return None


def g7221_available():
    return _load_g7221() is not None


class G7221Encoder:

    def __init__(self, bit_rate=48000, sample_rate=32000):
        lib = _load_g7221()
        if lib is None:
            raise RuntimeError(f"libg722_1 unavailable: {_g7221_lib_error}")
        self._lib = lib
        self._state = lib.g722_1_encode_init(None, int(bit_rate), int(sample_rate))
        if not self._state:
            raise RuntimeError("g722_1_encode_init failed")
        self.samples_per_frame = sample_rate // 50
        self.bytes_per_frame = bit_rate // 50 // 8

    def encode(self, pcm16):
        samples = len(pcm16) // 2
        if samples == 0:
            return b""
        in_buf = (ctypes.c_int16 * samples).from_buffer_copy(pcm16)
        out_len = (samples // self.samples_per_frame + 1) * self.bytes_per_frame
        out_buf = (ctypes.c_uint8 * out_len)()
        n = self._lib.g722_1_encode(self._state, out_buf, in_buf, samples)
        if n <= 0:
            return b""
        return bytes(out_buf[:n])

    def close(self):
        if self._state:
            for name in ("g722_1_encode_release", "g722_1_encode_free"):
                fn = getattr(self._lib, name, None)
                if fn is not None:
                    try:
                        fn(self._state)
                    except Exception:
                        pass
                    if name.endswith("_free"):
                        break
            self._state = None


class G7221Decoder:

    def __init__(self, bit_rate=48000, sample_rate=32000):
        lib = _load_g7221()
        if lib is None:
            raise RuntimeError(f"libg722_1 unavailable: {_g7221_lib_error}")
        self._lib = lib
        self._state = lib.g722_1_decode_init(None, int(bit_rate), int(sample_rate))
        if not self._state:
            raise RuntimeError("g722_1_decode_init failed")
        self.samples_per_frame = sample_rate // 50
        self.bytes_per_frame = bit_rate // 50 // 8

    def decode(self, data):
        data = bytes(data or b"")
        if not data:
            return b""
        in_buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        out_len = (len(data) // self.bytes_per_frame + 1) * self.samples_per_frame
        out_buf = (ctypes.c_int16 * out_len)()
        n = self._lib.g722_1_decode(self._state, out_buf, in_buf, len(data))
        if n <= 0:
            return b""
        return ctypes.string_at(out_buf, n * 2)

    def close(self):
        if self._state:
            for name in ("g722_1_decode_release", "g722_1_decode_free"):
                fn = getattr(self._lib, name, None)
                if fn is not None:
                    try:
                        fn(self._state)
                    except Exception:
                        pass
                    if name.endswith("_free"):
                        break
            self._state = None

"""Pure-Python G.726-32 (a.k.a. G.721) ADPCM codec.

Port of the public-domain Sun Microsystems reference implementation of the
CCITT G.721 ADPCM algorithm (32 kbit/s, 4 bits per 8 kHz sample). ffmpeg's
G.726 encoder buffers 2048 samples (~256 ms), which is far too much latency
for live paging, so this runs the algorithm directly per 20 ms frame.

RTP packing follows RFC 3551 ("big-endian": first code word in the most
significant nibble of each octet). Set OPS_G726_NIBBLE=lsb for AAL2-style
little-endian packing if a device needs it.
"""

import os

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
    """Encode one 16-bit linear PCM sample to a 4-bit G.726-32 code."""
    sl >>= 2  # 14-bit dynamic range
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
    """Decode a 4-bit G.726-32 code to a 16-bit linear PCM sample."""
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
    """s16le mono 8 kHz PCM -> G.726-32 bytes (4 bits/sample)."""

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
    """G.726-32 bytes -> s16le mono 8 kHz PCM."""

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

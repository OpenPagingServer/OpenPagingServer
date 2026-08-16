"""ctypes bindings for libg722_1 (Polycom Siren7/Siren14, G.722.1 / Annex C).

ffmpeg has no G.722.1 encoder, so encoding uses Steve Underwood's libg722_1
(https://github.com/neutrino38/libg722_1). Build it on the server with
scripts/build-g7221.sh, or install it so that libg722_1.so is on the loader
path. All helpers here raise RuntimeError if the library is unavailable.
"""

import ctypes
import ctypes.util
import os

_LIB_CANDIDATES = (
    os.environ.get("OPS_G7221_LIB", ""),
    "libg722_1.so.0",
    "libg722_1.so",
    "/usr/local/lib/libg722_1.so",
    "libg722_1.dll",
    "g722_1.dll",
)

_lib = None
_lib_error = None


def _load():
    global _lib, _lib_error
    if _lib is not None or _lib_error is not None:
        return _lib
    last_error = None
    names = [name for name in _LIB_CANDIDATES if name]
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
        _lib = lib
        return _lib
    _lib_error = last_error or OSError("libg722_1 not found")
    return None


def g7221_available():
    return _load() is not None


class G7221Encoder:
    """Encode 16-bit PCM to G.722.1 (Annex C when sample_rate=32000)."""

    def __init__(self, bit_rate=48000, sample_rate=32000):
        lib = _load()
        if lib is None:
            raise RuntimeError(f"libg722_1 unavailable: {_lib_error}")
        self._lib = lib
        self._state = lib.g722_1_encode_init(None, int(bit_rate), int(sample_rate))
        if not self._state:
            raise RuntimeError("g722_1_encode_init failed")
        self.samples_per_frame = sample_rate // 50  # 20 ms
        self.bytes_per_frame = bit_rate // 50 // 8

    def encode(self, pcm16):
        """Encode s16le mono PCM at the encoder sample rate; len must be a multiple of samples_per_frame."""
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
    """Decode G.722.1 bitstream to 16-bit PCM at the decoder sample rate."""

    def __init__(self, bit_rate=48000, sample_rate=32000):
        lib = _load()
        if lib is None:
            raise RuntimeError(f"libg722_1 unavailable: {_lib_error}")
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

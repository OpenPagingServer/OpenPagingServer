#!/usr/bin/env bash
# Build and install libg722_1 (G.722.1 / Annex C "Siren14" encoder+decoder)
# needed for the G7221C codec. Run on the server (Debian/Ubuntu):
#   sudo apt-get install -y git build-essential autoconf automake libtool
#   sudo bash scripts/build-g7221.sh
set -euo pipefail

SRC_DIR="${1:-/usr/local/src/libg722_1}"

if [ ! -d "$SRC_DIR/.git" ]; then
    git clone https://github.com/neutrino38/libg722_1 "$SRC_DIR"
fi
cd "$SRC_DIR"

[ -x ./configure ] || ./autogen.sh
./configure

# The Makefile runs table-generator helper tools (./make_dct4_tables etc.)
# without building them first; compile them by hand.
for gen in src/make_*.c; do
    [ -e "$gen" ] || continue
    out="${gen%.c}"
    gcc -O2 -I src -I . -o "$out" "$gen" -lm
done

make -j"$(nproc)"
make install
ldconfig

python3 - <<'PY'
import ctypes
lib = ctypes.CDLL("libg722_1.so")
lib.g722_1_encode_init.restype = ctypes.c_void_p
state = lib.g722_1_encode_init(None, 48000, 32000)
assert state, "g722_1_encode_init failed"
print("libg722_1 OK: G.722.1C (32 kHz, 48 kbit/s) encoder available")
PY

#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="${1:-/usr/local/src/libg722_1}"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

install_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update
        $SUDO apt-get install -y git build-essential autoconf automake libtool
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y git gcc make autoconf automake libtool
    elif command -v yum >/dev/null 2>&1; then
        $SUDO yum install -y git gcc make autoconf automake libtool
    elif command -v pacman >/dev/null 2>&1; then
        $SUDO pacman -Sy --noconfirm git base-devel autoconf automake libtool
    elif command -v zypper >/dev/null 2>&1; then
        $SUDO zypper install -y git gcc make autoconf automake libtool
    elif command -v apk >/dev/null 2>&1; then
        $SUDO apk add git build-base autoconf automake libtool
    else
        echo "No supported package manager found; install git, a C toolchain, autoconf, automake and libtool manually." >&2
        exit 1
    fi
}

install_deps

if [ ! -d "$SRC_DIR/.git" ]; then
    git clone https://github.com/neutrino38/libg722_1 "$SRC_DIR"
fi
cd "$SRC_DIR"

[ -x ./configure ] || ./autogen.sh
./configure

for gen in src/make_*.c; do
    [ -e "$gen" ] || continue
    out="${gen%.c}"
    gcc -O2 -I src -I . -o "$out" "$gen" -lm
done

make -j"$(nproc)"
$SUDO make install
$SUDO ldconfig

python3 - <<'PY'
import ctypes
lib = ctypes.CDLL("libg722_1.so")
lib.g722_1_encode_init.restype = ctypes.c_void_p
state = lib.g722_1_encode_init(None, 48000, 32000)
assert state, "g722_1_encode_init failed"
print("libg722_1 OK: G.722.1C (32 kHz, 48 kbit/s) encoder available")
PY

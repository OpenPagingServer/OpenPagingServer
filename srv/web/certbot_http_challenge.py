#!/usr/bin/env python3

import os
import socket
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


CHALLENGE_ROOT = Path(os.getenv("OPS_CERTBOT_HTTP_PATH", "/var/lib/openpagingserver/acme-challenges"))
STOP_FILE = CHALLENGE_ROOT / ".server-stop"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass


def port_80_in_use():
    try:
        with socket.create_connection(("127.0.0.1", 80), timeout=1):
            return True
    except OSError:
        return False


def start():
    CHALLENGE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    try:
        STOP_FILE.unlink()
    except FileNotFoundError:
        pass
    if port_80_in_use():
        return 0
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=os.name != "nt",
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if port_80_in_use():
            return 0
        time.sleep(0.1)
    return 1


def serve():
    handler = lambda *args, **kwargs: QuietHandler(*args, directory=str(CHALLENGE_ROOT), **kwargs)
    try:
        server = ThreadingHTTPServer((os.getenv("WEB_HOST", "0.0.0.0"), 80), handler)
    except OSError:
        return 0
    server.timeout = 1
    while not STOP_FILE.exists():
        server.handle_request()
    server.server_close()
    return 0


def stop():
    CHALLENGE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    STOP_FILE.touch()
    return 0


def deploy():
    unit = str(os.getenv("OPS_SYSTEMD_UNIT", "openpagingserver.service") or "").strip()
    if unit and os.name != "nt":
        subprocess.run(["systemctl", "try-restart", unit], check=False, timeout=60)
    return 0


if __name__ == "__main__":
    actions = {"start": start, "serve": serve, "stop": stop, "deploy": deploy}
    action = actions.get(sys.argv[1] if len(sys.argv) > 1 else "")
    raise SystemExit(action() if action else 2)

import hashlib
import random
import time

from srv.web.app import *

BASIC_CAPTCHA_SESSION_KEY = "login_basic_captcha_hash"
BASIC_CAPTCHA_EXPIRES_KEY = "login_basic_captcha_expires"
BASIC_CAPTCHA_TTL_SECONDS = 300
CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _basic_captcha_hash(value):
    normalized = str(value or "").strip().lower()
    return hashlib.sha256((normalized + "|" + app.secret_key).encode()).hexdigest()


def _captcha_code(length=6):
    return "".join(secrets.choice(CAPTCHA_CHARS) for _ in range(length))


def _svg_noise(rng):
    elements = []
    for _ in range(16):
        x1 = rng.randint(0, 220)
        y1 = rng.randint(0, 70)
        x2 = rng.randint(0, 220)
        y2 = rng.randint(0, 70)
        color = rng.choice(["#90caf9", "#ffcc80", "#a5d6a7", "#ce93d8", "#b0bec5"])
        opacity = rng.uniform(0.18, 0.42)
        elements.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{rng.randint(1, 3)}" opacity="{opacity:.2f}" />'
        )
    for _ in range(18):
        cx = rng.randint(0, 220)
        cy = rng.randint(0, 70)
        radius = rng.randint(1, 4)
        color = rng.choice(["#1976d2", "#ef6c00", "#2e7d32", "#6a1b9a"])
        elements.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{color}" opacity="0.18" />')
    return "".join(elements)


def _captcha_svg(code):
    rng = random.SystemRandom()
    text_elements = []
    for idx, char in enumerate(code):
        x = 24 + idx * 29 + rng.randint(-2, 3)
        y = 45 + rng.randint(-5, 5)
        angle = rng.randint(-18, 18)
        font_size = rng.randint(29, 35)
        color = rng.choice(["#0d47a1", "#1b5e20", "#4a148c", "#bf360c"])
        text_elements.append(
            f'<text x="{x}" y="{y}" transform="rotate({angle} {x} {y})" '
            f'fill="{color}" font-size="{font_size}" font-family="Georgia, Times New Roman, serif" '
            f'font-weight="700">{h(char)}</text>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="70" viewBox="0 0 220 70" role="img" aria-label="CAPTCHA image">'
        '<rect width="220" height="70" rx="4" fill="#f7fbff" />'
        f'{_svg_noise(rng)}'
        '<path d="M5 47 C 48 28, 82 66, 128 38 S 184 22, 215 49" fill="none" stroke="#1565c0" stroke-width="2" opacity="0.28" />'
        f'{"".join(text_elements)}'
        '</svg>'
    )


def handle_request():
    try:
        data = settings()
    except Exception:
        data = {}
    provider = str(data.get("login_captcha_provider") or "disabled").strip().lower()
    if provider != "basic":
        abort(404)
    code = _captcha_code()
    session[BASIC_CAPTCHA_SESSION_KEY] = _basic_captcha_hash(code)
    session[BASIC_CAPTCHA_EXPIRES_KEY] = str(time.time() + BASIC_CAPTCHA_TTL_SECONDS)
    response = Response(_captcha_svg(code), mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response

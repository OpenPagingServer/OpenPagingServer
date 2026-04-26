#!/usr/bin/env python3
import io
import os
import re
import subprocess
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Optional

from flask import Flask, Response, request, send_file
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import HTTPException

app = Flask(__name__)

ASSETS_DIR = Path("/var/lib/openpagingserver/assets")
RESOLUTION_RE = re.compile(r"^(?P<w>\d{1,5})x(?P<h>\d{1,5})$")
HEX_COLOR_RE = re.compile(r"^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$")
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS

cache_lock = threading.Lock()
render_cache = OrderedDict()
inflight = {}
MAX_CACHE_ENTRIES = 512

def plain_error(status_code: int, message: str) -> Response:
    return Response(f"{message}\n", status=status_code, mimetype="text/plain; charset=utf-8")

@app.errorhandler(HTTPException)
def handle_http_exception(exc: HTTPException):
    message = exc.description if exc.description else exc.name
    return plain_error(exc.code or 500, str(message))

@app.errorhandler(Exception)
def handle_unexpected_exception(exc: Exception):
    return plain_error(500, "Internal server error")

def parse_resolution(value: str | None) -> tuple[int, int]:
    if not value:
        return 320, 240
    value = value.strip().lower()
    match = RESOLUTION_RE.fullmatch(value)
    if not match:
        raise ValueError("Invalid resolution. Use WIDTHxHEIGHT.")
    w = int(match.group("w"))
    h = int(match.group("h"))
    if w < 32 or h < 32 or w > 4096 or h > 4096:
        raise ValueError("Resolution out of range.")
    return w, h

def parse_color(value: str | None, default: str) -> tuple[int, int, int]:
    raw = (value if value is not None else default).strip()
    if not raw:
        raise ValueError("Invalid color value.")
    if HEX_COLOR_RE.fullmatch(raw):
        raw = "#" + raw
    try:
        color = ImageColor.getrgb(raw)
    except Exception:
        raise ValueError("Invalid color value.")
    if isinstance(color, tuple) and len(color) >= 3:
        return color[0], color[1], color[2]
    raise ValueError("Invalid color value.")

def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())

def which(cmd: str) -> Optional[str]:
    from shutil import which as _which
    return _which(cmd)

@lru_cache(maxsize=1)
def font_catalog() -> dict[str, list[str]]:
    catalog: dict[str, list[str]] = {}
    fc_list = which("fc-list")
    if fc_list:
        try:
            result = subprocess.run(
                [fc_list, ":", "file", "family"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                if ":" not in line:
                    continue
                file_part, family_part = line.split(":", 1)
                file_path = file_part.strip()
                if not file_path:
                    continue
                families = [f.strip() for f in family_part.split(",") if f.strip()]
                for family in families:
                    key = normalize_name(family)
                    catalog.setdefault(key, [])
                    if file_path not in catalog[key]:
                        catalog[key].append(file_path)
        except Exception:
            pass

    common_dirs = [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        str(Path.home() / ".fonts"),
        str(Path.home() / ".local/share/fonts"),
    ]
    for root in common_dirs:
        p = Path(root)
        if not p.exists():
            continue
        for ext in ("*.ttf", "*.otf", "*.ttc"):
            for file_path in p.rglob(ext):
                key = normalize_name(file_path.stem)
                catalog.setdefault(key, [])
                fp = str(file_path)
                if fp not in catalog[key]:
                    catalog[key].append(fp)
    return catalog

@lru_cache(maxsize=32)
def fc_match_font(name: str) -> Optional[str]:
    fc_match = which("fc-match")
    if not fc_match:
        return None
    try:
        result = subprocess.run(
            [fc_match, "-f", "%{file}\n", name],
            capture_output=True,
            text=True,
            check=False,
        )
        out = result.stdout.strip().splitlines()
        if out:
            path = out[0].strip()
            if path and Path(path).exists():
                return path
    except Exception:
        return None
    return None

def resolve_font_path(query: str | None) -> Optional[str]:
    catalog = font_catalog()
    candidates = []
    if query:
        candidates.append(query)
    candidates.extend(
        [
            "DejaVu Sans",
            "Arial",
            "Liberation Sans",
            "Noto Sans",
            "Sans",
        ]
    )

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue

        p = Path(candidate)
        if p.exists() and p.is_file():
            return str(p)

        matched = fc_match_font(candidate)
        if matched:
            return matched

        key = normalize_name(candidate)
        if key in catalog and catalog[key]:
            return catalog[key][0]

        for font_key, paths in catalog.items():
            if key in font_key or font_key in key:
                if paths:
                    return paths[0]

    return None

def file_info(path: Optional[str]) -> tuple[str, int, int]:
    if not path:
        return ("", 0, 0)
    p = Path(path)
    try:
        stat = p.stat()
        return (str(p), stat.st_mtime_ns, stat.st_size)
    except Exception:
        return (str(p), 0, 0)

def resolve_asset_path(symbol_name: str) -> Optional[Path]:
    if not symbol_name:
        return None
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(symbol_name).name
    if not safe_name:
        return None

    exact = ASSETS_DIR / safe_name
    if exact.is_file() and exact.suffix.lower() in ALLOWED_IMAGE_SUFFIXES:
        return exact

    stem = Path(safe_name).stem
    matches = []
    for candidate in ASSETS_DIR.iterdir():
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
            continue
        if candidate.stem == stem:
            matches.append(candidate)

    if matches:
        matches.sort()
        return matches[0]

    return None

def load_valid_image(path: Path) -> Image.Image:
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as img:
            return img.convert("RGBA")
    except UnidentifiedImageError:
        raise ValueError("Symbol is not a valid image.")
    except Exception:
        raise ValueError("Symbol is not a valid image.")

def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]

def wrap_paragraph(draw: ImageDraw.ImageDraw, paragraph: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    if paragraph == "":
        return [""]

    words = paragraph.split(" ")
    lines: list[str] = []
    current = ""

    for word in words:
        test = word if not current else current + " " + word
        if text_width(draw, test, font) <= max_width:
            current = test
            continue

        if current:
            lines.append(current)
            current = ""

        if text_width(draw, word, font) <= max_width:
            current = word
            continue

        chunk = ""
        for ch in word:
            test_chunk = chunk + ch
            if text_width(draw, test_chunk, font) <= max_width:
                chunk = test_chunk
            else:
                if chunk:
                    lines.append(chunk)
                chunk = ch
        current = chunk

    if current:
        lines.append(current)

    return lines

def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    paragraphs = text.splitlines() if text else [""]
    for idx, paragraph in enumerate(paragraphs):
        lines.extend(wrap_paragraph(draw, paragraph, font, max_width))
        if idx != len(paragraphs) - 1:
            lines.append("")
    return lines if lines else [""]

def choose_foreground(bg_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = bg_rgb
    luminance = (0.2126 * r) + (0.7152 * g) + (0.0722 * b)
    return (0, 0, 0) if luminance > 150 else (255, 255, 255)

def get_line_bbox(draw, line, font):
    if not line:
        ascent, descent = font.getmetrics()
        return 0, 0, 0, ascent + descent
    return draw.textbbox((0, 0), line, font=font)

def measure_text_block(draw, lines, font, spacing):
    max_w = 0
    total_h = 0
    line_metrics = []
    for line in lines:
        left, top, right, bottom = get_line_bbox(draw, line, font)
        w = right - left
        h = bottom - top
        max_w = max(max_w, w)
        total_h += h
        line_metrics.append((w, h, left, top))

    if lines:
        total_h += spacing * (len(lines) - 1)

    return max_w, total_h, line_metrics

def fit_text_advanced(draw, text, font_path, max_width, max_height, start_size):
    if start_size is None:
        size = max(16, min(max_height // 2, max_width // 4))
    else:
        size = max(8, start_size)

    best_fit = None

    while size >= 8:
        font = ImageFont.truetype(font_path, size=size)
        spacing = max(4, size // 6)
        lines = wrap_text(draw, text, font, max_width)
        tw, th, metrics = measure_text_block(draw, lines, font, spacing)
        
        if tw <= max_width and th <= max_height:
            return font, lines, spacing, tw, th, size, metrics
        
        if best_fit is None:
            best_fit = (font, lines, spacing, tw, th, size, metrics)
            
        size -= 2

    font = ImageFont.truetype(font_path, size=8)
    spacing = 4
    lines = wrap_text(draw, text, font, max_width)
    tw, th, metrics = measure_text_block(draw, lines, font, spacing)
    return font, lines, spacing, tw, th, 8, metrics

def choose_best_layout(work_w, work_h, pad, gap, text, font_path, start_size, symbol_img, symbol_resolution, draw):
    def calc_layout(direction):
        sym_img_copy = symbol_img.copy() if symbol_img else None
        
        if direction == "H":
            sym_max_w = (work_w - 2*pad - gap) // 2
            sym_max_h = work_h - 2*pad
        else:
            sym_max_w = work_w - 2*pad
            sym_max_h = (work_h - 2*pad - gap) // 2
            
        if sym_img_copy:
            if symbol_resolution:
                rw, rh = parse_resolution(symbol_resolution)
                sym_img_copy = sym_img_copy.resize((rw * 4, rh * 4), RESAMPLE)
            else:
                sym_img_copy.thumbnail((max(1, sym_max_w), max(1, sym_max_h)), RESAMPLE)
        
        sw, sh = sym_img_copy.size if sym_img_copy else (0,0)
        
        if direction == "H":
            text_avail_w = max(1, work_w - 2*pad - sw - gap) if sym_img_copy else max(1, work_w - 2*pad)
            text_avail_h = max(1, work_h - 2*pad)
        else:
            text_avail_w = max(1, work_w - 2*pad)
            text_avail_h = max(1, work_h - 2*pad - sh - gap) if sym_img_copy else max(1, work_h - 2*pad)
            
        font, lines, spacing, tw, th, size, metrics = fit_text_advanced(
            draw, text, font_path, text_avail_w, text_avail_h, start_size
        )
        
        return {
            "dir": direction,
            "sym_img": sym_img_copy,
            "sw": sw, "sh": sh,
            "font": font, "lines": lines, "spacing": spacing,
            "tw": tw, "th": th,
            "size": size,
            "metrics": metrics
        }

    if not symbol_img or not text:
        return calc_layout("H")
        
    layout_h = calc_layout("H")
    layout_v = calc_layout("V")
    
    if layout_h["size"] > layout_v["size"]:
        return layout_h
    elif layout_v["size"] > layout_h["size"]:
        return layout_v
    else:
        area_h = layout_h["sw"] * layout_h["sh"]
        area_v = layout_v["sw"] * layout_v["sh"]
        return layout_h if area_h >= area_v else layout_v

def render_thumbnail(
    width: int,
    height: int,
    text: str,
    bg_rgb: tuple[int, int, int],
    fg_rgb: tuple[int, int, int],
    font_path: str,
    start_size: Optional[int],
    symbol_name: Optional[str],
    symbol_resolution: Optional[str],
) -> Image.Image:
    scale = 4
    work_w = width * scale
    work_h = height * scale

    image = Image.new("RGBA", (work_w, work_h), bg_rgb + (255,))
    draw = ImageDraw.Draw(image)

    symbol_img = None
    if symbol_name:
        symbol_path = resolve_asset_path(symbol_name)
        if symbol_path:
            symbol_img = load_valid_image(symbol_path)
            bbox = symbol_img.getbbox()
            if bbox:
                symbol_img = symbol_img.crop(bbox)

    pad = max(16, int(min(work_w, work_h) * 0.08))
    gap = max(12, int(min(work_w, work_h) * 0.05))

    layout = choose_best_layout(work_w, work_h, pad, gap, text, font_path, start_size, symbol_img, symbol_resolution, draw)
    
    direction = layout["dir"]
    sym_img = layout["sym_img"]
    sw, sh = layout["sw"], layout["sh"]
    font = layout["font"]
    lines = layout["lines"]
    spacing = layout["spacing"]
    tw, th = layout["tw"], layout["th"]
    metrics = layout["metrics"]
    
    if not sym_img and not text:
        return image.resize((width, height), RESAMPLE).convert("RGB")
        
    if not sym_img:
        start_y = (work_h - th) // 2
        curr_y = start_y
        for line, (w, h, left, top) in zip(lines, metrics):
            start_x = (work_w - w) // 2
            if line:
                draw.text((start_x - left, curr_y - top), line, fill=fg_rgb, font=font)
            curr_y += h + spacing
            
    elif not text:
        start_x = (work_w - sw) // 2
        start_y = (work_h - sh) // 2
        image.paste(sym_img, (start_x, start_y), sym_img)
        
    else:
        if direction == "H":
            total_w = sw + gap + tw
            total_h = max(sh, th)
            
            start_x = (work_w - total_w) // 2
            start_y = (work_h - total_h) // 2
            
            sym_y = start_y + (total_h - sh) // 2
            image.paste(sym_img, (start_x, sym_y), sym_img)
            
            text_x_base = start_x + sw + gap
            curr_y = start_y + (total_h - th) // 2
            
            for line, (w, h, left, top) in zip(lines, metrics):
                line_x = text_x_base + (tw - w) // 2
                if line:
                    draw.text((line_x - left, curr_y - top), line, fill=fg_rgb, font=font)
                curr_y += h + spacing
        else:
            total_w = max(sw, tw)
            total_h = sh + gap + th
            
            start_x = (work_w - total_w) // 2
            start_y = (work_h - total_h) // 2
            
            sym_x = start_x + (total_w - sw) // 2
            image.paste(sym_img, (sym_x, start_y), sym_img)
            
            curr_y = start_y + sh + gap
            for line, (w, h, left, top) in zip(lines, metrics):
                line_x = start_x + (total_w - w) // 2
                if line:
                    draw.text((line_x - left, curr_y - top), line, fill=fg_rgb, font=font)
                curr_y += h + spacing

    return image.resize((width, height), RESAMPLE).convert("RGB")

def cache_key_for_request(
    width: int,
    height: int,
    text: str,
    bg_rgb: tuple[int, int, int],
    fg_rgb: tuple[int, int, int],
    font_query: str,
    start_size: Optional[int],
    symbol_name: Optional[str],
    symbol_resolution: Optional[str],
    font_path: Optional[str],
    symbol_path: Optional[Path],
) -> tuple:
    font_info = file_info(font_path)
    symbol_info = file_info(str(symbol_path)) if symbol_path else ("", 0, 0)
    return (
        width,
        height,
        text,
        bg_rgb,
        fg_rgb,
        font_query,
        start_size,
        symbol_name or "",
        symbol_resolution or "",
        font_info,
        symbol_info,
    )

def get_or_render(key: tuple, renderer):
    with cache_lock:
        cached = render_cache.get(key)
        if cached is not None:
            render_cache.move_to_end(key)
            return cached
        entry = inflight.get(key)
        if entry is None:
            entry = {"event": threading.Event(), "error": None, "result": None}
            inflight[key] = entry
            leader = True
        else:
            leader = False

    if not leader:
        entry["event"].wait()
        if entry["error"] is not None:
            raise entry["error"]
        return entry["result"]

    try:
        result = renderer()
        with cache_lock:
            render_cache[key] = result
            render_cache.move_to_end(key)
            while len(render_cache) > MAX_CACHE_ENTRIES:
                render_cache.popitem(last=False)
            entry["result"] = result
        return result
    except Exception as exc:
        with cache_lock:
            entry["error"] = exc
        raise
    finally:
        with cache_lock:
            inflight.pop(key, None)
            entry["event"].set()

@app.get("/thumb")
def thumb():
    try:
        text = request.args.get("text", "")
        width, height = parse_resolution(request.args.get("resolution") or request.args.get("res"))
        bg_rgb = parse_color(request.args.get("bg"), "000000")
        fg_param = request.args.get("fg") or request.args.get("color")
        fg_rgb = parse_color(fg_param, "") if fg_param else choose_foreground(bg_rgb)
        font_query = request.args.get("font") or "DejaVu Sans"
        size_param = request.args.get("size")
        start_size = int(size_param) if size_param is not None and size_param != "" else None
        symbol_name = request.args.get("symbol")
        symbol_resolution = request.args.get("symbolresolution") or request.args.get("symbolres")

        font_path = resolve_font_path(font_query)
        if not font_path:
            return plain_error(500, "No usable font found on the system.")

        symbol_path = None
        if symbol_name:
            symbol_path = resolve_asset_path(symbol_name)
            if symbol_path is None:
                return plain_error(404, "Symbol not found.")
            try:
                with Image.open(symbol_path) as probe:
                    probe.verify()
            except UnidentifiedImageError:
                return plain_error(400, "Symbol is not a valid image.")
            except Exception:
                return plain_error(400, "Symbol is not a valid image.")

        key = cache_key_for_request(
            width,
            height,
            text,
            bg_rgb,
            fg_rgb,
            font_query,
            start_size,
            symbol_name,
            symbol_resolution,
            font_path,
            symbol_path,
        )

        def renderer():
            image = render_thumbnail(
                width,
                height,
                text,
                bg_rgb,
                fg_rgb,
                font_path,
                start_size,
                symbol_name,
                symbol_resolution,
            )
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()

        png_bytes = get_or_render(key, renderer)
    except ValueError as exc:
        return plain_error(400, str(exc))
    except RuntimeError as exc:
        return plain_error(500, str(exc))

    response = send_file(io.BytesIO(png_bytes), mimetype="image/png", download_name="thumb.png")
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response

@app.get("/")
def index():
    return plain_error(404, "Not found")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "6975"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
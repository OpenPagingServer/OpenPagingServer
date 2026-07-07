import base64
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TTS_TOKEN_PREFIX = "%tts("
TTS_TOKEN_SUFFIX = ")"
TTS_VOICE_CACHE_SECONDS = 30.0
TTS_PREVIEW_TTL_SECONDS = 300.0
DEFAULT_PIPER_SAMPLE_RATE = 22050
GOOGLE_TTS_URL = "https://translate.google.com/translate_tts"
GOOGLE_TTS_CHUNK_LIMIT = 170
GOOGLE_TTS_TIMEOUT_SECONDS = 20
GOOGLE_TTS_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
GOOGLE_TTS_LANGUAGES = [
    ("ar", "Arabic"),
    ("bn", "Bengali"),
    ("cs", "Czech"),
    ("da", "Danish"),
    ("de", "German"),
    ("el", "Greek"),
    ("en", "English"),
    ("en-GB", "English (United Kingdom)"),
    ("en-US", "English (United States)"),
    ("es", "Spanish"),
    ("fi", "Finnish"),
    ("fil", "Filipino"),
    ("fr", "French"),
    ("he", "Hebrew"),
    ("hi", "Hindi"),
    ("hu", "Hungarian"),
    ("id", "Indonesian"),
    ("it", "Italian"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("ms", "Malay"),
    ("nl", "Dutch"),
    ("no", "Norwegian"),
    ("pl", "Polish"),
    ("pt", "Portuguese"),
    ("pt-BR", "Portuguese (Brazil)"),
    ("ro", "Romanian"),
    ("ru", "Russian"),
    ("sv", "Swedish"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("th", "Thai"),
    ("tr", "Turkish"),
    ("uk", "Ukrainian"),
    ("vi", "Vietnamese"),
    ("zh-CN", "Chinese (Simplified)"),
    ("zh-TW", "Chinese (Traditional)"),
]

_tts_voice_cache = {"expires": 0.0, "voices": []}
_tts_voice_cache_lock = threading.Lock()
_tts_preview_cache = {}
_tts_preview_cache_lock = threading.Lock()


def split_audio_entries(value):
    return [part.strip() for part in str(value or "").split(":") if part.strip()]


def join_audio_entries(entries):
    return ":".join(str(entry or "").strip() for entry in entries if str(entry or "").strip())


def _tts_b64encode(value):
    encoded = base64.urlsafe_b64encode(value).decode("ascii")
    return encoded.rstrip("=")


def _tts_b64decode(value):
    padded = str(value or "") + ("=" * ((4 - (len(str(value or "")) % 4)) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def normalize_tts_payload(payload):
    data = dict(payload or {})
    engine = str(data.get("engine") or "").strip().lower()
    voice = str(data.get("voice") or "").strip()
    text = str(data.get("text") or "")
    if engine not in {"festival", "swift", "piper", "google"} or not voice or not text.strip():
        return None
    normalized = {
        "engine": engine,
        "voice": voice,
        "voice_label": str(data.get("voice_label") or voice).strip() or voice,
        "text": text,
    }
    if engine == "piper":
        model_path = str(data.get("model_path") or "").strip()
        config_path = str(data.get("config_path") or "").strip()
        sample_rate = int(data.get("sample_rate") or 0)
        if model_path:
            normalized["model_path"] = model_path
        if config_path:
            normalized["config_path"] = config_path
        if sample_rate > 0:
            normalized["sample_rate"] = sample_rate
    return normalized


def encode_tts_token(payload):
    normalized = normalize_tts_payload(payload)
    if not normalized:
        raise ValueError("Invalid TTS payload")
    raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return TTS_TOKEN_PREFIX + _tts_b64encode(raw) + TTS_TOKEN_SUFFIX


def decode_tts_token(token):
    raw = str(token or "").strip()
    if not (raw.startswith(TTS_TOKEN_PREFIX) and raw.endswith(TTS_TOKEN_SUFFIX)):
        return None
    try:
        payload = json.loads(_tts_b64decode(raw[len(TTS_TOKEN_PREFIX):-len(TTS_TOKEN_SUFFIX)]).decode("utf-8"))
    except Exception:
        return None
    return normalize_tts_payload(payload)


def is_tts_token(token):
    return decode_tts_token(token) is not None


def tts_preview_text(text, limit=120):
    collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)].rstrip() + "..."


def _voice_id(engine, unique_value):
    digest = hashlib.sha1(f"{engine}\n{unique_value}".encode("utf-8", "ignore")).hexdigest()
    return f"{engine}-{digest[:12]}"


def _existing_dirs(*values):
    results = []
    seen = set()
    for value in values:
        if not value:
            continue
        for raw in str(value).split(os.pathsep):
            raw = raw.strip()
            if not raw:
                continue
            path = Path(raw).expanduser()
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            key = str(resolved).lower()
            if key in seen or not resolved.is_dir():
                continue
            seen.add(key)
            results.append(resolved)
    return results


def _swift_acknowledgements_path():
    candidates = [
        os.getenv("OPS_SWIFT_ACKNOWLEDGEMENTS", "").strip(),
        "/opt/swift/doc/acknowledgements",
    ]
    for candidate in candidates:
        path = Path(str(candidate or "").strip())
        if path.is_file():
            return path
    return None


def _swift_brand_name():
    path = _swift_acknowledgements_path()
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        for token in re.findall(r"\bC[A-Za-z]*l\b", text):
            return token
    return "Swift"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _festival_command():
    return shutil.which("festival")


def _festival_text2wave_command():
    return shutil.which("text2wave")


def _festival_voice_eval_expr(voice):
    token = str(voice or "").strip()
    if not token:
        return ""
    if token.startswith("voice_"):
        return f"({token})"
    return f"(voice_{token})"


def _write_festival_script(payload, output_target):
    voice = str(payload.get("voice") or "").strip()
    text = json.dumps(str(payload.get("text") or ""))
    destination = json.dumps(str(output_target))
    voice_expr = _festival_voice_eval_expr(voice)
    body = [
        voice_expr,
        f"(set! utt1 (Utterance Text {text}))",
        "(utt.synth utt1)",
        f"(utt.save.wave utt1 {destination} 'riff)",
    ]
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".scm", delete=False)
    try:
        handle.write("\n".join(line for line in body if line))
        handle.write("\n")
    finally:
        handle.close()
    return handle.name


def _festival_voices():
    command = _festival_command()
    if not command or platform.system() != "Linux":
        return []
    script_handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".scm", delete=False)
    try:
        script_handle.write("(print (voice.list))\n")
    finally:
        script_handle.close()
    try:
        result = subprocess.run(
            [command, "-b", script_handle.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except Exception:
        try:
            os.unlink(script_handle.name)
        except OSError:
            pass
        return []
    try:
        os.unlink(script_handle.name)
    except OSError:
        pass
    output = (result.stdout or b"").decode("utf-8", "ignore")
    group_match = re.search(r"\(([^()]*)\)", output.replace("\r", " ").replace("\n", " "))
    source = group_match.group(1) if group_match else output
    seen = set()
    voices = []
    for token in re.findall(r"[A-Za-z0-9_+\-]+", source):
        lowered = token.lower()
        if lowered in seen or lowered in {"nil", "festival", "speech", "system"} or token.isdigit():
            continue
        seen.add(lowered)
        voice_name = token[6:] if token.startswith("voice_") else token
        voices.append(
            {
                "id": _voice_id("festival", voice_name),
                "engine": "festival",
                "engine_label": "Festival",
                "voice": voice_name,
                "voice_label": voice_name,
                "display_name": voice_name,
            }
        )
    return voices


def _swift_command_candidates():
    return [
        os.getenv("OPS_SWIFT_BIN", "").strip(),
        shutil.which("swift") or "",
        "/usr/local/bin/swift",
        "/opt/swift/bin/swift",
        "/usr/bin/swift",
    ]


def _is_swift_tts_command(command):
    resolved_command = shutil.which(command) if command and not Path(command).is_absolute() else command
    if not resolved_command or not Path(resolved_command).exists():
        return False
    try:
        result = subprocess.run(
            [resolved_command, "-V"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except Exception:
        return False
    output = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "ignore")
    return bool(re.search(r"\bswift\b", output, re.IGNORECASE))


def _swift_commands():
    commands = []
    seen = set()
    for candidate in _swift_command_candidates():
        resolved = shutil.which(candidate) if candidate and not Path(candidate).is_absolute() else candidate
        resolved = str(resolved or "").strip()
        if not resolved:
            continue
        key = resolved.lower()
        if key in seen:
            continue
        seen.add(key)
        if _is_swift_tts_command(resolved):
            commands.append(resolved)
    return commands


def _swift_command():
    commands = _swift_commands()
    return commands[0] if commands else ""


def _file_starts_with_riff(path):
    try:
        with Path(path).open("rb") as handle:
            return handle.read(4) == b"RIFF"
    except OSError:
        return False


def _synthesize_swift_with_command(command, payload, output_path):
    process = subprocess.Popen(
        [
            command,
            "-n",
            str(payload.get("voice") or ""),
            str(payload.get("text") or ""),
            "-o",
            str(output_path),
            "-p",
            "audio/output-format=riff",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process.communicate(timeout=60), process.returncode


def _swift_voice_dirs():
    return _existing_dirs(
        os.getenv("OPS_SWIFT_VOICES_DIR", ""),
        "/opt/swift/voices",
        "/usr/local/share/swift/voices",
    )


def _swift_voices():
    if platform.system() != "Linux":
        return []
    commands = _swift_commands()
    if not commands:
        return []
    seen = set()
    voices = []
    brand_name = _swift_brand_name()
    for root in _swift_voice_dirs():
        try:
            children = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda item: item.name.lower())
        except OSError:
            continue
        for path in children:
            key = path.name.lower()
            if key in seen:
                continue
            seen.add(key)
            voices.append(
                {
                    "id": _voice_id("swift", path.name),
                    "engine": "swift",
                    "engine_label": brand_name,
                    "voice": path.name,
                    "voice_label": f"{brand_name} {path.name}",
                    "display_name": f"{brand_name} {path.name}",
                }
            )
    return voices


def _piper_supported():
    return importlib.util.find_spec("piper") is not None


def _piper_model_dirs():
    return _existing_dirs(
        os.getenv("OPS_PIPER_DATA_DIR", ""),
        os.getenv("PIPER_DATA_DIR", ""),
        str(BASE_DIR / "piper"),
        str(BASE_DIR / "voices" / "piper"),
        str(Path.home() / ".local" / "share" / "piper"),
        "/usr/share/piper",
        "/opt/piper",
        "/var/lib/openpagingserver/piper",
    )


def _piper_voice_entry(model_path):
    model_path = Path(model_path)
    config_path = model_path.with_suffix(model_path.suffix + ".json")
    config = _read_json(config_path) if config_path.is_file() else {}
    sample_rate = int(
        ((config.get("audio") or {}).get("sample_rate"))
        or config.get("sample_rate")
        or DEFAULT_PIPER_SAMPLE_RATE
    )
    label = model_path.stem
    return {
        "id": _voice_id("piper", str(model_path.resolve())),
        "engine": "piper",
        "engine_label": "Piper",
        "voice": label,
        "voice_label": label,
        "display_name": label,
        "model_path": str(model_path.resolve()),
        "config_path": str(config_path.resolve()) if config_path.is_file() else "",
        "sample_rate": sample_rate,
    }


def _piper_voices():
    if not _piper_supported():
        return []
    seen = set()
    voices = []
    for root in _piper_model_dirs():
        try:
            models = sorted(root.rglob("*.onnx"), key=lambda item: str(item).lower())
        except OSError:
            continue
        for model_path in models:
            try:
                resolved = model_path.resolve()
            except OSError:
                resolved = model_path
            key = str(resolved).lower()
            if key in seen or not resolved.is_file():
                continue
            seen.add(key)
            voices.append(_piper_voice_entry(resolved))
    return voices


def _google_voices():
    voices = []
    for code, label in GOOGLE_TTS_LANGUAGES:
        google_label = f"Google {label}"
        voices.append(
            {
                "id": _voice_id("google", code),
                "engine": "google",
                "engine_label": "Google",
                "voice": code,
                "voice_label": google_label,
                "display_name": google_label,
            }
        )
    return voices


def available_tts_voices(force_refresh=False):
    now = time.time()
    with _tts_voice_cache_lock:
        if not force_refresh and _tts_voice_cache["expires"] > now:
            return list(_tts_voice_cache["voices"])
    voices = _swift_voices() + _festival_voices() + _piper_voices() + _google_voices()
    engine_order = {"swift": 0, "festival": 1, "piper": 2, "google": 3}
    voices = sorted(
        voices,
        key=lambda item: (
            engine_order.get(str(item.get("engine") or "").strip().lower(), 99),
            str(item.get("display_name") or item.get("voice_label") or item.get("voice") or "").lower(),
        ),
    )
    with _tts_voice_cache_lock:
        _tts_voice_cache["voices"] = list(voices)
        _tts_voice_cache["expires"] = now + TTS_VOICE_CACHE_SECONDS
    return voices


def available_tts_voice_map(force_refresh=False):
    return {voice["id"]: voice for voice in available_tts_voices(force_refresh=force_refresh)}


def tts_payload_from_voice_id(voice_id, text):
    voice = available_tts_voice_map().get(str(voice_id or "").strip())
    if not voice:
        raise RuntimeError("Selected TTS voice is not available.")
    return _tts_payload_from_voice_entry(voice, text)


def _tts_payload_from_voice_entry(voice, text):
    payload = {
        "engine": voice.get("engine"),
        "voice": voice.get("voice"),
        "voice_label": voice.get("voice_label") or voice.get("display_name") or voice.get("voice"),
        "text": str(text or ""),
    }
    if voice.get("engine") == "piper":
        payload["model_path"] = voice.get("model_path") or ""
        payload["config_path"] = voice.get("config_path") or ""
        payload["sample_rate"] = int(voice.get("sample_rate") or DEFAULT_PIPER_SAMPLE_RATE)
    normalized = normalize_tts_payload(payload)
    if not normalized:
        raise RuntimeError("Invalid TTS payload.")
    return normalized


def _google_local_fallback_voice():
    local_voices = [voice for voice in available_tts_voices(force_refresh=True) if str(voice.get("engine") or "").strip().lower() != "google"]
    swift_voices = [voice for voice in local_voices if str(voice.get("engine") or "").strip().lower() == "swift"]
    if swift_voices:
        swift_voices.sort(key=lambda item: (str(item.get("display_name") or item.get("voice_label") or item.get("voice") or "").lower(), str(item.get("voice") or "").lower()))
        return swift_voices[0]
    festival_voices = [voice for voice in local_voices if str(voice.get("engine") or "").strip().lower() == "festival"]
    if not festival_voices:
        return None
    for preferred in ("kal", "kal_diphone"):
        for voice in festival_voices:
            if str(voice.get("voice") or "").strip().lower() == preferred:
                return voice
    for voice in festival_voices:
        if "kal" in str(voice.get("voice") or "").strip().lower():
            return voice
    festival_voices.sort(key=lambda item: (str(item.get("display_name") or item.get("voice_label") or item.get("voice") or "").lower(), str(item.get("voice") or "").lower()))
    return festival_voices[0]


def _google_local_fallback_payload(payload):
    normalized = normalize_tts_payload(payload)
    if not normalized:
        raise RuntimeError("Invalid TTS payload.")
    fallback_voice = _google_local_fallback_voice()
    if not fallback_voice:
        raise RuntimeError("Google TTS failed and no local fallback voice is available.")
    return _tts_payload_from_voice_entry(fallback_voice, normalized.get("text") or "")


def tts_voice_id_for_payload(payload, voices=None):
    normalized = normalize_tts_payload(payload)
    if not normalized:
        return ""
    voices = voices if voices is not None else available_tts_voices()
    engine = normalized.get("engine")
    for voice in voices:
        if voice.get("engine") != engine:
            continue
        if engine == "piper":
            left = str(voice.get("model_path") or "").strip().lower()
            right = str(normalized.get("model_path") or "").strip().lower()
            if left and right and left == right:
                return voice.get("id") or ""
        elif str(voice.get("voice") or "").strip() == str(normalized.get("voice") or "").strip():
            return voice.get("id") or ""
    return ""


def _purge_tts_preview_cache(now=None):
    moment = float(now if now is not None else time.time())
    expired = [key for key, value in _tts_preview_cache.items() if float(value.get("expires") or 0) <= moment]
    for key in expired:
        _tts_preview_cache.pop(key, None)


def store_tts_preview_payload(payload):
    normalized = normalize_tts_payload(payload)
    if not normalized:
        raise RuntimeError("Invalid TTS payload.")
    preview_id = uuid.uuid4().hex
    with _tts_preview_cache_lock:
        _purge_tts_preview_cache()
        _tts_preview_cache[preview_id] = {
            "payload": normalized,
            "expires": time.time() + TTS_PREVIEW_TTL_SECONDS,
        }
    return preview_id


def store_tts_preview_file(payload):
    normalized = normalize_tts_payload(payload)
    if not normalized:
        raise RuntimeError("Invalid TTS payload.")
    preview_path = synthesize_tts_to_file(normalized)
    preview_id = uuid.uuid4().hex
    with _tts_preview_cache_lock:
        _purge_tts_preview_cache()
        _tts_preview_cache[preview_id] = {
            "payload": normalized,
            "file_path": str(preview_path),
            "expires": time.time() + TTS_PREVIEW_TTL_SECONDS,
        }
    return preview_id


def get_tts_preview_payload(preview_id):
    key = str(preview_id or "").strip()
    if not key:
        return None
    with _tts_preview_cache_lock:
        _purge_tts_preview_cache()
        entry = _tts_preview_cache.get(key) or {}
        payload = entry.get("payload")
    return normalize_tts_payload(payload)


def get_tts_preview_file(preview_id):
    key = str(preview_id or "").strip()
    if not key:
        return "", None
    with _tts_preview_cache_lock:
        _purge_tts_preview_cache()
        entry = _tts_preview_cache.get(key) or {}
        payload = normalize_tts_payload(entry.get("payload"))
        file_path = str(entry.get("file_path") or "").strip()
    if not file_path:
        return "", payload
    return file_path, payload


def ffmpeg_input_args_for_tts_source(source):
    source_format = str((source or {}).get("input_format") or "").strip().lower()
    if source_format == "wav":
        return ["-i", "pipe:0"]
    if source_format == "s16le":
        sample_rate = int((source or {}).get("sample_rate") or DEFAULT_PIPER_SAMPLE_RATE)
        return ["-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0"]
    if source_format == "file":
        return ["-i", str((source or {}).get("file_path") or "")]
    raise RuntimeError("Unsupported TTS source format.")


def cleanup_tts_source(source):
    process = (source or {}).get("process")
    if process is not None:
        try:
            if process.stdout is not None:
                process.stdout.close()
        except Exception:
            pass
        try:
            if process.stderr is not None:
                process.stderr.close()
        except Exception:
            pass
        try:
            if process.poll() is None:
                process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=1)
        except Exception:
            pass
    cleanup = (source or {}).get("cleanup")
    if callable(cleanup):
        try:
            cleanup()
        except Exception:
            pass


def _google_tts_chunks(text, limit=GOOGLE_TTS_CHUNK_LIMIT):
    remaining = re.sub(r"\s+", " ", str(text or "")).strip()
    chunks = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit + 1]
        split_at = max(window.rfind(marker) for marker in (" ", ".", ",", "!", "?", ";", ":"))
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


def _google_tts_request(chunk, language_code, index, total):
    params = urllib.parse.urlencode(
        {
            "ie": "UTF-8",
            "client": "tw-ob",
            "q": chunk,
            "tl": language_code,
            "total": str(total),
            "idx": str(index),
            "textlen": str(len(chunk)),
        }
    )
    request = urllib.request.Request(
        GOOGLE_TTS_URL + "?" + params,
        headers={
            "User-Agent": GOOGLE_TTS_USER_AGENT,
            "Referer": "https://translate.google.com/",
            "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.1",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=GOOGLE_TTS_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", "ignore").strip()
        raise RuntimeError(message or f"Google TTS returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Google TTS request failed: {exc.reason}") from exc


def _download_google_tts_mp3(payload, output_path=None):
    language_code = str(payload.get("voice") or "").strip()
    if not language_code:
        raise RuntimeError("Google TTS language is missing.")
    chunks = _google_tts_chunks(payload.get("text"))
    if not chunks:
        raise RuntimeError("Google TTS text is empty.")
    if output_path:
        output = Path(output_path)
    else:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        output = Path(handle.name)
        handle.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(output, "wb") as handle:
            total = len(chunks)
            for index, chunk in enumerate(chunks):
                audio_bytes = _google_tts_request(chunk, language_code, index, total)
                if not audio_bytes:
                    raise RuntimeError("Google TTS returned an empty audio response.")
                handle.write(audio_bytes)
    except Exception:
        try:
            output.unlink()
        except OSError:
            pass
        raise
    return str(output)


def _ffmpeg_convert_to_wav(input_path, output_path):
    result = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-y", "-i", str(input_path), str(output_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not Path(output_path).is_file():
        message = ((result.stderr or b"") + (result.stdout or b"")).decode("utf-8", "ignore").strip()
        raise RuntimeError(message or "ffmpeg could not convert the generated audio.")


def iter_tts_ffmpeg_chunks(payload, ffmpeg_output_args, chunk_size=None, pad_byte=b"\x00"):
    source = None
    ffmpeg = None
    fallback_path = ""
    read_size = int(chunk_size or 4096)
    try:
        try:
            source = spawn_tts_source(payload)
            ffmpeg = subprocess.Popen(
                ["ffmpeg", "-v", "quiet", *ffmpeg_input_args_for_tts_source(source), *list(ffmpeg_output_args or [])],
                stdin=(source.get("process").stdout if source.get("process") is not None else None),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            fallback_path = synthesize_tts_to_file(payload)
            ffmpeg = subprocess.Popen(
                ["ffmpeg", "-v", "quiet", "-i", fallback_path, *list(ffmpeg_output_args or [])],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        while True:
            chunk = ffmpeg.stdout.read(read_size)
            if not chunk:
                break
            if chunk_size and len(chunk) < chunk_size:
                yield chunk.ljust(chunk_size, pad_byte)
            else:
                yield chunk
    finally:
        if ffmpeg is not None:
            try:
                if ffmpeg.stdout is not None:
                    ffmpeg.stdout.close()
            except Exception:
                pass
            try:
                ffmpeg.wait(timeout=1)
            except Exception:
                try:
                    ffmpeg.kill()
                except Exception:
                    pass
        if source is not None:
            cleanup_tts_source(source)
        if fallback_path:
            try:
                os.unlink(fallback_path)
            except OSError:
                pass


def _spawn_festival_tts_source(payload):
    text2wave = _festival_text2wave_command()
    if text2wave:
        voice_expr = _festival_voice_eval_expr(payload.get("voice"))
        process = subprocess.Popen(
            [text2wave, "-eval", voice_expr] if voice_expr else [text2wave],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdin is not None:
            process.stdin.write(str(payload.get("text") or "").encode("utf-8"))
            process.stdin.close()
        return {
            "process": process,
            "input_format": "wav",
            "sample_rate": 0,
            "cleanup": None,
        }
    command = _festival_command()
    if not command:
        raise RuntimeError("Festival is not installed.")
    script_path = _write_festival_script(payload, "-")
    try:
        process = subprocess.Popen(
            [command, "-b", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception:
        try:
            os.unlink(script_path)
        except OSError:
            pass
        raise
    return {
        "process": process,
        "input_format": "wav",
        "sample_rate": 0,
        "cleanup": lambda: os.unlink(script_path) if os.path.exists(script_path) else None,
    }


def _spawn_swift_tts_source(payload):
    wav_path = synthesize_tts_to_file(payload)
    return {
        "process": None,
        "input_format": "file",
        "file_path": wav_path,
        "cleanup": lambda: os.unlink(wav_path) if os.path.exists(wav_path) else None,
    }


def _spawn_piper_tts_source(payload):
    if not _piper_supported():
        raise RuntimeError("Piper is not installed.")
    model_path = Path(str(payload.get("model_path") or "").strip())
    if not model_path.is_file():
        raise RuntimeError("Piper voice model is not available.")
    process = subprocess.Popen(
        [sys.executable, "-m", "piper", "-m", str(model_path), "--output-raw", "--", str(payload.get("text") or "")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "process": process,
        "input_format": "s16le",
        "sample_rate": int(payload.get("sample_rate") or DEFAULT_PIPER_SAMPLE_RATE),
        "cleanup": None,
    }


def _spawn_google_tts_source(payload):
    try:
        mp3_path = _download_google_tts_mp3(payload)
    except Exception:
        return spawn_tts_source(_google_local_fallback_payload(payload))
    return {
        "process": None,
        "input_format": "file",
        "file_path": mp3_path,
        "cleanup": lambda: os.unlink(mp3_path) if os.path.exists(mp3_path) else None,
    }


def spawn_tts_source(payload):
    normalized = normalize_tts_payload(payload)
    if not normalized:
        raise RuntimeError("Invalid TTS payload.")
    engine = normalized.get("engine")
    if engine == "festival":
        return _spawn_festival_tts_source(normalized)
    if engine == "swift":
        return _spawn_swift_tts_source(normalized)
    if engine == "piper":
        return _spawn_piper_tts_source(normalized)
    if engine == "google":
        return _spawn_google_tts_source(normalized)
    raise RuntimeError("Unsupported TTS engine.")


def _synthesize_festival_to_file(payload, output_path):
    text2wave = _festival_text2wave_command()
    if text2wave:
        voice_expr = _festival_voice_eval_expr(payload.get("voice"))
        command = [text2wave, "-o", str(output_path)]
        if voice_expr:
            command.extend(["-eval", voice_expr])
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_data, stderr_data = process.communicate(str(payload.get("text") or "").encode("utf-8"), timeout=60)
        if process.returncode != 0 or not Path(output_path).is_file():
            message = ((stderr_data or b"") + (stdout_data or b"")).decode("utf-8", "ignore").strip()
            raise RuntimeError(message or "Festival could not render the selected voice.")
        return
    command = _festival_command()
    if not command:
        raise RuntimeError("Festival is not installed.")
    script_path = _write_festival_script(payload, output_path)
    try:
        result = subprocess.run(
            [command, "-b", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
    if result.returncode != 0 or not Path(output_path).is_file():
        raise RuntimeError("Festival could not render the selected voice.")


def _synthesize_swift_to_file(payload, output_path):
    commands = _swift_commands()
    if not commands:
        raise RuntimeError(f"{_swift_brand_name()} Swift is not installed.")
    last_message = ""
    for command in commands:
        temp_output = str(output_path) + ".swift-tmp"
        try:
            try:
                Path(temp_output).unlink()
            except OSError:
                pass
            (stdout_data, stderr_data), return_code = _synthesize_swift_with_command(command, payload, temp_output)
            if return_code != 0 or not Path(temp_output).is_file():
                last_message = ((stderr_data or b"") + (stdout_data or b"")).decode("utf-8", "ignore").strip()
                continue
            if _file_starts_with_riff(temp_output):
                Path(temp_output).replace(output_path)
                return
            _ffmpeg_convert_to_wav(temp_output, output_path)
            if Path(output_path).is_file() and _file_starts_with_riff(output_path):
                return
        except Exception as exc:
            last_message = str(exc)
        finally:
            try:
                Path(temp_output).unlink()
            except OSError:
                pass
    raise RuntimeError(last_message or f"{_swift_brand_name()} Swift could not render the selected voice.")


def _synthesize_piper_to_file(payload, output_path):
    if not _piper_supported():
        raise RuntimeError("Piper is not installed.")
    model_path = Path(str(payload.get("model_path") or "").strip())
    if not model_path.is_file():
        raise RuntimeError("Piper voice model is not available.")
    result = subprocess.run(
        [sys.executable, "-m", "piper", "-m", str(model_path), "-f", str(output_path), "--", str(payload.get("text") or "")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not Path(output_path).is_file():
        message = ((result.stderr or b"") + (result.stdout or b"")).decode("utf-8", "ignore").strip()
        raise RuntimeError(message or "Piper could not render the selected voice.")


def _synthesize_google_to_file(payload, output_path):
    mp3_path = ""
    try:
        mp3_path = _download_google_tts_mp3(payload)
        _ffmpeg_convert_to_wav(mp3_path, output_path)
    except Exception:
        return synthesize_tts_to_file(_google_local_fallback_payload(payload), output_path)
    finally:
        if mp3_path:
            try:
                os.unlink(mp3_path)
            except OSError:
                pass


def synthesize_tts_to_file(payload, output_path=None):
    normalized = normalize_tts_payload(payload)
    if not normalized:
        raise RuntimeError("Invalid TTS payload.")
    if output_path:
        output = Path(output_path)
    else:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        output = Path(handle.name)
        handle.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    engine = normalized.get("engine")
    if engine == "festival":
        _synthesize_festival_to_file(normalized, str(output))
    elif engine == "swift":
        _synthesize_swift_to_file(normalized, str(output))
    elif engine == "piper":
        _synthesize_piper_to_file(normalized, str(output))
    elif engine == "google":
        _synthesize_google_to_file(normalized, str(output))
    else:
        raise RuntimeError("Unsupported TTS engine.")
    return str(output)

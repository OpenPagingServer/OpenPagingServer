#!/usr/bin/env python3

import ipaddress
import os
import re
import socket
import uuid
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from active_broadcast_store import (
    expire_active_broadcasts_any_message,
    expire_active_broadcasts_by_template_ids,
    expire_active_broadcasts_triggered_by_template,
    list_active_broadcasts,
    mark_active_broadcast_delivery,
    put_active_broadcast,
)
from group_features import build_monitor_message_child_records, selected_group_ids
from tts import decode_tts_token, encode_tts_token, join_audio_entries, split_audio_entries


TYPE_MAP = {
    "text": "TextMessage",
    "audio": "AudioMessage",
    "text+audio": "Text+AudioMessage",
    "liveaudio": "Page",
    "liveaudio+text": "Page",
    "Page": "Page",
    "AudioMessage": "AudioMessage",
    "TextMessage": "TextMessage",
    "Text+AudioMessage": "Text+AudioMessage",
}


def runtime_type(value):
    return TYPE_MAP.get(str(value or "").strip(), str(value or "").strip() or "TextMessage")


def legacy_type(value):
    token = str(value or "").strip()
    if token == "TextMessage":
        return "text"
    if token == "AudioMessage":
        return "audio"
    if token == "Text+AudioMessage":
        return "text+audio"
    if token == "Page":
        return "liveaudio"
    return token


def is_audio_type(value):
    return legacy_type(value) in ("audio", "text+audio", "liveaudio", "liveaudio+text")


def is_text_type(value):
    return legacy_type(value) in ("text", "text+audio", "liveaudio+text")


MODULE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MESSAGE_VARIABLE_RE = re.compile(r"\$\{([A-Za-z0-9_+\-]+)(?::([^{}]*))?\}")
MESSAGE_VARIABLE_SENDER_TOKEN_RE = re.compile(r"\[(RAW|USERNAME|CNAM|CID)\]", re.IGNORECASE)
MESSAGE_VARIABLE_FORMAT_TOKENS = [
    ("YYYY", "%Y"),
    ("YY", "%y"),
    ("MM", "%m"),
    ("DD", "%d"),
    ("HH", "%H"),
    ("hh", "%I"),
    ("mm", "%M"),
    ("ss", "%S"),
    ("A", "__OPS_AMPM__"),
]
MESSAGE_VARIABLE_API_MAX_URL_LENGTH = int(os.getenv("OPS_MESSAGE_VARIABLE_API_MAX_URL_LENGTH", "2048"))
MESSAGE_VARIABLE_API_ALLOW_PRIVATE = os.getenv("OPS_MESSAGE_VARIABLE_API_ALLOW_PRIVATE", "").strip().lower() in {"1", "true", "yes", "on"}
MESSAGE_VARIABLE_API_DOCS_NOTE = "More information: view online documentation."
MESSAGE_EXPIRATION_MINUTES_RE = re.compile(r"(\d+)\s*m$", re.IGNORECASE)
MESSAGE_EXPIRATION_MESSAGE_ID_RE = re.compile(r"^\d+$")


def safe_module_key(value):
    key = str(value or "").strip()
    return key if MODULE_KEY_RE.fullmatch(key) else ""


def safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def docs_setting_enabled(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _decode_legacy_vendor_value(value):
    return str(value or "").replace(r"\"", '"').replace(r"\'", "'").replace(r"\\", "\\")


def _parse_legacy_vendor_pairs(text):
    values = {}
    position = 0
    pattern = re.compile(
        r"\s*([A-Za-z0-9_-]+)\s*=\s*"
        r"(?:\"((?:\\.|[^\"])*)\"|'((?:\\.|[^'])*)'|([^,]*))\s*"
        r"(?:,|$)"
    )
    while position < len(text):
        match = pattern.match(text, position)
        if not match:
            return {}
        key = safe_module_key(match.group(1))
        if not key:
            return {}
        raw_value = next((group for group in match.groups()[1:] if group is not None), "")
        values[key] = _decode_legacy_vendor_value(raw_value.strip())
        position = match.end()
    return values


def parse_vendor_specific(raw):
    if isinstance(raw, dict):
        return {safe_module_key(key): value for key, value in raw.items() if safe_module_key(key)}
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, dict):
        return {safe_module_key(key): value for key, value in decoded.items() if safe_module_key(key)}
    return _parse_legacy_vendor_pairs(text)


def serialize_vendor_specific(values):
    clean = {}
    for key, value in (values or {}).items():
        module_key = safe_module_key(key)
        if not module_key:
            continue
        if value in (None, ""):
            continue
        if isinstance(value, dict):
            nested = {str(k): v for k, v in value.items() if str(k).strip() and v not in (None, "")}
            if nested:
                clean[module_key] = nested
            continue
        clean[module_key] = value
    if not clean:
        return ""
    return json.dumps(clean, separators=(",", ":"), sort_keys=True)


def module_vendor_value(raw, module_id):
    module_key = safe_module_key(module_id)
    if not module_key:
        return ""
    values = parse_vendor_specific(raw)
    value = values.get(module_key, "")
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    return "" if value is None else str(value)


def merge_module_vendor_value(raw, module_id, value):
    module_key = safe_module_key(module_id)
    values = parse_vendor_specific(raw)
    if module_key:
        if value in (None, ""):
            values.pop(module_key, None)
        else:
            values[module_key] = value
    return serialize_vendor_specific(values)


def table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    rows = cursor.fetchall()
    columns = set()
    for row in rows:
        if isinstance(row, dict):
            columns.add(row.get("Field"))
        else:
            columns.add(row[0])
    return {column for column in columns if column}


def row_get(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def query_setting(cursor, parameter, default=""):
    try:
        cursor.execute("SELECT value FROM systemsettings WHERE parameter = %s LIMIT 1", (parameter,))
        row = cursor.fetchone()
    except Exception:
        return default
    if row is None:
        return default
    if isinstance(row, dict):
        value = row.get("value")
    else:
        value = row[0] if row else None
    return default if value in (None, "") else str(value)


def product_name_value(cursor):
    return query_setting(cursor, "product_name", "Open Paging Server")


def normalize_sender_context(sender="", context=None):
    raw_context = dict(context or {})
    raw = str(raw_context.get("raw") or raw_context.get("sender") or sender or "").strip()
    username = ""
    cnam = ""
    cid = ""
    for key in ("username", "sender_username", "user", "user_name"):
        value = str(raw_context.get(key) or "").strip()
        if value:
            username = value
            break
    for key in ("cnam", "sender_cnam", "calleridname", "caller_id_name", "callerid_name"):
        value = str(raw_context.get(key) or "").strip()
        if value:
            cnam = value
            break
    for key in ("cid", "sender_cid", "calleridnumber", "caller_id_number", "callerid_number"):
        value = str(raw_context.get(key) or "").strip()
        if value:
            cid = value
            break
    raw_sender_match = re.match(r"^\s*(.*?)\s*(?:<)?(\+?\d[\d().\-\s]{4,}\d)(?:>)?\s*$", raw)
    if raw_sender_match:
        cid = cid or re.sub(r"\s+", " ", raw_sender_match.group(2).strip())
        guessed_name = raw_sender_match.group(1).strip(" -<>()")
        if guessed_name:
            cnam = cnam or guessed_name
    if not username and raw and not cnam and not cid:
        username = raw
    display_parts = []
    if cnam:
        display_parts.append(cnam)
    if cid:
        display_parts.append(cid)
    if not display_parts and username:
        display_parts.append(username)
    if not display_parts and raw:
        display_parts.append(raw)
    return {
        "raw": raw,
        "username": username,
        "cnam": cnam,
        "cid": cid,
        "display": " ".join(part for part in display_parts if part).strip(),
    }


def message_variable_sender_value(option, sender_context):
    context = normalize_sender_context(context=sender_context)
    if not option:
        return context.get("display") or context.get("raw") or ""

    def replace_token(match):
        key = match.group(1).strip().lower()
        if key == "username":
            return context.get("username") or ""
        if key == "cnam":
            return context.get("cnam") or ""
        if key == "cid":
            return context.get("cid") or ""
        return context.get("raw") or ""

    rendered = MESSAGE_VARIABLE_SENDER_TOKEN_RE.sub(replace_token, str(option or ""))
    return re.sub(r"\s{2,}", " ", rendered).strip()


def message_variable_format(now, option, default_format):
    pattern = str(option or "").strip() or default_format
    translated = pattern
    for token, replacement in MESSAGE_VARIABLE_FORMAT_TOKENS:
        translated = translated.replace(token, replacement)
    rendered = now.strftime(translated)
    return rendered.replace("__OPS_AMPM__", now.strftime("%p"))


def append_message_variable_docs_note(message, show_online_docs=None, cursor=None):
    if not message:
        return message
    enabled = docs_setting_enabled(show_online_docs, default=None)
    if enabled is None and cursor is not None:
        enabled = docs_setting_enabled(query_setting(cursor, "show_online_docs", "1"))
    if enabled is None:
        enabled = False
    if not enabled or MESSAGE_VARIABLE_API_DOCS_NOTE in message:
        return message
    return f"{message} {MESSAGE_VARIABLE_API_DOCS_NOTE}"


def message_variable_api_host_error(hostname, port, cursor=None, show_online_docs=None):
    host = str(hostname or "").strip()
    if not host:
        return "Invalid host."
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
        resolved = [ip]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except OSError:
            return "Host could not be resolved."
        resolved = []
        for family, _socktype, _proto, _canonname, sockaddr in infos:
            if family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            address = str(sockaddr[0] or "").split("%", 1)[0].strip()
            if not address:
                continue
            try:
                resolved.append(ipaddress.ip_address(address))
            except ValueError:
                return "Host resolved to an invalid address."
        if not resolved:
            return "Host could not be resolved."
    if MESSAGE_VARIABLE_API_ALLOW_PRIVATE:
        return ""
    for address in resolved:
        if not address.is_global:
            return append_message_variable_docs_note(
                "Destination not permitted.",
                show_online_docs=show_online_docs,
                cursor=cursor,
            )
    return ""


def validate_message_variable_api_url(url, cursor=None, show_online_docs=None):
    raw_url = str(url or "").strip()
    if not raw_url:
        return "", "URL is required."
    if len(raw_url) > MESSAGE_VARIABLE_API_MAX_URL_LENGTH:
        return raw_url, "URL is too long."
    if any(ch in raw_url for ch in "\r\n\t"):
        return raw_url, "Invalid URL."
    parsed = urllib.parse.urlsplit(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return raw_url, "Invalid URL."
    if parsed.username or parsed.password:
        return raw_url, "User credentials in URLs are not allowed."
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return raw_url, "Invalid port."
    if port < 1 or port > 65535:
        return raw_url, "Invalid port."
    host_error = message_variable_api_host_error(parsed.hostname, port, cursor=cursor, show_online_docs=show_online_docs)
    if host_error:
        return raw_url, host_error
    normalized = urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
    return normalized, ""


class MessageVariableApiRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, cursor=None, show_online_docs=None):
        super().__init__()
        self.cursor = cursor
        self.show_online_docs = show_online_docs

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected_url = urllib.parse.urljoin(req.full_url, newurl)
        safe_url, error = validate_message_variable_api_url(
            redirected_url,
            cursor=self.cursor,
            show_online_docs=self.show_online_docs,
        )
        if error:
            raise urllib.error.HTTPError(req.full_url, code, error, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def message_variable_api_fetch(option, cursor=None, show_online_docs=None):
    url, validation_error = validate_message_variable_api_url(option, cursor=cursor, show_online_docs=show_online_docs)
    if not url:
        return {"url": "", "status_code": 0, "body": "", "error": ""}
    if validation_error:
        return {"url": url, "status_code": 0, "body": "", "error": validation_error}
    try:
        opener = urllib.request.build_opener(
            MessageVariableApiRedirectHandler(cursor=cursor, show_online_docs=show_online_docs)
        )
        request = urllib.request.Request(url, headers={"User-Agent": "OpenPagingServer"})
        with opener.open(request, timeout=4) as response:
            payload = response.read(16384).decode("utf-8", errors="ignore").strip()
            return {
                "url": url,
                "status_code": safe_int(getattr(response, "status", None), response.getcode() or 200),
                "body": payload,
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read(16384).decode("utf-8", errors="ignore").strip()
        except Exception:
            payload = ""
        return {
            "url": url,
            "status_code": safe_int(getattr(exc, "code", 0), 0),
            "body": payload,
            "error": str(exc.reason or f"HTTP {getattr(exc, 'code', 0)}").strip(),
        }
    except Exception as exc:
        return {"url": url, "status_code": 0, "body": "", "error": str(exc).strip()}


def message_variable_api_value(option, cache, cursor=None, show_online_docs=None):
    url, _validation_error = validate_message_variable_api_url(option, cursor=cursor, show_online_docs=show_online_docs)
    if not url:
        url = str(option or "").strip()
    if not url:
        return "${api}"
    if url in cache:
        return cache[url]
    payload = message_variable_api_fetch(url, cursor=cursor, show_online_docs=show_online_docs).get("body") or ""
    cache[url] = payload
    return payload


def expand_message_variables(text, cursor, sender="", sender_context=None, now=None, api_cache=None, product_name=""):
    raw_text = str(text or "")
    if not raw_text:
        return raw_text
    timestamp = now or datetime.now()
    sender_info = normalize_sender_context(sender=sender, context=sender_context)
    cache = api_cache if api_cache is not None else {}
    product = str(product_name or product_name_value(cursor) or "Open Paging Server")

    def replace_variable(match):
        key = str(match.group(1) or "").strip().lower()
        option = match.group(2)
        if key == "date":
            return message_variable_format(timestamp, option, "MM/DD/YYYY")
        if key == "time":
            return message_variable_format(timestamp, option, "hh:mm A")
        if key in {"date+time", "datetime"}:
            return message_variable_format(timestamp, option, "MM/DD/YYYY hh:mm A")
        if key == "sender":
            return message_variable_sender_value(option, sender_info)
        if key == "api":
            return message_variable_api_value(option, cache, cursor=cursor)
        if key == "productname":
            return product
        return match.group(0)

    return MESSAGE_VARIABLE_RE.sub(replace_variable, raw_text)


def expand_broadcast_record_variables(cursor, record, source_values=None):
    sender = str((record or {}).get("sender") or "").strip()
    sender_context = normalize_sender_context(sender=sender, context=source_values or {})
    issued = (record or {}).get("issued")
    if not isinstance(issued, datetime):
        issued = datetime.now()
    api_cache = {}
    product = product_name_value(cursor)
    for key in ("shortmessage", "longmessage"):
        if key in record:
            record[key] = expand_message_variables(
                record.get(key),
                cursor,
                sender=sender,
                sender_context=sender_context,
                now=issued,
                api_cache=api_cache,
                product_name=product,
            )
    if "audio" in record:
        audio_entries = []
        for entry in split_audio_entries(record.get("audio")):
            payload = decode_tts_token(entry)
            if not payload:
                audio_entries.append(entry)
                continue
            payload["text"] = expand_message_variables(
                payload.get("text"),
                cursor,
                sender=sender,
                sender_context=sender_context,
                now=issued,
                api_cache=api_cache,
                product_name=product,
            )
            audio_entries.append(encode_tts_token(payload))
        record["audio"] = join_audio_entries(audio_entries)
    return record


def split_message_expiration_tokens(value):
    raw = str(value or "").strip()
    if not raw:
        return []
    if "|" not in raw:
        return [raw]
    return [part.strip() for part in raw.split("|") if part.strip()]


def _normalize_message_target_parts(body):
    any_message = False
    message_ids = []
    seen = set()
    for part in str(body or "").replace(",", ".").split("."):
        token = part.strip()
        if not token:
            continue
        if token == "*":
            any_message = True
            continue
        if not MESSAGE_EXPIRATION_MESSAGE_ID_RE.fullmatch(token) or token in seen:
            continue
        seen.add(token)
        message_ids.append(token)
    return any_message, message_ids


def _normalize_message_expiration_token(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.lower() == "manual":
        return "manual"
    minute_match = MESSAGE_EXPIRATION_MINUTES_RE.fullmatch(raw)
    if minute_match:
        return f"{int(minute_match.group(1))}m"
    if raw.lower().startswith("msg="):
        any_message, message_ids = _normalize_message_target_parts(raw[4:])
        if any_message:
            return "msg=*"
        if message_ids:
            return "msg=" + ".".join(message_ids)
        return ""
    return raw


def normalize_message_expiration_value(value):
    tokens = []
    seen = set()
    for token in split_message_expiration_tokens(value):
        normalized = _normalize_message_expiration_token(token)
        if not normalized:
            continue
        if normalized == "0m":
            return "0m"
        if normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return "|".join(tokens)


def message_expiration_trigger_targets(value):
    any_message = False
    message_ids = []
    seen = set()
    normalized = normalize_message_expiration_value(value) or str(value or "").strip()
    for token in split_message_expiration_tokens(normalized):
        if not token.lower().startswith("msg="):
            continue
        token_any_message, token_ids = _normalize_message_target_parts(token[4:])
        if token_any_message:
            any_message = True
        for message_id in token_ids:
            if message_id in seen:
                continue
            seen.add(message_id)
            message_ids.append(message_id)
    return {"any_message": any_message, "message_ids": message_ids}


def message_expiration_is_immediate(value):
    normalized = normalize_message_expiration_value(value) or str(value or "").strip()
    for token in split_message_expiration_tokens(normalized):
        if token.lower() == "0m":
            return True
    return False


def parse_message_expiration(value, issued=None):
    normalized = normalize_message_expiration_value(value)
    raw = normalized or str(value or "").strip()
    if not raw:
        return None, raw
    if message_expiration_is_immediate(raw):
        return None, "0m"
    expires_at = None
    base = issued or datetime.now()
    for token in split_message_expiration_tokens(raw):
        minute_match = MESSAGE_EXPIRATION_MINUTES_RE.fullmatch(token)
        if not minute_match:
            continue
        minutes = int(minute_match.group(1))
        if minutes <= 0:
            continue
        candidate = base + timedelta(minutes=minutes)
        if expires_at is None or candidate < expires_at:
            expires_at = candidate
    return expires_at, raw


def serialize_message_expiration(
    immediate=False,
    manual=False,
    after_enabled=False,
    after_minutes=None,
    when_message=False,
    any_message=False,
    message_ids=None,
):
    if immediate:
        return "0m"
    tokens = []
    if manual:
        tokens.append("manual")
    if after_enabled:
        try:
            minutes = int(str(after_minutes or "").strip())
        except (TypeError, ValueError):
            minutes = 0
        if minutes >= 1:
            tokens.append(f"{minutes}m")
    if when_message:
        if any_message:
            tokens.append("msg=*")
        else:
            seen = set()
            ids = []
            for message_id in message_ids or []:
                token = str(message_id or "").strip()
                if not MESSAGE_EXPIRATION_MESSAGE_ID_RE.fullmatch(token) or token in seen:
                    continue
                seen.add(token)
                ids.append(token)
            if ids:
                tokens.append("msg=" + ".".join(ids))
    return "|".join(tokens) if tokens else "manual"


def message_expiration_state(value):
    normalized = normalize_message_expiration_value(value) or str(value or "").strip()
    state = {
        "normalized": normalized or "manual",
        "immediate": False,
        "manual": False,
        "after_enabled": False,
        "after_minutes": "1",
        "when_message": False,
        "any_message": False,
        "message_ids": set(),
    }
    if not normalized:
        state["manual"] = True
        return state
    if message_expiration_is_immediate(normalized):
        state["normalized"] = "0m"
        state["immediate"] = True
        return state
    recognized = False
    for token in split_message_expiration_tokens(normalized):
        if token.lower() == "manual":
            state["manual"] = True
            recognized = True
            continue
        minute_match = MESSAGE_EXPIRATION_MINUTES_RE.fullmatch(token)
        if minute_match:
            minutes = int(minute_match.group(1))
            if minutes >= 1:
                state["after_enabled"] = True
                state["after_minutes"] = str(minutes)
                recognized = True
            continue
        if token.lower().startswith("msg="):
            targets = message_expiration_trigger_targets(token)
            state["when_message"] = True
            state["any_message"] = bool(targets["any_message"])
            state["message_ids"].update(targets["message_ids"])
            recognized = True
    if not recognized:
        state["manual"] = True
        state["normalized"] = "manual"
    elif not state["manual"] and not state["after_enabled"] and not state["when_message"]:
        state["manual"] = True
    return state


def history_update_delivery(cursor, broadcast_ids, status):
    columns = table_columns(cursor, "broadcasts")
    if "delivery" not in columns:
        return
    ids = [str(broadcast_id).strip() for broadcast_id in (broadcast_ids or []) if str(broadcast_id).strip()]
    if not ids:
        return
    placeholders = ", ".join(["%s"] * len(ids))
    cursor.execute(
        f"UPDATE broadcasts SET delivery = %s WHERE id IN ({placeholders})",
        tuple([status] + ids),
    )


def _serialize_group_ids(group_ids):
    tokens = []
    seen = set()
    for group_id in group_ids or []:
        token = str(group_id or "").strip()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return ".".join(tokens)


def _history_update_groups(cursor, updates):
    columns = table_columns(cursor, "broadcasts")
    if "groups" not in columns:
        return
    for broadcast_id, groups_value in updates:
        token = str(broadcast_id or "").strip()
        if not token:
            continue
        cursor.execute(
            "UPDATE broadcasts SET `groups` = %s WHERE id = %s",
            (str(groups_value or "").strip(), token),
        )


def _monitor_child_records_for_parent(parent_id, snapshots):
    wanted = str(parent_id or "").strip()
    return [
        record
        for record in (snapshots or [])
        if str(record.get("source_broadcast_id") or "").strip() == wanted
    ]


def _sync_monitor_children_for_parent(cursor, parent_record, snapshots):
    existing_children = _monitor_child_records_for_parent((parent_record or {}).get("id"), snapshots)
    _excluded_targets, rebuilt_children = build_monitor_message_child_records(cursor, parent_record)
    if rebuilt_children:
        child = dict(rebuilt_children[0])
        if existing_children:
            existing = existing_children[0]
            preserved_id = str(existing.get("id") or "").strip()
            if preserved_id:
                child["id"] = preserved_id
            child["delivery"] = str(existing.get("delivery") or parent_record.get("delivery") or "pending")
        put_active_broadcast(child)
        for extra in existing_children[1:]:
            extra_id = str(extra.get("id") or "").strip()
            if extra_id:
                mark_active_broadcast_delivery(extra_id, "expired")
        return
    for existing in existing_children:
        existing_id = str(existing.get("id") or "").strip()
        if existing_id:
            mark_active_broadcast_delivery(existing_id, "expired")


def _expire_matching_broadcasts_for_groups(cursor, matcher, trigger_groups, exclude_broadcast_ids=None):
    trigger_value = str(trigger_groups or "").strip()
    if not trigger_value:
        return []
    target_group_ids = selected_group_ids(trigger_value, cursor=cursor)
    if not target_group_ids:
        return []
    target_group_set = set(target_group_ids)
    excluded = {str(broadcast_id or "").strip() for broadcast_id in (exclude_broadcast_ids or []) if str(broadcast_id or "").strip()}
    snapshots = list_active_broadcasts(limit=5000)
    expired_ids = []
    group_updates = []
    for record in snapshots:
        if not record or bool(record.get("monitor_child")):
            continue
        record_id = str(record.get("id") or "").strip()
        if not record_id or record_id in excluded or not matcher(record):
            continue
        record_group_ids = selected_group_ids(record.get("groups"), cursor=cursor)
        if not record_group_ids:
            continue
        remaining_group_ids = [group_id for group_id in record_group_ids if group_id not in target_group_set]
        if len(remaining_group_ids) == len(record_group_ids):
            continue
        if not remaining_group_ids:
            mark_active_broadcast_delivery(record_id, "expired")
            for child in _monitor_child_records_for_parent(record_id, snapshots):
                child_id = str(child.get("id") or "").strip()
                if child_id:
                    mark_active_broadcast_delivery(child_id, "expired")
            expired_ids.append(record_id)
            continue
        updated_record = dict(record)
        updated_record["groups"] = _serialize_group_ids(remaining_group_ids)
        put_active_broadcast(updated_record)
        _sync_monitor_children_for_parent(cursor, updated_record, snapshots)
        group_updates.append((record_id, updated_record["groups"]))
    if group_updates:
        _history_update_groups(cursor, group_updates)
    return expired_ids


def parse_expires(value, issued=None):
    return parse_message_expiration(value, issued)


def is_emergency_priority(value):
    return str(value or "").strip().lower() == "emergency"


def fetch_template(cursor, message_id):
    columns = table_columns(cursor, "messages")
    wanted = [
        "messageid",
        "name",
        "shortmessage",
        "longmessage",
        "icon",
        "color",
        "type",
        "expires",
        "image",
        "audio",
        "priority",
        "vendor_specific",
    ]
    selected = [column for column in wanted if column in columns]
    cursor.execute(
        f"SELECT {', '.join(selected)} FROM messages WHERE messageid = %s LIMIT 1",
        (message_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(zip(selected, row))


def insert_broadcast_record(cursor, values):
    columns = table_columns(cursor, "broadcasts")
    insert_columns = [column for column in values if column in columns]
    placeholders = ", ".join(["%s"] * len(insert_columns))
    column_sql = ", ".join(f"`{column}`" for column in insert_columns)
    cursor.execute(
        f"INSERT INTO broadcasts ({column_sql}) VALUES ({placeholders})",
        tuple(values[column] for column in insert_columns),
    )
    put_active_broadcast(values)
    return values["id"], values.get("expires_rule")


def create_broadcast_record(values, groups=None, sender=None):
    issued = datetime.now()
    expires_at, expires_rule = parse_expires(values.get("expires"), issued)
    return {
        "id": str(values.get("id") or "").strip() or uuid.uuid4().hex,
        "name": values.get("name") or "",
        "shortmessage": values.get("shortmessage") or "",
        "longmessage": values.get("longmessage") or "",
        "icon": values.get("icon") or "",
        "color": values.get("color") or "",
        "vendor_specific": values.get("vendor_specific") or "",
        "template_id": values.get("template_id"),
        "expires_rule": values.get("expires_rule") or expires_rule,
        "type": runtime_type(values.get("type")),
        "expires": expires_at,
        "issued": issued,
        "groups": str(groups if groups is not None else values.get("groups") or ""),
        "image": values.get("image") or "",
        "audio": values.get("audio") or "",
        "sender": sender if sender is not None else values.get("sender") or "",
        "priority": values.get("priority") or "Normal",
        "delivery": "pending",
    }


def create_custom_broadcast(cursor, values, groups=None, sender=None):
    record = create_broadcast_record(values, groups=groups, sender=sender)
    expand_broadcast_record_variables(cursor, record, source_values=values)
    excluded_targets, monitor_children = build_monitor_message_child_records(cursor, record)
    if excluded_targets:
        record["exclude_targets"] = list(excluded_targets)
    broadcast_id, expires_rule = insert_broadcast_record(cursor, record)
    for child in monitor_children:
        put_active_broadcast(child)
    return broadcast_id, expires_rule


def create_broadcast_from_template(cursor, template, groups, sender="", overrides=None):
    values = {
        "name": template.get("name") or "",
        "shortmessage": template.get("shortmessage") or "",
        "longmessage": template.get("longmessage") or "",
        "icon": template.get("icon") or "",
        "color": template.get("color") or "",
        "vendor_specific": template.get("vendor_specific") or "",
        "template_id": template.get("messageid"),
        "type": template.get("type"),
        "expires": template.get("expires"),
        "image": template.get("image") or "",
        "audio": template.get("audio") or "",
        "priority": template.get("priority") or "Normal",
    }
    for key, value in (overrides or {}).items():
        if key in values and value not in (None, ""):
            values[key] = value
    return create_custom_broadcast(cursor, values, groups=groups, sender=sender)


def expire_message_rule_broadcasts(cursor, expires_rule, exclude_broadcast_ids=None, trigger_groups=None, trigger_priority=None):
    if is_emergency_priority(trigger_priority):
        return
    targets = message_expiration_trigger_targets(expires_rule)
    template_ids = targets["message_ids"]
    if not template_ids:
        return
    if str(trigger_groups or "").strip():
        wanted_templates = {str(template_id).strip() for template_id in template_ids if str(template_id).strip()}
        expired_ids = _expire_matching_broadcasts_for_groups(
            cursor,
            lambda record: str((record or {}).get("template_id") or "").strip() in wanted_templates,
            trigger_groups,
            exclude_broadcast_ids=exclude_broadcast_ids,
        )
    else:
        expired_ids = expire_active_broadcasts_by_template_ids(
            template_ids,
            exclude_broadcast_ids=exclude_broadcast_ids,
        )
    history_update_delivery(cursor, expired_ids, "expired")


def expire_broadcasts_triggered_by_template(cursor, template_id, exclude_broadcast_ids=None, trigger_groups=None, trigger_priority=None):
    if is_emergency_priority(trigger_priority):
        return
    token = str(template_id or "").strip()
    if str(trigger_groups or "").strip():
        expired_ids = _expire_matching_broadcasts_for_groups(
            cursor,
            lambda record: token in message_expiration_trigger_targets((record or {}).get("expires_rule")).get("message_ids", []),
            trigger_groups,
            exclude_broadcast_ids=exclude_broadcast_ids,
        )
    else:
        expired_ids = expire_active_broadcasts_triggered_by_template(
            template_id,
            exclude_broadcast_ids=exclude_broadcast_ids,
        )
    history_update_delivery(cursor, expired_ids, "expired")


def expire_any_message_rule_broadcasts(cursor, exclude_broadcast_ids=None, trigger_groups=None, trigger_priority=None):
    if is_emergency_priority(trigger_priority):
        return
    if str(trigger_groups or "").strip():
        expired_ids = _expire_matching_broadcasts_for_groups(
            cursor,
            lambda record: message_expiration_trigger_targets((record or {}).get("expires_rule")).get("any_message"),
            trigger_groups,
            exclude_broadcast_ids=exclude_broadcast_ids,
        )
    else:
        expired_ids = expire_active_broadcasts_any_message(exclude_broadcast_ids=exclude_broadcast_ids)
    history_update_delivery(cursor, expired_ids, "expired")

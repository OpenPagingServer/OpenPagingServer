import hashlib
import os
import threading
import time
from collections import deque
from datetime import datetime

from flask import Flask, Response, jsonify, request

import endpoints
from broadcasts import (
    create_custom_broadcast,
    expire_any_message_rule_broadcasts,
    expire_message_rule_broadcasts,
    parse_vendor_specific,
    safe_module_key,
    serialize_vendor_specific,
)
from srv.web.app import (
    create_broadcast,
    db,
    ensure_api_token_schema,
    ensure_message_vendor_schema,
    query_all,
    query_one,
    verify_api_token_value,
)


app = Flask(__name__)
app.config.update(JSON_SORT_KEYS=False)

API_RATE_LIMIT_BUCKETS = {}
API_RATE_LIMIT_LOCK = threading.Lock()
VALID_PRIORITIES = {"Low", "Normal", "High", "Emergency"}
CUSTOM_MESSAGE_FIELDS = {
    "name",
    "type",
    "shortmessage",
    "longmessage",
    "icon",
    "color",
    "image",
    "audio",
    "expires",
    "priority",
    "vendor_specific",
}


def int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def api_client_ip():
    return str(request.remote_addr or "unknown")


def bearer_token_fingerprint():
    header = str(request.headers.get("Authorization") or "")
    if not header.lower().startswith("bearer "):
        return ""
    value = header.split(" ", 1)[1].strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def api_rate_key():
    token_key = bearer_token_fingerprint()
    return f"token:{token_key}" if token_key else f"ip:{api_client_ip()}"


def rate_limit_exceeded(scope, key, limit, window_seconds):
    if limit <= 0 or window_seconds <= 0:
        return False, 0
    now = time.monotonic()
    bucket_key = (scope, str(key or "unknown"))
    with API_RATE_LIMIT_LOCK:
        bucket = API_RATE_LIMIT_BUCKETS.setdefault(bucket_key, deque())
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(window_seconds - (now - bucket[0])))
            return True, retry_after
        bucket.append(now)
    return False, 0


def api_rate_limited_response(retry_after):
    response = jsonify(error="Too many requests. Please wait and try again.")
    response.status_code = 429
    response.headers["Retry-After"] = str(max(1, int(retry_after or 1)))
    return response


def check_api_rate_limit(scope, key, limit, window_seconds):
    limited, retry_after = rate_limit_exceeded(scope, key, limit, window_seconds)
    if limited:
        return api_rate_limited_response(retry_after)
    return None


@app.before_request
def enforce_api_rate_limits():
    if str(os.getenv("API_RATE_LIMIT_ENABLE", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return None
    key = api_rate_key()
    has_token = bool(bearer_token_fingerprint())
    minute_default = 300 if has_token else 90
    checks = [
        ("api-minute", key, int_env("API_RATE_LIMIT_PER_MINUTE", minute_default), 60),
        ("api-hour", key, int_env("API_RATE_LIMIT_PER_HOUR", 3000), 3600),
    ]
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        checks.append(("api-write-minute", key, int_env("API_RATE_LIMIT_WRITE_PER_MINUTE", 120), 60))
    for scope, rate_key, limit, window_seconds in checks:
        response = check_api_rate_limit(scope, rate_key, limit, window_seconds)
        if response is not None:
            return response
    return None


def enforce_api_send_rate_limits(token, module_id=""):
    token_id = token.get("id") if isinstance(token, dict) else ""
    user_id = token.get("user_id") if isinstance(token, dict) else ""
    sender_key = f"token:{token_id or api_rate_key()}:module:{module_id or 'api'}"
    checks = [
        ("api-send-minute", sender_key, int_env("API_SEND_RATE_LIMIT_PER_MINUTE", 30), 60),
        ("api-send-hour", sender_key, int_env("API_SEND_RATE_LIMIT_PER_HOUR", 300), 3600),
    ]
    if user_id not in (None, ""):
        checks.append(("api-send-user-minute", user_id, int_env("API_SEND_USER_RATE_LIMIT_PER_MINUTE", 60), 60))
    for scope, key, limit, window_seconds in checks:
        response = check_api_rate_limit(scope, key, limit, window_seconds)
        if response is not None:
            return response
    return None


def current_token():
    ensure_api_token_schema()
    header = str(request.headers.get("Authorization") or "")
    if not header.lower().startswith("bearer "):
        return None
    token_value = header.split(" ", 1)[1].strip()
    if not token_value:
        return None
    rows = query_all(
        """
        SELECT
            t.id, t.user_id, t.token_hash, t.expires_at, u.username, u.role
        FROM api_tokens t
        JOIN users u ON u.id = t.user_id
        ORDER BY t.created_at DESC, t.id DESC
        """
    )
    record = next((row for row in rows if verify_api_token_value(token_value, row.get("token_hash"))), None)
    if not record:
        return None
    expires_at = record.get("expires_at")
    if expires_at and str(expires_at) not in {"0000-00-00 00:00:00", "None"}:
        row = query_one("SELECT NOW() AS now_value")
        now_value = row.get("now_value") if row else None
        if isinstance(expires_at, str):
            expires_at = datetime.strptime(expires_at.split(".", 1)[0], "%Y-%m-%d %H:%M:%S")
        if now_value and expires_at <= now_value:
            return None
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE api_tokens SET last_used_at=NOW() WHERE id=%s", (record["id"],))
        conn.commit()
    finally:
        conn.close()
    return record


def require_token():
    token = current_token()
    if token:
        return token
    response = jsonify(error="Unauthorized")
    response.status_code = 401
    response.headers["WWW-Authenticate"] = 'Bearer realm="Open Paging Server API"'
    return response


def payload_dict():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form.to_dict(flat=False)
        payload = {key: value[0] if len(value) == 1 else value for key, value in payload.items()}
    return payload if isinstance(payload, dict) else {}


def clean_group_value(value):
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            parts.extend(str(item or "").replace(",", ".").split("."))
    else:
        parts = str(value or "").replace(",", ".").split(".")
    clean = []
    for part in parts:
        part = part.strip()
        if part and part not in clean:
            clean.append(part)
    return ".".join(clean)


def validate_groups(group_value):
    groups = clean_group_value(group_value)
    if not groups:
        return "", "group_id is required"
    if groups == "0":
        return groups, ""
    missing = []
    for group_id in groups.split("."):
        if not query_one("SELECT id FROM `groups` WHERE id=%s LIMIT 1", (group_id,)):
            missing.append(group_id)
    if missing:
        return "", "Group not found: " + ", ".join(missing)
    return groups, ""


def resolve_module(payload):
    raw_module_id = payload.get("module_id") or payload.get("module") or ""
    module_id = safe_module_key(raw_module_id)
    if raw_module_id and not module_id:
        return "", (jsonify(error="module_id is invalid"), 400)
    if not module_id:
        return "", None
    if module_id == "siptrunks":
        return module_id, None
    packages = endpoints.discover_endpoint_packages(extract_if_trusted=True)
    package = packages.get(module_id)
    if not package or not package.get("trusted"):
        return "", (jsonify(error="Input module is not trusted or is not installed"), 403)
    endpoints.upsert_module_package_registry(packages)
    manifest = package.get("manifest") or {}
    input_type = manifest.get("input_type") or manifest.get("type") or "Output"
    if not endpoints.module_type_has_input(input_type):
        return "", (jsonify(error="Module type must be Input or Input+Output to send messages"), 403)
    row = query_one("SELECT enabled FROM endpointmodulesloaded WHERE `dir`=%s LIMIT 1", (module_id,))
    if row and str(row.get("enabled") or "").strip().lower() != "true":
        return "", (jsonify(error="Input module is disabled"), 403)
    return module_id, None


def resolve_sender(payload, token, module_id=""):
    if module_id:
        sender_id = str(payload.get("sender_id") or payload.get("user_id") or "").strip()
        if sender_id:
            row = query_one("SELECT id, username FROM users WHERE id=%s LIMIT 1", (sender_id,))
            if not row:
                return "", (jsonify(error="Sender user not found"), 404)
            return row.get("username") or f"user:{row.get('id')}", None
        sender = str(payload.get("sender") or payload.get("sender_name") or "").strip()
        if sender:
            return sender[:100], None
    return token.get("username") or "API", None


def request_priority(payload, default=None):
    priority = str(payload.get("priority") or default or "").strip()
    if not priority:
        return None, ""
    if priority not in VALID_PRIORITIES:
        return None, "priority must be one of " + ", ".join(sorted(VALID_PRIORITIES))
    return priority, ""


def request_vendor_specific(payload, module_id=""):
    keys = ["vendor_specific", "vendor_parameters", "module_parameters"]
    raw = next((payload.get(key) for key in keys if key in payload), None)
    if raw in (None, ""):
        return None
    if module_id:
        decoded = parse_vendor_specific(raw)
        if decoded and module_id in decoded:
            return serialize_vendor_specific(decoded)
        return serialize_vendor_specific({module_id: raw})
    if isinstance(raw, dict):
        return serialize_vendor_specific(raw)
    decoded = parse_vendor_specific(raw)
    return serialize_vendor_specific(decoded) if decoded else str(raw)


def create_api_custom_broadcast(values, groups, sender):
    ensure_message_vendor_schema()
    conn = db()
    try:
        with conn.cursor() as cur:
            broadcast_id, expires_rule = create_custom_broadcast(cur, values, groups=groups, sender=sender)
            trigger_priority = values.get("priority")
            if str(trigger_priority or "").strip().lower() != "emergency":
                expire_message_rule_broadcasts(
                    cur,
                    expires_rule,
                    [broadcast_id],
                    trigger_groups=groups,
                )
                expire_any_message_rule_broadcasts(
                    cur,
                    [broadcast_id],
                    trigger_groups=groups,
                )
        conn.commit()
        return broadcast_id
    finally:
        conn.close()


@app.errorhandler(404)
def not_found(_exc):
    return jsonify(error="Not found"), 404


@app.route("/")
def index():
    return jsonify(service="Open Paging Server API")


@app.route("/messages")
def messages():
    token = require_token()
    if not isinstance(token, dict):
        return token
    ensure_message_vendor_schema()
    rows = query_all(
        """
        SELECT messageid, name, type, shortmessage, longmessage, color, audio, expires, priority, vendor_specific
        FROM messages
        ORDER BY name ASC
        """
    )
    return jsonify(messages=rows)


@app.route("/groups")
def groups():
    token = require_token()
    if not isinstance(token, dict):
        return token
    rows = query_all("SELECT id, name, members FROM `groups` ORDER BY name ASC")
    return jsonify(groups=rows)


@app.route("/send-message", methods=["POST"])
def send_message():
    token = require_token()
    if not isinstance(token, dict):
        return token
    payload = payload_dict()
    module_id, module_error = resolve_module(payload)
    if module_error:
        return module_error
    rate_response = enforce_api_send_rate_limits(token, module_id)
    if rate_response is not None:
        return rate_response
    sender, sender_error = resolve_sender(payload, token, module_id)
    if sender_error:
        return sender_error
    message_id = str(payload.get("message_id") or "").strip()
    group_id, group_error = validate_groups(payload.get("group_id") or payload.get("groups") or payload.get("group_ids"))
    if group_error:
        return jsonify(error=group_error), 400
    if not message_id:
        return send_custom_message_payload(payload, token, module_id, sender, group_id)
    message = query_one("SELECT messageid, name FROM messages WHERE messageid=%s LIMIT 1", (message_id,))
    if not message:
        return jsonify(error="Message not found"), 404
    ensure_message_vendor_schema()
    priority, priority_error = request_priority(payload)
    if priority_error:
        return jsonify(error=priority_error), 400
    overrides = {}
    if priority:
        overrides["priority"] = priority
    vendor_specific = request_vendor_specific(payload, module_id)
    if vendor_specific is not None:
        overrides["vendor_specific"] = vendor_specific
    create_broadcast(message_id, group_id, sender, overrides=overrides or None)
    return jsonify(
        status="sent",
        mode="template",
        module_id=module_id or None,
        sender=sender,
        message_id=message_id,
        group_id=group_id,
        message_name=message.get("name"),
    )


def send_custom_message_payload(payload, _token, module_id, sender, group_id):
    msg_type = str(payload.get("type") or payload.get("message_type") or "").strip()
    if not msg_type:
        return jsonify(error="message_id is required unless a custom message type is provided"), 400
    priority, priority_error = request_priority(payload, default="Normal")
    if priority_error:
        return jsonify(error=priority_error), 400
    values = {key: payload.get(key, "") for key in CUSTOM_MESSAGE_FIELDS}
    values["type"] = msg_type
    values["name"] = values.get("name") or "Custom message"
    values["priority"] = priority or "Normal"
    if isinstance(values.get("audio"), (list, tuple)):
        values["audio"] = ":".join(str(item).strip() for item in values["audio"] if str(item).strip())
    vendor_specific = request_vendor_specific(payload, module_id)
    values["vendor_specific"] = vendor_specific or ""
    broadcast_id = create_api_custom_broadcast(values, group_id, sender)
    return jsonify(
        status="sent",
        mode="custom",
        module_id=module_id or None,
        sender=sender,
        broadcast_id=broadcast_id,
        group_id=group_id,
        priority=values["priority"],
    )


@app.route("/send-custom-message", methods=["POST"])
def send_custom_message():
    token = require_token()
    if not isinstance(token, dict):
        return token
    payload = payload_dict()
    module_id, module_error = resolve_module(payload)
    if module_error:
        return module_error
    rate_response = enforce_api_send_rate_limits(token, module_id)
    if rate_response is not None:
        return rate_response
    sender, sender_error = resolve_sender(payload, token, module_id)
    if sender_error:
        return sender_error
    group_id, group_error = validate_groups(payload.get("group_id") or payload.get("groups") or payload.get("group_ids"))
    if group_error:
        return jsonify(error=group_error), 400
    return send_custom_message_payload(payload, token, module_id, sender, group_id)


if __name__ == "__main__":
    app.run("127.0.0.1", int(os.getenv("PORT", "8088")))

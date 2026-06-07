import os
from datetime import datetime

from flask import Flask, Response, jsonify, request

from srv.web.app import (
    create_broadcast,
    db,
    ensure_api_token_schema,
    query_all,
    query_one,
    verify_api_token_value,
)


app = Flask(__name__)
app.config.update(JSON_SORT_KEYS=False)


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
    rows = query_all(
        """
        SELECT messageid, name, type, shortmessage, longmessage, color, audio, expires, priority
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
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form.to_dict()
    message_id = str(payload.get("message_id") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    if not message_id or not group_id:
        return jsonify(error="message_id and group_id are required"), 400
    message = query_one("SELECT messageid, name FROM messages WHERE messageid=%s LIMIT 1", (message_id,))
    group = query_one("SELECT id, name FROM `groups` WHERE id=%s LIMIT 1", (group_id,))
    if not message:
        return jsonify(error="Message not found"), 404
    if not group:
        return jsonify(error="Group not found"), 404
    create_broadcast(message_id, group_id, token.get("username") or "API")
    return jsonify(status="sent", message_id=message_id, group_id=group_id, message_name=message.get("name"), group_name=group.get("name"))


if __name__ == "__main__":
    app.run("127.0.0.1", int(os.getenv("PORT", "8088")))

import os
from datetime import datetime
from pathlib import Path

from broadcasts import expand_message_variables
from srv.web.app import *
from tts import (
    get_tts_preview_file,
    get_tts_preview_payload,
    store_tts_preview_file,
    tts_payload_from_voice_id,
)


def _preview_sender_context(user):
    username = str((user or {}).get("username") or session.get("username") or "User").strip() or "User"
    return username, {
        "sender": username,
        "username": username,
        "sender_username": username,
    }


def _render_preview_text(text, user):
    sender, sender_context = _preview_sender_context(user)
    conn = db()
    try:
        with conn.cursor() as cur:
            return expand_message_variables(
                text,
                cur,
                sender=sender,
                sender_context=sender_context,
                now=datetime.now(),
            )
    finally:
        conn.close()


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        voice_id = str(payload.get("voice_id") or request.form.get("voice_id") or "").strip()
        raw_text = str(payload.get("text") or request.form.get("text") or "")
        if not voice_id:
            return jsonify(ok=False, error="Select a TTS voice first."), 400
        if not raw_text.strip():
            return jsonify(ok=False, error="Enter TTS text first."), 400
        try:
            tts_payload = tts_payload_from_voice_id(voice_id, _render_preview_text(raw_text, user))
            preview_id = store_tts_preview_file(tts_payload)
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True, preview_url=f"/messages/tts-preview?preview_id={h(preview_id)}")
    preview_path, preview_payload = get_tts_preview_file(request.args.get("preview_id", ""))
    if not preview_payload:
        abort(404)
    if not preview_path:
        abort(404)
    try:
        audio_bytes = Path(preview_path).read_bytes()
    finally:
        try:
            os.unlink(preview_path)
        except OSError:
            pass
    response = Response(audio_bytes, mimetype="audio/wav")
    response.headers["Cache-Control"] = "no-store"
    return response

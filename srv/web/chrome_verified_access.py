"""ChromeOS user attestation for receive-only desktop sessions."""
import base64
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import jsonify, request

import srv.web.app as web

PROVIDER = "chrome_verified_access"
API = "https://verifiedaccess.googleapis.com/v2/challenge:"
SCOPE = "https://www.googleapis.com/auth/verifiedaccess"
SERVICE_ACCOUNT_FILE = Path("/var/spool/openpagingserver/.chrome-verified-access.json")
SERVICE_ACCOUNT_MAX_BYTES = 64 * 1024


def store_service_account(upload):
    from google.auth.exceptions import GoogleAuthError
    from google.oauth2.service_account import Credentials

    contents = upload.stream.read(SERVICE_ACCOUNT_MAX_BYTES + 1)
    if len(contents) > SERVICE_ACCOUNT_MAX_BYTES:
        raise ValueError("File is too large")
    info = json.loads(contents)
    if (
        not isinstance(info, dict)
        or info.get("type") != "service_account"
        or info.get("token_uri") != "https://oauth2.googleapis.com/token"
    ):
        raise ValueError("Invalid service account")
    try:
        Credentials.from_service_account_info(info, scopes=[SCOPE])
    except (GoogleAuthError, ValueError, TypeError) as exc:
        raise ValueError("Invalid service account key") from exc

    # Publish the complete key atomically. NamedTemporaryFile creates it with
    # owner-only permissions and the temporary name is on the same filesystem.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=SERVICE_ACCOUNT_FILE.parent, prefix=".verified-access-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(contents)
        os.replace(temporary_path, SERVICE_ACCOUNT_FILE)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _attestation_fields(message):
    # Chromium SignedData and ChallengeResponse contain length-delimited
    # fields. Extract the signed challenge without trusting client metadata.
    fields = {}
    offset = 0
    while offset < len(message):
        tag = message[offset]
        offset += 1
        if tag not in (10, 18, 26) or tag in fields:
            raise ValueError("Invalid attestation field")
        length = 0
        for shift in range(0, 28, 7):
            byte = message[offset]
            offset += 1
            length |= (byte & 127) << shift
            if byte < 128:
                break
        else:
            raise ValueError("Invalid attestation length")
        end = offset + length
        if end > len(message):
            raise ValueError("Truncated attestation field")
        fields[tag] = message[offset:end]
        offset = end
    return fields


def _challenge_digest(challenge):
    fields = _attestation_fields(challenge)
    return hashlib.sha256(
        len(fields[10]).to_bytes(4, "big") + fields[10] + fields[18]
    ).hexdigest()


def _consume_challenge(request_id, digest):
    conn = web.db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """UPDATE desktop_sso_requests SET status='consumed', consumed_at=NOW()
                   WHERE request_id=%s AND secret_hash=%s AND status='chromeos'
                   AND expires_at > NOW()""",
                (request_id, digest),
            )
            consumed = cursor.rowcount == 1
        conn.commit()
        return consumed
    finally:
        conn.close()


def _eligible_user(email):
    users = web.query_all(
        """SELECT id, username, email, role, accountexpire, loginsleft
           FROM users WHERE LOWER(email)=%s LIMIT 2""",
        (email,),
    )
    if len(users) == 1 and users[0]["role"] != "guest" and not web.user_account_is_expired(users[0]):
        return users[0]
    return None


def _google_challenge(action, payload=None):
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    from requests import Session

    credentials = Credentials.from_service_account_file(str(SERVICE_ACCOUNT_FILE), scopes=[SCOPE])
    with Session() as session:
        transport = Request(session)

        def refresh_request(*args, **kwargs):
            kwargs["timeout"] = (4, 5)
            return transport(*args, **kwargs)

        credentials.refresh(refresh_request)

    # Use IPv4 for Verified Access without changing networking for other OPS
    # requests. Send the token and attestation to curl on stdin.
    config = [f"header = {json.dumps('Authorization: Bearer ' + credentials.token)}"]
    if payload is not None:
        config.append('header = "Content-Type: application/json"')
        config.append(f"data-binary = {json.dumps(json.dumps(payload))}")
    result = subprocess.run(
        ["curl", "--disable", "--ipv4", "--silent", "--show-error", "--request", "POST",
         "--connect-timeout", "4", "--max-time", "10", "--proto", "=https",
         "--write-out", "\n%{http_code}", "--config", "-", API + action],
        input="\n".join(config) + "\n",
        capture_output=True, text=True, encoding="utf-8", timeout=11, check=True,
    )
    body, _, status = result.stdout.rpartition("\n")
    status = int(status)
    return status, json.loads(body) if status == 200 else {}


def chromeos_session():
    config = web.settings()
    if config.get("chromeos_auto_login") != "1" or not SERVICE_ACCOUNT_FILE.is_file():
        return jsonify(error="Not found"), 404
    if request.headers.get(web.DESKTOP_CLIENT_HEADER) != "1" or web.demo_mode_enabled():
        return jsonify(error="Unauthorized"), 403
    if request.content_length and request.content_length > 70000:
        return jsonify(error="Invalid request"), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Invalid request"), 400
    email = str(payload.get("email") or "").strip().lower()
    domain = config.get("chromeos_workspace_domain", "")
    if not domain or len(email) > 254 or email.count("@") != 1 or email.split("@")[1] != domain:
        return jsonify(error="Verification failed"), 401

    try:
        if "challengeResponse" not in payload:
            status, result = _google_challenge("generate")
            if status != 200:
                web.app.logger.warning("Chrome Verified Access generate failed (upstream HTTP %s)", status)
                return jsonify(error="Verification unavailable"), 503
            challenge = result["challenge"]
            digest = _challenge_digest(base64.b64decode(challenge, validate=True))
            request_id = web.secrets.token_urlsafe(32)
            web.ensure_desktop_sso_schema()
            web.prune_desktop_sso_requests()
            web.execute(
                """INSERT INTO desktop_sso_requests (request_id, secret_hash, status, expires_at)
                   VALUES (%s,%s,'chromeos',NOW() + INTERVAL 2 MINUTE)""",
                (request_id, digest),
            )
            return jsonify(challenge=challenge, request_id=request_id)

        encoded = payload["challengeResponse"]
        request_id = str(payload.get("request_id") or "")
        if not isinstance(encoded, str) or len(encoded) > 65536 or len(request_id) != 43:
            return jsonify(error="Invalid response"), 400
        try:
            outer = _attestation_fields(base64.b64decode(encoded, validate=True))
            digest = _challenge_digest(_attestation_fields(outer[10])[10])
        except (IndexError, KeyError, ValueError):
            return jsonify(error="Invalid response"), 400
        web.ensure_desktop_sso_schema()
        if not _consume_challenge(request_id, digest):
            return jsonify(error="Verification failed"), 401
        status, result = _google_challenge(
            "verify", {"challengeResponse": encoded, "expectedIdentity": email},
        )
        if status == 429 or status >= 500:
            web.app.logger.warning("Chrome Verified Access verify failed (upstream HTTP %s)", status)
            return jsonify(error="Verification unavailable"), 503
        if status != 200 or result.get("keyTrustLevel") != "CHROME_OS_VERIFIED_MODE":
            return jsonify(error="Verification failed"), 401
    except Exception as exc:
        web.app.logger.warning(
            "Chrome Verified Access %s failed (%s)",
            "verify" if "challengeResponse" in payload else "generate",
            type(exc).__name__,
        )
        return jsonify(error="Verification unavailable"), 503

    user = _eligible_user(email)
    if not user:
        return jsonify(error="Verification failed"), 401
    ttl = int(config.get("chromeos_session_days") or "7") * 86400
    sid = web.create_user_session_record(
        user, auth_provider=PROVIDER, session_type="desktop",
        expires_at=datetime.now() + timedelta(seconds=ttl),
        user_agent=web.desktop_login_user_agent(),
    )
    return web.desktop_session_json(
        user, session_id=sid, include_refresh=False, token_ttl=ttl,
        extra={"chromeos_auto_login": True},
    )

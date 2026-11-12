"""Kerberos authentication for receive-only Windows desktop sessions."""
import base64
import binascii
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import jsonify, request

import srv.web.app as web

PROVIDER = "kerberos"
KEYTAB_FILE = Path("/var/spool/openpagingserver/.desktop-service.keytab")
KRB5_CONFIG_FILE = Path("/var/spool/openpagingserver/.desktop-krb5.conf")
KEYTAB_MAX_BYTES = 1024 * 1024
TOKEN_MAX_BYTES = 64 * 1024


def _atomic_private_write(path, contents):
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(contents)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def validate_keytab(service_principal, path=KEYTAB_FILE):
    import gssapi

    name = gssapi.Name(service_principal, name_type=gssapi.NameType.kerberos_principal)
    gssapi.Credentials(name=name, usage="accept", store={"keytab": str(path)})


def store_keytab(upload, service_principal=""):
    contents = upload.stream.read(KEYTAB_MAX_BYTES + 1)
    if not contents or len(contents) > KEYTAB_MAX_BYTES or contents[:2] not in (b"\x05\x01", b"\x05\x02"):
        raise ValueError("Invalid keytab")

    temporary_path = None
    try:
        KEYTAB_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=KEYTAB_FILE.parent, prefix=".desktop-keytab-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(contents)
        os.chmod(temporary_path, 0o600)
        if service_principal:
            validate_keytab(service_principal, temporary_path)
        os.replace(temporary_path, KEYTAB_FILE)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_krb5_config(realm, kdc):
    realm = str(realm or "").strip().upper()
    kdc = str(kdc or "").strip()
    if not realm or not kdc:
        KRB5_CONFIG_FILE.unlink(missing_ok=True)
        return
    contents = (
        "[libdefaults]\n"
        f" default_realm = {realm}\n"
        " dns_lookup_realm = false\n"
        " dns_lookup_kdc = true\n"
        " rdns = false\n"
        "[realms]\n"
        f" {realm} = {{\n  kdc = {kdc}\n }}\n"
    ).encode("utf-8")
    _atomic_private_write(KRB5_CONFIG_FILE, contents)
    os.environ["KRB5_CONFIG"] = str(KRB5_CONFIG_FILE)


def _eligible_user(principal):
    principal = str(principal or "").strip()
    username = principal.rsplit("@", 1)[0]
    users = web.query_all(
        """SELECT id, username, email, role, accountexpire, loginsleft
           FROM users
           WHERE LOWER(email)=LOWER(%s) OR LOWER(username)=LOWER(%s)
           LIMIT 3""",
        (principal, username),
    )
    email_matches = [user for user in users if str(user.get("email") or "").casefold() == principal.casefold()]
    candidates = email_matches or [user for user in users if str(user.get("username") or "").casefold() == username.casefold()]
    if len(candidates) == 1 and candidates[0]["role"] != "guest" and not web.user_account_is_expired(candidates[0]):
        return candidates[0]
    return None


def _accept_ticket(encoded_token, service_principal):
    if KRB5_CONFIG_FILE.is_file():
        os.environ["KRB5_CONFIG"] = str(KRB5_CONFIG_FILE)
    import gssapi

    try:
        token = base64.b64decode(encoded_token, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ValueError("Invalid Kerberos token") from exc
    if not token or len(token) > TOKEN_MAX_BYTES:
        raise ValueError("Invalid Kerberos token")
    name = gssapi.Name(service_principal, name_type=gssapi.NameType.kerberos_principal)
    credentials = gssapi.Credentials(name=name, usage="accept", store={"keytab": str(KEYTAB_FILE)})
    context = gssapi.SecurityContext(creds=credentials, usage="accept")
    output_token = context.step(token)
    if not context.complete or not context.initiator_name:
        raise ValueError("Kerberos exchange did not complete")
    encoded_output = base64.b64encode(bytes(output_token)).decode("ascii") if output_token else ""
    return str(context.initiator_name), encoded_output


def kerberos_session():
    config = web.settings()
    if config.get("kerberos_auto_login") != "1" or not KEYTAB_FILE.is_file():
        web.app.logger.warning("Kerberos desktop login requested while Kerberos auto-login is unavailable")
        return jsonify(error="Not found"), 404
    if request.headers.get(web.DESKTOP_CLIENT_HEADER) != "1" or web.demo_mode_enabled():
        return jsonify(error="Unauthorized"), 403
    if request.content_length and request.content_length > 100000:
        return jsonify(error="Invalid request"), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Invalid request"), 400

    realm = str(config.get("kerberos_realm") or "").strip().upper()
    service_principal = str(config.get("kerberos_spn") or "").strip()
    encoded_token = payload.get("token")
    if not realm or not service_principal or not isinstance(encoded_token, str):
        return jsonify(error="Invalid request"), 400
    try:
        principal, response_token = _accept_ticket(encoded_token, f"{service_principal}@{realm}")
    except ValueError as exc:
        web.app.logger.warning("Kerberos desktop token was rejected: %s", str(exc))
        return jsonify(error="Verification failed"), 401
    except ImportError:
        web.app.logger.exception("Kerberos server dependencies are missing")
        return jsonify(error="Verification unavailable"), 503
    except Exception as exc:
        web.app.logger.warning("Kerberos desktop verification failed (%s: %s)", type(exc).__name__, str(exc))
        return jsonify(error="Verification failed"), 401

    principal_realm = principal.rsplit("@", 1)[1].upper() if "@" in principal else ""
    if principal_realm != realm:
        web.app.logger.warning("Kerberos desktop principal used unexpected realm %s", principal_realm)
        return jsonify(error="Verification failed"), 401
    user = _eligible_user(principal)
    if not user:
        web.app.logger.warning("Kerberos desktop principal %s did not uniquely match an eligible OPS user", principal)
        return jsonify(error="Verification failed"), 401

    ttl = 7 * 86400
    sid = web.create_user_session_record(
        user,
        auth_provider=PROVIDER,
        session_type="desktop",
        expires_at=datetime.now() + timedelta(seconds=ttl),
        user_agent=web.desktop_login_user_agent(),
    )
    web.app.logger.info("Kerberos desktop login succeeded for OPS user ID %s", user.get("id"))
    return web.desktop_session_json(
        user,
        session_id=sid,
        include_refresh=False,
        token_ttl=ttl,
        extra={"auto_login_provider": PROVIDER, "negotiate_token": response_token},
    )

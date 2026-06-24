import ipaddress
import json
import urllib.parse
import urllib.request

from srv.web.app import *

try:
    import dns.resolver
except Exception:
    dns = None


def is_ip_literal(value):
    try:
        ipaddress.ip_address(str(value or "").strip().strip("[]"))
        return True
    except ValueError:
        return False


def has_valid_srv_records(name):
    if dns is not None:
        resolver = dns.resolver.Resolver()
        try:
            answers = resolver.resolve(name, "SRV")
        except Exception:
            return False
        for answer in answers:
            target = str(getattr(answer, "target", "") or "").strip().rstrip(".")
            port = int(getattr(answer, "port", 0) or 0)
            if target and port > 0:
                return True
        return False
    try:
        url = "https://dns.google/resolve?" + urllib.parse.urlencode({"name": name, "type": "SRV"})
        request_obj = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "OpenPagingServer"})
        with urllib.request.urlopen(request_obj, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore") or "{}")
        for answer in payload.get("Answer") or []:
            data = str((answer or {}).get("data") or "").strip()
            if data:
                return True
    except Exception:
        return False
    return False


def has_sip_service_records(host):
    host = str(host or "").strip().strip(".")
    if not host or is_ip_literal(host):
        return False
    if dns is not None:
        resolver = dns.resolver.Resolver()
        try:
            answers = resolver.resolve(host, "NAPTR")
        except Exception:
            answers = []
        for answer in answers:
            service = str(getattr(answer, "service", "") or "").upper()
            if service in {"SIP+D2U", "SIP+D2T", "SIPS+D2T"}:
                return True
    for prefix in ("_sip._udp.", "_sip._tcp.", "_sips._tcp."):
        if has_valid_srv_records(prefix + host):
            return True
    return False


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    host = str(request.args.get("host", "") or "").strip()
    if len(host) > 255:
        return jsonify(ok=False, has_service_records=False)
    return jsonify(ok=True, has_service_records=has_sip_service_records(host))

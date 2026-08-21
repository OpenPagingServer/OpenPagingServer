#!/usr/bin/env python3

import concurrent.futures
import ipaddress
import json
import os
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DNS_CHECK_INTERVAL_SECONDS = 3
DNS_QUERY_TIMEOUT_SECONDS = 4
PUBLIC_DOH_SERVICES = (
    ("Google Public DNS", "https://dns.google/resolve", {"edns_client_subnet": "0.0.0.0/0"}),
    ("Cloudflare Public DNS", "https://cloudflare-dns.com/dns-query", {}),
)
PUBLIC_DNS_SERVERS = (
    "1.1.1.1",
    "8.8.8.8",
)


def atomic_write_json(target, payload):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, target)


def write_state(job_dir, **values):
    payload = dict(values)
    payload["updated_at"] = int(time.time())
    atomic_write_json(job_dir / "challenge.json", payload)


def load_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return default
    return value


def record_challenge(job_dir, identifier, dns_name, dns_value):
    challenge_path = job_dir / "challenges.json"
    challenges = load_json(challenge_path, [])
    if not isinstance(challenges, list):
        challenges = []
    challenge = {
        "identifier": identifier,
        "dns_name": dns_name,
        "dns_value": dns_value,
    }
    if not any(
        item.get("dns_name") == dns_name and item.get("dns_value") == dns_value
        for item in challenges
        if isinstance(item, dict)
    ):
        challenges.append(challenge)
    atomic_write_json(challenge_path, challenges)
    return challenges


def normalized_doh_txt(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        pieces = []
        position = 0
        while position < len(text):
            if text[position] != '"':
                position += 1
                continue
            end = position + 1
            escaped = False
            while end < len(text):
                char = text[end]
                if char == '"' and not escaped:
                    break
                escaped = char == "\\" and not escaped
                if char != "\\":
                    escaped = False
                end += 1
            raw_piece = text[position:end + 1]
            try:
                pieces.append(json.loads(raw_piece))
            except (ValueError, json.JSONDecodeError):
                pieces.append(raw_piece.strip('"'))
            position = end + 1
        if pieces:
            return "".join(pieces)
    return text.strip('"')


def doh_txt_values(endpoint, name, extra_parameters=None):
    parameters = {"name": name, "type": "TXT"}
    parameters.update(extra_parameters or {})
    request = urllib.request.Request(
        endpoint + "?" + urllib.parse.urlencode(parameters),
        headers={"Accept": "application/dns-json", "User-Agent": "OpenPagingServer-Certbot/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=DNS_QUERY_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        if int(payload.get("Status", -1)) != 0:
            return []
    except (TypeError, ValueError):
        return None
    values = []
    for answer in payload.get("Answer", []):
        if not isinstance(answer, dict):
            continue
        try:
            if int(answer.get("type", 0)) == 16:
                values.append(normalized_doh_txt(answer.get("data")))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(values))


def dns_query_packet(name, transaction_id):
    labels = str(name or "").strip().rstrip(".").encode("idna").split(b".")
    if not labels or any(not label or len(label) > 63 for label in labels):
        raise ValueError("Invalid DNS name.")
    question_name = b"".join(bytes((len(label),)) + label for label in labels) + b"\x00"
    header = struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
    return header + question_name + struct.pack("!HH", 16, 1)


def skip_dns_name(packet, offset):
    while True:
        if offset >= len(packet):
            raise ValueError("Truncated DNS name.")
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("Truncated DNS compression pointer.")
            return offset + 2
        if length & 0xC0:
            raise ValueError("Invalid DNS label.")
        offset += 1
        if length == 0:
            return offset
        offset += length
        if offset > len(packet):
            raise ValueError("Truncated DNS label.")


def parse_dns_txt_response(packet, transaction_id):
    if len(packet) < 12:
        raise ValueError("Truncated DNS response.")
    response_id, flags, question_count, answer_count, _authority_count, _additional_count = struct.unpack(
        "!HHHHHH", packet[:12]
    )
    if response_id != transaction_id or not flags & 0x8000:
        raise ValueError("Invalid DNS response.")
    if flags & 0x000F:
        return []
    offset = 12
    for _question in range(question_count):
        offset = skip_dns_name(packet, offset)
        if offset + 4 > len(packet):
            raise ValueError("Truncated DNS question.")
        offset += 4
    values = []
    for _answer in range(answer_count):
        offset = skip_dns_name(packet, offset)
        if offset + 10 > len(packet):
            raise ValueError("Truncated DNS answer.")
        record_type, record_class, _ttl, data_length = struct.unpack("!HHIH", packet[offset:offset + 10])
        offset += 10
        data_end = offset + data_length
        if data_end > len(packet):
            raise ValueError("Truncated DNS record data.")
        if record_type == 16 and record_class == 1:
            position = offset
            pieces = []
            while position < data_end:
                piece_length = packet[position]
                position += 1
                if position + piece_length > data_end:
                    raise ValueError("Truncated DNS TXT value.")
                pieces.append(packet[position:position + piece_length].decode("utf-8", errors="replace"))
                position += piece_length
            values.append("".join(pieces))
        offset = data_end
    return list(dict.fromkeys(values))


def receive_dns_tcp_packet(connection):
    length_bytes = b""
    while len(length_bytes) < 2:
        piece = connection.recv(2 - len(length_bytes))
        if not piece:
            raise OSError("DNS server closed the TCP connection.")
        length_bytes += piece
    expected = struct.unpack("!H", length_bytes)[0]
    packet = b""
    while len(packet) < expected:
        piece = connection.recv(expected - len(packet))
        if not piece:
            raise OSError("DNS server returned a truncated TCP response.")
        packet += piece
    return packet


def dns_txt_values(server, name):
    transaction_id = int.from_bytes(os.urandom(2), "big")
    query = dns_query_packet(name, transaction_id)
    try:
        address_options = socket.getaddrinfo(server, 53, type=socket.SOCK_DGRAM)
        if not address_options:
            return None
        family, socket_type, protocol, _canonical_name, address = address_options[0]
        with socket.socket(family, socket_type, protocol) as connection:
            connection.settimeout(DNS_QUERY_TIMEOUT_SECONDS)
            connection.connect(address)
            connection.send(query)
            packet = connection.recv(65535)
        if len(packet) >= 4 and struct.unpack("!H", packet[2:4])[0] & 0x0200:
            with socket.create_connection((server, 53), timeout=DNS_QUERY_TIMEOUT_SECONDS) as connection:
                connection.sendall(struct.pack("!H", len(query)) + query)
                packet = receive_dns_tcp_packet(connection)
        return parse_dns_txt_response(packet, transaction_id)
    except (OSError, TypeError, ValueError, struct.error):
        return None


def configured_dns_servers():
    configured = [item.strip() for item in str(os.getenv("OPS_DNS_RESOLVERS", "") or "").split(",") if item.strip()]
    try:
        resolv_conf = Path("/etc/resolv.conf").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        resolv_conf = ""
    for raw_line in resolv_conf.splitlines():
        parts = raw_line.split()
        if len(parts) >= 2 and parts[0].lower() == "nameserver":
            configured.append(parts[1])
    configured.extend(PUBLIC_DNS_SERVERS)
    servers = []
    for value in configured:
        try:
            server = str(ipaddress.ip_address(value.strip().strip("[]")))
        except ValueError:
            continue
        if server not in servers:
            servers.append(server)
    return servers


def public_dns_observations(challenges):
    names = list(dict.fromkeys(challenge["dns_name"] for challenge in challenges))
    observations = {service_name: {} for service_name, _endpoint, _parameters in PUBLIC_DOH_SERVICES}
    dns_servers = configured_dns_servers()
    observations.update({f"DNS resolver {server}": {} for server in dns_servers})
    service_count = len(PUBLIC_DOH_SERVICES) + len(dns_servers)
    worker_count = min(32, max(1, len(names) * service_count))
    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        for service_name, endpoint, parameters in PUBLIC_DOH_SERVICES:
            for name in names:
                future = executor.submit(doh_txt_values, endpoint, name, parameters)
                jobs[future] = (service_name, name)
        for server in dns_servers:
            service_name = f"DNS resolver {server}"
            for name in names:
                future = executor.submit(dns_txt_values, server, name)
                jobs[future] = (service_name, name)
        for future in concurrent.futures.as_completed(jobs):
            service_name, name = jobs[future]
            try:
                observations[service_name][name] = future.result()
            except Exception:
                observations[service_name][name] = None
    return observations


def ready_public_resolvers(challenges, observations):
    return [
        service_name
        for service_name in observations
        if all(
            challenge["dns_value"] in (observations.get(service_name, {}).get(challenge["dns_name"]) or [])
            for challenge in challenges
        )
    ]


def public_dns_ready(challenges, observations):
    return bool(ready_public_resolvers(challenges, observations))


def dns_wait_status_message(observations):
    unavailable = [
        service_name
        for service_name, answers in observations.items()
        if any(values is None for values in answers.values())
    ]
    returned_values = [
        value
        for answers in observations.values()
        for values in answers.values()
        for value in (values or [])
    ]
    if returned_values:
        return "Public DNS returned TXT record(s), but none matched the expected Certbot value."
    if len(unavailable) == len(observations):
        return f"The server could not query a public DNS resolver. Retrying in {DNS_CHECK_INTERVAL_SECONDS} seconds."
    if unavailable:
        return (
            f"The TXT record is not visible yet; {', '.join(unavailable)} could not be queried. "
            f"Retrying in {DNS_CHECK_INTERVAL_SECONDS} seconds."
        )
    return f"The TXT record is not visible yet. Checking public DNS again in {DNS_CHECK_INTERVAL_SECONDS} seconds."


def main():
    job_dir_value = str(os.getenv("OPS_CERTBOT_JOB_DIR", "") or "").strip()
    identifier = str(os.getenv("CERTBOT_IDENTIFIER") or os.getenv("CERTBOT_DOMAIN") or "").strip()
    validation = str(os.getenv("CERTBOT_VALIDATION", "") or "").strip()
    if not job_dir_value or not identifier or not validation:
        print("OpenPagingServer Certbot DNS hook is missing its job state or challenge variables.", file=sys.stderr)
        return 2

    job_dir = Path(job_dir_value)
    dns_name = "_acme-challenge." + identifier.lstrip("*.").rstrip(".")
    try:
        remaining = int(os.getenv("CERTBOT_REMAINING_CHALLENGES", "0") or 0)
    except ValueError:
        remaining = 0
    all_identifiers = str(os.getenv("CERTBOT_ALL_IDENTIFIERS") or os.getenv("CERTBOT_ALL_DOMAINS") or identifier).split(",")
    total = max(1, len([item for item in all_identifiers if item.strip()]))
    challenges = record_challenge(job_dir, identifier, dns_name, validation)

    if remaining > 0:
        write_state(
            job_dir,
            status="collecting",
            status_message="Certbot is preparing all DNS challenges.",
            challenge_number=len(challenges),
            challenge_total=total,
        )
        return 0

    first = challenges[0]
    write_state(
        job_dir,
        status="waiting_public",
        status_message="Checking public DNS for the TXT record now.",
        dns_name=first["dns_name"],
        dns_value=first["dns_value"],
        challenges=challenges,
        challenge_number=len(challenges),
        challenge_total=len(challenges),
        next_check_at=int(time.time()),
    )

    while True:
        if (job_dir / "cancel").exists():
            write_state(job_dir, status="cancelled", challenges=challenges)
            return 2
        observations = public_dns_observations(challenges)
        ready_resolvers = ready_public_resolvers(challenges, observations)
        ready = public_dns_ready(challenges, observations)
        write_state(
            job_dir,
            status="validating" if ready else "waiting_public",
            status_message=(
                f"{', '.join(ready_resolvers)} returned every expected TXT record. Certbot is validating them."
                if ready
                else dns_wait_status_message(observations)
            ),
            dns_name=first["dns_name"],
            dns_value=first["dns_value"],
            challenges=challenges,
            external_observations=observations,
            ready_resolvers=ready_resolvers,
            challenge_number=len(challenges),
            challenge_total=len(challenges),
            next_check_at=int(time.time() + DNS_CHECK_INTERVAL_SECONDS),
        )
        if ready:
            return 0
        for _second in range(DNS_CHECK_INTERVAL_SECONDS):
            if (job_dir / "cancel").exists():
                write_state(job_dir, status="cancelled", challenges=challenges)
                return 2
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())

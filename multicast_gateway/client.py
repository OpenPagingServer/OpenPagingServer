#!/usr/bin/env python3

import getpass
import json
import os
import signal
import socket
import ssl
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from lib import (
    DEFAULT_PORT,
    FileInstanceLock,
    GatewayPeerStore,
    MULTICAST_GATEWAY_PROVISION_PATH,
    NODE_KIND_GATEWAY,
    default_gateway_db_path,
    default_gateway_key_path,
    default_gateway_lock_path,
    host_port_for_display,
    load_identity,
    normalize_host,
    normalize_label,
    normalize_public_key,
    parse_peer_target,
    peer_status_from_timestamp,
    public_key_preview,
)
from runtime import GatewayRuntime


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def prompt(message, default=""):
    raw = input(message)
    if raw == "" and default != "":
        return default
    return raw


def normalized_server_url(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Server address is required.")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid HTTP or HTTPS server address.")
    base = parsed._replace(path="/", params="", query="", fragment="")
    return base.geturl()


def detect_advertise_host(remote_host):
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect((remote_host, 80))
        address = str(probe.getsockname()[0] or "").strip()
        if address and not address.startswith("127."):
            return address
    except OSError:
        return ""
    finally:
        if probe is not None:
            probe.close()
    return ""


def post_json(url, payload, verify_tls=True):
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()
    with urlopen(request, timeout=20, context=context) as response:
        text = response.read().decode("utf-8")
    return json.loads(text or "{}")


def get_json(url, verify_tls=True):
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()
    with urlopen(request, timeout=20, context=context) as response:
        text = response.read().decode("utf-8")
    return json.loads(text or "{}")


def validate_server_url(server_url, verify_tls=True):
    endpoint = urljoin(server_url, MULTICAST_GATEWAY_PROVISION_PATH)
    payload = get_json(endpoint, verify_tls=verify_tls)
    if str(payload.get("status") or "") != "success" or str(payload.get("service") or "") != "multicast-gateway-provision":
        raise ValueError("The server is reachable, but it is not a valid Open Paging Server Multicast Gateway provisioning endpoint.")
    return payload


def ensure_local_label(store, preferred=""):
    current = normalize_label(store.get_setting("local_label", ""))
    if current:
        return current
    value = normalize_label(preferred) or socket.gethostname()
    store.set_setting("local_label", value)
    return value


def show_public_key(identity):
    print(f"Public Key: {identity['public_key']}")
    print("")


def show_server_list(store):
    rows = store.list_peers()
    if not rows:
        print("No provisioned servers.")
        print("")
        return
    now_value = datetime.now().timestamp()
    for row in rows:
        print(f"Label: {row.get('label') or '(unlabeled)'}")
        print(f"IPaddr: {host_port_for_display(row.get('host'), row.get('port'))}")
        print(f"PublicKey: {public_key_preview(row.get('public_key'))}")
        print(f"Status: {peer_status_from_timestamp(row.get('last_seen'), now=now_value)}")
        if row.get("last_ip"):
            print(f"Online IP: {row.get('last_ip')}")
        print("")


def add_server_manually(store):
    label = normalize_label(prompt("Label: ").strip())
    target = prompt("IP address: ").strip()
    public_key = prompt("Public key: ").strip()
    host, port = parse_peer_target(target)
    store.upsert_peer(label or host, host, port, normalize_public_key(public_key), peer_type="gateway", enabled=1)
    print("Server added.")
    print("")


def remove_server(store):
    rows = store.list_peers()
    if not rows:
        print("No servers to remove.")
        print("")
        return
    for index, row in enumerate(rows, start=1):
        print(f"{index}.) {row.get('label') or row.get('host')} ({host_port_for_display(row.get('host'), row.get('port'))})")
    print("")
    choice = prompt("Select server to remove: ").strip()
    if not choice.isdigit():
        print("Invalid selection.")
        print("")
        return
    index = int(choice) - 1
    if index < 0 or index >= len(rows):
        print("Invalid selection.")
        print("")
        return
    store.delete_peer(rows[index].get("id"))
    print("Server removed.")
    print("")


def provision_to_ops(store, identity):
    server_url = normalized_server_url(prompt("Server address: ").strip())
    parsed = urlparse(server_url)
    if parsed.scheme == "http":
        print("Warning: HTTP is in use. Your admin username and password will not be encrypted.")
        print("")
    verify_tls = True
    while True:
        try:
            validate_server_url(server_url, verify_tls=verify_tls)
            break
        except ssl.SSLCertVerificationError:
            print("Warning: The server certificate is untrusted.")
            answer = prompt("Continue anyway? (y/N): ").strip().lower()
            if answer != "y":
                print("")
                return
            verify_tls = False
        except HTTPError as exc:
            print(f"Server validation failed: HTTP {exc.code}")
            print("")
            return
        except URLError as exc:
            print(f"Server validation failed: {exc}")
            print("")
            return
        except Exception as exc:
            print(f"Server validation failed: {exc}")
            print("")
            return
    username = prompt("Admin username: ").strip()
    password = getpass.getpass("Admin password: ")
    ops_label = normalize_label(prompt("Label for Open Paging Server in Multicast Gateway: ").strip())
    local_label = normalize_label(prompt("Label for this Multicast Gateway in Open Paging Server: ").strip())
    if local_label:
        store.set_setting("local_label", local_label)
    advertise_host = detect_advertise_host(parsed.hostname or "")
    payload = {
        "username": username,
        "password": password,
        "public_key": identity["public_key"],
        "gateway_label": local_label or ensure_local_label(store),
        "ops_label": ops_label,
        "udp_host": advertise_host,
        "udp_port": DEFAULT_PORT,
    }
    endpoint = urljoin(server_url, MULTICAST_GATEWAY_PROVISION_PATH)
    while True:
        try:
            response = post_json(endpoint, payload, verify_tls=verify_tls)
            break
        except ssl.SSLCertVerificationError:
            print("Warning: The server certificate is untrusted.")
            answer = prompt("Continue anyway? (y/N): ").strip().lower()
            if answer != "y":
                print("")
                return
            verify_tls = False
        except HTTPError as exc:
            text = exc.read().decode("utf-8", errors="ignore")
            try:
                payload = json.loads(text or "{}")
                message = payload.get("message") or payload.get("error") or str(exc)
            except Exception:
                message = text or str(exc)
            print(message)
            print("")
            return
        except URLError as exc:
            print(f"Provisioning failed: {exc}")
            print("")
            return
        except Exception as exc:
            print(f"Provisioning failed: {exc}")
            print("")
            return
    if str(response.get("status") or "") != "success":
        print(str(response.get("message") or "Provisioning failed."))
        print("")
        return
    peer_key = normalize_public_key(response.get("public_key"))
    peer_label = ops_label or normalize_label(response.get("product_name")) or "Open Paging Server"
    udp_host = normalize_host(response.get("udp_host")) or normalize_host(parsed.hostname) or ""
    udp_port = int(response.get("udp_port") or DEFAULT_PORT)
    store.upsert_peer(peer_label, udp_host, udp_port, peer_key, peer_type="ops", enabled=1)
    print("Provisioning complete.")
    print("")


def first_launch_setup(store, identity):
    print("Welcome to Multicast Gateway")
    print("")
    print("Please select an option:")
    print("")
    print("1.) Provision to Open Paging Server")
    print("2.) Add server manually")
    print("")
    choice = prompt("Selection: ").strip()
    if choice == "1":
        provision_to_ops(store, identity)
        return
    if choice == "2":
        add_server_manually(store)
        return
    print("Invalid selection.")
    print("")


def console_menu(store, identity):
    if not store.list_peers():
        first_launch_setup(store, identity)
    while True:
        show_public_key(identity)
        show_server_list(store)
        print("Please select an option:")
        print("")
        print("1.) Start Multicast Gateway")
        print("2.) Provision to Open Paging Server")
        print("3.) Add server manually")
        print("4.) Remove server")
        print("5.) Show public key")
        print("6.) Exit")
        print("")
        choice = prompt("Selection: ").strip()
        if choice == "1":
            return "start"
        if choice == "2":
            provision_to_ops(store, identity)
            continue
        if choice == "3":
            add_server_manually(store)
            continue
        if choice == "4":
            remove_server(store)
            continue
        if choice == "5":
            continue
        if choice == "6":
            return "exit"
        print("Invalid selection.")
        print("")


runtime = None
instance_lock = None


def shutdown(_sig=None, _frame=None):
    global runtime, instance_lock
    if runtime is not None:
        runtime.stop()
    if instance_lock is not None:
        instance_lock.close()


def start_gateway_runtime(store, identity):
    global runtime, instance_lock
    instance_lock = FileInstanceLock(default_gateway_lock_path(BASE_DIR))
    if not instance_lock.acquire():
        print("Multicast Gateway is already running", flush=True)
        print("")
        return False
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    runtime = GatewayRuntime(
        identity=identity,
        peer_store=store,
        node_kind=NODE_KIND_GATEWAY,
        local_label=ensure_local_label(store),
        bind_host=os.getenv("OPS_MULTICAST_GATEWAY_BIND", "0.0.0.0"),
        port=DEFAULT_PORT,
        enable_capture=True,
        enable_rebroadcast=True,
        allow_local_source=False,
        log=log,
    )
    try:
        runtime.serve()
    finally:
        shutdown()
    return True


def run_gateway(review=False):
    store = GatewayPeerStore(default_gateway_db_path(BASE_DIR))
    identity = load_identity(default_gateway_key_path(BASE_DIR))
    ensure_local_label(store)
    if review:
        while True:
            action = console_menu(store, identity)
            if action != "start":
                return 0
            if start_gateway_runtime(store, identity):
                return 0
        return 0
    start_gateway_runtime(store, identity)
    return 0


def main(argv=None):
    args = list(argv if argv is not None else sys.argv[1:])
    review = "-r" in args or "--review" in args
    return run_gateway(review=review)


if __name__ == "__main__":
    raise SystemExit(main())

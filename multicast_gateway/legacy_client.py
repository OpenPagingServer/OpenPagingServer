#!/usr/bin/env python3

import ipaddress
import json
import os
import select
import socket
import struct
import sys
import threading
import time
import uuid
from datetime import datetime

DEFAULT_PORT = int(os.getenv("OPS_MULTICAST_GATEWAY_PORT", "8710"))
UDP_REGISTER_INTERVAL = float(os.getenv("OPS_MULTICAST_GATEWAY_UDP_REGISTER_INTERVAL", "3"))
TCP_HEARTBEAT_INTERVAL = float(os.getenv("OPS_MULTICAST_GATEWAY_TCP_HEARTBEAT_INTERVAL", "10"))
udp_sockets = {}


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def recv_exact(sock, size, deadline=None):
    data = bytearray()
    while len(data) < size:
        try:
            chunk = sock.recv(size - len(data))
        except socket.timeout:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("timed out reading frame")
            continue
        if not chunk:
            raise OSError("connection closed")
        data.extend(chunk)
    return bytes(data)


def recv_frame(sock, deadline=None):
    header_len, payload_len = struct.unpack("!II", recv_exact(sock, 8, deadline))
    if header_len <= 0 or header_len > 65536:
        raise ValueError("invalid header length")
    if payload_len < 0 or payload_len > 1048576:
        raise ValueError("invalid payload length")
    header = json.loads(recv_exact(sock, header_len, deadline).decode("utf-8"))
    payload = recv_exact(sock, payload_len, deadline) if payload_len else b""
    return header, payload


def parse_datagram_frame(data):
    if len(data) < 8:
        raise ValueError("frame too short")
    header_len, payload_len = struct.unpack("!II", data[:8])
    expected = 8 + header_len + payload_len
    if header_len <= 0 or payload_len < 0 or expected != len(data):
        raise ValueError("invalid datagram frame")
    header = json.loads(data[8:8 + header_len].decode("utf-8"))
    payload = data[8 + header_len:]
    return header, payload


def default_ipv4_multicast_interface():
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 53))
        address = str(probe.getsockname()[0] or "").strip()
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    finally:
        if probe is not None:
            probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM):
            address = str(info[4][0] or "").strip()
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    return None


def parse_target(raw):
    value = str(raw or "").strip()
    if not value:
        raise ValueError("an IP address is required")
    if value.startswith("[") and "]" in value:
        host, _, rest = value[1:].partition("]")
        port = DEFAULT_PORT
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
        return host, port
    if value.count(":") == 1 and "." in value.split(":", 1)[0]:
        host, port_text = value.split(":", 1)
        return host.strip(), int(port_text)
    return value, DEFAULT_PORT


def prompt_target():
    while True:
        try:
            host, port = parse_target(input("Open Paging Server IP address: "))
        except Exception as exc:
            log(str(exc))
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            log("Enter a valid IPv4 or IPv6 address.")
            continue
        return host, port


def udp_socket_for_family(family):
    sock = udp_sockets.get(family)
    if sock is not None:
        return sock
    if family == 6:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        interface = default_ipv4_multicast_interface()
        if interface:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
            except OSError:
                pass
    udp_sockets[family] = sock
    return sock


def rebroadcast_packet(header, payload):
    address = str(header.get("address") or "").split("%", 1)[0].strip()
    if not address:
        return
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return
    if not ip.is_multicast:
        return
    try:
        port = int(header.get("port") or 0)
    except (TypeError, ValueError):
        return
    if port < 1 or port > 65535:
        return
    ttl = header.get("ttl")
    try:
        ttl_value = int(ttl) if ttl not in (None, "") else 1
    except (TypeError, ValueError):
        ttl_value = 1
    if ip.version == 6:
        sock = udp_socket_for_family(6)
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, ttl_value)
        except OSError:
            pass
        sock.sendto(payload, (str(ip), port, 0, 0))
        return
    sock = udp_socket_for_family(4)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl_value)
    except OSError:
        pass
    sock.sendto(payload, (str(ip), port))


def configure_tcp_socket(sock):
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass


def udp_register_payload(relay_id):
    return json.dumps(
        {"kind": "register", "relay_id": relay_id, "service": "multicastgateway"},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def udp_loop(udp_sock, relay_id, stop_event):
    payload = udp_register_payload(relay_id)
    next_register = 0.0
    while not stop_event.is_set():
        now = time.monotonic()
        if now >= next_register:
            try:
                udp_sock.send(payload)
            except OSError:
                pass
            next_register = now + UDP_REGISTER_INTERVAL
        try:
            data = udp_sock.recv(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            header, packet = parse_datagram_frame(data)
            rebroadcast_packet(header, packet)
        except Exception:
            continue


def relay_hello(relay_id):
    return json.dumps(
        {
            "role": "relay",
            "relay_id": relay_id,
            "service": "multicastgateway",
            "supports_udp": True,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"


def connect_and_forward(host, port):
    relay_id = uuid.uuid4().hex
    server_ip = ipaddress.ip_address(host)
    server_endpoint = (host, port, 0, 0) if server_ip.version == 6 else (host, port)
    udp_family = socket.AF_INET6 if server_ip.version == 6 else socket.AF_INET
    udp_sock = socket.socket(udp_family, socket.SOCK_DGRAM)
    udp_sock.settimeout(1.0)
    udp_sock.connect(server_endpoint)
    stop_event = threading.Event()
    udp_thread = threading.Thread(target=udp_loop, args=(udp_sock, relay_id, stop_event), daemon=True)
    udp_thread.start()
    connected_once = False
    control_connected = False
    last_loss_log = 0.0
    while True:
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=5.0)
            configure_tcp_socket(sock)
            sock.settimeout(1.0)
            sock.sendall(relay_hello(relay_id))
            if not connected_once:
                log(f"connected to {host}:{port} using udp real-time with tcp fallback")
                connected_once = True
            elif not control_connected:
                log(f"reconnected to {host}:{port}")
            control_connected = True
            next_heartbeat = time.monotonic() + TCP_HEARTBEAT_INTERVAL
            while True:
                now = time.monotonic()
                if now >= next_heartbeat:
                    sock.sendall(b"\n")
                    next_heartbeat = now + TCP_HEARTBEAT_INTERVAL
                readable, _, _ = select.select([sock], [], [], 0.5)
                if not readable:
                    continue
                deadline = time.monotonic() + 5.0
                header, payload = recv_frame(sock, deadline=deadline)
                rebroadcast_packet(header, payload)
        except KeyboardInterrupt:
            stop_event.set()
            raise
        except Exception as exc:
            control_connected = False
            now = time.monotonic()
            if now - last_loss_log >= 10.0:
                log(f"tcp control reconnecting: {exc}")
                last_loss_log = now
            time.sleep(2)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def main():
    if len(sys.argv) > 1:
        host, port = parse_target(sys.argv[1])
    else:
        host, port = prompt_target()
    connect_and_forward(host, port)


if __name__ == "__main__":
    main()

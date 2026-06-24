#!/usr/bin/env python3

import ipaddress
import json
import os
import select
import signal
import socket
import struct
import threading
import time
from collections import deque
from datetime import datetime

DEFAULT_HOST = os.getenv("OPS_MULTICAST_GATEWAY_BIND", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("OPS_MULTICAST_GATEWAY_PORT", "8710"))
RELAY_UDP_FRESH_SECONDS = float(os.getenv("OPS_MULTICAST_GATEWAY_RELAY_UDP_FRESH_SECONDS", "15"))
RELAY_TCP_QUEUE_LIMIT = int(os.getenv("OPS_MULTICAST_GATEWAY_RELAY_TCP_QUEUE_LIMIT", "32"))
shutdown_event = threading.Event()
relay_clients = {}
relay_lock = threading.Lock()
udp_server_socks = {}


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("connection closed")
        data.extend(chunk)
    return bytes(data)


def recv_line(sock, limit=8192):
    data = bytearray()
    while len(data) < limit:
        chunk = sock.recv(1)
        if not chunk:
            break
        if chunk == b"\n":
            break
        data.extend(chunk)
    return bytes(data)


def frame_bytes(header, payload):
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_bytes = bytes(payload or b"")
    return struct.pack("!II", len(header_bytes), len(payload_bytes)) + header_bytes + payload_bytes


def recv_frame(sock):
    header_len, payload_len = struct.unpack("!II", recv_exact(sock, 8))
    if header_len <= 0 or header_len > 65536:
        raise ValueError("invalid header length")
    if payload_len < 0 or payload_len > 1048576:
        raise ValueError("invalid payload length")
    header = json.loads(recv_exact(sock, header_len).decode("utf-8"))
    payload = recv_exact(sock, payload_len) if payload_len else b""
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


def normalize_packet_header(header):
    if not isinstance(header, dict):
        return None
    address = str(header.get("address") or "").split("%", 1)[0].strip()
    if not address:
        return None
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if not ip.is_multicast:
        return None
    try:
        port = int(header.get("port") or 0)
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    normalized = {
        "address": str(ip),
        "port": port,
        "family": 6 if ip.version == 6 else 4,
    }
    ttl = header.get("ttl")
    if ttl not in (None, ""):
        try:
            normalized["ttl"] = int(ttl)
        except (TypeError, ValueError):
            pass
    return normalized


def configure_tcp_socket(sock):
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass


def listener_bind_specs(host, port):
    value = str(host or "").strip()
    if value in {"", "*", "0.0.0.0"}:
        specs = [(socket.AF_INET, ("0.0.0.0", port))]
        if socket.has_ipv6:
            specs.append((socket.AF_INET6, ("::", port, 0, 0)))
        return specs
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        infos = socket.getaddrinfo(value, port, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, socket.AI_PASSIVE)
        specs = []
        seen = set()
        for family, _socktype, _proto, _canonname, sockaddr in infos:
            if family == socket.AF_INET:
                bind_addr = (sockaddr[0], sockaddr[1])
            elif family == socket.AF_INET6:
                bind_addr = (sockaddr[0], sockaddr[1], 0, 0)
            else:
                continue
            key = (family, bind_addr)
            if key in seen:
                continue
            seen.add(key)
            specs.append(key)
        if specs:
            return specs
        raise ValueError(f"unable to resolve bind host {value!r}")
    if ip.version == 6:
        return [(socket.AF_INET6, (value, port, 0, 0))]
    return [(socket.AF_INET, (value, port))]


def format_bind_addr(address):
    if isinstance(address, tuple) and len(address) >= 4:
        return f"[{address[0]}]:{address[1]}"
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return str(address)


def create_bound_socket(family, socktype, bind_addr):
    sock = socket.socket(family, socktype)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6 and hasattr(socket, "IPPROTO_IPV6") and hasattr(socket, "IPV6_V6ONLY"):
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except OSError:
            pass
    sock.bind(bind_addr)
    return sock


def udp_socket_for_peer(address):
    if not isinstance(address, tuple):
        return None
    family = socket.AF_INET6 if len(address) >= 4 else socket.AF_INET
    return udp_server_socks.get(family)


class RelayClient:
    def __init__(self, relay_id):
        self.relay_id = relay_id
        self.peer = ("", 0)
        self.conn = None
        self.udp_addr = None
        self.last_udp = 0.0
        self.lock = threading.Lock()
        self.queue = deque()
        self.queue_event = threading.Event()
        self.stop_event = threading.Event()
        self.writer_thread = threading.Thread(target=self.writer_loop, daemon=True)
        self.writer_thread.start()

    def set_connection(self, conn, peer):
        configure_tcp_socket(conn)
        previous = None
        with self.lock:
            previous = self.conn
            self.conn = conn
            self.peer = peer
        if previous is not None and previous is not conn:
            try:
                previous.close()
            except OSError:
                pass
        self.queue_event.set()

    def clear_connection(self, conn=None):
        previous = None
        with self.lock:
            if conn is not None and self.conn is not conn:
                return
            previous = self.conn
            self.conn = None
        if previous is not None:
            try:
                previous.close()
            except OSError:
                pass

    def update_udp(self, address):
        with self.lock:
            changed = address != self.udp_addr
            self.udp_addr = address
            self.last_udp = time.monotonic()
        return changed

    def udp_ready(self):
        with self.lock:
            return self.udp_addr is not None and (time.monotonic() - self.last_udp) <= RELAY_UDP_FRESH_SECONDS

    def queue_packet(self, packet):
        if self.udp_ready():
            with self.lock:
                udp_addr = self.udp_addr
            udp_sock = udp_socket_for_peer(udp_addr)
            if udp_sock is not None:
                try:
                    udp_sock.sendto(packet, udp_addr)
                    return
                except OSError:
                    pass
        with self.lock:
            if self.conn is None:
                return
            while len(self.queue) >= RELAY_TCP_QUEUE_LIMIT:
                self.queue.popleft()
            self.queue.append(packet)
        self.queue_event.set()

    def writer_loop(self):
        while not self.stop_event.is_set():
            self.queue_event.wait(0.5)
            self.queue_event.clear()
            while not self.stop_event.is_set():
                with self.lock:
                    conn = self.conn
                    packet = self.queue.popleft() if self.queue else None
                if conn is None or packet is None:
                    break
                try:
                    conn.sendall(packet)
                except OSError:
                    self.clear_connection(conn)
                    break

    def stop(self):
        self.stop_event.set()
        self.queue_event.set()
        self.clear_connection()


def relay_snapshot():
    with relay_lock:
        return list(relay_clients.values())


def get_relay(relay_id):
    key = str(relay_id or "").strip()
    if not key:
        raise ValueError("relay_id is required")
    with relay_lock:
        relay = relay_clients.get(key)
        if relay is None:
            relay = RelayClient(key)
            relay_clients[key] = relay
        return relay


def remove_all_relays():
    with relay_lock:
        relays = list(relay_clients.values())
        relay_clients.clear()
    for relay in relays:
        relay.stop()


def broadcast_packet(header, payload):
    packet = frame_bytes(header, payload)
    for relay in relay_snapshot():
        relay.queue_packet(packet)


def handle_source(conn, peer):
    configure_tcp_socket(conn)
    log(f"multicast gateway source connected peer={peer[0]}:{peer[1]}")
    try:
        while not shutdown_event.is_set():
            header, payload = recv_frame(conn)
            normalized = normalize_packet_header(header)
            if normalized is None:
                continue
            broadcast_packet(normalized, payload)
    except Exception as exc:
        log(f"multicast gateway source closed peer={peer[0]}:{peer[1]} error={exc}")
    finally:
        try:
            conn.close()
        except OSError:
            pass


def handle_relay(conn, peer, hello):
    relay = get_relay(hello.get("relay_id"))
    relay.set_connection(conn, peer)
    log(f"multicast gateway relay connected peer={peer[0]}:{peer[1]} relay={relay.relay_id}")
    try:
        while not shutdown_event.is_set():
            readable, _, _ = select.select([conn], [], [], 1.0)
            if not readable:
                continue
            chunk = conn.recv(4096)
            if not chunk:
                break
    except OSError as exc:
        log(f"multicast gateway relay closed peer={peer[0]}:{peer[1]} relay={relay.relay_id} error={exc}")
    finally:
        relay.clear_connection(conn)


def handle_client(conn, peer):
    try:
        raw = recv_line(conn)
        if not raw:
            conn.close()
            return
        hello = json.loads(raw.decode("utf-8"))
        role = str(hello.get("role") or "").strip().lower()
        if role == "source":
            handle_source(conn, peer)
            return
        if role == "relay":
            handle_relay(conn, peer, hello)
            return
        raise ValueError("invalid role")
    except Exception as exc:
        log(f"multicast gateway handshake error peer={peer[0]}:{peer[1]} error={exc}")
        try:
            conn.close()
        except OSError:
            pass


def udp_listener(sock, stop_event):
    while not stop_event.is_set():
        try:
            data, address = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            payload = json.loads(data.decode("utf-8"))
            if str(payload.get("kind") or "").strip().lower() != "register":
                continue
            relay = get_relay(payload.get("relay_id"))
            if relay.update_udp(address):
                log(f"multicast gateway relay udp active relay={relay.relay_id} via={address[0]}:{address[1]}")
        except Exception:
            continue


def tcp_accept_loop(sock, stop_event):
    while not stop_event.is_set():
        try:
            conn, peer = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=handle_client, args=(conn, peer), daemon=True).start()


def serve(host=DEFAULT_HOST, port=DEFAULT_PORT, stop_event=None):
    global udp_server_socks
    stop_event = stop_event or shutdown_event
    tcp_servers = []
    udp_servers = []
    listen_addrs = []
    for family, bind_addr in listener_bind_specs(host, port):
        tcp_server = None
        udp_server = None
        try:
            tcp_server = create_bound_socket(family, socket.SOCK_STREAM, bind_addr)
            tcp_server.listen(25)
            tcp_server.settimeout(1.0)
            udp_server = create_bound_socket(family, socket.SOCK_DGRAM, bind_addr)
            udp_server.settimeout(1.0)
            tcp_servers.append(tcp_server)
            udp_servers.append(udp_server)
            udp_server_socks[family] = udp_server
            listen_addrs.append(format_bind_addr(bind_addr))
        except OSError as exc:
            log(f"multicast gateway bind skipped address={format_bind_addr(bind_addr)} error={exc}")
            if tcp_server is not None:
                try:
                    tcp_server.close()
                except OSError:
                    pass
            if udp_server is not None:
                try:
                    udp_server.close()
                except OSError:
                    pass
    if not tcp_servers or not udp_servers:
        raise OSError(f"unable to bind multicast gateway on {host}:{port}")
    for udp_server in udp_servers:
        threading.Thread(target=udp_listener, args=(udp_server, stop_event), daemon=True).start()
    for tcp_server in tcp_servers:
        threading.Thread(target=tcp_accept_loop, args=(tcp_server, stop_event), daemon=True).start()
    log(f"multicast gateway listening on {', '.join(listen_addrs)} tcp+udp")
    try:
        while not stop_event.is_set():
            time.sleep(0.25)
    finally:
        for tcp_server in tcp_servers:
            try:
                tcp_server.close()
            except OSError:
                pass
        for udp_server in udp_servers:
            try:
                udp_server.close()
            except OSError:
                pass
        udp_server_socks = {}
        remove_all_relays()


def shutdown(_sig=None, _frame=None):
    shutdown_event.set()


def main():
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    serve()


if __name__ == "__main__":
    main()

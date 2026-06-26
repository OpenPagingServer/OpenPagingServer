#!/usr/bin/env python3

import ipaddress
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from lib import (
    CODE_HELLO,
    CODE_LOCAL_SOURCE,
    CODE_MEDIA,
    DEFAULT_PORT,
    DuplicatePacketFilter,
    HELLO_INTERVAL_SECONDS,
    NODE_KIND_GATEWAY,
    PEER_ADDRESS_CACHE_SECONDS,
    PRESENCE_UPDATE_INTERVAL_SECONDS,
    decode_hello_body,
    decode_local_source_packet,
    decode_media_body,
    decode_secure_packet,
    encode_hello_body,
    encode_media_body,
    encode_secure_packet,
    monotonic_now,
    rebroadcast_multicast_packet,
)


def listener_bind_specs(host, port):
    value = str(host or "").strip()
    if value in {"", "*", "0.0.0.0"}:
        specs = [(socket.AF_INET, ("0.0.0.0", port))]
        if socket.has_ipv6:
            specs.append((socket.AF_INET6, ("::", port, 0, 0)))
        return specs
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    if ip.version == 6:
        return [(socket.AF_INET6, (value, port, 0, 0))]
    return [(socket.AF_INET, (value, port))]


def create_bound_socket(family, bind_addr):
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6 and hasattr(socket, "IPPROTO_IPV6") and hasattr(socket, "IPV6_V6ONLY"):
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except OSError:
            pass
    sock.bind(bind_addr)
    sock.settimeout(1.0)
    return sock


def format_bind_addr(address):
    if isinstance(address, tuple) and len(address) >= 4:
        return f"[{address[0]}]:{address[1]}"
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return str(address)


def resolve_peer_addresses(host, port):
    targets = []
    try:
        for family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_DGRAM):
            if family == socket.AF_INET:
                targets.append((family, (sockaddr[0], sockaddr[1])))
            elif family == socket.AF_INET6:
                targets.append((family, (sockaddr[0], sockaddr[1], 0, 0)))
    except OSError:
        return []
    seen = set()
    unique = []
    for item in targets:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def parse_ipv4_multicast_udp_frame(frame):
    data = bytes(frame or b"")
    if len(data) < 42:
        return None
    ether_type = struct.unpack("!H", data[12:14])[0]
    if ether_type != 0x0800:
        return None
    version_ihl = data[14]
    if version_ihl >> 4 != 4:
        return None
    ihl = (version_ihl & 0x0F) * 4
    if ihl < 20 or len(data) < 14 + ihl + 8:
        return None
    protocol = data[23]
    if protocol != 17:
        return None
    ttl = data[22]
    destination = str(ipaddress.ip_address(data[30:34]))
    if not ipaddress.ip_address(destination).is_multicast:
        return None
    udp_offset = 14 + ihl
    destination_port = struct.unpack("!H", data[udp_offset + 2:udp_offset + 4])[0]
    udp_length = struct.unpack("!H", data[udp_offset + 4:udp_offset + 6])[0]
    if udp_length < 8 or len(data) < udp_offset + udp_length:
        return None
    payload = data[udp_offset + 8:udp_offset + udp_length]
    return {"address": destination, "port": destination_port, "ttl": ttl, "family": 4, "payload": payload}


class GatewayRuntime:
    def __init__(
        self,
        identity,
        peer_store,
        node_kind,
        local_label,
        bind_host="0.0.0.0",
        port=DEFAULT_PORT,
        enable_capture=False,
        enable_rebroadcast=True,
        allow_local_source=False,
        log=None,
    ):
        self.identity = identity
        self.peer_store = peer_store
        self.node_kind = int(node_kind or NODE_KIND_GATEWAY)
        self.local_label = str(local_label or "").strip()
        self.bind_host = str(bind_host or "0.0.0.0").strip() or "0.0.0.0"
        self.port = int(port or DEFAULT_PORT)
        self.enable_capture = bool(enable_capture)
        self.enable_rebroadcast = bool(enable_rebroadcast)
        self.allow_local_source = bool(allow_local_source)
        self.log = log or (lambda msg: None)
        self.stop_event = threading.Event()
        self.duplicate_filter = DuplicatePacketFilter()
        self.server_sockets = {}
        self.peer_cache = {}
        self.peer_cache_at = 0.0
        self.peer_cache_lock = threading.Lock()
        self.peer_address_cache = {}
        self.peer_address_cache_lock = threading.Lock()
        self.presence_cache = {}
        self.presence_cache_lock = threading.Lock()

    def refresh_peers(self, force=False):
        now_value = monotonic_now()
        with self.peer_cache_lock:
            if not force and (now_value - self.peer_cache_at) < 2.0:
                return self.peer_cache
            self.peer_cache = self.peer_store.peer_map()
            self.peer_cache_at = now_value
            return self.peer_cache

    def _resolve_peer_addresses(self, host, port, force=False):
        host_value = str(host or "").strip()
        port_value = int(port or self.port)
        if not host_value:
            return []
        cache_key = (host_value, port_value)
        now_value = monotonic_now()
        with self.peer_address_cache_lock:
            cached = self.peer_address_cache.get(cache_key)
            if cached is not None and not force and (now_value - cached[0]) < PEER_ADDRESS_CACHE_SECONDS:
                return cached[1]
        targets = resolve_peer_addresses(host_value, port_value)
        with self.peer_address_cache_lock:
            self.peer_address_cache[cache_key] = (now_value, targets)
        return targets

    def _note_peer_presence(self, public_key, source_ip):
        key_value = str(public_key or "").strip()
        ip_value = str(source_ip or "").split("%", 1)[0].strip()
        if not key_value or not ip_value:
            return
        now_value = monotonic_now()
        with self.presence_cache_lock:
            cached = self.presence_cache.get(key_value)
            if cached is not None and cached[0] == ip_value and (now_value - cached[1]) < PRESENCE_UPDATE_INTERVAL_SECONDS:
                return
            self.presence_cache[key_value] = (ip_value, now_value)
        try:
            self.peer_store.update_presence(key_value, ip_value)
        except Exception:
            pass

    def _socket_for_family(self, family):
        return self.server_sockets.get(family)

    def _send_datagram(self, family, target, payload):
        sock = self._socket_for_family(family)
        if sock is None:
            return False
        try:
            sock.sendto(payload, target)
            return True
        except OSError:
            return False

    def _send_to_peer(self, peer, payload):
        targets = self._resolve_peer_addresses(peer.get("host"), int(peer.get("port") or self.port))
        if not targets:
            targets = self._resolve_peer_addresses(peer.get("host"), int(peer.get("port") or self.port), force=True)
        if not targets:
            return False
        delivered = False
        for family, address in targets:
            delivered = self._send_datagram(family, address, payload) or delivered
        return delivered

    def send_hello_to_peers(self):
        body = encode_hello_body(self.node_kind, self.local_label)
        peers = self.refresh_peers(force=False)
        for peer in peers.values():
            try:
                payload = encode_secure_packet(CODE_HELLO, self.identity, peer.get("public_key"), body)
            except Exception:
                continue
            self._send_to_peer(peer, payload)

    def _forward_media_to_peers(self, media, exclude_public_key=None):
        payload_body = encode_media_body(media.get("address"), media.get("port"), media.get("payload"), family=media.get("family"), ttl=media.get("ttl"))
        peers = self.refresh_peers(force=False)
        for peer in peers.values():
            if exclude_public_key and str(peer.get("public_key")) == str(exclude_public_key):
                continue
            try:
                payload = encode_secure_packet(CODE_MEDIA, self.identity, peer.get("public_key"), payload_body)
            except Exception:
                continue
            self._send_to_peer(peer, payload)

    def _handle_secure_packet(self, packet, source_addr):
        peers = self.refresh_peers(force=False)
        try:
            code, sender_public_key, body, _peer = decode_secure_packet(packet, self.identity, peers)
        except KeyError:
            try:
                code, sender_public_key, body, _peer = decode_secure_packet(packet, self.identity, self.refresh_peers(force=True))
            except Exception:
                return
        except Exception:
            return
        source_ip = str(source_addr[0] or "").split("%", 1)[0].strip()
        self._note_peer_presence(sender_public_key, source_ip)
        if code == CODE_HELLO:
            try:
                decode_hello_body(body)
            except Exception:
                pass
            return
        if code != CODE_MEDIA:
            return
        try:
            media = decode_media_body(body)
        except Exception:
            return
        if self.duplicate_filter.seen(media.get("address"), media.get("port"), media.get("payload")):
            return
        if self.enable_rebroadcast:
            try:
                rebroadcast_multicast_packet(media.get("address"), media.get("port"), media.get("payload"), family=socket.AF_INET6 if int(media.get("family") or 4) == 6 else socket.AF_INET, ttl=media.get("ttl"))
            except Exception:
                pass
        self._forward_media_to_peers(media, exclude_public_key=sender_public_key)

    def _handle_local_source_packet(self, packet, source_addr):
        if not self.allow_local_source:
            return
        source_ip = str(source_addr[0] or "").split("%", 1)[0].strip()
        try:
            ip_obj = ipaddress.ip_address(source_ip)
        except ValueError:
            return
        if not ip_obj.is_loopback:
            return
        try:
            media = decode_local_source_packet(packet)
        except Exception:
            return
        self.duplicate_filter.seen(media.get("address"), media.get("port"), media.get("payload"))
        self._forward_media_to_peers(media)

    def _socket_loop(self, sock):
        while not self.stop_event.is_set():
            try:
                packet, source_addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if packet.startswith(CODE_LOCAL_SOURCE):
                self._handle_local_source_packet(packet, source_addr)
                continue
            if packet[:2] not in {CODE_HELLO, CODE_MEDIA}:
                continue
            self._handle_secure_packet(packet, source_addr)

    def _hello_loop(self):
        while not self.stop_event.is_set():
            try:
                self.send_hello_to_peers()
            except Exception as exc:
                self.log(f"multicast gateway hello error: {exc}")
            self.stop_event.wait(HELLO_INTERVAL_SECONDS)

    def _capture_loop(self):
        if not self.enable_capture or os.name == "nt" or not hasattr(socket, "AF_PACKET"):
            return
        raw_sock = None
        try:
            raw_sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0800))
            raw_sock.settimeout(1.0)
        except OSError as exc:
            self.log(f"multicast gateway local capture disabled: {exc}")
            if raw_sock is not None:
                try:
                    raw_sock.close()
                except OSError:
                    pass
            return
        self.log("multicast gateway local multicast capture enabled")
        try:
            while not self.stop_event.is_set():
                try:
                    frame = raw_sock.recv(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                media = parse_ipv4_multicast_udp_frame(frame)
                if not media:
                    continue
                if self.duplicate_filter.seen(media.get("address"), media.get("port"), media.get("payload")):
                    continue
                self._forward_media_to_peers(media)
        finally:
            try:
                raw_sock.close()
            except OSError:
                pass

    def serve(self):
        bind_addrs = []
        sockets = {}
        try:
            for family, bind_addr in listener_bind_specs(self.bind_host, self.port):
                sock = create_bound_socket(family, bind_addr)
                sockets[family] = sock
                bind_addrs.append(format_bind_addr(bind_addr))
        except OSError:
            for sock in sockets.values():
                try:
                    sock.close()
                except OSError:
                    pass
            raise
        self.server_sockets = sockets
        self.log(f"multicast gateway listening on {', '.join(bind_addrs)} udp")
        threads = []
        for sock in self.server_sockets.values():
            thread = threading.Thread(target=self._socket_loop, args=(sock,), daemon=True)
            thread.start()
            threads.append(thread)
        hello_thread = threading.Thread(target=self._hello_loop, daemon=True)
        hello_thread.start()
        threads.append(hello_thread)
        if self.enable_capture:
            capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            capture_thread.start()
            threads.append(capture_thread)
        try:
            while not self.stop_event.is_set():
                time.sleep(0.25)
        finally:
            for sock in self.server_sockets.values():
                try:
                    sock.close()
                except OSError:
                    pass
            self.server_sockets = {}

    def stop(self):
        self.stop_event.set()

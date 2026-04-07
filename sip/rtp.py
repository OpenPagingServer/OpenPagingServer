import random
import socket
import struct
import threading
import time

class RTPSession:
    def __init__(self, target_ip, target_port, payload_generator, on_finish=None):
        self.target_ip = target_ip
        self.target_port = target_port
        self.payload_generator = payload_generator
        self.on_finish = on_finish
        self.stop_event = threading.Event()
        self.thread = None
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("0.0.0.0", 0))
        self.local_port = self.socket.getsockname()[1]

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        try:
            self.socket.close()
        except:
            pass

    def build_packet(self, sequence, timestamp, ssrc, payload):
        return struct.pack("!BBHII", 0x80, 0x00, sequence, timestamp, ssrc) + payload

    def run(self):
        sequence = random.randrange(0, 65536)
        timestamp = random.randrange(0, 4294967296)
        ssrc = random.randrange(0, 4294967296)
        next_send = time.monotonic()

        try:
            for payload in self.payload_generator:
                if self.stop_event.is_set():
                    break
                packet = self.build_packet(sequence, timestamp, ssrc, payload)
                try:
                    self.socket.sendto(packet, (self.target_ip, self.target_port))
                except:
                    break
                sequence = (sequence + 1) & 0xFFFF
                timestamp = (timestamp + 160) & 0xFFFFFFFF
                next_send += 0.02
                sleep_for = next_send - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
        finally:
            try:
                self.socket.close()
            except:
                pass
            if self.on_finish is not None:
                try:
                    self.on_finish()
                except:
                    pass
                    
                    
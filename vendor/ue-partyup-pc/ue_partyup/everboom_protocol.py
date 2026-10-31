"""EVERBOOM framing recovered from the user's official-app capture."""
import time
from .ue_protocol import UE


def crc16(data):
    value = 65535
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0x8408 if value & 1 else value >> 1
    return value


def packet(category, operation, sequence, payload=b''):
    if not 0 <= category <= 255 or not 0 <= operation <= 15 or not 1 <= sequence <= 15:
        raise ValueError('Invalid CPP request identifiers')
    body = bytes([0, category, (operation << 4) | sequence]) + payload
    if len(body)>4095:
        raise ValueError('CPP payload exceeds frame length limit')
    prefix = bytes([0x13,0xa0 | (len(body)>>8),len(body)&255]) + body
    return prefix + crc16(prefix).to_bytes(2, 'little')

def extract_frames(buffer):
    frames=[]
    while len(buffer)>=3:
        if buffer[0]!=0x13 or buffer[1]&0xf0!=0xa0:
            del buffer[0]
            continue
        size=((buffer[1]&15)<<8 | buffer[2])+5
        if size<8:
            del buffer[0]
            continue
        if len(buffer)<size:
            break
        # The inspected app bypasses its CRC validator on receive. Zero trailers
        # are present in the tested speakers; validate nonzero trailers when supplied.
        trailer = int.from_bytes(buffer[size-2:size], 'little')
        if trailer and crc16(buffer[:size-2]) != trailer:
            del buffer[0]
            continue
        frames.append(bytes(buffer[:size]))
        del buffer[:size]
    return frames

class RequestIds:
    def __init__(self): self.value=0
    def next(self):
        self.value=self.value%15+1
        return self.value

class Everboom(UE):
    def request(self, category, operation, sequence, payload=b'', seconds=2):
        data = packet(category, operation, sequence, payload)
        self.send_all(data)
        return self.read_modern(seconds, expected=(category, operation, sequence))

    def read_modern(self, seconds, expected=None):
        import math
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Read duration must be finite and nonnegative.')
        frames = []
        deadline = time.monotonic() + seconds
        if self.buffer and time.monotonic()-getattr(self, '_partial_started', 0) >= 2:
            self.buffer.clear()
            self._partial_started = 0
        while time.monotonic() < deadline:
            data = self.recv_chunk(deadline-time.monotonic())
            if data is None:
                continue
            self.buffer.extend(data)
            frames.extend(extract_frames(self.buffer))
            if self.buffer:
                if not getattr(self, '_partial_started', 0):
                    self._partial_started = time.monotonic()
                elif time.monotonic()-self._partial_started >= 2:
                    self.buffer.clear()
                    self._partial_started = 0
            else:
                self._partial_started = 0
            # Stop on this request's reply, not just any traffic. Keep every
            # parsed frame in the batch so Session retains unsolicited events.
            # An error reply also ends the wait; Session decodes it as an error.
            if expected is not None:
                category, operation, sequence = expected
                if any(f[4] == category and f[5] == (operation << 4) | sequence for f in frames):
                    return frames
        return frames



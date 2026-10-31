"""Native Windows RFCOMM transport with bounded connection and I/O."""
import ctypes as c
from ctypes import wintypes as w
import math
import time
import uuid
from .test_connection import Address
from .diagnose import SPP

INVALID_SOCKET = c.c_size_t(-1).value
SOL_SOCKET = 0xffff
SO_ERROR = 0x1007
SO_SNDTIMEO = 0x1005
SO_RCVTIMEO = 0x1006
FIONBIO = 0x8004667e
PENDING_CONNECT_ERRORS = (10035, 10036, 10037)


class FDSet(c.Structure):
    # Winsock fd_set has a count followed by pointer-sized SOCKETs.
    _fields_ = [('count', c.c_uint), ('sockets', c.c_size_t * 64)]


class Timeval(c.Structure):
    _fields_ = [('seconds', w.LONG), ('microseconds', w.LONG)]


class UE:
    def __init__(self, address, connect_timeout=15, io_timeout=1):
        if not isinstance(address, int) or isinstance(address, bool) or not 0 < address < 1 << 48:
            raise ValueError('Invalid Bluetooth speaker address.')
        if not math.isfinite(connect_timeout) or not math.isfinite(io_timeout) or min(connect_timeout, io_timeout) <= 0:
            raise ValueError('Bluetooth timeouts must be finite and positive.')
        self.sock = None
        self._started = False
        self.buffer = bytearray()
        self.received_bytes = 0
        self.io_timeout = io_timeout
        self.ws = ws = c.WinDLL('Ws2_32.dll')
        socket_type = c.c_size_t
        for name, arguments, result in (
            ('WSAStartup', [w.WORD, c.c_void_p], c.c_int),
            ('WSACleanup', [], c.c_int),
            ('WSAGetLastError', [], c.c_int),
            ('socket', [c.c_int, c.c_int, c.c_int], socket_type),
            ('connect', [socket_type, c.c_void_p, c.c_int], c.c_int),
            ('closesocket', [socket_type], c.c_int),
            ('send', [socket_type, c.c_void_p, c.c_int, c.c_int], c.c_int),
            ('recv', [socket_type, c.c_void_p, c.c_int, c.c_int], c.c_int),
            ('ioctlsocket', [socket_type, c.c_long, c.POINTER(w.ULONG)], c.c_int),
            ('setsockopt', [socket_type, c.c_int, c.c_int, c.c_void_p, c.c_int], c.c_int),
            ('getsockopt', [socket_type, c.c_int, c.c_int, c.c_void_p, c.POINTER(c.c_int)], c.c_int),
            ('select', [c.c_int, c.POINTER(FDSet), c.POINTER(FDSet), c.POINTER(FDSet), c.POINTER(Timeval)], c.c_int),
        ):
            function = getattr(ws, name)
            function.argtypes, function.restype = arguments, result
        try:
            error = ws.WSAStartup(0x0202, c.byref(c.create_string_buffer(512)))
            if error:
                raise RuntimeError(f'Winsock startup failed ({error}).')
            self._started = True
            sock = ws.socket(32, 1, 3)  # AF_BTH, SOCK_STREAM, RFCOMM
            if sock == INVALID_SOCKET:
                raise RuntimeError(f'Bluetooth socket error {ws.WSAGetLastError()}.')
            self.sock = sock
            self._timeout(SO_RCVTIMEO, io_timeout)
            self._timeout(SO_SNDTIMEO, io_timeout)
            self._blocking(False)
            destination = Address()
            destination.family = 32
            destination.address = address
            destination.service[:] = uuid.UUID(SPP).bytes_le
            deadline = time.monotonic() + connect_timeout
            if ws.connect(sock, c.byref(destination), c.sizeof(destination)):
                error = ws.WSAGetLastError()
                if error not in PENDING_CONNECT_ERRORS:
                    raise RuntimeError(f'Bluetooth connect error {error}.')
                self._wait_connect(deadline)
            self._blocking(True)
        except BaseException:
            self.close()
            raise

    def _blocking(self, enabled):
        value = w.ULONG(0 if enabled else 1)
        if self.ws.ioctlsocket(self.sock, FIONBIO, c.byref(value)):
            raise RuntimeError(f'Bluetooth socket mode error {self.ws.WSAGetLastError()}.')

    def _timeout(self, option, seconds):
        milliseconds = w.DWORD(max(1, min(0xffffffff, math.ceil(seconds * 1000))))
        if self.ws.setsockopt(self.sock, SOL_SOCKET, option, c.byref(milliseconds), c.sizeof(milliseconds)):
            raise RuntimeError(f'Bluetooth timeout configuration failed ({self.ws.WSAGetLastError()}).')

    def _wait_connect(self, deadline):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Bluetooth connection timed out.')
            writable, failed = FDSet(), FDSet()
            for sockets in (writable, failed):
                sockets.count = 1
                sockets.sockets[0] = self.sock
            whole = int(remaining)
            timeout = Timeval(whole, min(999999, max(1, int((remaining - whole) * 1000000))))
            ready = self.ws.select(0, None, c.byref(writable), c.byref(failed), c.byref(timeout))
            if ready == 0:
                raise RuntimeError('Bluetooth connection timed out.')
            if ready < 0:
                raise RuntimeError(f'Bluetooth connection wait failed ({self.ws.WSAGetLastError()}).')
            error, size = c.c_int(), c.c_int(c.sizeof(c.c_int))
            if self.ws.getsockopt(self.sock, SOL_SOCKET, SO_ERROR, c.byref(error), c.byref(size)):
                raise RuntimeError(f'Bluetooth connection result failed ({self.ws.WSAGetLastError()}).')
            if error.value:
                raise RuntimeError(f'Bluetooth connect error {error.value}.')
            if writable.count:
                return
            raise RuntimeError('Bluetooth connection failed without a socket error.')

    def _require_open(self):
        if self.sock is None:
            raise RuntimeError('Bluetooth connection is closed.')

    def close(self):
        # Clear ownership first so repeated closes cannot close a reused handle.
        sock, self.sock = self.sock, None
        started, self._started = self._started, False
        try:
            if sock is not None:
                self.ws.closesocket(sock)
        finally:
            if started:
                self.ws.WSACleanup()

    def send_all(self, data, seconds=None):
        """Send one complete frame; close on failed writes, never replay it."""
        self._require_open()
        seconds = self.io_timeout if seconds is None else seconds
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('Send timeout must be finite and positive.')
        data = bytes(data)
        payload = c.create_string_buffer(data, max(1, len(data)))
        deadline = time.monotonic() + seconds
        sent = 0
        try:
            while sent < len(data):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('Bluetooth send timed out; command outcome is unknown.')
                self._timeout(SO_SNDTIMEO, min(self.io_timeout, remaining))
                count = self.ws.send(self.sock, c.byref(payload, sent), len(data) - sent, 0)
                if count <= 0 or count > len(data) - sent:
                    error = self.ws.WSAGetLastError() if count < 0 else 0
                    raise RuntimeError(f'Bluetooth send failed ({error}); command outcome is unknown.')
                sent += count
        except BaseException:
            self.close()
            raise
        return sent

    def recv_chunk(self, seconds=None):
        """Return bytes, None for a timeout, or raise on EOF/transport failure."""
        self._require_open()
        seconds = self.io_timeout if seconds is None else seconds
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('Receive timeout must be finite and positive.')
        data = c.create_string_buffer(4096)
        try:
            self._timeout(SO_RCVTIMEO, min(self.io_timeout, seconds))
            count = self.ws.recv(self.sock, data, len(data), 0)
            if count == 0:
                raise RuntimeError('Bluetooth connection closed by the speaker (EOF).')
            if count < 0:
                error = self.ws.WSAGetLastError()
                if error in (10060, 10035):
                    return None
                raise RuntimeError(f'Bluetooth receive failed ({error}).')
            self.received_bytes += count
            return data.raw[:count]
        except BaseException:
            self.close()
            raise

    def command(self, opcode, payload=b'', seconds=2):
        packet = bytes([len(payload) + 2]) + opcode.to_bytes(2, 'big') + payload
        self.send_all(packet)
        return self.read(seconds)

    def read(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Read duration must be finite and nonnegative.')
        frames = []
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return frames
            data = self.recv_chunk(remaining)
            if data is None:
                continue
            self.buffer.extend(data)
            while self.buffer and len(self.buffer) >= self.buffer[0] + 1:
                size = self.buffer[0] + 1
                frames.append(bytes(self.buffer[:size]))
                del self.buffer[:size]



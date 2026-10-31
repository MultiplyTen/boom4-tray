"""Open and close each paired EVERBOOM SPP connection; send zero payload bytes."""
import ctypes as c
from ctypes import wintypes as w
import json
import multiprocessing as mp
import uuid
from pathlib import Path
from datetime import datetime, timezone
from .diagnose import Device, Search, SPP


class Address(c.Structure):
    _pack_ = 1
    _fields_ = [('family', w.USHORT), ('address', c.c_ulonglong),
                ('service', c.c_ubyte * 16), ('port', w.ULONG)]


def paired_devices():
    api = c.WinDLL('BluetoothAPIs.dll', use_last_error=True)
    api.BluetoothFindFirstDevice.argtypes = [c.POINTER(Search), c.POINTER(Device)]
    api.BluetoothFindFirstDevice.restype = w.HANDLE
    api.BluetoothFindNextDevice.argtypes = [w.HANDLE, c.POINTER(Device)]
    api.BluetoothFindNextDevice.restype = w.BOOL
    api.BluetoothFindDeviceClose.argtypes = [w.HANDLE]
    api.BluetoothFindDeviceClose.restype = w.BOOL
    search = Search()
    search.size = c.sizeof(search)
    search.authenticated = search.remembered = search.connected = True
    device = Device()
    device.size = c.sizeof(device)
    handle = api.BluetoothFindFirstDevice(c.byref(search), c.byref(device))
    if not handle:
        if c.get_last_error() == 259:
            return []
        raise c.WinError(c.get_last_error())
    found = []
    try:
        while True:
            if device.authenticated:
                found.append((device.address, device.name))
            if not api.BluetoothFindNextDevice(handle, c.byref(device)):
                error = c.get_last_error()
                if error != 259:
                    raise c.WinError(error)
                break
    finally:
        api.BluetoothFindDeviceClose(handle)
    return list(dict.fromkeys(found))


def targets():
    return [address for address, name in paired_devices() if 'EVERBOOM' in name.upper()]


def local_radios():
    """Read local Bluetooth source addresses without discovery or speaker writes."""
    class RadioSearch(c.Structure):
        _fields_ = [('size', w.DWORD)]
    class RadioInfo(c.Structure):
        _fields_ = [('size', w.DWORD), ('address', c.c_ulonglong), ('name', w.WCHAR*248),
                    ('device_class', w.ULONG), ('subversion', w.USHORT), ('manufacturer', w.USHORT)]
    api = c.WinDLL('BluetoothAPIs.dll', use_last_error=True)
    kernel = c.WinDLL('Kernel32.dll', use_last_error=True)
    api.BluetoothFindFirstRadio.argtypes = [c.POINTER(RadioSearch), c.POINTER(w.HANDLE)]
    api.BluetoothFindFirstRadio.restype = w.HANDLE
    api.BluetoothFindNextRadio.argtypes = [w.HANDLE, c.POINTER(w.HANDLE)]
    api.BluetoothFindNextRadio.restype = w.BOOL
    api.BluetoothGetRadioInfo.argtypes = [w.HANDLE, c.POINTER(RadioInfo)]
    api.BluetoothGetRadioInfo.restype = w.DWORD
    api.BluetoothFindRadioClose.argtypes = [w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    search, radio = RadioSearch(), w.HANDLE()
    search.size = c.sizeof(search)
    handle = api.BluetoothFindFirstRadio(c.byref(search), c.byref(radio))
    if not handle:
        if c.get_last_error() in (259, 0):
            return []
        raise c.WinError(c.get_last_error())
    result = []
    try:
        while True:
            info = RadioInfo(); info.size = c.sizeof(info)
            try:
                error = api.BluetoothGetRadioInfo(radio, c.byref(info))
                if error:
                    raise c.WinError(error)
                result.append((info.address, info.name))
            finally:
                kernel.CloseHandle(radio)
            if not api.BluetoothFindNextRadio(handle, c.byref(radio)):
                error = c.get_last_error()
                if error != 259:
                    raise c.WinError(error)
                break
    finally:
        api.BluetoothFindRadioClose(handle)
    return result


def connect_worker(address, pipe):
    ws = c.WinDLL('Ws2_32.dll')
    ws.WSAStartup.argtypes = [w.WORD, c.c_void_p]
    ws.WSAStartup.restype = c.c_int
    ws.WSACleanup.argtypes = []
    ws.WSAGetLastError.restype = c.c_int
    ws.socket.argtypes = [c.c_int, c.c_int, c.c_int]
    ws.socket.restype = c.c_size_t
    ws.connect.argtypes = [c.c_size_t, c.c_void_p, c.c_int]
    ws.connect.restype = c.c_int
    ws.closesocket.argtypes = [c.c_size_t]
    ws.closesocket.restype = c.c_int
    sock = None
    started = False
    try:
        data = (c.c_ulonglong * 64)()
        error = ws.WSAStartup(0x0202, c.byref(data))
        if error:
            raise RuntimeError(f'WSAStartup error {error}')
        started = True
        sock = ws.socket(32, 1, 3)  # AF_BTH, SOCK_STREAM, RFCOMM
        if sock == c.c_size_t(-1).value:
            sock = None
            raise RuntimeError(f'Bluetooth socket error {ws.WSAGetLastError()}')
        destination = Address()
        destination.family = 32
        destination.address = address
        destination.service[:] = uuid.UUID(SPP).bytes_le
        destination.port = 0  # resolve SPP service through SDP
        error = ws.connect(sock, c.byref(destination), c.sizeof(destination))
        if error:
            pipe.send({'connected': False, 'winsock_error': ws.WSAGetLastError()})
        else:
            pipe.send({'connected': True, 'application_bytes_sent': 0})
    except Exception as exc:
        pipe.send({'connected': False, 'error': str(exc)})
    finally:
        if sock is not None:
            ws.closesocket(sock)
        if started:
            ws.WSACleanup()
        pipe.close()


def main():
    report = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'test': 'SPP service connection only; no application commands', 'speakers': []}
    try:
        for index, address in enumerate(targets(), 1):
            receiver, sender = mp.Pipe(duplex=False)
            process = mp.Process(target=connect_worker, args=(address, sender))
            process.start()
            sender.close()
            try:
                result = receiver.recv() if receiver.poll(20) else {
                    'connected': False, 'error': 'Connection timed out after 20 seconds'}
            except EOFError:
                result = {'connected': False, 'error': 'Connection worker exited unexpectedly'}
            finally:
                process.join(2)
                if process.is_alive():
                    process.terminate()
                    process.join(3)
                receiver.close()
            result['label'] = f'EVERBOOM {index}'
            report['speakers'].append(result)
            print(f'{result["label"]}: {result}', flush=True)
    except Exception as exc:
        report['error'] = str(exc)
        print(report['error'])
    path = Path(__file__).with_name('connection-report.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Report saved to {path}')
    return 0 if report['speakers'] and all(s['connected'] for s in report['speakers']) else 1



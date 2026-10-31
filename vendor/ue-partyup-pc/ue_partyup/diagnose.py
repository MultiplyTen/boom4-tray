"""Read Windows' cached EVERBOOM records and installed Classic BT services.

No discovery, pairing, connection, or speaker control writes. Standard library only.
"""
import ctypes as c
from ctypes import wintypes as w
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


class SystemTime(c.Structure):
    _fields_ = [(name, w.WORD) for name in
                ('year', 'month', 'weekday', 'day', 'hour', 'minute', 'second', 'ms')]


class Device(c.Structure):
    _fields_ = [('size', w.DWORD), ('address', c.c_ulonglong),
                ('device_class', w.ULONG), ('connected', w.BOOL),
                ('remembered', w.BOOL), ('authenticated', w.BOOL),
                ('last_seen', SystemTime), ('last_used', SystemTime),
                ('name', w.WCHAR * 248)]


class Search(c.Structure):
    _fields_ = [('size', w.DWORD), ('authenticated', w.BOOL),
                ('remembered', w.BOOL), ('unknown', w.BOOL),
                ('connected', w.BOOL), ('inquiry', w.BOOL),
                ('timeout', c.c_ubyte), ('radio', w.HANDLE)]


class Guid(c.Structure):
    _fields_ = [('bytes', c.c_ubyte * 16)]


SPP = '00001101-0000-1000-8000-00805f9b34fb'


def collect():
    if sys.platform != 'win32':
        raise RuntimeError('This diagnostic requires Windows.')
    api = c.WinDLL('BluetoothAPIs.dll', use_last_error=True)
    api.BluetoothFindFirstDevice.argtypes = [c.POINTER(Search), c.POINTER(Device)]
    api.BluetoothFindFirstDevice.restype = w.HANDLE
    api.BluetoothFindNextDevice.argtypes = [w.HANDLE, c.POINTER(Device)]
    api.BluetoothFindNextDevice.restype = w.BOOL
    api.BluetoothFindDeviceClose.argtypes = [w.HANDLE]
    api.BluetoothFindDeviceClose.restype = w.BOOL
    api.BluetoothEnumerateInstalledServices.argtypes = [w.HANDLE, c.POINTER(Device),
                                                       c.POINTER(w.DWORD), c.POINTER(Guid)]
    api.BluetoothEnumerateInstalledServices.restype = w.DWORD
    search = Search()
    search.size = c.sizeof(Search)
    search.authenticated = search.remembered = search.connected = True
    device = Device()
    device.size = c.sizeof(Device)
    result = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'mode': 'cached Windows Classic Bluetooth information only',
              'limitations': ['Not a live reachability test.',
                              'Does not enumerate BLE GATT characteristics.',
                              'SPP presence does not prove PartyUp support.'],
              'speakers': []}
    handle = api.BluetoothFindFirstDevice(c.byref(search), c.byref(device))
    if not handle:
        error = c.get_last_error()
        result['enumeration_error'] = error
        result['message'] = ('No cached devices found.' if error == 259 else
                             f'Windows enumeration failed: {c.FormatError(error)}')
        return result
    try:
        while True:
            if 'EVERBOOM' in device.name.upper():
                count = w.DWORD(32)
                services = (Guid * count.value)()
                error = api.BluetoothEnumerateInstalledServices(
                    None, c.byref(device), c.byref(count), services)
                if error == 234 and count.value <= 4096:
                    services = (Guid * count.value)()
                    error = api.BluetoothEnumerateInstalledServices(
                        None, c.byref(device), c.byref(count), services)
                ids = ([str(uuid.UUID(bytes_le=bytes(services[i].bytes)))
                        for i in range(count.value)] if error == 0 else [])
                result['speakers'].append({
                    'label': f'EVERBOOM {len(result["speakers"]) + 1}',
                    'connected_reported_by_windows': bool(device.connected),
                    'remembered': bool(device.remembered),
                    'authenticated': bool(device.authenticated),
                    'installed_service_uuids': ids,
                    'serial_port_service_present': SPP in ids,
                    'service_query_error': int(error)})
            if not api.BluetoothFindNextDevice(handle, c.byref(device)):
                error = c.get_last_error()
                if error != 259:
                    result['next_device_error'] = error
                break
    finally:
        api.BluetoothFindDeviceClose(handle)
    return result


def main():
    try:
        report = collect()
    except Exception as exc:
        report = {'error': str(exc), 'speakers': []}
    destination = Path(__file__).with_name('diagnostic-report.json')
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    failed = 'error' in report or report.get('enumeration_error', 259) != 259 or 'next_device_error' in report
    if failed:
        print('Bluetooth inspection was incomplete or unavailable; speaker count is unknown.')
    else:
        print(f'Found {len(report["speakers"])} cached EVERBOOM record(s).')
    for speaker in report['speakers']:
        print(f'{speaker["label"]}: serial control service present = '
              f'{speaker["serial_port_service_present"]}')
    print(report.get('error', report.get('message', 'No speaker settings changed.')))
    print(f'Report saved to {destination}')
    return 1 if failed else 0



"""Independent implementation of the current app's CPP speaker controls.

Feature IDs are stable protocol identities; their runtime indices are discovered
on each device through Session.feature(). See docs/CONTROLS.md for provenance and
the distinction between implementation and physical speaker acceptance tests.
"""
from dataclasses import dataclass
from typing import Sequence

from .session import ProtocolError, UnsupportedFeature


NAME = 0x0101
BATTERY = 0x0104
REMOTE_POWER = 0x0106
AUTO_SLEEP = 0x0108
VOLUME = 0x0200
EQ = 0x0201
APOLLO_BROADCASTER = 0x0202
XUP = 0x0204
XUP_RECEIVER = 0x0205
AVRCP = 0x0207
OUTDOOR = 0x020D

# CPP codes differ from the legacy app's enum ordinals. The speaker advertises
# which subset it understands; do not show every preset on every model.
EQ_PRESETS = {
    0: 'Voices', 1: 'Cramped spaces', 2: 'Bass jump', 3: 'Standard',
    4: 'Custom', 5: 'Bass boost', 6: 'Outdoor', 7: 'Game / Cinema',
    8: 'Podcast', 9: 'Vocal', 10: 'Reflective room',
    12: 'Deep relax', 13: 'Podcast / Vocal',
}
PLAYBACK_COMMANDS = {
    'stop': 0, 'play': 1, 'pause': 2, 'play_pause': 3,
    'next': 4, 'previous': 5,
    'fast_forward_press': 6, 'fast_forward_release': 7,
    'rewind_press': 8, 'rewind_release': 9,
    'next_group': 10, 'previous_group': 11,
}
IDENTIFY_SIGNALS = {
    'solid': 0, 'blink': 1, 'alternate': 2,
    'power_on_sound': 0x11, 'power_off_sound': 0x12,
    'pairing_sound': 0x13, 'max_volume_sound': 0x14,
    'low_battery_sound': 0x15, 'disconnected_sound': 0x16,
    'connected_sound': 0x17, 'factory_reset_sound': 0x18,
    'alarm_on_sound': 0x19, 'alarm_off_sound': 0x1A,
}
CHARGING_STATES = {
    0: 'Not charging', 1: 'Slow charging', 2: 'Fast charging',
    3: 'Fully charged', 4: 'Charging error',
}
SLEEP_PRESETS = {15: '15 minutes', 30: '30 minutes', 60: '1 hour', 180: '3 hours', 0: 'Never'}
FEATURES = {
    'name': NAME, 'battery': BATTERY, 'volume': VOLUME, 'eq': EQ,
    'outdoor': OUTDOOR, 'sleep_timer': AUTO_SLEEP,
    'remote_activation': REMOTE_POWER, 'power': REMOTE_POWER,
    'playback': AVRCP, 'partyup': XUP, 'sticky': XUP, 'identify': XUP,
    'stereo_balance': APOLLO_BROADCASTER,
    'public': XUP_RECEIVER,
}


class UnsupportedControl(UnsupportedFeature):
    """The current protocol inventory does not establish this control."""


@dataclass(frozen=True)
class BatteryInfo:
    """Both state-of-charge bytes retained; userSOC is displayed by the app."""
    battery: int
    user_soc: int
    charging_code: int

    @property
    def percent(self):
        # Out-of-range / unknown sentinels are not displayed as percentages.
        return self.user_soc if self.user_soc <= 100 else None

    @property
    def level(self):
        return self.battery

    @property
    def charging_state(self):
        return CHARGING_STATES.get(self.charging_code, f'Unknown ({self.charging_code})')


@dataclass(frozen=True)
class EQInfo:
    mode: int
    bands: tuple[int, ...]

    @property
    def label(self):
        return EQ_PRESETS.get(self.mode, f'Unknown ({self.mode})')


def integer(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label} must be an integer from {low} to {high}.')
    return value


def byte_value(payload, label):
    if not payload:
        raise ProtocolError(f'Empty {label} reply.')
    return payload[0]


def boolean(payload, label):
    value = byte_value(payload, label)
    if value not in (0, 1):
        raise ProtocolError(f'Invalid {label} boolean ({value}).')
    return value == 1


def bool_payload(value):
    if not isinstance(value, bool):
        raise ValueError('The setting must be True or False.')
    return bytes([value])


def mac_bytes(address):
    return integer(address, 1, (1 << 48) - 1, 'Bluetooth address').to_bytes(6, 'big')


def name_payload(name, maximum):
    if not isinstance(name, str) or not name.strip() or '\x00' in name:
        raise ValueError('Enter a nonempty speaker name without NUL characters.')
    encoded = name.encode('utf-8')
    integer(maximum, 1, 255, 'Maximum name length')
    if len(encoded) > maximum:
        raise ValueError(f'This speaker accepts at most {maximum} UTF-8 bytes in its name.')
    return bytes([len(encoded)]) + encoded


def decode_name(payload):
    # GET_NAME returns maximum-name-length first, then a length-prefixed string.
    if len(payload) < 2 or len(payload) < payload[1] + 2:
        raise ProtocolError('Truncated speaker-name reply.')
    try:
        return payload[2:payload[1] + 2].decode('utf-8')
    except UnicodeDecodeError as error:
        raise ProtocolError('Speaker name is not valid UTF-8.') from error


def decode_battery(payload):
    if len(payload) < 3:
        raise ProtocolError('Truncated battery reply.')
    return BatteryInfo(*payload[:3])


def eq_payload(mode, bands):
    integer(mode, 0, 255, 'EQ mode')
    if mode not in EQ_PRESETS:
        raise ValueError('This EQ preset has not been mapped from the current protocol.')
    if len(bands) != 5:
        raise ValueError('EQ needs exactly five band values.')
    return bytes([mode] + [integer(band, -128, 127, 'EQ band') & 255 for band in bands])


def decode_eq(payload):
    if len(payload) < 6:
        raise ProtocolError('Truncated EQ reply.')
    return EQInfo(payload[0], tuple(value if value < 128 else value - 256 for value in payload[1:6]))


class Settings:
    """Controls sharing an already-connected Session; never owns the connection."""
    def __init__(self, session):
        self.session = session
        self._capability_errors = {}

    async def _read(self, feature, operation):
        index = await self.session.feature(feature)
        return await self.session.request(index, operation, read=True)

    async def _write(self, feature, operation, payload=b''):
        index = await self.session.feature(feature)
        reply = await self.session.request(index, operation, payload)
        if reply:
            raise ProtocolError('Speaker returned an unexpected setter acknowledgement.')

    async def name(self):
        return decode_name(await self._read(NAME, 0))

    async def max_name_length(self):
        return byte_value(await self._read(NAME, 3), 'maximum name length')

    async def rename(self, name):
        # Check local validity before querying and the device limit before writing.
        name_payload(name, 255)
        payload = name_payload(name, await self.max_name_length())
        await self._write(NAME, 1, payload)

    async def battery(self):
        return decode_battery(await self._read(BATTERY, 0))

    async def volume(self):
        return byte_value(await self._read(VOLUME, 0), 'volume')

    async def set_volume(self, level):
        # Current Android VolumeControlPresenter treats all CPP devices as
        # 32-step speakers and sends these levels directly to Device.setVolume.
        await self._write(VOLUME, 1, bytes([integer(level, 0, 31, 'Volume')]))

    async def eq(self):
        return decode_eq(await self._read(EQ, 0))

    async def eq_modes(self):
        payload = await self._read(EQ, 2)
        if not payload or payload[0] != len(payload) - 1:
            raise ProtocolError('Invalid EQ-preset count.')
        return tuple(payload[1:])

    async def set_eq(self, mode: int, bands: Sequence[int]):
        payload = eq_payload(mode, bands)
        if mode not in await self.eq_modes():
            raise UnsupportedControl('This speaker does not advertise the selected EQ preset.')
        await self._write(EQ, 1, payload)

    async def outdoor(self):
        return not boolean(await self._read(OUTDOOR, 0), 'outdoor mode')

    async def set_outdoor(self, enabled):
        bool_payload(enabled)
        await self._write(OUTDOOR, 1, bool_payload(not enabled))

    async def sleep_timer(self):
        return byte_value(await self._read(AUTO_SLEEP, 0), 'sleep timer')

    async def set_sleep_timer(self, value):
        integer(value, 0, 255, 'Sleep timer in minutes')
        if value not in SLEEP_PRESETS:
            raise ValueError('Choose Never, 15 minutes, 30 minutes, 1 hour, or 3 hours.')
        await self._write(AUTO_SLEEP, 1, bytes([value]))

    async def remote_activation(self):
        return boolean(await self._read(REMOTE_POWER, 0), 'remote activation')

    async def set_remote_activation(self, enabled):
        await self._write(REMOTE_POWER, 1, bool_payload(enabled))

    async def power_off(self, silent=False):
        await self._write(REMOTE_POWER, 2, bool_payload(silent))

    async def playback(self, command, source_address):
        if command not in PLAYBACK_COMMANDS:
            raise ValueError('Unknown playback command.')
        payload = bytes([PLAYBACK_COMMANDS[command]]) + mac_bytes(source_address)
        await self._write(AVRCP, 0, payload)

    async def sync_volume(self):
        await self._write(XUP, 12)

    async def sticky(self):
        return boolean(await self._read(XUP, 9), 'persistent PartyUp group')

    async def set_sticky(self, enabled):
        payload = bool_payload(enabled)
        from .groups import broadcasting
        state = await self._read(XUP, 1)
        if broadcasting(state):
            raise UnsupportedControl('Stop PartyUp before changing Remember PartyUp group; changing it during playback can interrupt audio.')
        await self._write(XUP, 8, payload)

    async def public(self):
        return boolean(await self._read(XUP_RECEIVER, 0), 'PartyUp visibility')

    async def set_public(self, enabled):
        await self._write(XUP_RECEIVER, 1, bool_payload(enabled))

    async def identify(self, target_address=None, signal='blink'):
        if signal not in IDENTIFY_SIGNALS:
            raise ValueError('Unknown identification signal.')
        destination = 0 if target_address is None else 1
        address = self.session.speaker.address if target_address is None else target_address
        await self._write(XUP, 10, bytes([destination]) + mac_bytes(address) + bytes([IDENTIFY_SIGNALS[signal]]))

    async def stereo_balance(self):
        raise UnsupportedControl('The current app exposes an Apollo stereo setter, but its readback is not decoded.')

    async def set_stereo_balance(self, value):
        # Apollo TWS, separate from XUp PartyUp. No fabricated XUp readback.
        await self._write(APOLLO_BROADCASTER, 4, bytes([integer(value, -128, 127, 'Raw stereo balance') & 255]))

    async def capabilities(self):
        result, queried = {}, {}
        self._capability_errors = {}
        for name, feature in FEATURES.items():
            if feature not in queried:
                try:
                    await self.session.feature(feature)
                    queried[feature] = (True, None)
                except UnsupportedFeature as error:
                    queried[feature] = (False, str(error))
                except Exception as error:
                    # Unavailable lookup is not proof of no support.
                    queried[feature] = (None, str(error))
            result[name], error = queried[feature]
            if error:
                self._capability_errors[name] = error
        result['standby'] = False
        result['power_on'] = False
        return result

    async def snapshot(self):
        result = {'capabilities': await self.capabilities(), 'errors': {}, 'max_volume': 31,
                  'sleep_timer_unit': 'minutes (0 = Never)'}
        getters = {
            'name': self.name, 'battery': self.battery, 'volume': self.volume,
            'eq': self.eq, 'supported_eq_modes': self.eq_modes,
            'outdoor': self.outdoor, 'sleep_timer': self.sleep_timer,
            'remote_activation': self.remote_activation, 'sticky': self.sticky,
            'public': self.public,
        }
        for name, getter in getters.items():
            capability = 'eq' if name == 'supported_eq_modes' else name
            if result['capabilities'].get(capability) is not True:
                result[name] = None
                result['errors'][name] = self._capability_errors.get(capability, 'Feature is unavailable.')
                continue
            try:
                result[name] = await getter()
            except Exception as error:
                result[name] = None
                result['errors'][name] = str(error)
        result['stereo_balance'] = None
        result['errors']['stereo_balance'] = 'Apollo stereo readback is not yet decoded.'
        return result

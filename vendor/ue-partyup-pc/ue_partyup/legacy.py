"""Experimental Centurion/SPP adapter for older BOOM and MEGABOOM speakers.

The current Android app still ships this protocol alongside CPP. Constants and
payload layouts were checked against that app and historical UE app evidence.
No legacy model has been tested on hardware by this project. A successful probe
establishes only that a device answered these queries, not audio compatibility.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Callable

from .ue_protocol import UE


# CenturionCommand in the inspected current UE app. Deliberately exclude OTA,
# flash, resets and raw arbitrary command execution from this adapter.
READ_COMMANDS = {
    0x0001: 0x0002,  # protocol
    0x0003: 0x0004,  # firmware version (read only)
    0x0006: 0x0007,  # device ID
    0x0163: 0x0164,  # sonification profile
    0x016D: 0x016E,  # name
    0x0175: 0x0176,  # charging information (raw, not guessed battery layout)
    0x0177: 0x0178,  # EQ preset
    0x0188: 0x0189,  # custom EQ table
    0x018E: 0x018F,  # 32-step volume
    0x0191: 0x0192,  # 16-step volume
    0x0193: 0x0194,  # sticky TWS / PartyUp
    0x019B: 0x019C,  # TWS balance
    0x0414: 0x0415,  # broadcast state
    0x042A: 0x042B,  # public / promiscuous PartyUp
}
WRITE_COMMANDS = frozenset((
    0x016B,  # announce battery
    0x016F,  # name
    0x018D,  # 32-step volume
    0x0190,  # 16-step volume
    0x0195,  # sticky PartyUp
    0x01B6,  # remote power off
    0x0413,  # broadcast state
    0x0417,  # add receiver
    0x0419,  # remove receiver
    0x041B,  # sync broadcast volume
    0x0426,  # public PartyUp
))
BROADCAST_AUDIO_OPTIONS = frozenset((0, 0x50, 0x60, 0x70, 0x90, 0xA0, 0xB0))


class LegacyProtocolError(RuntimeError):
    """A reply did not confirm the requested legacy operation."""


class LegacyUnsupportedError(LegacyProtocolError):
    """The device explicitly rejected a legacy command as unrecognized."""


class LegacyExperimentRequired(LegacyProtocolError):
    """Experimental legacy writes were not enabled or not probed."""


@dataclass(frozen=True)
class LegacyProbe:
    protocol: str
    device_id: str
    model: str
    profile: str
    name: str
    broadcast_available: bool
    broadcast_state: tuple[int, int] | None
    hardware_verified: bool = False
    limitations: tuple[str, ...] = (
        "Legacy PC audio grouping has not been tested on hardware.",
        "Mixed legacy/CPP groups and ten-speaker playback remain unverified.",
    )


def model_from_device_id(device_id: str) -> tuple[str, str]:
    """Classify the queried device ID using the current app's model entries.

    Cached Windows display names are not used here. BOOM 3 exists in both legacy
    Mendocino and CPP MendocinoCR forms, so product names cannot select protocol.
    """
    models = (
        (r"UE Boom\s?", "BOOM", "SMALL"),
        (r"MEGABOOM?", "MEGABOOM", "BIG"),
        (r"MAXIMUS", "BOOM 2", "SMALL"),
        (r"MENDOCIN", "BOOM 3", "SMALL"),
        (r"HUMBOLDT", "MEGABOOM 3", "BIG"),
    )
    for pattern, model, profile in models:
        if re.fullmatch(pattern, device_id, re.IGNORECASE):
            return model, profile
    return "Unknown legacy device", "NO_GROUP"


def encode_packet(opcode: int, payload: bytes = b"") -> bytes:
    if not isinstance(opcode, int) or isinstance(opcode, bool) or not 0 <= opcode <= 0xFFFF:
        raise ValueError("Legacy opcode must be a 16-bit integer.")
    if not isinstance(payload, bytes) or len(payload) > 253:
        raise ValueError("Legacy payload must be bytes of at most 253 bytes.")
    return bytes((len(payload) + 2,)) + opcode.to_bytes(2, "big") + payload


def decode_frame(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < 3 or frame[0] != len(frame) - 1:
        raise LegacyProtocolError("Malformed legacy reply.")
    return int.from_bytes(frame[1:3], "big"), frame[3:]


class LegacyTransport(UE):
    def acknowledge(self, opcode: int) -> None:
        # UEDeviceConnector acknowledges information and unsolicited messages.
        # This acknowledges receipt only; it does not alter a speaker setting.
        packet = encode_packet(0, opcode.to_bytes(2, "big") + b"\0")
        self.send_all(packet)


class LegacySession:
    """One serialized legacy control connection, read only unless opted in.

    Both a protocol reply and a broadcast query must succeed before any setting
    or group mutation. Writes are sent once: an ambiguous timeout is not retried.
    """

    def __init__(self, speaker, experimental: bool = False,
                 transport_factory: Callable | None = None):
        self.speaker = speaker
        self.experimental = experimental
        self.transport_factory = transport_factory or LegacyTransport
        self.transport = None
        self.protocol_verified = False
        self.broadcast_verified = False
        self.model_profile = "NO_GROUP"
        self._lock = asyncio.Lock()

    async def connect(self) -> "LegacySession":
        if self.transport is None:
            self.transport = await asyncio.to_thread(self.transport_factory, self.speaker.address)
        return self

    def close(self) -> None:
        transport, self.transport = self.transport, None
        self.protocol_verified = self.broadcast_verified = False
        self.model_profile = "NO_GROUP"
        if transport is not None:
            transport.close()

    async def __aenter__(self) -> "LegacySession":
        return await self.connect()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _exchange(self, opcode: int, expected: int, payload: bytes, seconds: float) -> bytes:
        if self.transport is None:
            raise LegacyProtocolError("Connect to the speaker before requesting controls.")
        frames = self.transport.command(opcode, payload, seconds)
        result = None
        error = None
        for frame in frames:
            reply, data = decode_frame(frame)
            if reply == 0:
                if len(data) != 3 or int.from_bytes(data[:2], "big") != opcode:
                    continue  # Other-command ACKs never satisfy this request.
                if data[2] == 1:
                    error = LegacyUnsupportedError(f"Legacy command 0x{opcode:04X} is not supported.")
                elif data[2] != 0:
                    error = LegacyProtocolError(f"Legacy command 0x{opcode:04X} failed (status {data[2]}).")
                elif expected == 0:
                    result = b""
                # An ACK of a query does not substitute for its information reply.
            else:
                self.transport.acknowledge(reply)
                if reply == expected:
                    result = data
        if error is not None:
            raise error
        if result is None:
            raise LegacyProtocolError(f"No matching reply to legacy command 0x{opcode:04X}; result is unknown.")
        return result

    async def query(self, opcode: int, expected: int, payload: bytes = b"", seconds: float = 2) -> bytes:
        if READ_COMMANDS.get(opcode) != expected:
            raise ValueError("Only supported read-only legacy query/reply pairs are allowed.")
        if payload:
            raise ValueError("These legacy information queries do not take a payload.")
        async with self._lock:
            if opcode == 1:
                self.protocol_verified = False
            result = await asyncio.to_thread(self._exchange, opcode, expected, payload, seconds)
            if opcode == 1:
                if not result:
                    raise LegacyProtocolError("Empty legacy protocol reply.")
                self.protocol_verified = True
            if opcode == 6:
                identity = result.rstrip(b"\0").decode("utf-8", errors="replace")
                self.model_profile = model_from_device_id(identity)[1]
            return result

    def _require_experiment(self) -> None:
        if not self.experimental:
            raise LegacyExperimentRequired("Enable experimental legacy controls before changing this speaker.")
        if not self.protocol_verified or not self.broadcast_verified:
            raise LegacyExperimentRequired("Probe legacy protocol and PartyUp capability successfully before changing this speaker.")
        if self.model_profile == "NO_GROUP":
            raise LegacyExperimentRequired("The queried device identity is not a known legacy BOOM/MEGABOOM model.")

    async def _set(self, opcode: int, payload: bytes = b"") -> None:
        self._require_experiment()
        if opcode not in WRITE_COMMANDS:
            raise ValueError("Unsupported legacy mutation.")
        encode_packet(opcode, payload)  # Validate before sending.
        async with self._lock:
            await asyncio.to_thread(self._exchange, opcode, 0, payload, 2)

    async def query_broadcast(self) -> tuple[int, int]:
        self.broadcast_verified = False
        data = await self.query(0x0414, 0x0415)
        if len(data) != 2 or data[0] not in (0, 1, 2, 3) or data[1] not in BROADCAST_AUDIO_OPTIONS:
            raise LegacyProtocolError("Unrecognized legacy PartyUp configuration.")
        self.broadcast_verified = True
        return data[0], data[1]

    async def probe(self) -> LegacyProbe:
        """Read protocol, queried identity and PartyUp capability; no mutations."""
        self.broadcast_verified = False
        self.model_profile = "NO_GROUP"
        protocol = await self.query(1, 2)
        identity = (await self.query(6, 7)).rstrip(b"\0").decode("utf-8", errors="replace")
        name = (await self.query(0x016D, 0x016E)).rstrip(b"\0").decode("utf-8", errors="replace")
        model, profile = model_from_device_id(identity)
        try:
            broadcast = await self.query_broadcast()
            available = True
        except LegacyUnsupportedError:
            broadcast, available = None, False
        # Timeouts are not a negative capability result; callers must surface them.
        return LegacyProbe(protocol.hex(), identity, model, profile, name, available, broadcast)

    async def set_broadcast(self, state: int, audio: int = 0) -> None:
        if state not in (0, 1, 2, 3) or audio not in BROADCAST_AUDIO_OPTIONS:
            raise ValueError("Invalid legacy broadcast state/audio option.")
        await self._set(0x0413, bytes((state, audio)))

    def _guest_address(self, address: int) -> bytes:
        if not isinstance(address, int) or isinstance(address, bool) or not 0 < address < 1 << 48:
            raise ValueError("A guest must have a nonzero 48-bit Bluetooth address.")
        if address == self.speaker.address:
            raise ValueError("The main speaker cannot join itself as a guest.")
        return address.to_bytes(6, "big")

    async def add_guest(self, address: int, audio_mode: int = 3) -> None:
        if audio_mode not in (1, 2, 3):
            raise ValueError("Legacy guest mode must be left, right or center.")
        await self._set(0x0417, self._guest_address(address) + bytes((audio_mode,)))

    async def remove_guest(self, address: int) -> None:
        await self._set(0x0419, self._guest_address(address))

    async def sync_volume(self) -> None:
        await self._set(0x041B)

    async def get_name(self) -> str:
        return (await self.query(0x016D, 0x016E)).rstrip(b"\0").decode("utf-8", errors="replace")

    async def set_name(self, name: str) -> None:
        if not isinstance(name, str) or not name or "\0" in name:
            raise ValueError("A speaker name must be nonempty and contain no null characters.")
        encoded = name.encode("utf-8")
        if len(encoded) > 48:
            raise ValueError("Legacy names are limited to 48 UTF-8 bytes.")
        await self._set(0x016F, encoded + b"\0")

    async def get_volume(self, steps: int = 32) -> int:
        if steps not in (16, 32):
            raise ValueError("Legacy volume uses 16 or 32 steps.")
        opcode, expected = (0x018E, 0x018F) if steps == 32 else (0x0191, 0x0192)
        data = await self.query(opcode, expected)
        if len(data) != 1 or data[0] >= steps:
            raise LegacyProtocolError("Invalid legacy volume reply.")
        return data[0]

    async def set_volume(self, volume: int, steps: int = 32) -> None:
        if steps not in (16, 32) or not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume < steps:
            raise ValueError("Volume must fit the selected 16- or 32-step range.")
        await self._set(0x018D if steps == 32 else 0x0190, bytes((volume,)))

    async def set_sticky(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("Sticky PartyUp state must be a boolean.")
        await self._set(0x0195, bytes((int(enabled),)))

    async def set_public(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("Public PartyUp state must be a boolean.")
        await self._set(0x0426, bytes((int(enabled),)))

    async def announce_battery(self) -> None:
        await self._set(0x016B)

    async def power_off(self) -> None:
        await self._set(0x01B6)


class LegacySettings:
    """Settings facade with the same core controls as the modern adapter.

    The caller owns the already connected and probed LegacySession. Optional
    information reads report each error independently. A timeout is kept as
    unknown, while only an explicit unrecognized-command ACK means unsupported.
    """

    def __init__(self, session: LegacySession):
        self.session = session
        self._volume_steps = None

    async def name(self) -> str:
        return await self.session.get_name()

    async def max_name_length(self) -> int:
        # The archived/current legacy setter's UTF-8 limit, not character count.
        return 48

    async def rename(self, name: str) -> None:
        await self.session.set_name(name)

    async def volume(self) -> int:
        if self._volume_steps is not None:
            return await self.session.get_volume(self._volume_steps)
        try:
            value = await self.session.get_volume(32)
            self._volume_steps = 32
            return value
        except LegacyUnsupportedError:
            value = await self.session.get_volume(16)
            self._volume_steps = 16
            return value

    async def set_volume(self, volume: int) -> None:
        if not isinstance(volume, int) or isinstance(volume, bool) or volume < 0:
            raise ValueError("Volume must be a nonnegative integer.")
        # Query before choosing a mutation; never discover the range by sending
        # a 32-step set and replaying it as a 16-step set after an uncertain reply.
        if self._volume_steps is None:
            await self.volume()
        await self.session.set_volume(volume, self._volume_steps)

    async def sticky(self) -> bool:
        data = await self.session.query(0x0193, 0x0194)
        return self._boolean_reply(data, "sticky PartyUp")

    async def set_sticky(self, enabled: bool) -> None:
        await self.session.set_sticky(enabled)

    async def public(self) -> bool:
        data = await self.session.query(0x042A, 0x042B)
        return self._boolean_reply(data, "public PartyUp")

    async def set_public(self, enabled: bool) -> None:
        await self.session.set_public(enabled)

    @staticmethod
    def _boolean_reply(data: bytes, setting: str) -> bool:
        if len(data) != 1 or data[0] not in (0, 1):
            raise LegacyProtocolError(f"Invalid legacy {setting} reply.")
        return bool(data[0])

    async def announce_battery(self) -> None:
        await self.session.announce_battery()

    async def power_off(self, silent: bool = False) -> None:
        if silent:
            raise LegacyUnsupportedError("A separate silent legacy power-off mode has not been verified.")
        await self.session.power_off()

    async def sync_volume(self) -> None:
        await self.session.sync_volume()

    async def snapshot(self) -> dict:
        ready = self.session.protocol_verified and self.session.broadcast_verified and self.session.model_profile != "NO_GROUP"
        capabilities = dict.fromkeys((
            "battery", "eq", "outdoor", "sleep_timer", "remote_activation",
            "playback", "identify", "stereo_balance",
        ), False)
        capabilities.update({
            "name": None, "volume": None, "sticky": None, "public": None,
            "power": bool(ready), "partyup": bool(ready),
            "announce_battery": bool(ready),
        })
        result = {"capabilities": capabilities, "errors": {},
                  "max_volume": None, "max_name_length": 48,
                  "battery": None, "eq": None, "outdoor": None,
                  "sleep_timer": None, "remote_activation": None,
                  "supported_eq_modes": (), "experimental": True}
        for key, read in (("name", self.name), ("volume", self.volume),
                          ("sticky", self.sticky), ("public", self.public)):
            try:
                result[key] = await read()
                capabilities[key] = True
            except LegacyUnsupportedError as error:
                result[key] = None
                result["errors"][key] = str(error)
                capabilities[key] = False
            except LegacyProtocolError as error:
                result[key] = None
                result["errors"][key] = str(error)
                # None remains unknown; an absent response isn't unsupported.
            except RuntimeError as error:
                result[key] = None
                result["errors"][key] = str(error)
        if self._volume_steps is not None:
            result["max_volume"] = self._volume_steps - 1
        return result

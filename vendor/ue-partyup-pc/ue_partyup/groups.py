"""Ten-speaker PartyUp orchestration with individual failure reporting."""
from dataclasses import dataclass, field
import asyncio
from bleak import BleakClient
from .controller import GROUP_UUID
from .session import Session, ProtocolError, NoReply

MAX_SPEAKERS = 10
XUP = 0x0204


def broadcasting(payload):
    """Scanning (1) and broadcasting (2) are separate flags in the UE app."""
    if len(payload) != 2 or payload[0] not in (0, 1, 2, 3):
        raise ProtocolError('Invalid PartyUp state reply.')
    return bool(payload[0] & 2)


def validate_group(host, guests, experimental=False):
    if len(guests) + 1 > MAX_SPEAKERS:
        raise ValueError('Choose one main speaker and up to nine guests (ten total).')
    addresses = [s.address for s in [host, *guests]]
    if len(set(addresses)) != len(addresses):
        raise ValueError('Each speaker can appear only once in a group.')
    if any(s.experimental for s in [host, *guests]) and not experimental:
        raise ValueError('Enable experimental compatibility before using older or unidentified speakers.')


def receiver_snapshot(frames, index):
    """Parse complete receiver-list notifications; preserve raw status without guessing audio."""
    result = None
    for frame in frames:
        if len(frame) < 9 or frame[4:6] != bytes([index, 0]):
            continue
        payload = frame[6:-2]
        count = payload[0]
        if len(payload) != 1 + count * 10:
            continue
        result = {int.from_bytes(payload[1+i*10:7+i*10], 'big'):
                  int.from_bytes(payload[7+i*10:9+i*10], 'big') for i in range(count)}
    return result


def receivers(frames, index):
    return receiver_snapshot(frames, index) or {}


@dataclass
class MemberResult:
    speaker: object
    state: str
    detail: str = ''


@dataclass
class GroupResult:
    action: str
    host_on: bool | None
    members: list = field(default_factory=list)
    reported: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def __str__(self):
        state = 'enabled' if self.host_on else 'off' if self.host_on is False else 'unconfirmed'
        lines = [f'Main speaker PartyUp: {state}.']
        lines.extend(f'{m.speaker.label}: {m.state}' + (f' — {m.detail}' if m.detail else '')
                     for m in self.members)
        lines.extend(self.warnings)
        if self.action in ('start', 'add'):
            lines.append('Play PC audio through the selected main speaker. Receiver reports do not verify audible playback.')
        return '\n'.join(lines)


class GroupManager:
    def __init__(self, session_factory=Session, client_factory=BleakClient):
        self.session_factory = session_factory
        self.client_factory = client_factory

    async def set_channel(self, host, guest, channel, experimental=False):
        validate_group(host, [guest], experimental)
        if isinstance(channel, bool) or channel not in (1, 2, 3):
            raise ValueError('Choose left (1), right (2), or center (3).')
        # Read-probe selection is shared with the per-speaker settings adapters.
        from .controls import settings_session
        async with settings_session(host, experimental) as settings:
            session = settings.session
            if hasattr(session, 'add_guest'):
                state, _ = await session.query_broadcast()
                if not state:
                    raise ProtocolError('Start the group before assigning a channel.')
                await session.add_guest(guest.address, channel)
            else:
                index = await session.feature(XUP)
                state = await session.request(index, 1, read=True)
                if not broadcasting(state):
                    raise ProtocolError('Start the group before assigning a channel.')
                await self._ack(session, index, 2, guest.address.to_bytes(6, 'big') + bytes([channel]))
        return f'{guest.label}: channel command accepted; verify left/right audio on hardware.'

    async def run(self, action, host, guests=(), progress=lambda m: None, experimental=False):
        guests = list(guests)
        validate_group(host, guests, experimental)
        if action not in ('start', 'add', 'remove', 'stop', 'status'):
            raise ValueError('Unknown group action.')
        if action in ('start', 'add', 'remove') and not guests:
            raise ValueError('Select at least one guest speaker.')
        progress('Connecting to the main speaker…')
        # Probe modern CPP first; legacy is an explicitly selected adapter, never a write fallback.
        if host.experimental:
            return await self._experimental(action, host, guests, progress)
        async with self.session_factory(host) as session:
            index = await session.feature(XUP)
            configuration = None
            async def mode():
                nonlocal configuration
                reply = await session.request(index, 1, read=True)
                enabled = broadcasting(reply)
                configuration = reply
                return enabled
            on = await mode()
            snapshot = receiver_snapshot(session.notifications, index)
            if action in ('start', 'add') and on and snapshot is None:
                await session.listen(2)
                snapshot = receiver_snapshot(session.notifications, index)
                if snapshot is None:
                    raise ProtocolError('Current group membership is unknown. Check status or stop the group before starting a new selection.')
            existing = set(snapshot or {}) if on else set()
            if action in ('start', 'add') and len(existing | {g.address for g in guests}) > MAX_SPEAKERS-1:
                raise ValueError('The reported receivers plus this selection exceed nine guests. Remove a guest first.')
            if action in ('start', 'add') and not on:
                await self._ack(session, index, 0, b'\3\0')
                on = await mode()
                if not on:
                    raise ProtocolError('Main speaker did not enable PartyUp.')
            needs_ble_join = action in ('start', 'add') and any(
                not guest.experimental and guest.address not in existing for guest in guests)
            if needs_ble_join and configuration[0] == 3:
                # REA: XupReceiverBLEController.initBroadcastMode changes 3->2
                # on the broadcaster, preserving audio options; suspend is a no-op.
                # The PC discovers the guest, so the host need not scan as well.
                progress('Preparing the main speaker for the guest connection…')
                await self._ack(session, index, 0, bytes([2, configuration[1]]))
                on = await mode()
                if configuration[0] != 2:
                    raise ProtocolError('Broadcaster-only mode was not confirmed; guest join was not sent.')
            result = GroupResult(action, on)
            if action == 'stop':
                # The tested two-speaker sequence stops the broadcaster first;
                # its guests then become reachable for their BLE clear writes.
                try:
                    await self._ack(session, index, 0, b'\0\0')
                    result.host_on = await mode()
                except (Exception, asyncio.CancelledError) as exc:
                    if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
                        raise
                    result.host_on = None
                    result.warnings.append(f'Host stop unconfirmed: {exc}')
            for number, guest in enumerate(guests, 1):
                if action == 'status':
                    continue
                if action in ('start', 'add') and on and guest.address in existing:
                    # A receiver report is not proof of audio, but it is enough
                    # to avoid needlessly issuing the same join during playback.
                    result.members.append(MemberResult(guest, 'reported by main speaker',
                                                       'Already reported; no join command sent.'))
                    continue
                progress(f'{"Joining" if action in ("start", "add") else "Removing"} {guest.label} ({number}/{len(guests)})…')
                host_removed = False
                write_accepted = False
                phase = 'connecting'
                try:
                    joining = action in ('start', 'add')
                    if guest.experimental:
                        await self._ack(session, index, 2 if joining else 3,
                                        guest.address.to_bytes(6, 'big') + (b'\3' if joining else b''))
                    else:
                        if action == 'remove':
                            await self._ack(session, index, 3, guest.address.to_bytes(6, 'big'))
                            host_removed = True
                        mac = ':'.join(f'{b:02X}' for b in guest.address.to_bytes(6, 'big'))
                        async def write_guest():
                            nonlocal write_accepted, phase
                            async with self.client_factory(mac, timeout=12) as client:
                                phase = 'writing'
                                # Current app maps CENTER to BLE code0 (SPP uses3).
                                value = host.address.to_bytes(6, 'big') + b'\1\0' if joining else bytes(8)
                                await asyncio.wait_for(client.write_gatt_char(GROUP_UUID, value, response=True), 8)
                                write_accepted = True
                                phase = 'closing the control connection'
                        await asyncio.wait_for(write_guest(), 25)
                    result.members.append(MemberResult(guest, 'join accepted' if joining else 'leave accepted'))
                except (Exception, asyncio.CancelledError) as exc:
                    if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
                        raise
                    detail = str(exc) or f'Timed out while {phase}; guest result is unknown.'
                    if write_accepted:
                        state = 'join accepted' if joining else 'leave accepted'
                        detail = f'Write acknowledged; connection cleanup unavailable: {detail}'
                    else:
                        state = 'host removal accepted; guest clear unconfirmed' if host_removed else 'unconfirmed'
                    result.members.append(MemberResult(guest, state, detail))
            try:
                await session.listen(2)
            except (Exception, asyncio.CancelledError) as exc:
                if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
                    raise
                result.warnings.append(f'Receiver refresh unavailable: {exc}')
            result.reported = receivers(session.notifications, index)
            for member in result.members:
                if member.state == 'join accepted' and member.speaker.address in result.reported:
                    member.state = 'reported by main speaker'
            if action == 'status':
                result.members = [MemberResult(g, 'reported by main speaker' if g.address in result.reported
                                              else 'no receiver report observed') for g in guests]
            if action != 'status':
                try:
                    result.host_on = await mode()
                except (Exception, asyncio.CancelledError) as exc:
                    if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
                        raise
                    result.host_on = None
                    result.warnings.append(f'Final host state unavailable: {exc}')
            return result

    @staticmethod
    async def _ack(session, index, function, payload=b''):
        reply = await session.request(index, function, payload)
        if reply:
            raise ProtocolError('Unexpected mutation acknowledgement payload.')

    async def _experimental(self, action, host, guests, progress):
        # Advertising names do not uniquely determine protocol. Modern read probe precedes legacy probe.
        try:
            async with self.session_factory(host) as session:
                await session.feature(XUP)
        except NoReply:
            pass
        else:
            from dataclasses import replace
            return await self.run(action, replace(host, experimental=False), guests, progress, experimental=True)
        from .legacy import LegacySession
        async with LegacySession(host, experimental=True) as session:
            probe = await session.probe()
            if not probe.broadcast_available or probe.profile == 'NO_GROUP':
                raise ProtocolError('This legacy speaker did not confirm a known PartyUp profile.')
            state, _ = probe.broadcast_state
            result = GroupResult(action, bool(state))
            if action in ('start', 'add') and state:
                raise ProtocolError('Existing legacy membership is unknown. Stop that group before starting a selection of up to ten speakers.')
            if action in ('start', 'add') and not state:
                await session.set_broadcast(3, 0)
                result.host_on = bool((await session.query_broadcast())[0])
            for guest in guests:
                if action == 'status':
                    continue
                try:
                    if action in ('start', 'add'):
                        await session.add_guest(guest.address, 3)
                    else:
                        await session.remove_guest(guest.address)
                    result.members.append(MemberResult(guest, 'command accepted; hardware confirmation required'))
                except (Exception, asyncio.CancelledError) as exc:
                    if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
                        raise
                    result.members.append(MemberResult(guest, 'unconfirmed', str(exc)))
            if action == 'stop':
                await session.set_broadcast(0, 0)
                result.host_on = bool((await session.query_broadcast())[0])
            return result

"""BOOM 4 control layer. No discovery-time writes or automatic group changes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
VENDOR = ROOT / 'vendor' / 'ue-partyup-pc'
if VENDOR.is_dir():
    sys.path.insert(0, str(VENDOR))

from ue_partyup.controller import Speaker
from ue_partyup.groups import GroupManager, XUP, broadcasting, receiver_snapshot
from ue_partyup.session import Session, UnsupportedFeature
from ue_partyup.settings import Settings
from ue_partyup.test_connection import paired_devices

# Source: docs/PROTOCOL_SOURCE.md and the linked upstream compatibility table.
# Host audio options and guest AddReceiver
# channel values are distinct namespaces; do not substitute BLE CENTER=0 here.
# These are source-backed experimental controls, not acoustic verification.
CHANNEL_MODES = {
    '同播（完整声音）': (0x00, 3),
    '左右立体声（主左／副右）': (0x50, 2),
    # Match the historical official-app XUpFragment.onSwapSpeakersClick flow:
    # 0x50 <-> 0x90, with the guest assigned RIGHT in both cases. The APK
    # derived current compatibility table maps host 0x90 to reverse/right.
    # This is not the unverified alternative of 0x60 + guest LEFT.
    '交换左右（主右／副左）': (0x90, 2),
}


def host_channel_label(audio_option, host_on):
    if host_on is False:
        return '主音箱声道：未组队'
    if host_on is not True or audio_option is None:
        return '主音箱声道：未确认'
    names = {0: '完整声音', 0x50: '左', 0x60: '右', 0x70: '完整声音',
             0x90: '右', 0xA0: '左', 0xB0: '完整声音'}
    return '主音箱声道：' + names.get(audio_option, '模式未识别')


class ChannelChangeError(RuntimeError):
    """A channel write was attempted; no automatic retry or rollback is safe."""


@dataclass(frozen=True)
class ChannelResult:
    mode: str
    audio_option: int


def data_directory():
    directory = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Boom4Tray'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def discover_speakers():
    # Enumerate Windows' paired devices only: no active scan or connections.
    return sorted((Speaker(address, name, experimental=True)
                   for address, name in paired_devices()
                   if re.search(r'\b(?:MEGA)?BOOM\s*4\b', name, re.I)),
                  key=lambda speaker: (speaker.name, speaker.address))


def load_selection():
    try:
        data = json.loads((data_directory() / 'selection.json').read_text('utf-8'))
        return {key: value for key, value in data.items()
                if key in ('host', 'guest') and type(value) is int and 0 < value < 1 << 48}
    except (OSError, ValueError, AttributeError):
        return {}


def save_selection(host, guest):
    path = data_directory() / 'selection.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'host': host.address, 'guest': guest.address}), 'utf-8')
    temporary.replace(path)


@dataclass
class Snapshot:
    host_on: bool | None = None
    guest_reported: bool | None = None
    volume: int | None = None
    battery: int | None = None
    errors: tuple[str, ...] = ()
    audio_option: int | None = None


class Controller:
    def __init__(self, session_factory=Session, manager=None):
        self.session_factory = session_factory
        self.manager = manager or GroupManager(session_factory=session_factory)

    @staticmethod
    def validate(host, guest=None):
        if not isinstance(host, Speaker):
            raise ValueError('请先选择主音箱。')
        if guest is not None and not isinstance(guest, Speaker):
            raise ValueError('请选择有效的副音箱。')
        if guest is not None and host.address == guest.address:
            raise ValueError('主音箱与副音箱必须是不同设备。')

    async def status(self, host, guest=None):
        self.validate(host, guest)
        errors = []
        async with self.session_factory(host) as session:
            index = await session.feature(XUP)
            configuration = await session.request(index, 1, read=True)
            on = broadcasting(configuration)
            # Only contact the host. Reading a guest can interrupt PartyUp audio.
            snapshot = receiver_snapshot(session.notifications, index)
            if on and snapshot is None:
                await session.listen(1)
                snapshot = receiver_snapshot(session.notifications, index)
            settings = Settings(session)
            values = {}
            for name, getter in (('volume', settings.volume), ('battery', settings.battery)):
                try:
                    value = await getter()
                    if name == 'battery':
                        value = value.percent
                    elif not 0 <= value <= 31:
                        raise ValueError('音箱返回了未知的音量范围。')
                    values[name] = value
                except Exception as error:
                    errors.append(f'{name}: {error}')
            member = (guest.address in snapshot) if guest and snapshot is not None else None
            return Snapshot(on, member, errors=tuple(errors), audio_option=configuration[1], **values)

    async def group(self, action, host, guest, progress=lambda message: None):
        self.validate(host, guest)
        if not isinstance(guest, Speaker):
            raise ValueError('请先选择另一台副音箱；两台需要分别与 Windows 配对。')
        if action not in ('start', 'stop'):
            raise ValueError('未知组队操作。')
        # The upstream adapter probes CPP before any settings write. BOOM 4 is
        # explicitly experimental; there is no guessed opcode or firmware path.
        return await self.manager.run(action, host, [guest], progress, experimental=True)

    async def volume(self, host, level=None, delta=None):
        self.validate(host)
        if level is not None and (type(level) is not int or not 0 <= level <= 31):
            raise ValueError('音量必须介于 0 与 31 之间。')
        if delta is not None and (type(delta) is not int or not -31 <= delta <= 31):
            raise ValueError('无效的音量增量。')
        async with self.session_factory(host) as session:
            settings = Settings(session)
            before = await settings.volume()
            if not 0 <= before <= 31:
                raise ValueError('音箱返回了未知的音量范围，未发送修改。')
            target = max(0, min(31, before + delta)) if delta is not None else level
            if target is None:
                return before
            if target != before:
                await settings.set_volume(target)
            actual = await settings.volume()
            if actual != target:
                raise RuntimeError('音量修改未通过回读确认，请刷新状态后再试。')
            return actual

    async def sync_volume(self, host):
        self.validate(host)
        async with self.session_factory(host) as session:
            index = await session.feature(XUP)
            if not broadcasting(await session.request(index, 1, read=True)):
                raise ValueError('请先开启 PartyUp，再同步组队音量。')
            await Settings(session).sync_volume()

    async def channel_mode(self, host, guest, mode):
        self.validate(host, guest)
        if not isinstance(guest, Speaker):
            raise ValueError('请选择副音箱后再设置声道。')
        if not isinstance(mode, str) or mode not in CHANNEL_MODES:
            raise ValueError('请选择同播、左右立体声或交换左右。')
        audio_option, guest_channel = CHANNEL_MODES[mode]
        # A fresh CPP root handshake is performed by Session. Never select a
        # protocol from the marketing name or fall back to a guessed write.
        async with self.session_factory(host) as session:
            index = await session.feature(XUP)
            before = await session.request(index, 1, read=True)
            if not broadcasting(before):
                raise ValueError('请先开启 PartyUp，再应用声道。')
            # Preserve broadcasting/scanning flags. Do not stop/restart the
            # group, change sticky settings, or open a direct guest session.
            wanted = bytes([before[0], audio_option])
            try:
                # Every mutation is sent once and requires the correlated ACK.
                reply = await session.request(index, 0, wanted)
                if reply:
                    raise RuntimeError('主音箱声道设置返回了未知回复。')
                actual = await session.request(index, 1, read=True)
                if actual != wanted:
                    raise RuntimeError('主音箱声道配置未通过回读确认。')
                reply = await session.request(index, 2,
                    guest.address.to_bytes(6, 'big') + bytes([guest_channel]))
                if reply:
                    raise RuntimeError('副音箱声道设置返回了未知回复。')
                actual = await session.request(index, 1, read=True)
                if actual != wanted:
                    raise RuntimeError('切换后主音箱声道配置未通过回读确认。')
            except Exception as error:
                raise ChannelChangeError(
                    '声道切换未完全确认，可能已有部分设置生效。请刷新状态；'
                    '如需恢复，选择“同播（完整声音）”并应用。') from error
            # Only host config has exact readback. Guest ACK is not proof of
            # audible channel separation; the UI always asks for a listening test.
            return ChannelResult(mode, audio_option)


def friendly_error(error):
    text = str(error)
    if isinstance(error, ChannelChangeError):
        return text
    if '10060' in text or 'timed out' in text.lower() or 'No matching' in text:
        return '音箱没有及时回应。请保持开机，暂时关闭手机蓝牙，再刷新状态。'
    if '10061' in text or '10064' in text or '10051' in text:
        return '暂时连不上音箱。请确认已在 Windows 配对、音箱已开机，手机没有占用控制连接。'
    if isinstance(error, UnsupportedFeature):
        return '这台音箱没有确认所需控制功能，未继续发送修改。'
    if 'membership is unknown' in text:
        return '已有组队，但成员未确认。请先刷新；必要时点结束 PartyUp，再重新开启。'
    if 'No known legacy' in text or 'protocol' in text.lower():
        return '未确认兼容的 BOOM 4 控制协议，未继续修改。'
    return text or type(error).__name__

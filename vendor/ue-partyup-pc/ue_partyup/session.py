"""Serialized, feature-discovering CPP control session."""
import asyncio
from .everboom_protocol import Everboom, RequestIds


class UnsupportedFeature(RuntimeError):
    pass


class ProtocolError(RuntimeError):
    pass


class NoReply(ProtocolError):
    pass


class Session:
    def __init__(self, speaker, transport_factory=Everboom):
        self.speaker = speaker
        self.factory = transport_factory
        self.transport = None
        self.ids = RequestIds()
        self.features = {}
        self.notifications = []
        self.lock = asyncio.Lock()
        self.connect_lock = asyncio.Lock()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        self.close()

    async def connect(self):
        async with self.connect_lock:
            if self.transport is not None:
                return self
            pending = asyncio.create_task(asyncio.to_thread(self.factory, self.speaker.address))
            try:
                self.transport = await asyncio.shield(pending)
            except asyncio.CancelledError:
                # A native connect cannot be canceled mid-call. Dispose of its
                # eventual socket rather than losing ownership when the caller leaves.
                def dispose(completed):
                    try:
                        completed.result().close()
                    except BaseException:
                        pass
                pending.add_done_callback(dispose)
                raise
            try:
                # A read-only root lookup proves the protocol before any mutation.
                await self.feature(1)
            except BaseException:
                self.close()
                raise
            return self

    async def feature(self, feature_id):
        if feature_id not in self.features:
            reply = await self.request(0, 0, feature_id.to_bytes(2, 'big'), read=True)
            if len(reply) != 3:
                raise ProtocolError('Invalid feature lookup reply.')
            if reply[0] == 0:
                raise UnsupportedFeature(f'This speaker does not offer feature 0x{feature_id:04X}.')
            self.features[feature_id] = reply[0]
        return self.features[feature_id]

    async def request(self, category, function, payload=b'', read=False, seconds=2):
        if self.transport is None:
            raise RuntimeError('Connect to the speaker first.')
        async with self.lock:
            for attempt in range(2 if read else 1):
                sequence = self.ids.next()
                frames = await asyncio.to_thread(self.transport.request, category, function,
                                                sequence, payload, seconds)
                self.notifications.extend(f for f in frames if len(f) >= 8 and f[5] & 15 == 0)
                replies = [f for f in frames if len(f) >= 8 and f[4] == category
                           and f[5] == function * 16 + sequence]
                if replies:
                    reply = replies[-1]
                    if reply[3] == 0xff:
                        if len(reply) != 9:
                            raise ProtocolError('Malformed speaker error reply.')
                        raise ProtocolError(f'Speaker rejected this command (error {reply[6]}).')
                    return reply[6:-2]
            raise NoReply('No matching speaker reply. Keep it on and close the phone app.')

    async def listen(self, seconds=2):
        async with self.lock:
            frames = await asyncio.to_thread(self.transport.read_modern, seconds)
            self.notifications.extend(f for f in frames if len(f) >= 8 and f[5] & 15 == 0)
            return frames

    def close(self):
        if self.transport is not None:
            transport, self.transport = self.transport, None
            transport.close()

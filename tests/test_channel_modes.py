"""Channel changes use one host connection, exact readback, no write retry."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boom_backend import CHANNEL_MODES, ChannelChangeError, Controller, host_channel_label
from ue_partyup.controller import Speaker
from ue_partyup.groups import XUP
from ue_partyup.session import NoReply

HOST = Speaker(0x100000000001, 'BOOM 4 A', True)
GUEST = Speaker(0x100000000002, 'BOOM 4 B', True)


class Session:
    def __init__(self, states, replies=(b'', b'')):
        self.states, self.replies = iter(states), iter(replies)
        self.requests = []
        self.closed = False

    async def __aenter__(self): return self
    async def __aexit__(self, *args): self.closed = True

    async def feature(self, feature):
        assert feature == XUP
        return 7

    async def request(self, index, function, payload=b'', read=False):
        self.requests.append((index, function, payload, read))
        result = next(self.states) if read else next(self.replies)
        if isinstance(result, Exception):
            raise result
        return result


class ChannelModeTests(unittest.IsolatedAsyncioTestCase):
    def test_mapping_matches_source_backed_official_app_swap_flow(self):
        self.assertEqual(list(CHANNEL_MODES.values()), [(0, 3), (0x50, 2), (0x90, 2)])

    async def test_every_mode_preserves_host_flags_and_sets_guest_after_host_readback(self):
        for name, (audio, channel) in CHANNEL_MODES.items():
            for flags in (2, 3):
                with self.subTest(mode=name, flags=flags):
                    state = bytes([flags, audio])
                    session = Session([bytes([flags, 0]), state, state])
                    factory = Mock(return_value=session)
                    result = await Controller(factory).channel_mode(HOST, GUEST, name)
                    self.assertEqual(result.audio_option, audio)
                    factory.assert_called_once_with(HOST)
                    self.assertEqual(session.requests, [
                        (7, 1, b'', True), (7, 0, state, False),
                        (7, 1, b'', True),
                        (7, 2, GUEST.address.to_bytes(6, 'big') + bytes([channel]), False),
                        (7, 1, b'', True)])
                    self.assertTrue(session.closed)

    async def test_not_broadcasting_or_invalid_state_never_writes(self):
        for state in (b'\0\0', b'\1\0', b'\xff\0', b'\2'):
            with self.subTest(state=state):
                session = Session([state])
                with self.assertRaises((ValueError, RuntimeError)):
                    await Controller(Mock(return_value=session)).channel_mode(HOST, GUEST, next(iter(CHANNEL_MODES)))
                self.assertEqual(session.requests, [(7, 1, b'', True)])

    async def test_invalid_selection_or_mode_rejected_before_connecting(self):
        factory = Mock()
        for host, guest, mode in ((HOST, HOST, 'invalid'), (HOST, GUEST, 'invalid'),
                                 (HOST, None, next(iter(CHANNEL_MODES))),
                                 (HOST, GUEST, None), (HOST, GUEST, [])):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                await Controller(factory).channel_mode(host, guest, mode)
        factory.assert_not_called()

    async def test_host_mismatch_never_sets_guest_or_rolls_back(self):
        session = Session([b'\2\0', b'\2\0'])
        with self.assertRaises(ChannelChangeError):
            await Controller(Mock(return_value=session)).channel_mode(HOST, GUEST, list(CHANNEL_MODES)[1])
        self.assertEqual([function for _, function, _, read in session.requests if not read], [0])
        self.assertTrue(session.closed)

    async def test_missing_or_bad_host_ack_is_unknown_without_retry(self):
        for reply in (NoReply('No matching speaker reply'), b'\1'):
            session = Session([b'\2\0'], [reply])
            with self.assertRaises(ChannelChangeError):
                await Controller(Mock(return_value=session)).channel_mode(HOST, GUEST, list(CHANNEL_MODES)[1])
            self.assertEqual(len(session.requests), 2)

    async def test_guest_unknown_result_is_not_retried_or_rolled_back(self):
        target = bytes([3, CHANNEL_MODES[list(CHANNEL_MODES)[1]][0]])
        for reply in (NoReply('No matching speaker reply'), b'\1'):
            session = Session([b'\3\0', target], [b'', reply])
            with self.assertRaises(ChannelChangeError) as error:
                await Controller(Mock(return_value=session)).channel_mode(HOST, GUEST, list(CHANNEL_MODES)[1])
            self.assertIn('部分设置生效', str(error.exception))
            self.assertEqual([function for _, function, _, read in session.requests if not read], [0, 2])

    async def test_final_readback_failure_is_not_reported_as_success(self):
        target = bytes([2, CHANNEL_MODES[list(CHANNEL_MODES)[1]][0]])
        session = Session([b'\2\0', target, NoReply('No reply')])
        with self.assertRaises(ChannelChangeError):
            await Controller(Mock(return_value=session)).channel_mode(HOST, GUEST, list(CHANNEL_MODES)[1])
        self.assertEqual(len(session.requests), 5)

    def test_unknown_host_options_remain_unknown(self):
        self.assertIn('未确认', host_channel_label(None, True))
        self.assertIn('模式未识别', host_channel_label(255, True))
        self.assertIn('未组队', host_channel_label(0, False))


if __name__ == '__main__':
    unittest.main()

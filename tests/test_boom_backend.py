"""Mocked Bluetooth contracts; no Windows discovery, audio or hardware I/O."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boom_backend import Controller
from boom_tray import App
from ue_partyup.controller import Speaker
from ue_partyup.groups import XUP
from ue_partyup.session import NoReply, ProtocolError
from ue_partyup.settings import VOLUME, BATTERY


HOST = Speaker(0x100000000001, "BOOM 4 A", experimental=True)
GUEST = Speaker(0x100000000002, "BOOM 4 B", experimental=True)


class ScriptedSession:
    """Emulate feature/getter replies, recording every application request."""

    def __init__(self, state=b"\2\0", volumes=(10,), notifications=(), setters=()):
        self.state = state
        self.volumes = iter(volumes)
        self.notifications = list(notifications)
        self.setters = iter(setters)
        self.requests = []
        self.features = []
        self.listen_calls = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def feature(self, feature):
        self.features.append(feature)
        return {XUP: 11, VOLUME: 6, BATTERY: 7}[feature]

    async def request(self, category, function, payload=b"", read=False):
        self.requests.append((category, function, payload, read))
        if (category, function) == (11, 1):
            return self.state
        if (category, function) == (6, 0):
            value = next(self.volumes)
            if isinstance(value, Exception):
                raise value
            return bytes([value])
        if (category, function) == (7, 0):
            return bytes([80, 78, 0])
        if (category, function) == (6, 1):
            value = next(self.setters, b"")
            if isinstance(value, Exception):
                raise value
            return value
        raise AssertionError(f"Unexpected command: {(category, function)}")

    async def listen(self, seconds):
        self.listen_calls.append(seconds)


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_speaker_rejected_before_any_connection_or_group_dispatch(self):
        factory, manager = Mock(), SimpleNamespace(run=AsyncMock())
        controller = Controller(factory, manager)
        for operation in (controller.status(HOST, HOST), controller.group("start", HOST, HOST)):
            with self.assertRaises(ValueError):
                await operation
        factory.assert_not_called()
        manager.run.assert_not_awaited()

    async def test_invalid_guest_type_rejected_before_backend_access(self):
        factory, manager = Mock(), SimpleNamespace(run=AsyncMock())
        controller = Controller(factory, manager)
        with self.assertRaises(ValueError):
            await controller.group("start", HOST, "not a speaker")
        factory.assert_not_called()
        manager.run.assert_not_awaited()

    async def test_status_only_connects_host_and_preserves_unknown_membership(self):
        session = ScriptedSession()
        factory = Mock(return_value=session)
        result = await Controller(factory).status(HOST, GUEST)
        factory.assert_called_once_with(HOST)
        self.assertTrue(session.closed)
        self.assertTrue(result.host_on)
        self.assertIsNone(result.guest_reported)
        self.assertEqual((result.volume, result.battery), (10, 78))
        self.assertEqual(session.listen_calls, [1])
        self.assertTrue(all(read for _, _, _, read in session.requests))
        self.assertEqual(set(session.features), {XUP, VOLUME, BATTERY})

    async def test_status_malformed_group_reply_fails_without_volume_or_guest_reads(self):
        session = ScriptedSession(state=b"\x07\0")
        factory = Mock(return_value=session)
        with self.assertRaises(ProtocolError):
            await Controller(factory).status(HOST, GUEST)
        factory.assert_called_once_with(HOST)
        self.assertEqual(session.requests, [(11, 1, b"", True)])
        self.assertTrue(session.closed)

    async def test_volume_invalid_input_does_not_even_open_a_connection(self):
        factory = Mock()
        controller = Controller(factory)
        for kwargs in ({"level": -1}, {"level": 32}, {"level": True},
                       {"level": 2.5}, {"delta": -32}, {"delta": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await controller.volume(HOST, **kwargs)
        factory.assert_not_called()

    async def test_volume_invalid_device_range_does_not_write(self):
        session = ScriptedSession(volumes=(255,))
        with self.assertRaises(ValueError):
            await Controller(Mock(return_value=session)).volume(HOST, level=5)
        self.assertEqual(session.requests, [(6, 0, b"", True)])

    async def test_volume_set_once_then_requires_exact_readback(self):
        session = ScriptedSession(volumes=(10, 11))
        result = await Controller(Mock(return_value=session)).volume(HOST, delta=1)
        self.assertEqual(result, 11)
        self.assertEqual(session.requests, [(6, 0, b"", True), (6, 1, b"\x0b", False),
                                            (6, 0, b"", True)])
        self.assertTrue(session.closed)

    async def test_volume_mismatched_or_missing_readback_raises_without_second_write(self):
        for after in (10, NoReply("No matching reply")):
            with self.subTest(after=type(after).__name__):
                session = ScriptedSession(volumes=(10, after))
                with self.assertRaises(RuntimeError):
                    await Controller(Mock(return_value=session)).volume(HOST, level=15)
                self.assertEqual(sum((category, function) == (6, 1)
                                     for category, function, _, _ in session.requests), 1)
                self.assertTrue(session.closed)

    async def test_volume_unknown_setter_result_is_not_retried_or_reported_as_success(self):
        session = ScriptedSession(volumes=(10,), setters=(NoReply("No matching reply"),))
        with self.assertRaises(NoReply):
            await Controller(Mock(return_value=session)).volume(HOST, level=15)
        self.assertEqual(len(session.requests), 2)
        self.assertEqual(session.requests[-1], (6, 1, b"\x0f", False))
        self.assertTrue(session.closed)

    async def test_group_dispatches_once_and_returns_partial_result_unchanged(self):
        unknown = SimpleNamespace(host_on=None, members=[], warnings=["Unknown outcome"])
        manager = SimpleNamespace(run=AsyncMock(return_value=unknown))
        progress = Mock()
        controller = Controller(Mock(), manager)
        result = await controller.group("start", HOST, GUEST, progress)
        self.assertIs(result, unknown)
        manager.run.assert_awaited_once_with("start", HOST, [GUEST], progress, experimental=True)
        controller.session_factory.assert_not_called()

    async def test_group_timeout_propagates_with_one_dispatch(self):
        manager = SimpleNamespace(run=AsyncMock(side_effect=TimeoutError("outcome unknown")))
        with self.assertRaises(TimeoutError):
            await Controller(Mock(), manager).group("stop", HOST, GUEST)
        self.assertEqual(manager.run.await_count, 1)


class Variable:
    def __init__(self): self.value = ""
    def set(self, value): self.value = value
    def get(self): return self.value


def result_view():
    """The real result renderer, with plain variables and no Tk window."""
    return SimpleNamespace(busy=True, progress=SimpleNamespace(stop=Mock()),
        group_text=Variable(), status_text=Variable(), channel_text=Variable(),
        update_controls=Mock(), show=Mock())


class ResultPresentationTests(unittest.TestCase):
    def test_group_unknown_or_partial_results_do_not_show_full_acceptance(self):
        cases = [("start", None, "join accepted"), ("start", True, "unconfirmed"),
                 ("start", True, None), ("stop", False, "host removal accepted; guest clear unconfirmed")]
        for action, host_on, member_state in cases:
            with self.subTest(action=action, host_on=host_on, member=member_state):
                view = result_view()
                result = SimpleNamespace(host_on=host_on,
                    members=[] if member_state is None else [SimpleNamespace(state=member_state)])
                App.on_result(view, action, result, None)
                self.assertIn("未完全确认", view.status_text.get())
                self.assertNotIn("组队指令已接受", view.status_text.get())
                self.assertNotIn("移除副音箱指令已接受", view.status_text.get())
                if host_on is None:
                    self.assertEqual(view.group_text.get(), "未确认")

    def test_group_transport_error_does_not_leave_confirmed_state(self):
        view = result_view()
        view.group_text.set("已开启")
        App.on_result(view, "start", None, NoReply("No matching reply"))
        self.assertEqual(view.group_text.get(), "未确认")
        self.assertIn("操作未确认", view.status_text.get())
        view.show.assert_called_once()


if __name__ == "__main__":
    unittest.main()

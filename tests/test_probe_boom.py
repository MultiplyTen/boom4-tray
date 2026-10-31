"""The diagnostic must never issue a mutation or broaden its command scope."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_boom import (ReadOnlySession, probe_session, model_hint, NAME,
                             BATTERY, VOLUME, XUP, Session, failure)
from ue_partyup.controller import Speaker


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_mutations_and_unlisted_reads_rejected_before_transport(self):
        session = ReadOnlySession(Speaker(1))
        session.features = {NAME: 5, XUP: 11}
        for args, kwargs in [((5, 1, b"a"), {}), ((11, 0, b"\3\0"), {}),
                             ((0, 0, b"\2\1"), {"read": True}),
                             ((11, 9), {"read": True}), ((5, 0), {})]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    await session.request(*args, **kwargs)

    async def test_allowed_requests_force_short_read_timeout(self):
        session = ReadOnlySession(Speaker(1))
        session.features = {XUP: 11}
        with patch.object(Session, "request", return_value=b"\0\0") as original:
            await session.request(11, 1, read=True, seconds=20)
        original.assert_awaited_once_with(11, 1, b"", read=True, seconds=.75)

    async def test_single_connection_known_getters_only_and_report_redaction(self):
        class FakeSession:
            def __init__(self, speaker):
                self.features = {1: 1}
                self.requests = []
                self.closed = False
                self.connections = 0
            async def connect(self): self.connections += 1
            async def feature(self, feature):
                self.features[feature] = {XUP: 11, NAME: 5, VOLUME: 6, BATTERY: 7}[feature]
                return self.features[feature]
            async def request(self, index, function, payload=b"", read=False):
                assert read is True and payload == b""
                self.requests.append((index, function))
                return {(11, 1): b"\0\0", (5, 0): b"\xff\x0bPrivateName",
                        (6, 0): b"\x0a", (7, 0): b"\x50\x50\0"}[(index, function)]
            def close(self): self.closed = True
        instance = FakeSession(None)
        report = await probe_session(Speaker(1, "SecretName"), lambda speaker: instance)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(instance.requests, [(11, 1), (5, 0), (6, 0), (7, 0)])
        self.assertEqual(instance.connections, 1)
        self.assertTrue(instance.closed)
        self.assertNotIn("PrivateName", str(report))
        self.assertNotIn("SecretName", str(report))
        self.assertFalse(report["audio_playback_verified"])

    async def test_group_query_failure_stops_before_other_reads_and_closes(self):
        class BrokenSession:
            def __init__(self): self.features = {}; self.closed = False
            async def connect(self): pass
            async def feature(self, feature):
                self.assert_xup = feature == XUP
                raise TimeoutError("secret speaker 11:22:33:44:55:66")
            def close(self): self.closed = True
        instance = BrokenSession()
        report = await probe_session(Speaker(1), lambda speaker: instance)
        self.assertEqual(list(report["queries"]), ["group"])
        self.assertTrue(instance.closed)
        self.assertNotIn("secret", str(report))

    def test_model_hint_does_not_keep_custom_name(self):
        self.assertEqual(model_hint("Alice's BOOM 4 (private)"), "BOOM 4")
        self.assertEqual(model_hint("Secret Speaker"), "UE speaker")

    def test_only_known_numeric_transport_errors_are_retained(self):
        self.assertEqual(failure(RuntimeError("Bluetooth connect error 10064."))["winsock_error"], 10064)
        self.assertNotIn("secret", str(failure(RuntimeError("secret 11:22:33:44:55:66"))))


if __name__ == "__main__":
    unittest.main()

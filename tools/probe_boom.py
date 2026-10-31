"""Bounded, read-only BOOM CPP diagnostics; never starts or stops PartyUp.

With no option this only lists paired UE speakers. Use --speaker 1 to inspect a
known main speaker, or --all only while the speakers are idle and ungrouped.
Opening a control connection to an active PartyUp guest can interrupt its audio,
even when every application command is a getter. An observed active main stops
the remaining probes. Speaker names and Bluetooth addresses are never reported.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from functools import partial
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "ue-partyup-pc"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

sys.path.insert(0, str(ROOT))
from boom_backend import discover_speakers as discover
from ue_partyup.everboom_protocol import Everboom
from ue_partyup.session import Session, ProtocolError, UnsupportedFeature
from ue_partyup.settings import NAME, BATTERY, VOLUME, XUP, Settings


READ_FEATURES = frozenset((1, NAME, BATTERY, VOLUME, XUP))
READ_FUNCTIONS = {NAME: frozenset((0,)), BATTERY: frozenset((0,)),
                  VOLUME: frozenset((0,)), XUP: frozenset((1,))}


class ReadOnlySession(Session):
    """Restrict the upstream session to five lookups and four known getters."""

    def __init__(self, speaker, transport_factory=None):
        factory = transport_factory or partial(Everboom, connect_timeout=3, io_timeout=.5)
        super().__init__(speaker, transport_factory=factory)

    async def request(self, category, function, payload=b"", read=False, seconds=.75):
        if read is not True:
            raise ValueError("The hardware probe accepts read requests only.")
        if category == 0:
            allowed = function == 0 and len(payload) == 2 and int.from_bytes(payload, "big") in READ_FEATURES
        else:
            allowed = not payload and any(
                index == category and function in READ_FUNCTIONS.get(feature, ())
                for feature, index in self.features.items())
        if not allowed:
            raise ValueError("This command is outside the read-only probe whitelist.")
        return await super().request(category, function, payload, read=True, seconds=min(seconds, .75))


def model_hint(name):
    match = re.search(r"\b((?:MEGA)?BOOM\s*[234]?|EVERBOOM|EPICBOOM|HYPERBOOM)\b", str(name).upper())
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else "UE speaker"


def inventory(speakers):
    return [{"speaker": i, "model_hint": model_hint(s.name)} for i, s in enumerate(speakers, 1)]


def failure(error):
    # Raw backend exceptions can include private device names/addresses.
    result = {"status": "unsupported" if isinstance(error, UnsupportedFeature) else "unavailable",
              "error_type": type(error).__name__}
    match = re.fullmatch(r"Bluetooth (?:connect|socket|socket mode) error (\d+)\.", str(error))
    if match:
        result["winsock_error"] = int(match.group(1))
    if str(error) == "Bluetooth connection timed out.":
        result["reason"] = "connection_timeout"
    return result


async def probe_session(speaker, session_factory=ReadOnlySession):
    """One connection, no fallback/reconnect, no capability sweep or setters.

    This function is suitable for an app worker. The CLI additionally isolates it
    in a process so a stalled native Bluetooth driver cannot hang the caller.
    ``Session.connect`` performs its own read-only CPP root-feature lookup.
    """
    result = {"status": "unavailable", "cpp_confirmed": False, "queries": {},
              "audio_playback_verified": False, "settings_changed": False}
    session = session_factory(speaker)
    try:
        await session.connect()
        result["cpp_confirmed"] = True
        try:
            index = await session.feature(XUP)
            reply = await session.request(index, 1, read=True)
            if len(reply) != 2 or reply[0] not in (0, 1, 2, 3):
                raise ProtocolError("Invalid PartyUp state reply.")
            result["queries"]["group"] = {"status": "ok", "state": reply[0],
                "broadcasting": bool(reply[0] & 2), "audio_option": reply[1]}
        except Exception as error:
            result["queries"]["group"] = failure(error)
            result["status"] = "partial"
            return result

        settings = Settings(session)
        for key, getter in (("name", settings.name), ("volume", settings.volume),
                            ("battery", settings.battery)):
            try:
                value = await getter()
                if key == "name":
                    # Verify parsing but retain no personal/custom speaker name.
                    data = {"name_read": True, "model_hint": model_hint(value)}
                elif key == "battery":
                    data = {"percent": value.percent, "charging_code": value.charging_code}
                else:
                    data = {"level": value, "maximum": 31}
                result["queries"][key] = {"status": "ok", **data}
            except Exception as error:
                result["queries"][key] = failure(error)
                # A missing response is not a reason to continue touching the device.
                if not isinstance(error, UnsupportedFeature):
                    break
        result["feature_indices"] = {f"0x{key:04X}": value for key, value in session.features.items()}
        result["status"] = "ok" if all(q["status"] == "ok" for q in result["queries"].values()) else "partial"
    except Exception as error:
        result["connection"] = failure(error)
    finally:
        session.close()
    return result


def _worker(speaker, sender):
    try:
        sender.send(asyncio.run(probe_session(speaker)))
    except BaseException as error:
        sender.send({"status": "unavailable", "error_type": type(error).__name__,
                     "cpp_confirmed": False, "audio_playback_verified": False})
    finally:
        sender.close()


def probe_bounded(speaker, timeout=22, context=None):
    """Hard limit for each device, with OS socket cleanup on driver stalls."""
    context = context or mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(speaker, sender), daemon=True)
    process.start()
    sender.close()
    try:
        if receiver.poll(timeout):
            try:
                return receiver.recv()
            except EOFError:
                return {"status": "unavailable", "error_type": "WorkerExited", "cpp_confirmed": False}
        return {"status": "unavailable", "error_type": "DeviceTimeout", "cpp_confirmed": False}
    finally:
        if process.is_alive():
            process.join(.25)
        if process.is_alive():
            process.terminate()
        process.join(2)
        receiver.close()


def save_report(report, path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--speaker", type=int, help="Probe one known main by its inventory number (1-based).")
    selection.add_argument("--all", action="store_true", help="Probe sequentially; use only while idle/ungrouped.")
    parser.add_argument("--output", type=Path, default=ROOT / "hardware-status.json")
    args = parser.parse_args(argv)
    report = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
              "read_only": True, "audio_playback_verified": False, "speakers": []}
    try:
        speakers = discover()
    except Exception as error:
        report["inventory_error"] = failure(error)
        save_report(report, args.output)
        print("Paired-speaker inventory unavailable (" + type(error).__name__ + ").")
        return 1
    report["speakers"] = inventory(speakers)
    print(f"Paired UE speakers: {len(speakers)}. Device names/addresses are redacted.")
    for item in report["speakers"]:
        print(f"  Speaker {item['speaker']}: {item['model_hint']}")
    if args.speaker is not None and not 1 <= args.speaker <= len(speakers):
        parser.error("--speaker must be a listed inventory number.")
    selected = range(len(speakers)) if args.all else ([args.speaker - 1] if args.speaker else [])
    for index in selected:
        result = probe_bounded(speakers[index])
        report["speakers"][index]["probe"] = result
        print(f"Speaker {index + 1}: CPP={result.get('cpp_confirmed', False)}, status={result['status']}.")
        group = result.get("queries", {}).get("group", {})
        if group.get("broadcasting") is True:
            report["remaining_probes_skipped"] = "Active PartyUp main observed; guests were not contacted."
            print("Active PartyUp main observed. Remaining devices were not contacted.")
            break
        if group.get("status") != "ok":
            report["remaining_probes_skipped"] = "Group state unavailable; other devices were not contacted."
            print("Group state unavailable. Remaining devices were not contacted.")
            break
    save_report(report, args.output)
    print("Local report saved. No playback or setting commands were sent.")
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())

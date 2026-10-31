# Protocol provenance and vendored scope

BOOM 4 Tray uses a limited, unmodified subset of the MIT-licensed [UE PartyUp PC](https://github.com/behemothnemoth-a11y/ue-partyup-pc) implementation, pinned to commit `4076ace2da564e8fcf45c4ca93a705495d4f2bf2`.

The vendored Python files under `vendor/ue-partyup-pc/ue_partyup/` are:

```text
__init__.py
controller.py
controls.py
diagnose.py
everboom_protocol.py
groups.py
legacy.py
session.py
settings.py
test_connection.py
ue_protocol.py
```

The upstream MIT license is retained. Tcl/Tk and PyInstaller notices used by the Windows bundle are retained alongside it. Original upstream GUI, artwork, fonts, installation tools, test fixtures, research snapshots and user data are excluded from this project's published source.

The root application and diagnostic enumerate BOOM 4 candidates through `boom_backend.discover_speakers()`. The upstream `controller.discover()` path is not used; its original GUI/runtime dependency is intentionally not vendored.

## Channel settings

The host CPP audio option and the guest AddReceiver channel belong to different value sets:

| Requested output | Host audio option | Guest AddReceiver channel |
| --- | ---: | ---: |
| Both speakers play full audio | `0x00` | `3` |
| Main left, guest right | `0x50` | `2` |
| Main right, guest left | `0x90` | `2` |

The stereo and swap combinations follow the historical official-app UI flow, rather than inferring a new pairing from enum names. Current upstream documentation records corresponding CPP values. BOOM 4 Tray independently adds a bounded host-only change sequence and exact main-configuration readback. It never interprets an ACK as proof of acoustic separation; the original BOOM 4 pair was separately checked by listening, as recorded in [TESTING.md](TESTING.md).

References:

- [Current upstream compatibility and channel mapping](https://github.com/behemothnemoth-a11y/ue-partyup-pc/blob/4076ace2da564e8fcf45c4ca93a705495d4f2bf2/docs/COMPATIBILITY.md)
- [Historical stereo and swap UI flow](https://github.com/countableSet/ue-boom-re/blob/main/boom/src/main/java/com/logitech/ue/fragments/XUpFragment.java#L1518-L1567)

No APK, decompiled manufacturer implementation, raw packet capture or device identifier is distributed here. The historical source above is cited as protocol evidence, not copied into this project.

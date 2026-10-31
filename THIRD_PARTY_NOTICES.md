# Third-party notices

BOOM 4 Tray is an independent local utility. Ultimate Ears, BOOM and related names are trademarks of their owners. No manufacturer app, decompiled source, packet capture or manufacturer artwork is included in the portable bundle.

The project's original code, generated test tone and original icon are distributed under the MIT license in the root `LICENSE`. Third-party components retain the licenses below.

## UE PartyUp PC protocol implementation

- Source: https://github.com/behemothnemoth-a11y/ue-partyup-pc
- Pinned commit: `4076ace2da564e8fcf45c4ca93a705495d4f2bf2`
- Copyright (c) 2026 PartyUp PC contributors.
- License: MIT. The complete license is retained in `vendor/ue-partyup-pc/LICENSE` in the source checkout and `_internal/licenses/ue-partyup-pc/LICENSE` in the portable bundle.

Only its protocol and Windows Bluetooth helpers are used. The original application artwork and fonts are not used. The blue/purple two-speaker tray icon was drawn in code for this utility.

## Runtime components

| Component | Version | License / source |
| --- | --- | --- |
| Python / Tcl / Tk | Build interpreter versions | [Python license](https://docs.python.org/3/license.html), [Tcl/Tk](https://www.tcl.tk/software/tcltk/license.html) |
| bleak | 3.0.2 | MIT, https://github.com/hbldh/bleak |
| Pillow | 13.0.0 | MIT-CMU, https://github.com/python-pillow/Pillow |
| pystray | 0.19.5 | LGPLv3, https://github.com/moses-palmer/pystray |
| six | Installed dependency version | MIT, https://github.com/benjaminp/six |
| PyWinRT projections / runtime | Installed dependency versions | MIT, https://github.com/pywinrt/pywinrt |

Installed versions and supplied license notices for bleak, Pillow, pystray, six and WinRT distributions are retained in their `_internal/*.dist-info` folders. The unmodified pystray Python source is also retained under `_internal/licenses/pystray-source` so that it remains available independently of the executable. The source checkout and build script allow the utility to be rebuilt against a modified pystray implementation.

Python's complete license is retained under `_internal/licenses/python`. Tcl/Tk 8.6.15 notices are retained under `_internal/licenses/tcl` and `_internal/licenses/tk`.

PyInstaller 6.22.3 is build tooling. Its bootloader is distributed under GPLv2-or-later with the [PyInstaller bootloader exception](https://pyinstaller.org/en/stable/license.html), which permits bundling applications without imposing the GPL on the application.

The PyInstaller license collection, including the bootloader exception and Apache 2.0 terms for its runtime hooks, is retained under `_internal/licenses/pyinstaller/COPYING.txt`.

# Local Windows x64 folder bundle. No device selections or captures are bundled.
from pathlib import Path
import importlib.metadata
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
vendor = root / 'vendor' / 'ue-partyup-pc'
datas = [
    (str(root / 'assets' / 'boom4.ico'), 'assets'),
    (str(root / 'assets' / 'channel-test.wav'), 'assets'),
    (str(root / 'README.md'), '.'),
    (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
    (str(root / 'LICENSE'), '.'),
    (str(vendor / 'LICENSE'), 'licenses/ue-partyup-pc'),
    (str(Path(sys.base_prefix) / 'LICENSE.txt'), 'licenses/python'),
    (str(vendor / 'licenses' / 'Tcl-license.terms'), 'licenses/tcl'),
    (str(vendor / 'licenses' / 'Tk-license.terms'), 'licenses/tk'),
    (str(vendor / 'licenses' / 'pyinstaller' / 'licenses' / 'COPYING.txt'), 'licenses/pyinstaller'),
]

# Retain installed dependency versions and their bundled license notices.
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get('Name', '')
    normalized = name.lower().replace('_', '-')
    if normalized in ('bleak', 'pillow', 'pystray', 'six') or normalized.startswith('winrt-'):
        datas += copy_metadata(name)

# pystray is LGPLv3; keep its unmodified Python source available with the bundle.
for source, destination in collect_data_files('pystray', include_py_files=True):
    datas.append((source, 'licenses/pystray-source/' + destination))

analysis = Analysis(
    [str(root / 'boom_tray.py')],
    pathex=[str(root), str(vendor)],
    datas=datas,
    hiddenimports=collect_submodules('winrt') + ['pystray._win32'],
    excludes=[
        'pytest', 'unittest', 'pip', 'setuptools',
        'pystray._darwin', 'pystray._appindicator', 'pystray._gtk', 'pystray._xorg',
        # The controller imports only protocol helpers, not the upstream GUI/runtime.
        'ue_partyup.app', 'ue_partyup.runtime', 'ue_partyup.installer',
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    name='BOOM4-Tray', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(root / 'assets' / 'boom4.ico'),
)
bundle = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='BOOM4-Tray')


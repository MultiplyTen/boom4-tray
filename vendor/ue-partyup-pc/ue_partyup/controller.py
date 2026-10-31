from dataclasses import dataclass

GROUP_UUID='69c0f621-1354-4cf8-98a6-328b8faa1897'

@dataclass(frozen=True)
class Speaker:
    address: int
    name: str = 'EVERBOOM'
    experimental: bool = False
    def __post_init__(self):
        if isinstance(self.address, bool) or not isinstance(self.address, int) or not 0 < self.address < 1 << 48:
            raise ValueError('Invalid Bluetooth speaker address.')
    @property
    def label(self):
        return f'{self.name} · {self.address & 0xffffff:06X}'

def discover():
    from .test_connection import paired_devices
    import json, re
    from pathlib import Path
    try:
        from .runtime import data_directory
        known = json.loads((data_directory()/'devices.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        known = {}
    if not isinstance(known, dict):
        known = {}
    result = []
    for address, name in paired_devices():
        model = known.get(str(address), name)
        if not isinstance(model, str):
            model = name
        if re.search(r'\b(?:EVERBOOM|MEGABOOM(?:\s*[234])?|BOOM(?:\s*[234])?|EPICBOOM|HYPERBOOM)\b', model.upper()):
            result.append(Speaker(address, name, 'EVERBOOM' not in model.upper()))
    return sorted(result, key=lambda s:(s.name, s.address))

def matching(frames,category,operation,sequence):
    return [f for f in frames if len(f)>=8 and f[4]==category and f[5]==operation*16+sequence]

def acknowledged(frames,category,operation,sequence):
    return any(len(f)==8 and f[-2:]==b'\0\0' for f in matching(frames,category,operation,sequence))

async def control(action, host, guest, progress=lambda message: None):
    """Compatibility entry point for earlier two-speaker scripts."""
    from .groups import GroupManager
    return str(await GroupManager().run(action, host, [guest], progress))

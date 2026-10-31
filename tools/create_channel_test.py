"""Generate a deterministic channel-isolation WAV. Never opens an audio device."""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import sys
import wave

SAMPLE_RATE = 48_000
DURATION_SECONDS = 8
TONE_SECONDS = 1
FADE_SECONDS = 0.050
PEAK_DBFS = -18.0
PEAK_SAMPLE = math.floor(32767 * 10 ** (PEAK_DBFS / 20))
# (start second, channel index, frequency). All other samples remain exactly zero.
TONES = ((0, 0, 440), (2, 1, 660), (4, 0, 440), (6, 1, 660))


def fade_envelope(frame: int, tone_frames: int, fade_frames: int) -> float:
    distance = min(frame, tone_frames - frame - 1)
    if distance >= fade_frames:
        return 1.0
    return 0.5 * (1 - math.cos(math.pi * distance / (fade_frames - 1)))


def create_wave(path: Path) -> None:
    """Write 8 seconds of 16-bit little-endian stereo PCM at 48 kHz."""
    frames = SAMPLE_RATE * DURATION_SECONDS
    samples = array('h', [0]) * (frames * 2)
    tone_frames = SAMPLE_RATE * TONE_SECONDS
    fade_frames = round(SAMPLE_RATE * FADE_SECONDS)
    for start_second, channel, frequency in TONES:
        offset = start_second * SAMPLE_RATE
        for frame in range(tone_frames):
            envelope = fade_envelope(frame, tone_frames, fade_frames)
            value = PEAK_SAMPLE * envelope * math.sin(math.tau * frequency * frame / SAMPLE_RATE)
            samples[(offset + frame) * 2 + channel] = round(value)
    if sys.byteorder != 'little':
        samples.byteswap()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples.tobytes())


def main() -> None:
    target = Path(__file__).resolve().parents[1] / 'assets' / 'channel-test.wav'
    create_wave(target)
    print(f'Generated {target.name}: 8 s, stereo PCM 16-bit/48 kHz, peak <= -18 dBFS.')


if __name__ == '__main__':
    main()


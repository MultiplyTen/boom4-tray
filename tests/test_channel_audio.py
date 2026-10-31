"""Validate the deliverable audio without playing it or opening an audio device."""
from array import array
import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / 'assets' / 'channel-test.wav'


class ChannelAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with wave.open(str(ASSET), 'rb') as audio:
            cls.params = audio.getparams()
            cls.data = array('h', audio.readframes(audio.getnframes()))
        if sys.byteorder != 'little':
            cls.data.byteswap()
        cls.left = cls.data[0::2]
        cls.right = cls.data[1::2]

    def test_pcm_format_and_exact_duration(self):
        self.assertEqual(self.params.nchannels, 2)
        self.assertEqual(self.params.sampwidth, 2)
        self.assertEqual(self.params.framerate, 48_000)
        self.assertEqual(self.params.nframes, 8 * 48_000)
        self.assertEqual(self.params.comptype, 'NONE')
        self.assertEqual(len(self.data), 8 * 48_000 * 2)

    def test_non_target_channel_is_exactly_zero(self):
        for second, target, other in ((0, self.left, self.right), (2, self.right, self.left),
                                      (4, self.left, self.right), (6, self.right, self.left)):
            start, end = second * 48_000, (second + 1) * 48_000
            with self.subTest(second=second):
                self.assertTrue(any(target[start:end]))
                self.assertFalse(any(other[start:end]))

    def test_silence_between_tones_is_exactly_zero(self):
        for second in (1, 3, 5, 7):
            with self.subTest(second=second):
                self.assertFalse(any(self.data[second * 48_000 * 2:(second + 1) * 48_000 * 2]))

    def test_peak_is_quiet_and_not_clipped(self):
        peak = max(abs(sample) for sample in self.data)
        self.assertLessEqual(peak, math.floor(32767 * 10 ** (-18 / 20)))
        self.assertGreater(peak, 0)
        self.assertAlmostEqual(20 * math.log10(peak / 32767), -18.0, delta=0.02)

    def test_both_ends_fade_to_zero(self):
        for second, channel in ((0, self.left), (2, self.right), (4, self.left), (6, self.right)):
            signal = channel[second * 48_000:(second + 1) * 48_000]
            with self.subTest(second=second):
                self.assertEqual(signal[0], 0)
                self.assertEqual(signal[-1], 0)
                center_peak = max(abs(value) for value in signal[24_000:24_480])
                self.assertLess(max(abs(value) for value in signal[:240]), center_peak * 0.03)
                self.assertLess(max(abs(value) for value in signal[-240:]), center_peak * 0.03)

    def test_tones_have_distinct_documented_frequencies(self):
        for second, channel, expected in ((0, self.left, 440), (2, self.right, 660),
                                          (4, self.left, 440), (6, self.right, 660)):
            signal = channel[second * 48_000 + 4_800:second * 48_000 + 43_200]
            crossings = sum(before <= 0 < after for before, after in zip(signal, signal[1:]))
            with self.subTest(second=second):
                self.assertAlmostEqual(crossings / 0.8, expected, delta=2)

    def test_regeneration_is_byte_identical(self):
        spec = importlib.util.spec_from_file_location('channel_audio_generator', ROOT / 'tools' / 'create_channel_test.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'regenerated.wav'
            module.create_wave(path)
            self.assertEqual(path.read_bytes(), ASSET.read_bytes())


if __name__ == '__main__':
    unittest.main()


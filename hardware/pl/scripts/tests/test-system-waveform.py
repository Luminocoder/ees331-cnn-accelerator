import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("waveform", Path(__file__).parents[1] / "summarize-system-waveform.py")
waveform = importlib.util.module_from_spec(spec)
spec.loader.exec_module(waveform)


class WaveformTests(unittest.TestCase):
    def test_signed_boundaries(self):
        self.assertEqual(waveform.integer_value("01111111", 8, True), 127)
        self.assertEqual(waveform.integer_value("10000000", 8, True), -128)
        self.assertEqual(waveform.integer_value("11111111", 8, True), -1)
        self.assertEqual(waveform.integer_value("11111111", 8), 255)

    def test_unknown_is_not_zero(self):
        self.assertIsNone(waveform.integer_value("0xxxx", 32))
        self.assertIsNone(waveform.integer_value("z", 1))
        self.assertEqual(waveform.integer_value("0", 32, True), 0)

    def test_vcd_timescale_and_final_hold(self):
        fixture = "$timescale\n1ps\n$end\n$var wire 1 ! done $end\n$var wire 32 # score [31:0] $end\n$enddefinitions $end\n#0\nx!\nbx #\n#10000\n0!\nb0 #\n#20000\n1!\nb11111111111111111111111111111111 #\n#25000\n"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.vcd"
            path.write_text(fixture, encoding="utf-8")
            signals, end = waveform.read_vcd(path)
        self.assertEqual(end, 25)
        self.assertEqual(signals["done"]["changes"], [(0, "x"), (10, "0"), (20, "1")])
        self.assertEqual(waveform.integer_value(signals["score"]["changes"][-1][1], 32, True), -1)

    def test_missing_timescale_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.vcd"
            path.write_text("$enddefinitions $end\n#0\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "timescale"):
                waveform.read_vcd(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

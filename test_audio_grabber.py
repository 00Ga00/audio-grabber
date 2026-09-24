import importlib.machinery
import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent / "audio_grabber.pyw"
loader = importlib.machinery.SourceFileLoader("audio_grabber", str(MODULE_PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
audio_grabber = importlib.util.module_from_spec(spec)
loader.exec_module(audio_grabber)


class UtilityTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(audio_grabber.parse_time("1:02:03"), 3723)
        self.assertEqual(audio_grabber.parse_time("2：30"), 150)
        self.assertEqual(audio_grabber.parse_time("90.5"), 90.5)
        self.assertIsNone(audio_grabber.parse_time(""))

    def test_parse_time_rejects_invalid_values(self):
        for value in ("abc", "1:60", "1:2:60", "1:2:3:4", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audio_grabber.parse_time(value)

    def test_validate_url(self):
        self.assertEqual(audio_grabber.validate_url(" https://example.com/a "), "https://example.com/a")
        for value in ("", "example.com", "file:///tmp/a", "javascript:alert(1)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audio_grabber.validate_url(value)

    def test_safe_filename(self):
        self.assertEqual(audio_grabber.safe_filename('a<b>:c/"d"'), "a_b__c__d_")
        self.assertEqual(audio_grabber.safe_filename("..."), "audio")


if __name__ == "__main__":
    unittest.main()

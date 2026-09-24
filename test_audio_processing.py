import os
import tempfile
import unittest

import audio_processing


class AudioProcessingTests(unittest.TestCase):
    def test_filter_chain_is_optional(self):
        self.assertIsNone(audio_processing._filters(False, False, -45))

    def test_filter_chain_combines_trim_and_normalization(self):
        chain = audio_processing._filters(True, True, -45)
        self.assertIn("silenceremove", chain)
        self.assertIn("areverse", chain)
        self.assertIn("loudnorm", chain)

    def test_unique_path_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            existing = os.path.join(folder, "recording.wav")
            open(existing, "wb").close()
            self.assertEqual(
                audio_processing.unique_path(folder, "recording", "wav"),
                os.path.join(folder, "recording (1).wav"),
            )


if __name__ == "__main__":
    unittest.main()

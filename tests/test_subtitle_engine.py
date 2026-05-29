from __future__ import annotations

import unittest

from path_setup import install_paths


install_paths()

from capture_express.subtitle_engine import (  # noqa: E402
    SubtitleSegment,
    _segments_from_payload,
    format_srt_time,
    parse_srt,
    segments_to_srt,
)


class SubtitleEngineTest(unittest.TestCase):
    def test_format_srt_time(self) -> None:
        self.assertEqual(format_srt_time(0), "00:00:00,000")
        self.assertEqual(format_srt_time(65.432), "00:01:05,432")
        self.assertEqual(format_srt_time(3661.007), "01:01:01,007")

    def test_segments_to_srt_and_parse(self) -> None:
        source = [
            SubtitleSegment(index=1, start=0.0, end=1.25, text="Bonjour"),
            SubtitleSegment(index=2, start=1.5, end=3.0, text="On lance la machine."),
        ]
        srt = segments_to_srt(source)

        self.assertIn("00:00:00,000 --> 00:00:01,250", srt)
        self.assertIn("On lance la machine.", srt)

        parsed = parse_srt(srt)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].text, "Bonjour")
        self.assertEqual(parsed[1].start, 1.5)

    def test_known_whisper_hallucinations_are_rejected(self) -> None:
        payload = {
            "segments": [
                {"start": 0, "end": 3, "text": "Sous-titres realises para la communaute d'Amara.org"},
                {"start": 30, "end": 35, "text": "Voir une autre video ..."},
            ]
        }
        with self.assertRaises(RuntimeError):
            _segments_from_payload(payload)


if __name__ == "__main__":
    unittest.main()

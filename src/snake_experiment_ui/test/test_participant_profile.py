from pathlib import Path
import tempfile
import unittest

from snake_experiment_ui.participant_profile import (
    ParticipantProfile,
    ParticipantProfileError,
    video_is_available,
)


class ParticipantProfileTest(unittest.TestCase):
    def test_overload_sound_duration_defaults_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant_interface.yaml"
            base = ('schema_version: 1\nvideos:\n  experiment_presentation: ""\n'
                    '  mode_explanation:\n    baseline: ""\n    snake: ""\n')
            path.write_text(base)
            self.assertEqual(ParticipantProfile(path).overload_sound_duration_sec, 3.0)
            for value in ("0.25", "1", "3.0", "15.5"):
                path.write_text(base + f"sounds:\n  overload:\n    duration_sec: {value}\n")
                self.assertEqual(ParticipantProfile(path).overload_sound_duration_sec, float(value))
            for value in ("0", "-1", ".nan", ".inf", "true", '"3"', "null", "[]", str(10**400)):
                path.write_text(base + f"sounds:\n  overload:\n    duration_sec: {value}\n")
                with self.assertRaisesRegex(ParticipantProfileError, "duration_sec"):
                    ParticipantProfile(path)
            for value in ("sounds: []\n", "sounds:\n  overload: null\n"):
                path.write_text(base + value)
                with self.assertRaisesRegex(ParticipantProfileError, "sound configuration"):
                    ParticipantProfile(path)

    def test_preview_default_view_is_optional_and_accepts_six_views(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant_interface.yaml"
            base = (
                'schema_version: 1\nvideos:\n'
                '  experiment_presentation: ""\n'
                '  mode_explanation:\n    baseline: ""\n    snake: ""\n'
            )
            path.write_text(base)
            self.assertEqual(ParticipantProfile(path).preview_default_view, "view_1")
            for index in range(1, 7):
                path.write_text(base + f"preview:\n  default_view: view_{index}\n")
                self.assertEqual(ParticipantProfile(path).preview_default_view, f"view_{index}")
            for invalid in ('view_7', 'null', '1', '[view_1]', '""'):
                with self.subTest(invalid=invalid):
                    path.write_text(base + f"preview:\n  default_view: {invalid}\n")
                    with self.assertRaisesRegex(ParticipantProfileError, "default_view"):
                        ParticipantProfile(path)
            path.write_text(base + "preview: null\n")
            with self.assertRaisesRegex(ParticipantProfileError, "default_view"):
                ParticipantProfile(path)

    def test_packaged_profile_has_three_video_slots(self):
        path = Path(__file__).parents[1] / "config/participant_interface.yaml"
        profile = ParticipantProfile(path)
        for kind, mode in (
            ("experiment_presentation", None),
            ("mode_explanation", "baseline"),
            ("mode_explanation", "snake"),
        ):
            with self.subTest(kind=kind, mode=mode):
                resolved = profile.video_path(kind, mode)
                self.assertTrue(resolved is None or resolved.is_absolute())

    def test_absolute_and_relative_paths_are_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "participant_interface.yaml"
            path.write_text(
                'schema_version: 1\nvideos:\n'
                '  experiment_presentation: videos/presentation.mp4\n'
                '  mode_explanation:\n'
                '    baseline: /tmp/baseline.mp4\n'
                '    snake: videos/snake.mp4\n',
                encoding="utf-8",
            )
            profile = ParticipantProfile(path)
            self.assertEqual(
                profile.video_path("experiment_presentation"),
                root / "videos/presentation.mp4",
            )
            self.assertEqual(
                profile.video_path("mode_explanation", "baseline"),
                Path("/tmp/baseline.mp4"),
            )
            self.assertEqual(
                profile.video_path("mode_explanation", "snake"),
                root / "videos/snake.mp4",
            )

    def test_missing_or_invalid_slot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant_interface.yaml"
            path.write_text(
                'schema_version: 1\nvideos:\n'
                '  experiment_presentation: ""\n'
                '  mode_explanation:\n'
                '    baseline: ""\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ParticipantProfileError, "must be strings"):
                ParticipantProfile(path)
            path.write_text(
                'schema_version: 1\nvideos:\n'
                '  experiment_presentation: ""\n'
                '  mode_explanation:\n'
                '    baseline: ""\n'
                '    snake: ""\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ParticipantProfileError, "Unknown participant"):
                ParticipantProfile(path).video_path("mode_explanation", "automatic")
            path.write_text(
                'schema_version: 1\nvideos:\n'
                '  experiment_presentation: presentation.webm\n'
                '  mode_explanation:\n'
                '    baseline: ""\n'
                '    snake: ""\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ParticipantProfileError, "must be MP4"):
                ParticipantProfile(path)

    def test_only_existing_mp4_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mp4 = root / "presentation.mp4"
            webm = root / "presentation.webm"
            self.assertFalse(video_is_available(mp4))
            mp4.write_bytes(b"test video")
            webm.write_bytes(b"test video")
            self.assertTrue(video_is_available(mp4))
            self.assertFalse(video_is_available(webm))


if __name__ == "__main__":
    unittest.main()

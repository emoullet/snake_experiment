from pathlib import Path
import tempfile
import unittest

from snake_experiment_ui.participant_profile import (
    ParticipantProfile,
    ParticipantProfileError,
    state_image_is_available,
    video_is_available,
)


class ParticipantProfileTest(unittest.TestCase):
    def test_packaged_profile_has_five_png_slots(self):
        path = Path(__file__).parents[1] / "config/participant_interface.yaml"
        profile = ParticipantProfile(path)
        for mode, states in (("baseline", ("b1", "b2", "b3")), ("snake", ("b1", "b2"))):
            for state in states:
                with self.subTest(mode=mode, state=state):
                    self.assertIsNone(profile.state_image_path(mode, state))

    def test_state_images_are_optional_for_old_profiles_and_png_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "participant_interface.yaml"
            base = (
                'schema_version: 1\nvideos:\n'
                '  experiment_presentation: ""\n'
                '  mode_explanation:\n'
                '    baseline: ""\n'
                '    snake: ""\n'
            )
            path.write_text(base, encoding="utf-8")
            self.assertIsNone(ParticipantProfile(path).state_image_path("snake", "b2"))
            path.write_text(
                base + 'state_images:\n  baseline:\n    b1: pictures/b1.png\n'
                '    b2: ""\n    b3: ""\n  snake:\n    b1: ""\n    b2: ""\n',
                encoding="utf-8",
            )
            image = root / "pictures/b1.png"
            self.assertEqual(ParticipantProfile(path).state_image_path("baseline", "b1"), image)
            self.assertFalse(state_image_is_available(image))
            image.parent.mkdir()
            image.write_bytes(b"test png")
            self.assertTrue(state_image_is_available(image))
            with self.assertRaisesRegex(ParticipantProfileError, "Unknown participant state"):
                ParticipantProfile(path).state_image_path("snake", "b3")
            path.write_text(path.read_text().replace("pictures/b1.png", "pictures/b1.jpg"), encoding="utf-8")
            with self.assertRaisesRegex(ParticipantProfileError, "PNG paths"):
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

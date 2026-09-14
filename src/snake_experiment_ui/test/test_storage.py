import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from snake_experiment_ui.storage import CalibrationStorage


class CalibrationStorageTest(unittest.TestCase):
    def test_first_save_creates_latest_under_launch_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            launch_directory = Path(directory)
            storage = CalibrationStorage(launch_directory, "/ee_pose")

            saved = storage.save({"schema_version": 1, "poses": {}})

            self.assertEqual(
                saved,
                launch_directory / "calibrations" / "latest_calib.json",
            )
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            document = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(document["pose_topic"], "/ee_pose")
            self.assertTrue(document["calibration_id"].startswith("cal-"))
            self.assertFalse(storage.archive_directory.exists())
            self.assertFalse(list(storage.directory.glob("*.tmp")))

    def test_second_save_archives_previous_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = CalibrationStorage(directory, "/ee_pose")
            storage.save({"schema_version": 1, "marker": "first"})
            first_document = json.loads(
                storage.latest_path.read_text(encoding="utf-8")
            )

            saved = storage.save({"schema_version": 1, "marker": "second"})

            archives = list(storage.archive_directory.glob("archived-*.json"))
            self.assertEqual(saved, storage.latest_path)
            self.assertEqual(len(archives), 1)
            self.assertEqual(
                json.loads(archives[0].read_text(encoding="utf-8")),
                first_document,
            )
            self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)
            latest_document = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(latest_document["marker"], "second")
            self.assertNotEqual(
                latest_document["calibration_id"],
                first_document["calibration_id"],
            )

    def test_rapid_saves_produce_unique_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = CalibrationStorage(directory, "/ee_pose")

            for index in range(5):
                storage.save({"schema_version": 1, "marker": index})

            archives = list(storage.archive_directory.glob("archived-*.json"))
            self.assertEqual(len(archives), 4)
            self.assertEqual(len({path.name for path in archives}), 4)
            self.assertFalse(list(storage.directory.rglob("*.tmp")))

    def test_failed_final_replace_restores_previous_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = CalibrationStorage(directory, "/ee_pose")
            storage.save({"schema_version": 1, "marker": "first"})
            original = storage.latest_path.read_bytes()
            real_replace = os.replace

            def fail_new_latest(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    source_path.suffix == ".tmp"
                    and destination_path == storage.latest_path
                ):
                    raise OSError("simulated final replace failure")
                return real_replace(source, destination)

            with patch(
                "snake_experiment_ui.storage.os.replace",
                side_effect=fail_new_latest,
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated"):
                    storage.save({"schema_version": 1, "marker": "second"})

            self.assertEqual(storage.latest_path.read_bytes(), original)
            self.assertFalse(list(storage.archive_directory.glob("*.json")))
            self.assertFalse(list(storage.directory.rglob("*.tmp")))

    def test_working_directory_factory_captures_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            launch_directory = Path(directory)
            with patch(
                "snake_experiment_ui.storage.Path.cwd",
                return_value=launch_directory,
            ):
                storage = CalibrationStorage.from_working_directory("/pose")

            self.assertEqual(storage.launch_directory, launch_directory.resolve())
            self.assertEqual(
                storage.directory,
                launch_directory.resolve() / "calibrations",
            )


if __name__ == "__main__":
    unittest.main()

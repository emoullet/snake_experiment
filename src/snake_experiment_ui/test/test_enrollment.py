import csv
import json
from pathlib import Path
import tempfile
import unittest

from snake_experiment_ui.enrollment import (
    CSV_FIELDS,
    EnrollmentController,
    EnrollmentError,
    PLANS,
)


class FakeStack:
    def __init__(self):
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


class FakeModes:
    def __init__(self):
        self.active = None

    def activate(self, mode):
        self.active = mode

    def deactivate(self, mode):
        if self.active == mode:
            self.active = None

    def active_mode(self):
        return self.active


class EnrollmentControllerTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.experiment = self.sessions / "experiment"
        self.experiment.mkdir()
        self.calibration = self.root / "calibrations" / "latest_calib.json"
        self.calibration.parent.mkdir()
        self.calibration.write_text('{"schema_version": 1}\n', encoding="utf-8")
        self.bringup = self.root / "bringup"
        config = self.bringup / "joystick_mapper" / "config" / "profile.yaml"
        config.parent.mkdir(parents=True)
        config.write_text("profile: baseline\n", encoding="utf-8")
        ignored = self.bringup / "joystick_mapper" / "launch" / "mapper.launch.py"
        ignored.parent.mkdir(parents=True)
        ignored.write_text("ignored\n", encoding="utf-8")
        self.experiment_profile = self.root / "experiment.yaml"
        source_profile = Path(__file__).parents[1] / "config" / "experiment.yaml"
        self.experiment_profile.write_text(
            source_profile.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.stack = FakeStack()
        self.modes = FakeModes()
        self.controller = self.make_controller()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_controller(self, checkup_id="check-1"):
        return EnrollmentController(
            sessions_root=self.sessions,
            calibration_file=self.calibration,
            bringup_root=self.bringup,
            checkup_report_provider=lambda: {
                "schema_version": 1,
                "checkup_id": checkup_id,
                "status": "PASSED_WITH_WARNINGS",
            },
            provenance_provider=lambda: {"repositories": {".": {"commit": "abc", "dirty": False}}},
            stack_manager=self.stack,
            mode_manager=self.modes,
            experiment_profile=self.experiment_profile,
            utc_clock=lambda: "2026-09-15T12:34:56Z",
        )

    @staticmethod
    def form(pseudonym="A1B2C3"):
        return {
            "pseudonym": pseudonym,
            "gathered_consent": True,
            "handedness": "right",
            "joystick_experience": False,
            "visual_or_motor_impairment": False,
        }

    def test_selecting_parent_creates_exact_csv_header(self):
        state = self.controller.select_parent(str(self.experiment))
        self.assertEqual(state["workflow"], "parent_selected")
        with (self.experiment / "experiment_state.csv").open(newline="") as source:
            self.assertEqual(next(csv.reader(source)), CSV_FIELDS)

    def test_folder_browser_is_bounded_and_hides_outside_symlink(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.sessions / "escape").symlink_to(outside, target_is_directory=True)
        listing = self.controller.browse(str(self.sessions))
        self.assertNotIn("escape", [item["name"] for item in listing["directories"]])
        with self.assertRaisesRegex(EnrollmentError, "outside sessions_root"):
            self.controller.browse(str(outside))

    def test_folder_creation_is_bounded_and_browses_into_new_folder(self):
        listing = self.controller.create_folder(str(self.sessions), "Experiment 01")
        created = self.sessions / "Experiment 01"
        self.assertTrue(created.is_dir())
        self.assertEqual(listing["path"], str(created))
        self.assertEqual(listing["parent"], str(self.sessions))
        with self.assertRaisesRegex(EnrollmentError, "already exists"):
            self.controller.create_folder(str(self.sessions), "Experiment 01")
        for invalid in ("", ".hidden", "../escape", "nested/folder", "bad\\name"):
            with self.subTest(name=invalid):
                with self.assertRaisesRegex(EnrollmentError, "visible single name"):
                    self.controller.create_folder(str(self.sessions), invalid)

    def test_create_participant_persists_environment_and_partial_row(self):
        self.controller.select_parent(str(self.experiment))
        state = self.controller.create_participant(self.form())
        participant = state["participant"]
        self.assertIn(participant["experimental_plan"], PLANS)
        folder = self.experiment / "A1B2C3"
        self.assertTrue((folder / "experimental_environment/bringup/joystick_mapper/config/profile.yaml").is_file())
        self.assertFalse((folder / "experimental_environment/bringup/joystick_mapper/launch/mapper.launch.py").exists())
        self.assertTrue((folder / "calibration/latest_calib.json").is_file())
        self.assertTrue((folder / "checkups/check-1.json").is_file())
        self.assertTrue((folder / "experimental_environment/experiment.yaml").is_file())
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["active_checkup_report"], "checkups/check-1.json")
        with (self.experiment / "experiment_state.csv").open(newline="") as source:
            row = next(csv.DictReader(source))
        self.assertEqual(row["state"], "partial")
        self.assertEqual(row["starting_time"], "2026-09-15T12:34:56Z")

    def test_counterbalancing_uses_least_represented_plan(self):
        self.controller.select_parent(str(self.experiment))
        self.controller.create_participant(self.form("A1B2C3"))
        first_plan = self.controller.snapshot()["participant"]["experimental_plan"]
        second_controller = self.make_controller()
        second_controller.select_parent(str(self.experiment))
        second_controller.create_participant(self.form("D4E5F6"))
        second_plan = second_controller.snapshot()["participant"]["experimental_plan"]
        self.assertNotEqual(first_plan, second_plan)

    def test_cancel_deletes_only_a_new_participant(self):
        self.controller.select_parent(str(self.experiment))
        self.controller.create_participant(self.form())
        self.controller.cancel()
        self.assertFalse((self.experiment / "A1B2C3").exists())
        with (self.experiment / "experiment_state.csv").open(newline="") as source:
            self.assertEqual(list(csv.DictReader(source)), [])

    def test_resume_requires_environment_mismatch_acknowledgement(self):
        self.controller.select_parent(str(self.experiment))
        self.controller.create_participant(self.form())
        self.bringup.joinpath("joystick_mapper/config/profile.yaml").write_text(
            "profile: changed\n", encoding="utf-8"
        )
        resumed = self.make_controller("check-2")
        resumed.select_parent(str(self.experiment))
        with self.assertRaisesRegex(EnrollmentError, "Acknowledge"):
            resumed.resume_participant("A1B2C3")
        self.assertTrue(resumed.snapshot()["mismatches"])
        state = resumed.resume_participant("A1B2C3", acknowledge_mismatch=True)
        self.assertTrue(state["participant"]["resumed"])
        manifest = json.loads((self.experiment / "A1B2C3/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["checkup_reports"], ["checkups/check-1.json", "checkups/check-2.json"])
        self.assertEqual(manifest["active_checkup_report"], "checkups/check-2.json")
        resumed.cancel()
        self.assertTrue((self.experiment / "A1B2C3").is_dir())

    def test_launch_prepares_view_d_without_starting_ros_processes(self):
        self.controller.select_parent(str(self.experiment))
        self.controller.create_participant(self.form())
        state = self.controller.launch()
        self.assertFalse(self.stack.active)
        self.assertIsNone(self.modes.active)
        self.assertEqual(state["current_view"], "D")
        with self.assertRaisesRegex(EnrollmentError, "cannot be cancelled"):
            self.controller.cancel()

    def test_invalid_consent_and_csv_header_are_blocking(self):
        self.controller.select_parent(str(self.experiment))
        form = self.form()
        form["gathered_consent"] = False
        with self.assertRaisesRegex(EnrollmentError, "consent"):
            self.controller.create_participant(form)
        (self.experiment / "experiment_state.csv").write_text("wrong,header\n", encoding="utf-8")
        with self.assertRaisesRegex(EnrollmentError, "invalid header"):
            self.make_controller().select_parent(str(self.experiment))

    def test_pseudonym_is_unique_ascii_and_requires_selected_parent(self):
        with self.assertRaisesRegex(EnrollmentError, "Select"):
            self.controller.generate_pseudonym()
        self.controller.select_parent(str(self.experiment))
        generated = self.controller.generate_pseudonym()["pseudonym"]
        self.assertEqual(len(generated), 6)
        self.assertTrue(any(character.isalpha() for character in generated))
        self.assertTrue(any(character.isdigit() for character in generated))
        with self.assertRaisesRegex(EnrollmentError, "capital letters"):
            self.controller.create_participant(self.form("É1B2C3"))


if __name__ == "__main__":
    unittest.main()

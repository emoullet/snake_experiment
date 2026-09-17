import json
from pathlib import Path
import tempfile
import unittest

from snake_experiment_ui.experiment import (
    ExperimentController,
    ExperimentError,
    ExperimentProfile,
)


class FakeStack:
    def __init__(self):
        self.status = "inactive"
        self.start_count = 0
        self.stop_count = 0

    def start(self):
        self.start_count += 1
        self.status = "active"
        return self.snapshot()

    def stop(self):
        self.stop_count += 1
        self.status = "inactive"
        return self.snapshot()

    def shutdown(self):
        self.status = "inactive"

    def snapshot(self):
        return {"status": self.status, "error": None, "use_simulation": True}


class FakeModes:
    def __init__(self):
        self.active = None
        self.activations = []

    def activate(self, mode):
        self.active = mode
        self.activations.append(mode)
        return self.snapshot()

    def deactivate(self, mode):
        if self.active != mode:
            raise RuntimeError("wrong active mode")
        self.active = None
        return self.snapshot()

    def active_mode(self):
        return self.active

    def shutdown(self):
        self.active = None

    def snapshot(self):
        return {"status": "active" if self.active else "inactive", "active_mode": self.active, "error": None}


class ExperimentControllerTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.participant_folder = self.root / "A1B2C3"
        self.participant_folder.mkdir()
        (self.participant_folder / "manifest.json").write_text(
            json.dumps({"schema_version": 1, "pseudonym": "A1B2C3", "files": []}),
            encoding="utf-8",
        )
        self.source_profile = Path(__file__).parents[1] / "config" / "experiment.yaml"
        self.stack = FakeStack()
        self.modes = FakeModes()
        self.clock_tick = 0
        self.controller = ExperimentController(
            ExperimentProfile(self.source_profile),
            self.stack,
            self.modes,
            utc_clock=self.clock,
        )
        self.participant = {
            "pseudonym": "A1B2C3",
            "experimental_plan": "snake->baseline",
            "folder": str(self.participant_folder),
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    def clock(self):
        self.clock_tick += 1
        return f"2026-09-16T10:00:{self.clock_tick:02d}Z"

    def test_prepare_legacy_session_snapshots_profile_and_resolves_plan(self):
        state = self.controller.prepare(self.participant)
        self.assertEqual(state["current_panel"], "D")
        self.assertTrue(state["progress"]["late_initialization"])
        self.assertEqual(
            [block["mode"] for block in state["progress"]["blocks"]],
            ["snake", "baseline", "snake", "snake", "baseline", "baseline"],
        )
        self.assertTrue((self.participant_folder / "experimental_environment/experiment.yaml").is_file())
        manifest = json.loads((self.participant_folder / "manifest.json").read_text())
        self.assertTrue(manifest["late_initializations"])

    def test_sequence_is_strict_and_abort_resumes_same_folder(self):
        self.controller.prepare(self.participant)
        with self.assertRaisesRegex(ExperimentError, "next required"):
            self.controller.start("mode_2_discovery")
        state = self.controller.start("mode_1_discovery")
        self.assertEqual(state["current_panel"], "E")
        self.assertEqual(self.modes.active, "snake")
        folder = self.participant_folder / "snake_discovery"
        self.assertTrue((folder / "block.json").is_file())
        self.controller.abort("mode_1_discovery")
        state = self.controller.start("mode_1_discovery")
        self.assertEqual(state["progress"]["blocks"][0]["attempts"], 2)
        self.assertEqual(Path(state["participant"]["folder"]) / "snake_discovery", folder)

    def test_end_requires_confirmation_and_final_block_stops_stack(self):
        self.controller.prepare(self.participant)
        ids = [
            "mode_1_discovery",
            "mode_2_discovery",
            "mode_1_training",
            "mode_1_recording",
            "mode_2_training",
            "mode_2_recording",
        ]
        for block_id in ids:
            self.controller.start(block_id)
            with self.assertRaisesRegex(ExperimentError, "confirmation"):
                self.controller.end(block_id, False)
            state = self.controller.end(block_id, True)
        self.assertEqual(state["progress"]["workflow"], "sequence_completed")
        self.assertEqual(self.stack.status, "inactive")
        self.assertEqual(self.stack.stop_count, 1)

    def test_shutdown_marks_running_block_interrupted(self):
        self.controller.prepare(self.participant)
        self.controller.start("mode_1_discovery")
        self.controller.shutdown()
        progress = json.loads((self.participant_folder / "experiment_progress.json").read_text())
        self.assertEqual(progress["workflow"], "interrupted")
        self.assertEqual(progress["blocks"][0]["status"], "interrupted")
        self.assertIsNone(progress["current_block"])


if __name__ == "__main__":
    unittest.main()

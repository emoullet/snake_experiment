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
        self.fail_start = False

    def start(self):
        self.start_count += 1
        if self.fail_start:
            raise RuntimeError("stack startup failed")
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


class FakeRosbag:
    def __init__(self):
        self.active = False
        self.output = None
        self.topics = []
        self.starts = []
        self.valid = True

    def start(self, output, topics, storage="mcap"):
        self.active = True
        self.output = Path(output)
        self.topics = list(topics)
        self.starts.append(self.output.name)
        return self.snapshot()

    def stop(self):
        self.active = False
        return self._result()

    def shutdown(self):
        if not self.active:
            return None
        self.active = False
        return self._result()

    def _result(self):
        counts = {topic: 1 if self.valid else 0 for topic in self.topics}
        return {
            "output": str(self.output),
            "storage": "mcap",
            "message_counts": counts,
            "valid": self.valid,
            "missing_topics": [] if self.valid else list(self.topics),
            "metadata_error": None,
        }

    def snapshot(self):
        return {
            "status": "active" if self.active else "inactive",
            "error": None,
            "output": str(self.output) if self.output else None,
            "topics": self.topics,
            "storage": "mcap",
            "owned": self.active,
        }


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
        self.rosbag = FakeRosbag()
        self.clock_tick = 0
        self.controller = ExperimentController(
            ExperimentProfile(self.source_profile),
            self.stack,
            self.modes,
            self.rosbag,
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

    def test_profile_requires_mcap_and_standardised_instructions(self):
        profile = ExperimentProfile(self.source_profile)
        self.assertEqual(profile.data["rosbag"]["storage"], "mcap")
        self.assertTrue(
            profile.data["phases"]["discovery"]["instructions"]["placeholder"]
        )
        invalid = self.root / "invalid-experiment.yaml"
        invalid.write_text(
            self.source_profile.read_text(encoding="utf-8").replace(
                "storage: mcap", "storage: sqlite3"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ExperimentError, "storage must be 'mcap'"):
            ExperimentProfile(invalid)

    def test_sequence_is_strict_and_abort_resumes_same_folder(self):
        self.controller.prepare(self.participant)
        with self.assertRaisesRegex(ExperimentError, "next required"):
            self.controller.start("mode_2_discovery")
        state = self.controller.start("mode_1_discovery")
        self.assertEqual(state["current_panel"], "E")
        self.assertIsNone(self.modes.active)
        self.assertFalse(state["control_active"])
        folder = self.participant_folder / "snake_discovery"
        self.assertTrue((folder / "block.json").is_file())
        self.controller.abort("mode_1_discovery")
        self.assertEqual(self.stack.status, "inactive")
        self.assertEqual(self.stack.stop_count, 1)
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
            if "discovery" in block_id:
                self.controller.set_control(block_id, True)
                self.controller.set_control(block_id, False)
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

    def test_discovery_segments_are_numbered_and_gate_completion(self):
        self.controller.prepare(self.participant)
        self.controller.start("mode_1_discovery")
        with self.assertRaisesRegex(ExperimentError, "valid segment"):
            self.controller.end("mode_1_discovery", True)
        self.controller.set_control("mode_1_discovery", True)
        self.assertEqual(self.modes.active, "snake")
        self.controller.set_control("mode_1_discovery", False)
        self.controller.set_control("mode_1_discovery", True)
        state = self.controller.set_control("mode_1_discovery", False)
        self.assertEqual(self.rosbag.starts, ["rosbag_001", "rosbag_002"])
        self.assertTrue(state["can_end"])
        self.assertEqual(len(state["segments"]), 2)

    def test_incomplete_segment_does_not_unlock_end(self):
        self.controller.prepare(self.participant)
        self.controller.start("mode_1_discovery")
        self.rosbag.valid = False
        self.controller.set_control("mode_1_discovery", True)
        state = self.controller.set_control("mode_1_discovery", False)
        self.assertFalse(state["can_end"])
        self.assertEqual(state["segments"][0]["status"], "incomplete")

    def test_restart_restores_active_state_with_new_segment(self):
        self.controller.prepare(self.participant)
        self.controller.start("mode_1_discovery")
        self.controller.restart_stack("mode_1_discovery")
        self.assertFalse(self.controller.snapshot()["control_active"])
        self.controller.set_control("mode_1_discovery", True)
        state = self.controller.restart_stack("mode_1_discovery")
        self.assertTrue(state["control_active"])
        self.assertEqual(self.rosbag.starts, ["rosbag_001", "rosbag_002"])
        self.assertEqual(state["restart"]["status"], "complete")

    def test_restart_failure_keeps_current_block_recoverable(self):
        self.controller.prepare(self.participant)
        self.controller.start("mode_1_discovery")
        self.stack.fail_start = True
        with self.assertRaisesRegex(ExperimentError, "Unable to restart stack"):
            self.controller.restart_stack("mode_1_discovery")
        state = self.controller.snapshot()
        block = state["progress"]["blocks"][0]
        self.assertEqual(block["status"], "running")
        self.assertEqual(state["progress"]["current_block"], "mode_1_discovery")
        self.assertEqual(state["restart"]["status"], "error")

    def test_shutdown_closes_active_segment_before_interrupting_block(self):
        self.controller.prepare(self.participant)
        self.controller.start("mode_1_discovery")
        self.controller.set_control("mode_1_discovery", True)
        self.controller.shutdown()
        progress = json.loads(
            (self.participant_folder / "experiment_progress.json").read_text()
        )
        block = progress["blocks"][0]
        self.assertEqual(block["segments"][0]["stop_reason"], "interface_shutdown")
        self.assertEqual(block["status"], "interrupted")
        self.assertFalse(self.rosbag.active)


if __name__ == "__main__":
    unittest.main()

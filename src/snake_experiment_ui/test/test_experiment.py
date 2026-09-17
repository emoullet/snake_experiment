import json
import hashlib
from pathlib import Path
import tempfile
import unittest

import yaml

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


class ImmediateThread:
    def __init__(self, target, args=(), daemon=None):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


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
        calibration_folder = self.participant_folder / "calibration"
        calibration_folder.mkdir()
        self.calibration = {
            "schema_version": 1,
            "poses": {
                name: {
                    "frame_id": "base_link",
                    "position": {
                        "x": index * 0.1 + (0.02 if "out" in name else 0.0),
                        "y": 0.0,
                        "z": 0.4,
                    },
                    "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                }
                for index in (1, 2, 3)
                for name in (f"target_{index}", f"target_out_{index}")
            },
        }
        (calibration_folder / "latest_calib.json").write_text(
            json.dumps(self.calibration), encoding="utf-8"
        )
        self.source_profile = Path(__file__).parents[1] / "config" / "experiment.yaml"
        self.stack = FakeStack()
        self.modes = FakeModes()
        self.rosbag = FakeRosbag()
        self.clock_tick = 0
        self.monotonic_value = 10.0
        self.controller = ExperimentController(
            ExperimentProfile(self.source_profile),
            self.stack,
            self.modes,
            self.rosbag,
            utc_clock=self.clock,
            monotonic_clock=lambda: self.monotonic_value,
            thread_factory=ImmediateThread,
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

    def pose(self, name):
        return self.calibration["poses"][name]

    def complete_discovery(self, block_id):
        self.controller.start(block_id)
        self.controller.set_control(block_id, True)
        self.controller.set_control(block_id, False)
        self.controller.end(block_id, True)

    def complete_training(self, block_id):
        for _ in range(6):
            state = self.controller.prepare_training_trial(block_id)
            trial = next(
                trial
                for trial in state["training"]["trials"]
                if trial["id"] == state["training"]["current_trial_id"]
            )
            self.controller.update_ee_pose(
                self.pose(f"target_out_{trial['target_start']}")
            )
            self.controller.training_participant_ready(block_id)
            self.controller.start_training_attempt(block_id)
            self.controller.update_ee_pose(self.pose(f"target_{trial['target_end']}"))
            self.monotonic_value += 0.6
            self.controller.update_ee_pose(self.pose(f"target_{trial['target_end']}"))

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
            if "training" in block_id:
                self.complete_training(block_id)
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

    def test_training_generates_six_global_trials_and_gates_start_pose(self):
        self.controller.prepare(self.participant)
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        state = self.controller.start("mode_1_training")
        trials = state["training"]["trials"]
        self.assertEqual([trial["id"] for trial in trials], list(range(1, 7)))
        self.assertEqual(
            trials[0]["folder"], "snake_training_trial_001_01_1_2"
        )
        self.controller.prepare_training_trial("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_3"))
        with self.assertRaisesRegex(ExperimentError, "calibrated start pose"):
            self.controller.training_participant_ready("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        state = self.controller.training_participant_ready("mode_1_training")
        self.assertEqual(state["training"]["workflow"], "ready")

    def test_training_success_stops_recorder_after_stable_pose(self):
        self.controller.prepare(self.participant)
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.controller.training_participant_ready("mode_1_training")
        self.controller.start_training_attempt("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_2"))
        recording_state = self.controller.snapshot()
        self.assertTrue(self.rosbag.active)
        self.assertEqual(recording_state["training"]["current_trial"]["cycle"], 1)
        self.assertEqual(recording_state["training"]["current_attempt"]["number"], 1)
        self.assertEqual(recording_state["training"]["progress"], {"resolved": 0, "total": 6})
        self.assertEqual(recording_state["training"]["stability"]["duration_sec"], 0.0)
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose("target_2"))
        state = self.controller.snapshot()
        self.assertFalse(self.rosbag.active)
        self.assertEqual(state["training"]["trials"][0]["status"], "completed")
        self.assertEqual(self.rosbag.starts[-1], "attempt_001")
        trial_folder = self.participant_folder / "snake_training" / (
            "snake_training_trial_001_01_1_2"
        )
        self.assertTrue((trial_folder / "trial.json").is_file())
        self.assertTrue((trial_folder / "attempt_001" / "attempt.json").is_file())

    def test_training_stale_pose_blocks_readiness_and_recording(self):
        self.controller.prepare(self.participant)
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.monotonic_value += 0.6
        with self.assertRaisesRegex(ExperimentError, "calibrated start pose"):
            self.controller.training_participant_ready("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.controller.training_participant_ready("mode_1_training")
        self.monotonic_value += 0.6
        with self.assertRaisesRegex(ExperimentError, "left the calibrated start pose"):
            self.controller.start_training_attempt("mode_1_training")

    def test_training_invalid_mcap_requires_operator_resolution(self):
        self.controller.prepare(self.participant)
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.controller.training_participant_ready("mode_1_training")
        self.controller.start_training_attempt("mode_1_training")
        self.rosbag.valid = False
        self.controller.update_ee_pose(self.pose("target_2"))
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose("target_2"))
        state = self.controller.snapshot()
        attempt = state["training"]["trials"][0]["attempts"][0]
        self.assertEqual(state["training"]["workflow"], "decision_required")
        self.assertEqual(attempt["stop_reason"], "invalid_data")
        self.assertFalse(attempt["valid"])
        self.assertEqual(set(attempt["missing_topics"]), set(attempt["topics"]))

    def test_shutdown_invalidates_active_training_attempt(self):
        self.controller.prepare(self.participant)
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.controller.training_participant_ready("mode_1_training")
        self.controller.start_training_attempt("mode_1_training")
        self.controller.shutdown()
        progress = json.loads(
            (self.participant_folder / "experiment_progress.json").read_text()
        )
        block = progress["blocks"][2]
        attempt = block["training_trials"][0]["attempts"][0]
        self.assertEqual(block["status"], "interrupted")
        self.assertEqual(attempt["status"], "invalid")
        self.assertEqual(attempt["stop_reason"], "interface_shutdown")
        self.assertFalse(self.rosbag.active)

    def test_lot6_fields_are_migrated_from_legacy_profile(self):
        self.controller.prepare(self.participant)
        profile_path = (
            self.participant_folder / "experimental_environment" / "experiment.yaml"
        )
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        training = profile["phases"]["training"]
        training.pop("trial_folder_pattern")
        training.pop("attempt_pattern")
        profile["success_thresholds"] = {"linear_m": None, "angular_rad": None}
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        progress_path = self.participant_folder / "experiment_progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        progress["profile"]["sha256"] = digest
        training_block = progress["blocks"][2]
        for key in (
            "training_workflow",
            "training_trials",
            "current_trial_id",
            "training_deviations",
            "training_live",
        ):
            training_block.pop(key, None)
        training_block["settings"].pop("trial_folder_pattern")
        training_block["settings"].pop("attempt_pattern")
        training_block["success_thresholds"] = {
            "linear_m": None,
            "angular_rad": None,
        }
        progress_path.write_text(json.dumps(progress), encoding="utf-8")

        migrated = ExperimentController(
            ExperimentProfile(self.source_profile),
            self.stack,
            self.modes,
            self.rosbag,
            utc_clock=self.clock,
            monotonic_clock=lambda: self.monotonic_value,
            thread_factory=ImmediateThread,
        ).prepare(self.participant)
        block = migrated["progress"]["blocks"][2]
        self.assertEqual(len(block["training_trials"]), 6)
        self.assertEqual(block["success_thresholds"]["linear_mm"], 5.0)
        self.assertTrue(
            any("LOT 6 training fields" in warning for warning in migrated["progress"]["warnings"])
        )

    def test_manual_stop_incident_retry_and_deviation(self):
        self.controller.prepare(self.participant)
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.controller.training_participant_ready("mode_1_training")
        self.controller.start_training_attempt("mode_1_training")
        self.controller.add_training_incident("mode_1_training", "Joystick slipped")
        state = self.controller.stop_training_attempt("mode_1_training")
        self.assertEqual(state["training"]["workflow"], "decision_required")
        attempt = state["training"]["trials"][0]["attempts"][0]
        self.assertEqual(attempt["incidents"][0]["text"], "Joystick slipped")
        self.controller.resolve_training_attempt("mode_1_training", "retry")
        self.controller.prepare_training_trial("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.controller.training_participant_ready("mode_1_training")
        self.controller.start_training_attempt("mode_1_training")
        self.controller.stop_training_attempt("mode_1_training")
        state = self.controller.resolve_training_attempt(
            "mode_1_training", "advance_with_deviation"
        )
        self.assertEqual(
            state["training"]["trials"][0]["status"],
            "completed_with_deviation",
        )
        self.assertEqual(state["training"]["deviations"], [1])

    def test_restart_during_training_invalidates_attempt(self):
        self.controller.prepare(self.participant)
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.controller.training_participant_ready("mode_1_training")
        self.controller.start_training_attempt("mode_1_training")
        state = self.controller.restart_stack("mode_1_training")
        self.assertEqual(state["training"]["workflow"], "decision_required")
        self.assertEqual(
            state["training"]["trials"][0]["attempts"][0]["stop_reason"],
            "restart_stack",
        )
        self.assertEqual(self.modes.active, "snake")


if __name__ == "__main__":
    unittest.main()

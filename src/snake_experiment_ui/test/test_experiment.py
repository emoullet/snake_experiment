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
from snake_experiment_ui.go_to import GoToController


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
            "robot": {"id": "explorer", "ee_frame": "ft_frame"},
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
        self.calibration["poses"]["starting_point"] = {
            "frame_id": "base_link",
            "position": {"x": 0.0, "y": 0.0, "z": 0.4},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
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
        self.snake_held = None
        self.pose_targets = []
        self.passthrough_requests = 0
        self.go_to = GoToController(
            publish_target=self.pose_targets.append,
            publish_passthrough=self.record_passthrough,
            monotonic_clock=lambda: self.monotonic_value,
            utc_clock=self.clock,
        )
        self.controller = ExperimentController(
            ExperimentProfile(self.source_profile),
            self.stack,
            self.modes,
            self.rosbag,
            utc_clock=self.clock,
            monotonic_clock=lambda: self.monotonic_value,
            thread_factory=ImmediateThread,
            go_to_controller=self.go_to,
            snake_button_provider=lambda: self.snake_held,
        )
        self.participant = {
            "pseudonym": "A1B2C3",
            "experimental_plan": "snake->baseline",
            "folder": str(self.participant_folder),
        }

    def test_experiment_rejects_calibration_with_other_or_unknown_robot(self):
        path = self.participant_folder / "calibration/latest_calib.json"
        for robot in [None, {"id": "kinova", "ee_frame": "end_effector_link"}]:
            document = dict(self.calibration, robot=robot)
            path.write_text(json.dumps(document))
            with self.assertRaises(ExperimentError):
                self.controller.prepare(self.participant)
            self.assertEqual(self.stack.start_count, 0)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def clock(self):
        self.clock_tick += 1
        return f"2026-09-16T10:00:{self.clock_tick:02d}Z"

    def record_passthrough(self):
        self.passthrough_requests += 1

    def pose(self, name):
        return self.calibration["poses"][name]

    def prepare_session(self):
        state = self.controller.prepare(self.participant)
        if state["progress"]["presentation"]["status"] == "pending":
            self.controller.show_presentation()
            return self.controller.complete_presentation()
        return state

    def mark_as_new_session(self):
        profile_folder = self.participant_folder / "experimental_environment"
        profile_folder.mkdir(exist_ok=True)
        (profile_folder / "experiment.yaml").write_bytes(self.source_profile.read_bytes())
        manifest_path = self.participant_folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = [{"path": "experimental_environment/experiment.yaml"}]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def complete_go_to(self, pose_id):
        """Hold a pose long enough for the synthetic Go-to to complete."""
        self.controller.update_ee_pose(self.pose(pose_id))
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose(pose_id))

    def complete_discovery(self, block_id):
        self.controller.start(block_id)
        self.set_control(block_id, True)
        self.set_control(block_id, False)
        self.controller.end(block_id, True)

    def set_control(self, block_id, active):
        if active:
            block = next(
                item for item in self.controller.snapshot()["progress"]["blocks"]
                if item["id"] == block_id
            )
            if block["mode_explanation"]["status"] == "pending":
                self.controller.show_mode_explanation(block_id)
                self.controller.complete_mode_explanation(block_id)
        return self.controller.set_control(block_id, active)

    def complete_training(self, block_id):
        self.complete_trials(block_id, "training")

    def complete_recording(self, block_id):
        self.complete_trials(block_id, "recording")

    def complete_trials(self, block_id, phase):
        prepare = getattr(self.controller, f"prepare_{phase}_trial")
        start = getattr(self.controller, f"start_{phase}_attempt")
        state_key = phase
        count = len(self.controller.snapshot()[state_key]["trials"])
        for _ in range(count):
            state = prepare(block_id)
            trial = next(
                trial
                for trial in state[state_key]["trials"]
                if trial["id"] == state[state_key]["current_trial_id"]
            )
            self.complete_go_to(f"target_out_{trial['target_start']}")
            start(block_id)
            self.controller.update_ee_pose(self.pose(f"target_{trial['target_end']}"))
            self.monotonic_value += 0.6
            self.controller.update_ee_pose(self.pose(f"target_{trial['target_end']}"))

    def start_first_recording_block(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.complete_training("mode_1_training")
        self.controller.end("mode_1_training", True)
        return self.controller.start("mode_1_recording")

    def test_prepare_legacy_session_snapshots_profile_and_resolves_plan(self):
        state = self.prepare_session()
        self.assertEqual(state["current_view"], "D")
        self.assertTrue(state["progress"]["late_initialization"])
        self.assertEqual(
            [block["mode"] for block in state["progress"]["blocks"]],
            ["snake", "baseline", "snake", "snake", "baseline", "baseline"],
        )
        self.assertTrue((self.participant_folder / "experimental_environment/experiment.yaml").is_file())
        manifest = json.loads((self.participant_folder / "manifest.json").read_text())
        self.assertTrue(manifest["late_initializations"])

    def test_presentation_gates_new_session_and_persists_missing_video(self):
        self.mark_as_new_session()
        state = self.controller.prepare(self.participant)
        self.assertEqual(state["progress"]["presentation"]["status"], "pending")
        self.assertEqual(self.controller.participant_snapshot()["view"], "waiting")
        with self.assertRaisesRegex(ExperimentError, "Display the experiment presentation"):
            self.controller.complete_presentation()
        with self.assertRaisesRegex(ExperimentError, "Validate the experiment presentation"):
            self.controller.start("mode_1_discovery")
        self.assertEqual(self.stack.start_count, 0)
        self.controller.show_presentation()
        public = self.controller.participant_snapshot()
        self.assertEqual(public["view"], "A")
        self.assertNotIn("pseudonym", public)
        self.assertNotIn("folder", public)
        self.assertNotIn("axes", public)
        self.assertNotIn("buttons", public)
        state = self.controller.complete_presentation()
        self.assertTrue(state["progress"]["presentation"]["video_missing"])
        self.assertEqual(self.stack.start_count, 0)
        self.assertIn("video was unavailable", " ".join(state["progress"]["warnings"]))
        self.controller.start("mode_1_discovery")

    def test_configured_presentation_video_survives_prepare_and_resume(self):
        self.mark_as_new_session()
        video = self.root / "experiment.mp4"
        video.write_bytes(b"placeholder-test-video")
        controller = ExperimentController(
            ExperimentProfile(self.source_profile), self.stack, self.modes,
            self.rosbag, utc_clock=self.clock, presentation_video=video,
        )
        controller.prepare(self.participant)
        self.assertTrue(controller.participant_snapshot()["video_available"])
        controller.show_presentation()
        controller.complete_presentation()
        self.assertFalse(controller.snapshot()["progress"]["presentation"]["video_missing"])
        resumed = ExperimentController(
            ExperimentProfile(self.source_profile), self.stack, self.modes,
            self.rosbag, utc_clock=self.clock, presentation_video=video,
        )
        resumed.prepare(self.participant)
        self.assertEqual(resumed.participant_snapshot()["view"], "A")
        self.assertIsNone(resumed.participant_snapshot()["video_play_request_id"])
        self.assertEqual(resumed.snapshot()["progress"]["presentation"]["status"], "completed")

    def test_display_requests_replay_all_videos_without_changing_validation_or_processes(self):
        self.mark_as_new_session()
        self.controller.prepare(self.participant)
        progress_path = self.participant_folder / "experiment_progress.json"
        self.controller.show_presentation()
        first = self.controller.participant_snapshot()["video_play_request_id"]
        self.assertTrue(first)
        persisted = progress_path.read_bytes()
        self.controller.show_presentation()
        self.assertNotEqual(self.controller.participant_snapshot()["video_play_request_id"], first)
        self.assertEqual(progress_path.read_bytes(), persisted)
        self.controller.complete_presentation()
        persisted = progress_path.read_bytes()
        self.controller.show_presentation()
        self.assertEqual(progress_path.read_bytes(), persisted)
        self.assertEqual(self.controller.snapshot()["progress"]["presentation"]["status"], "completed")

        for block_id, mode in (("mode_1_discovery", "snake"), ("mode_2_discovery", "baseline")):
            self.controller.start(block_id)
            self.assertIsNone(self.controller.participant_snapshot()["video_play_request_id"])
            self.controller.show_mode_explanation(block_id)
            first = self.controller.participant_snapshot()["video_play_request_id"]
            self.assertEqual(self.controller.participant_snapshot()["mode"], mode)
            self.assertEqual(self.controller.participant_snapshot()["video_play_request_id"], first)
            self.controller.show_mode_explanation(block_id)
            self.assertNotEqual(self.controller.participant_snapshot()["video_play_request_id"], first)
            self.controller.complete_mode_explanation(block_id)
            self.controller.set_control(block_id, True)
            persisted = progress_path.read_bytes()
            starts, activations = len(self.rosbag.starts), list(self.modes.activations)
            self.controller.show_mode_explanation(block_id)
            self.assertEqual(progress_path.read_bytes(), persisted)
            self.assertEqual((len(self.rosbag.starts), self.modes.activations), (starts, activations))
            self.assertTrue(self.controller.snapshot()["control_active"])
            self.controller.set_control(block_id, False)
            self.controller.end(block_id, True)
            self.assertEqual(self.controller.participant_snapshot()["view"], "waiting")
            self.assertIsNone(self.controller.participant_snapshot()["video_play_request_id"])
            self.controller.show_presentation()
            self.assertEqual(self.controller.participant_snapshot()["view"], "A")
        self.assertNotIn("video_play_request", progress_path.read_text())

    def test_aborted_explanation_does_not_replay_automatically_on_resume(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.controller.show_mode_explanation("mode_1_discovery")
        self.assertTrue(self.controller.participant_snapshot()["video_play_request_id"])
        self.controller.abort("mode_1_discovery")
        self.controller.start("mode_1_discovery")
        self.assertEqual(self.controller.participant_snapshot()["view"], "B")
        self.assertIsNone(self.controller.participant_snapshot()["video_play_request_id"])

    def test_mode_explanation_gates_discovery_and_persists_across_resume(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        with self.assertRaisesRegex(ExperimentError, "Show the mode explanation"):
            self.controller.complete_mode_explanation("mode_1_discovery")
        with self.assertRaisesRegex(ExperimentError, "mode explanation"):
            self.controller.set_control("mode_1_discovery", True)
        with self.assertRaisesRegex(ExperimentError, "mode explanation"):
            self.controller.end("mode_1_discovery", True)
        self.assertEqual(self.controller.participant_snapshot()["view"], "waiting")
        self.controller.show_mode_explanation("mode_1_discovery")
        public = self.controller.participant_snapshot()
        self.assertEqual((public["view"], public["mode"]), ("B", "snake"))
        self.assertNotIn("pseudonym", public)
        self.assertNotIn("folder", public)
        state = self.controller.complete_mode_explanation("mode_1_discovery")
        explanation = state["progress"]["blocks"][0]["mode_explanation"]
        self.assertEqual(explanation["status"], "completed")
        self.assertTrue(explanation["shown_at_utc"])
        self.assertTrue(explanation["completed_at_utc"])
        self.assertTrue(explanation["video_missing"])
        self.assertEqual(self.controller.participant_snapshot()["view"], "B")
        self.assertEqual(self.controller.participant_snapshot()["phase"], "discovery")
        self.controller.record_mapper_local_mode("b2")
        self.assertIsNone(self.controller.participant_snapshot()["local_mode"])
        self.controller.set_control("mode_1_discovery", True)
        self.snake_held = True
        public = self.controller.participant_snapshot()
        self.assertEqual(public["local_mode"], "b2")
        self.assertTrue(public["snake_button_held"])
        self.controller.set_control("mode_1_discovery", False)
        self.assertIsNone(self.controller.participant_snapshot()["local_mode"])
        self.controller.abort("mode_1_discovery")
        self.assertEqual(self.controller.participant_snapshot()["view"], "waiting")
        resumed = ExperimentController(
            ExperimentProfile(self.source_profile), self.stack, self.modes,
            self.rosbag, utc_clock=self.clock,
        )
        resumed.prepare(self.participant)
        resumed.start("mode_1_discovery")
        self.assertEqual(resumed.participant_snapshot()["view"], "B")
        resumed.set_control("mode_1_discovery", True)
        resumed.set_control("mode_1_discovery", False)
        resumed.end("mode_1_discovery", True)
        self.assertEqual(resumed.participant_snapshot()["view"], "waiting")
        resumed.start("mode_2_discovery")
        self.assertEqual(resumed.participant_snapshot()["view"], "waiting")
        with self.assertRaisesRegex(ExperimentError, "mode explanation"):
            resumed.set_control("mode_2_discovery", True)
        resumed.show_mode_explanation("mode_2_discovery")
        self.assertEqual(resumed.participant_snapshot()["mode"], "baseline")
        resumed.complete_mode_explanation("mode_2_discovery")
        resumed.set_control("mode_2_discovery", True)
        resumed.record_mapper_local_mode("b3")
        self.assertEqual(resumed.participant_snapshot()["local_mode"], "b3")

    def test_mode_explanation_uses_configured_video_per_mode(self):
        videos = {}
        for mode in ("snake", "baseline"):
            path = self.root / f"{mode}.mp4"
            path.write_bytes(b"test video")
            videos[mode] = path
        controller = ExperimentController(
            ExperimentProfile(self.source_profile), self.stack, self.modes,
            self.rosbag, utc_clock=self.clock, mode_explanation_videos=videos,
        )
        controller.prepare(self.participant)
        controller.start("mode_1_discovery")
        controller.show_mode_explanation("mode_1_discovery")
        self.assertTrue(controller.participant_snapshot()["video_available"])
        self.assertTrue(controller.snapshot()["mode_explanation_video_available"])
        controller.complete_mode_explanation("mode_1_discovery")
        self.assertFalse(controller.snapshot()["progress"]["blocks"][0]["mode_explanation"]["video_missing"])
        controller.set_control("mode_1_discovery", True)
        controller.set_control("mode_1_discovery", False)
        controller.end("mode_1_discovery", True)
        controller.start("mode_2_discovery")
        controller.show_mode_explanation("mode_2_discovery")
        public = controller.participant_snapshot()
        self.assertEqual((public["view"], public["mode"]), ("B", "baseline"))
        self.assertTrue(public["video_available"])
        controller.complete_mode_explanation("mode_2_discovery")
        self.assertFalse(controller.snapshot()["progress"]["blocks"][1]["mode_explanation"]["video_missing"])

    def test_existing_progress_without_presentation_is_not_blocked(self):
        self.controller.prepare(self.participant)
        path = self.participant_folder / "experiment_progress.json"
        progress = json.loads(path.read_text(encoding="utf-8"))
        del progress["presentation"]
        path.write_text(json.dumps(progress), encoding="utf-8")
        state = self.controller.prepare(self.participant)
        self.assertEqual(state["progress"]["presentation"]["status"], "legacy_skipped")
        self.controller.start("mode_1_discovery")

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

        configurable = self.root / "configurable-experiment.yaml"
        configurable_data = yaml.safe_load(
            self.source_profile.read_text(encoding="utf-8")
        )
        configurable_data["phases"]["recording"]["cycles"] = 3
        configurable.write_text(
            yaml.safe_dump(configurable_data), encoding="utf-8"
        )
        profile = ExperimentProfile(configurable)
        recording_block = profile.blocks("snake->baseline")[3]
        self.assertEqual(len(recording_block["training_trials"]), 9)

    def test_discovery_bootstrap_starts_stack_without_control_or_recording(self):
        self.mark_as_new_session()
        self.controller.prepare(self.participant)
        self.controller.show_presentation()
        self.controller.complete_presentation()
        self.controller._command_allowed = lambda: False
        self.assertEqual(self.stack.status, "inactive")
        state = self.controller.start("mode_1_discovery")
        self.assertEqual(state["current_view"], "E")
        self.assertEqual(self.stack.status, "active")
        self.assertFalse(state["control_active"])
        self.assertEqual(self.modes.activations, [])
        self.assertEqual(self.rosbag.starts, [])
        self.controller.show_mode_explanation("mode_1_discovery")
        self.controller.complete_mode_explanation("mode_1_discovery")
        with self.assertRaises(ExperimentError):
            self.controller.set_control("mode_1_discovery", True)

    def test_sequence_is_strict_and_abort_resumes_same_folder(self):
        self.prepare_session()
        with self.assertRaisesRegex(ExperimentError, "next required"):
            self.controller.start("mode_2_discovery")
        state = self.controller.start("mode_1_discovery")
        self.assertEqual(state["current_view"], "E")
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
        self.prepare_session()
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
                self.controller.show_mode_explanation(block_id)
                self.controller.complete_mode_explanation(block_id)
                self.set_control(block_id, True)
                self.set_control(block_id, False)
            if "training" in block_id:
                self.complete_training(block_id)
            if "recording" in block_id:
                self.complete_recording(block_id)
            state = self.controller.end(block_id, True)
        self.assertEqual(state["progress"]["workflow"], "sequence_completed")
        self.assertEqual(self.stack.status, "inactive")
        self.assertEqual(self.stack.stop_count, 1)

    def test_shutdown_marks_running_block_interrupted(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.controller.shutdown()
        progress = json.loads((self.participant_folder / "experiment_progress.json").read_text())
        self.assertEqual(progress["workflow"], "interrupted")
        self.assertEqual(progress["blocks"][0]["status"], "interrupted")
        self.assertIsNone(progress["current_block"])

    def test_discovery_segments_are_numbered_and_gate_completion(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.controller.show_mode_explanation("mode_1_discovery")
        self.controller.complete_mode_explanation("mode_1_discovery")
        with self.assertRaisesRegex(ExperimentError, "valid segment"):
            self.controller.end("mode_1_discovery", True)
        self.set_control("mode_1_discovery", True)
        self.assertEqual(self.modes.active, "snake")
        self.set_control("mode_1_discovery", False)
        self.set_control("mode_1_discovery", True)
        state = self.set_control("mode_1_discovery", False)
        self.assertEqual(self.rosbag.starts, ["rosbag_001", "rosbag_002"])
        self.assertTrue(state["can_end"])
        self.assertEqual(len(state["segments"]), 2)

    def test_incomplete_segment_does_not_unlock_end(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.rosbag.valid = False
        self.set_control("mode_1_discovery", True)
        state = self.set_control("mode_1_discovery", False)
        self.assertFalse(state["can_end"])
        self.assertEqual(state["segments"][0]["status"], "incomplete")

    def test_restart_restores_active_state_with_new_segment(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.controller.restart_stack("mode_1_discovery")
        self.assertFalse(self.controller.snapshot()["control_active"])
        self.set_control("mode_1_discovery", True)
        state = self.controller.restart_stack("mode_1_discovery")
        self.assertTrue(state["control_active"])
        self.assertEqual(self.rosbag.starts, ["rosbag_001", "rosbag_002"])
        self.assertEqual(state["restart"]["status"], "complete")

    def test_restart_failure_keeps_current_block_recoverable(self):
        self.prepare_session()
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
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.set_control("mode_1_discovery", True)
        self.controller.shutdown()
        progress = json.loads(
            (self.participant_folder / "experiment_progress.json").read_text()
        )
        block = progress["blocks"][0]
        self.assertEqual(block["segments"][0]["stop_reason"], "interface_shutdown")
        self.assertEqual(block["status"], "interrupted")
        self.assertFalse(self.rosbag.active)

    def test_training_generates_six_global_trials_and_gates_start_pose(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        state = self.controller.start("mode_1_training")
        trials = state["training"]["trials"]
        self.assertEqual([trial["id"] for trial in trials], list(range(1, 7)))
        self.assertEqual(
            trials[0]["folder"], "snake_training_trial_001_01_1_2"
        )
        state = self.controller.prepare_training_trial("mode_1_training")
        self.assertTrue(state["go_to"]["motion"]["active"])
        self.assertEqual(
            state["go_to"]["motion"]["current"]["pose_id"],
            "target_out_1",
        )
        self.assertIsNone(self.modes.active)
        with self.assertRaisesRegex(ExperimentError, "active Go-to"):
            self.controller.start_training_attempt("mode_1_training")
        self.complete_go_to("target_out_1")
        self.controller.update_ee_pose(self.pose("target_3"))
        with self.assertRaisesRegex(ExperimentError, "left the calibrated start pose"):
            self.controller.start_training_attempt("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        state = self.controller.start_training_attempt("mode_1_training")
        self.assertEqual(state["training"]["workflow"], "recording")
        self.assertIsNotNone(state["training"]["current_trial"]["ready_at_utc"])
        self.controller.stop_training_attempt("mode_1_training")

    def test_participant_view_c_shows_target_only_during_recording(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        public = self.controller.participant_snapshot()
        self.assertEqual((public["view"], public["mode"], public["phase"]),
                         ("C", "snake", "training"))
        self.assertEqual(public["state"], "Waiting")
        self.assertIsNone(public["success_event_id"])
        self.assertIsNone(public["linear_mm"])
        self.assertNotIn("pseudonym", public)
        self.assertNotIn("folder", public)
        self.controller.record_mapper_local_mode("b1")
        self.assertEqual(self.controller.participant_snapshot()["local_mode"], "b1")
        self.assertIsNone(self.controller.participant_snapshot()["snake_button_held"])
        self.snake_held = True
        self.assertIs(self.controller.participant_snapshot()["snake_button_held"], True)
        self.snake_held = False
        self.assertIs(self.controller.participant_snapshot()["snake_button_held"], False)
        self.controller.record_mapper_local_mode("b3")
        self.assertIsNone(self.controller.participant_snapshot()["local_mode"])
        self.controller.prepare_training_trial("mode_1_training")
        public = self.controller.participant_snapshot()
        self.assertEqual(public["task"], "Preparing trial")
        self.assertNotIn("target 2", str(public).lower())
        self.complete_go_to("target_out_1")
        self.controller.start_training_attempt("mode_1_training")
        target = json.loads(json.dumps(self.pose("target_2")))
        target["position"]["x"] += 0.02
        self.controller.update_ee_pose(target)
        public = self.controller.participant_snapshot()
        self.assertEqual(public["task"], "Go to target 2")
        self.assertEqual(public["state"], "Recording")
        self.assertAlmostEqual(public["linear_mm"], 20.0)
        self.assertAlmostEqual(public["angular_deg"], 0.0)
        self.monotonic_value += 0.6
        self.assertIsNone(self.controller.participant_snapshot()["linear_mm"])
        self.controller.update_ee_pose(self.pose("target_2"))
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose("target_2"))
        self.assertEqual(self.controller.participant_snapshot()["state"], "Target reached")
        self.assertEqual(
            self.controller.participant_snapshot()["success_event_id"],
            "mode_1_training:1",
        )
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_2")
        self.controller.start_training_attempt("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_3"))
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose("target_3"))
        self.assertEqual(
            self.controller.participant_snapshot()["success_event_id"],
            "mode_1_training:2",
        )
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_3")
        self.controller.start_training_attempt("mode_1_training")
        self.controller.stop_training_attempt("mode_1_training")
        self.controller.resolve_training_attempt(
            "mode_1_training", "advance_with_deviation"
        )
        public = self.controller.participant_snapshot()
        self.assertEqual(public["state"], "Waiting")
        self.assertEqual(public["success_event_id"], "mode_1_training:2")
        self.controller.abort("mode_1_training")
        self.assertEqual(self.controller.participant_snapshot()["view"], "waiting")
        self.controller.start("mode_1_training")
        self.assertEqual(self.controller.participant_snapshot()["view"], "C")

    def test_participant_view_c_works_for_recording_and_baseline_b3(self):
        self.participant["experimental_plan"] = "baseline->snake"
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.record_mapper_local_mode("b3")
        self.assertEqual(self.controller.participant_snapshot()["local_mode"], "b3")
        self.assertIsNone(self.controller.participant_snapshot()["snake_button_held"])
        self.controller.clear_mapper_local_mode()
        self.assertIsNone(self.controller.participant_snapshot()["local_mode"])
        self.complete_training("mode_1_training")
        self.controller.end("mode_1_training", True)
        self.controller.start("mode_1_recording")
        public = self.controller.participant_snapshot()
        self.assertEqual((public["view"], public["mode"], public["phase"]),
                         ("C", "baseline", "recording"))
        self.controller.abort("mode_1_recording")
        self.assertEqual(self.controller.participant_snapshot()["view"], "waiting")

    def test_participant_local_mode_follows_mapper_messages_and_reset(self):
        self.participant["experimental_plan"] = "baseline->snake"
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")

        self.assertIsNone(self.controller.participant_snapshot()["local_mode"])
        for state in ("b1", "b2", "b3"):
            self.controller.try_record_mapper_local_mode(state)
            self.assertEqual(self.controller.participant_snapshot()["local_mode"], state)

        self.modes.active = None
        self.assertIsNone(self.controller.participant_snapshot()["local_mode"])
        self.controller.clear_mapper_local_mode()
        self.modes.active = "baseline"
        self.assertIsNone(self.controller.participant_snapshot()["local_mode"])
        self.controller.try_record_mapper_local_mode("b1")
        self.assertEqual(self.controller.participant_snapshot()["local_mode"], "b1")

    def test_training_go_to_suspends_and_restores_mapper(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        state = self.controller.start_go_to(
            "mode_1_training", "target_out_1"
        )
        self.assertTrue(state["go_to"]["motion"]["active"])
        self.assertIsNone(self.modes.active)
        with self.assertRaisesRegex(ExperimentError, "active Go-to"):
            self.controller.prepare_training_trial("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose("target_out_1"))
        state = self.controller.snapshot()
        self.assertFalse(state["go_to"]["motion"]["active"])
        self.assertEqual(state["go_to"]["history"][0]["status"], "succeeded")
        self.assertEqual(self.modes.active, "snake")
        self.assertEqual(self.passthrough_requests, 1)
        progress = json.loads(
            (self.participant_folder / "experiment_progress.json").read_text()
        )
        self.assertEqual(progress["blocks"][2]["go_to_history"][0]["status"], "succeeded")

    def test_legacy_participant_ready_transition_remains_supported(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        block_id = "mode_1_training"
        self.controller.start(block_id)
        self.controller.prepare_training_trial(block_id)
        self.complete_go_to("target_out_1")
        state = self.controller.training_participant_ready(block_id)
        self.assertEqual(state["training"]["workflow"], "ready")
        state = self.controller.start_training_attempt(block_id)
        self.assertEqual(state["training"]["workflow"], "recording")
        self.controller.stop_training_attempt(block_id)

    def test_abort_before_recording_requeues_trial_for_resume(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        block_id = "mode_1_training"
        self.controller.start(block_id)
        self.controller.prepare_training_trial(block_id)

        state = self.controller.abort(block_id)
        block = state["progress"]["blocks"][2]
        self.assertEqual(block["training_workflow"], "awaiting_prepare")
        self.assertIsNone(block["current_trial_id"])
        self.assertEqual(block["training_trials"][0]["status"], "pending")

        state = self.controller.start(block_id)
        self.assertEqual(state["training"]["workflow"], "awaiting_prepare")
        resumed = self.controller.prepare_training_trial(block_id)
        self.assertTrue(resumed["go_to"]["motion"]["active"])
        self.assertEqual(
            resumed["go_to"]["motion"]["current"]["pose_id"],
            "target_out_1",
        )

    def test_abort_during_recording_preserves_required_decision(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        block_id = "mode_1_training"
        self.controller.start(block_id)
        self.controller.prepare_training_trial(block_id)
        self.complete_go_to("target_out_1")
        self.controller.start_training_attempt(block_id)
        self.controller.add_training_incident(block_id)

        state = self.controller.abort(block_id)
        block = state["progress"]["blocks"][2]
        self.assertEqual(block["training_workflow"], "incident_review_required")
        self.assertEqual(
            block["training_trials"][0]["status"], "incident_review_required"
        )
        resumed = self.controller.start(block_id)
        self.assertEqual(
            resumed["training"]["workflow"], "incident_review_required"
        )
        reviewed = self.controller.review_training_incidents(
            block_id,
            [{"id": 1, "text": "Participant requested a stop"}],
            False,
        )
        self.assertEqual(reviewed["training"]["workflow"], "decision_required")

    def test_training_go_to_is_blocked_during_recording(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        block_id = "mode_1_training"
        self.controller.start(block_id)
        self.controller.prepare_training_trial(block_id)
        self.complete_go_to("target_out_1")
        self.controller.start_training_attempt(block_id)
        with self.assertRaisesRegex(ExperimentError, "acquisition"):
            self.controller.start_go_to(block_id, "target_out_1")

    def test_training_success_stops_recorder_after_stable_pose(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_1")
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

    def test_successful_attempt_waits_for_incident_review(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        block_id = "mode_1_training"
        self.controller.start(block_id)
        self.controller.prepare_training_trial(block_id)
        self.complete_go_to("target_out_1")
        self.controller.start_training_attempt(block_id)
        self.controller.add_training_incident(block_id)
        self.controller.update_ee_pose(self.pose("target_2"))
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose("target_2"))

        state = self.controller.snapshot()
        training = state["training"]
        attempt = training["current_attempt"]
        self.assertEqual(training["workflow"], "incident_review_required")
        self.assertTrue(training["incident_review_required"])
        self.assertEqual(training["incident_descriptions_missing"], 1)
        self.assertTrue(attempt["technical_outcome"]["completed"])
        self.assertFalse(state["go_to"]["available"])
        with self.assertRaisesRegex(ExperimentError, "incident review"):
            self.controller.start_go_to(block_id, "target_out_1")

        state = self.controller.review_training_incidents(
            block_id,
            [{"id": 1, "text": "Brief loss of visual contact"}],
            False,
        )
        self.assertEqual(state["training"]["trials"][0]["status"], "completed")
        self.assertEqual(state["training"]["workflow"], "awaiting_prepare")

    def test_incident_can_invalidate_technically_successful_attempt(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        block_id = "mode_1_training"
        self.controller.start(block_id)
        self.controller.prepare_training_trial(block_id)
        self.complete_go_to("target_out_1")
        self.controller.start_training_attempt(block_id)
        self.controller.add_training_incident(block_id)
        self.controller.update_ee_pose(self.pose("target_2"))
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose("target_2"))

        state = self.controller.review_training_incidents(
            block_id,
            [{"id": 1, "text": "Protocol instruction was incorrect"}],
            True,
        )
        attempt = state["training"]["current_attempt"]
        self.assertEqual(state["training"]["workflow"], "decision_required")
        self.assertEqual(attempt["status"], "invalid")
        self.assertEqual(attempt["stop_reason"], "incident_invalidated")
        self.assertTrue(attempt["technical_outcome"]["completed"])

    def test_training_stale_pose_blocks_readiness_and_recording(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_1")
        self.monotonic_value += 0.6
        with self.assertRaisesRegex(ExperimentError, "calibrated start pose"):
            self.controller.training_participant_ready("mode_1_training")
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.monotonic_value += 0.6
        with self.assertRaisesRegex(ExperimentError, "left the calibrated start pose"):
            self.controller.start_training_attempt("mode_1_training")

    def test_training_invalid_mcap_requires_operator_resolution(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_1")
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
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_1")
        self.controller.start_training_attempt("mode_1_training")
        self.controller.add_training_incident("mode_1_training")
        self.controller.shutdown()
        progress = json.loads(
            (self.participant_folder / "experiment_progress.json").read_text()
        )
        block = progress["blocks"][2]
        attempt = block["training_trials"][0]["attempts"][0]
        self.assertEqual(block["status"], "interrupted")
        self.assertEqual(attempt["status"], "incident_review_required")
        self.assertEqual(attempt["stop_reason"], "interface_shutdown")
        self.assertFalse(self.rosbag.active)

        resumed_controller = ExperimentController(
            ExperimentProfile(self.source_profile),
            self.stack,
            self.modes,
            self.rosbag,
            utc_clock=self.clock,
            monotonic_clock=lambda: self.monotonic_value,
            thread_factory=ImmediateThread,
        )
        resumed_controller.prepare(self.participant)
        resumed = resumed_controller.start("mode_1_training")
        self.assertEqual(
            resumed["training"]["workflow"], "incident_review_required"
        )

    def test_legacy_described_incident_is_normalised_without_text_change(self):
        attempt = {
            "status": "invalid",
            "valid": False,
            "stopped_at_utc": "2026-01-01T00:00:02Z",
            "stop_reason": "operator_stop",
            "incidents": [
                {
                    "at_utc": "2026-01-01T00:00:01Z",
                    "text": "Legacy operator description",
                }
            ],
        }
        changed = ExperimentController._ensure_attempt_incident_fields(attempt)
        self.assertTrue(changed)
        self.assertEqual(attempt["incidents"][0]["id"], 1)
        self.assertEqual(
            attempt["incidents"][0]["text"], "Legacy operator description"
        )
        self.assertEqual(
            attempt["incidents"][0]["described_at_utc"],
            "2026-01-01T00:00:01Z",
        )
        self.assertEqual(attempt["incident_review"]["status"], "complete")

    def test_trial_fields_are_migrated_from_legacy_profile(self):
        self.prepare_session()
        profile_path = (
            self.participant_folder / "experimental_environment" / "experiment.yaml"
        )
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        training = profile["phases"]["training"]
        training.pop("trial_folder_pattern")
        training.pop("attempt_pattern")
        recording = profile["phases"]["recording"]
        recording.pop("trial_folder_pattern")
        recording.pop("attempt_pattern")
        profile["success_thresholds"] = {"linear_m": None, "angular_rad": None}
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        progress_path = self.participant_folder / "experiment_progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        progress["profile"]["sha256"] = digest
        progress["blocks"][2].pop("view")
        for index in (2, 3):
            trial_block = progress["blocks"][index]
            if index == 2:
                for key in (
                    "training_workflow",
                    "training_trials",
                    "current_trial_id",
                    "training_deviations",
                    "training_live",
                ):
                    trial_block.pop(key, None)
            trial_block["settings"].pop("trial_folder_pattern")
            trial_block["settings"].pop("attempt_pattern")
            trial_block["success_thresholds"] = {
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
        training_block = migrated["progress"]["blocks"][2]
        recording_block = migrated["progress"]["blocks"][3]
        self.assertEqual(len(training_block["training_trials"]), 6)
        self.assertEqual(training_block["view"], "F")
        self.assertEqual(len(recording_block["training_trials"]), 30)
        self.assertEqual(training_block["success_thresholds"]["linear_mm"], 5.0)
        self.assertTrue(
            any("Training fields" in warning for warning in migrated["progress"]["warnings"])
        )
        self.assertTrue(
            any("Recording fields" in warning for warning in migrated["progress"]["warnings"])
        )

    def test_manual_stop_incident_retry_and_deviation(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_1")
        self.controller.start_training_attempt("mode_1_training")
        self.controller.add_training_incident("mode_1_training")
        self.controller.add_training_incident("mode_1_training")
        state = self.controller.stop_training_attempt("mode_1_training")
        self.assertEqual(state["training"]["workflow"], "incident_review_required")
        attempt = state["training"]["trials"][0]["attempts"][0]
        self.assertEqual([item["id"] for item in attempt["incidents"]], [1, 2])
        self.assertEqual([item["text"] for item in attempt["incidents"]], [None, None])
        self.assertFalse(attempt["technical_outcome"]["completed"])
        with self.assertRaisesRegex(ExperimentError, "Every incident"):
            self.controller.review_training_incidents(
                "mode_1_training",
                [{"id": 1, "text": "Joystick slipped"}],
                False,
            )
        with self.assertRaisesRegex(ExperimentError, "unique"):
            self.controller.review_training_incidents(
                "mode_1_training",
                [
                    {"id": 1, "text": "Joystick slipped"},
                    {"id": 1, "text": "Duplicate identifier"},
                ],
                False,
            )
        with self.assertRaisesRegex(ExperimentError, "Every incident"):
            self.controller.review_training_incidents(
                "mode_1_training",
                [
                    {"id": 1, "text": "Joystick slipped"},
                    {"id": 99, "text": "Unknown occurrence"},
                ],
                False,
            )
        with self.assertRaisesRegex(ExperimentError, "must not be empty"):
            self.controller.review_training_incidents(
                "mode_1_training",
                [
                    {"id": 1, "text": "Joystick slipped"},
                    {"id": 2, "text": "   "},
                ],
                False,
            )
        with self.assertRaisesRegex(ExperimentError, "must not exceed"):
            self.controller.review_training_incidents(
                "mode_1_training",
                [
                    {"id": 1, "text": "Joystick slipped"},
                    {"id": 2, "text": "x" * 2001},
                ],
                False,
            )
        state = self.controller.review_training_incidents(
            "mode_1_training",
            [
                {"id": 1, "text": "Joystick slipped"},
                {"id": 2, "text": "Participant paused"},
            ],
            False,
        )
        self.assertEqual(state["training"]["workflow"], "decision_required")
        attempt = state["training"]["trials"][0]["attempts"][0]
        self.assertEqual(attempt["incidents"][0]["text"], "Joystick slipped")
        self.assertEqual(attempt["incident_review"]["status"], "complete")
        self.controller.resolve_training_attempt("mode_1_training", "retry")
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_1")
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
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_1")
        self.controller.start_training_attempt("mode_1_training")
        self.controller.add_training_incident("mode_1_training")
        state = self.controller.restart_stack("mode_1_training")
        self.assertEqual(state["training"]["workflow"], "incident_review_required")
        self.assertEqual(
            state["training"]["trials"][0]["attempts"][0]["stop_reason"],
            "restart_stack",
        )
        self.assertEqual(self.modes.active, "snake")
        state = self.controller.review_training_incidents(
            "mode_1_training",
            [{"id": 1, "text": "Stack restart required"}],
            False,
        )
        self.assertEqual(state["training"]["workflow"], "decision_required")

    def test_recording_generates_thirty_trials_with_official_names(self):
        state = self.start_first_recording_block()
        trials = state["recording"]["trials"]
        self.assertIsNone(state["training"])
        self.assertEqual([trial["id"] for trial in trials], list(range(1, 31)))
        self.assertEqual(trials[0]["folder"], "snake_trial_001_01_1_2")
        self.assertEqual(trials[-1]["folder"], "snake_trial_030_10_3_1")
        self.assertEqual(state["recording"]["progress"], {"resolved": 0, "total": 30})
        with self.assertRaisesRegex(ExperimentError, "View F"):
            self.controller.prepare_training_trial("mode_1_recording")
        with self.assertRaisesRegex(ExperimentError, "Resolve every trial"):
            self.controller.end("mode_1_recording", True)

    def test_recording_success_and_retry_deviation_match_training(self):
        self.start_first_recording_block()
        block_id = "mode_1_recording"
        self.controller.prepare_recording_trial(block_id)
        self.complete_go_to("target_out_1")
        self.controller.start_recording_attempt(block_id)
        self.controller.add_recording_incident(block_id)
        stopped = self.controller.stop_recording_attempt(block_id)
        self.assertEqual(stopped["recording"]["workflow"], "incident_review_required")
        reviewed = self.controller.review_recording_incidents(
            block_id,
            [{"id": 1, "text": "Participant paused"}],
            False,
        )
        self.assertEqual(reviewed["recording"]["workflow"], "decision_required")
        self.controller.resolve_recording_attempt(block_id, "retry")

        self.controller.prepare_recording_trial(block_id)
        self.complete_go_to("target_out_1")
        self.controller.start_recording_attempt(block_id)
        self.controller.update_ee_pose(self.pose("target_2"))
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose("target_2"))
        state = self.controller.snapshot()
        trial = state["recording"]["trials"][0]
        self.assertEqual(trial["status"], "completed")
        self.assertEqual([attempt["name"] for attempt in trial["attempts"]], [
            "attempt_001",
            "attempt_002",
        ])
        trial_folder = self.participant_folder / "snake_recording" / (
            "snake_trial_001_01_1_2"
        )
        self.assertTrue((trial_folder / "attempt_001" / "attempt.json").is_file())
        self.assertTrue((trial_folder / "attempt_002" / "attempt.json").is_file())

    def test_recording_restart_invalidates_attempt_and_restores_mapper(self):
        self.start_first_recording_block()
        block_id = "mode_1_recording"
        self.controller.prepare_recording_trial(block_id)
        self.complete_go_to("target_out_1")
        self.controller.start_recording_attempt(block_id)
        state = self.controller.restart_stack(block_id)
        self.assertEqual(state["recording"]["workflow"], "decision_required")
        attempt = state["recording"]["trials"][0]["attempts"][0]
        self.assertEqual(attempt["stop_reason"], "restart_stack")
        self.assertEqual(self.modes.active, "snake")

    def test_recording_rejects_stale_start_and_invalid_mcap(self):
        self.start_first_recording_block()
        block_id = "mode_1_recording"
        self.controller.prepare_recording_trial(block_id)
        self.complete_go_to("target_out_1")
        self.monotonic_value += 0.6
        with self.assertRaisesRegex(ExperimentError, "calibrated start pose"):
            self.controller.recording_participant_ready(block_id)
        self.controller.update_ee_pose(self.pose("target_out_1"))
        self.controller.start_recording_attempt(block_id)
        self.controller.add_recording_incident(block_id)
        self.rosbag.valid = False
        self.controller.update_ee_pose(self.pose("target_2"))
        self.monotonic_value += 0.6
        self.controller.update_ee_pose(self.pose("target_2"))
        state = self.controller.snapshot()
        attempt = state["recording"]["trials"][0]["attempts"][0]
        self.assertEqual(
            state["recording"]["workflow"], "incident_review_required"
        )
        self.assertEqual(attempt["stop_reason"], "invalid_data")
        self.assertFalse(attempt["valid"])
        state = self.controller.review_recording_incidents(
            block_id,
            [{"id": 1, "text": "A non-invalidating observation"}],
            False,
        )
        self.assertEqual(state["recording"]["workflow"], "decision_required")
        self.assertFalse(state["recording"]["current_attempt"]["valid"])

    def test_shutdown_invalidates_active_official_recording(self):
        self.start_first_recording_block()
        block_id = "mode_1_recording"
        self.controller.prepare_recording_trial(block_id)
        self.complete_go_to("target_out_1")
        self.controller.start_recording_attempt(block_id)
        self.controller.shutdown()
        progress = json.loads(
            (self.participant_folder / "experiment_progress.json").read_text()
        )
        block = progress["blocks"][3]
        attempt = block["training_trials"][0]["attempts"][0]
        self.assertEqual(block["status"], "interrupted")
        self.assertEqual(attempt["stop_reason"], "interface_shutdown")
        self.assertFalse(self.rosbag.active)

    def test_deferred_go_to_finish_cannot_restore_control_after_protection_interruption(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        queued = []
        self.controller._go_to._feedback_finish_dispatch = lambda callback, record: queued.append((callback, record))
        self.controller.prepare_training_trial("mode_1_training")
        self.complete_go_to("target_out_1")
        self.assertEqual(len(queued), 1)
        self.assertIsNone(self.modes.active)
        self.controller.interrupt_overload({"event": 1, "state": "locked", "reason": "completed"})
        callback, record = queued.pop()
        callback(record)  # Even after a rearm, this old restoration was revoked.
        self.assertIsNone(self.modes.active)
        self.assertFalse(self.controller.snapshot()["control_active"])

    def test_initial_feedback_exception_requires_idle_processes_and_workflow(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        startup = {"fresh": True, "state": "normal", "event": 0,
                   "reason": "waiting_for_feedback", "blocked": True}
        self.assertTrue(self.controller.protection_startup_is_idle(startup))
        block = self.controller._block("mode_1_discovery")
        for field, value in (("control_active", True), ("current_segment", "rosbag_001"),
                             ("training_workflow", "recording")):
            previous = block.get(field)
            block[field] = value
            self.assertFalse(self.controller.protection_startup_is_idle(startup))
            block[field] = previous
        self.modes.active = "snake"
        self.assertFalse(self.controller.protection_startup_is_idle(startup))
        self.modes.active = None
        self.rosbag.active = True
        self.assertFalse(self.controller.protection_startup_is_idle(startup))
        self.rosbag.active = False
        self.go_to.start("starting_point", self.pose("starting_point"))
        self.assertFalse(self.controller.protection_startup_is_idle(startup))

    def test_clear_only_persisted_initial_feedback_warning_after_valid_feedback(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.controller.interrupt_overload({"state": "normal", "reason": "waiting_for_feedback", "event": 0})
        warning = self.controller.snapshot()["error"]
        healthy = {"fresh": True, "state": "normal", "blocked": False,
                   "pose_valid": True, "overload_valid": True, "overloaded": False}
        block = self.controller._block("mode_1_discovery")
        before = json.loads(json.dumps(block))
        for changes in ({"fresh": False}, {"blocked": True}, {"pose_valid": False},
                        {"overload_valid": False}, {"overloaded": True}, {"state": "error"}):
            self.controller.clear_initial_feedback_warning({**healthy, **changes})
            self.assertEqual(block, before)
        self.controller.clear_initial_feedback_warning(healthy)
        self.assertIsNone(block["error"])
        self.assertNotIn("protection_interruption", block)
        self.assertIsNone(self.controller.snapshot()["error"])
        self.assertIsNone(self.modes.active)
        self.assertFalse(self.rosbag.active)
        self.assertFalse(block["control_active"])
        for filename in ("experiment_progress.json", "snake_discovery/block.json"):
            saved = json.loads((self.participant_folder / filename).read_text())
            saved_block = saved if "blocks" not in saved else saved["blocks"][0]
            self.assertIsNone(saved_block["error"])
            self.assertNotIn("protection_interruption", saved_block)
        # A different error or a real episode must never be cleared.
        for interruption, error in ((before["protection_interruption"], "Another failure"),
                                    ({"state": "locked", "reason": "completed", "event": 1}, warning)):
            block["protection_interruption"] = interruption
            block["error"] = error
            self.controller._error = error
            self.controller.clear_initial_feedback_warning(healthy)
            self.assertEqual(block["error"], error)
            self.assertEqual(self.controller.snapshot()["error"], error)

    def test_initial_feedback_warning_can_be_cleaned_after_session_reload(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.controller.interrupt_overload({"state": "normal", "reason": "waiting_for_feedback", "event": 0})
        self.controller._block("mode_1_discovery")["status"] = "interrupted"
        self.controller._progress["current_block"] = None
        self.controller._persist(self.participant_folder)
        self.controller.prepare(self.participant)
        self.controller.clear_initial_feedback_warning({"fresh": True, "state": "normal", "blocked": False,
                                                     "pose_valid": True, "overload_valid": True})
        self.assertIsNone(self.controller._block("mode_1_discovery")["error"])

    def test_training_without_acquisition_reports_telemetry_loss_precisely(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        self.controller._command_allowed = lambda: False
        self.controller.interrupt_overload({"event": 0, "state": "unavailable", "reason": "no_fresh_status"})
        state = self.controller.snapshot()
        block = next(v for v in state["progress"]["blocks"] if v["id"] == "mode_1_training")
        self.assertIn("Protection status unavailable or stale", block["error"])
        self.assertNotIn("Effort overload", block["error"])
        self.assertFalse(state["control_active"])
        self.assertFalse(self.rosbag.active)
        persisted = json.loads((self.participant_folder / "experiment_progress.json").read_text())
        saved = next(v for v in persisted["blocks"] if v["id"] == "mode_1_training")
        self.assertEqual(saved["protection_interruption"]["reason"], "no_fresh_status")

    def test_feedback_fault_is_not_reported_as_effort_overload(self):
        self.prepare_session()
        self.complete_discovery("mode_1_discovery")
        self.complete_discovery("mode_2_discovery")
        self.controller.start("mode_1_training")
        for reason in ("invalid_pose", "invalid_overload_feedback", "invalid_feedback"):
            self.controller.interrupt_overload({"event": 1, "state": "error", "reason": reason})
            state = self.controller.snapshot()
            block = next(v for v in state["progress"]["blocks"] if v["id"] == "mode_1_training")
            self.assertIn("Invalid robot feedback", block["error"])
            self.assertIn(reason, block["error"])
            self.assertNotIn("Effort overload", block["error"])
            self.assertFalse(state["control_active"])

    def test_overload_invalidates_recording_and_requires_incident_review(self):
        self.start_first_recording_block()
        block_id = "mode_1_recording"
        self.controller.prepare_recording_trial(block_id)
        trial = self.controller.snapshot()["recording"]["trials"][0]
        self.complete_go_to(f"target_out_{trial['target_start']}")
        self.controller.start_recording_attempt(block_id)
        self.controller._command_allowed = lambda: False
        observed = []
        original_stop = self.rosbag.stop
        def stop():
            persisted = json.loads((self.participant_folder / "experiment_progress.json").read_text())
            block = next(v for v in persisted["blocks"] if v["id"] == block_id)
            observed.append(block["training_workflow"])
            return original_stop()
        self.rosbag.stop = stop
        self.modes.shutdown()  # Expected cutoff must not become a process-failure incident.
        self.controller.snapshot()
        self.controller.interrupt_overload({"event": 1})
        state = self.controller.snapshot()
        attempt = state["recording"]["trials"][0]["attempts"][0]
        self.assertEqual(observed, ["stopping_overload"])
        self.assertFalse(attempt["valid"])
        self.assertFalse(attempt["technical_outcome"]["successful_motion"])
        self.assertEqual(attempt["stop_reason"], "effort_overload")
        self.assertEqual(attempt["incidents"][0]["source"], "effort_overload")
        self.assertEqual(state["recording"]["workflow"], "incident_review_required")
        self.assertFalse(self.rosbag.active)
        self.assertFalse(state["control_active"])
        self.controller._command_allowed = lambda: True  # Explicit successful controller rearm.
        self.assertFalse(self.controller.snapshot()["control_active"])
        restored = self.controller.set_control(block_id, True)
        self.assertTrue(restored["control_active"])
        self.assertFalse(self.rosbag.active)  # No automatic acquisition restart.

    def test_feedback_fault_during_discovery_invalidates_segment(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.controller.show_mode_explanation("mode_1_discovery")
        self.controller.complete_mode_explanation("mode_1_discovery")
        self.controller.set_control("mode_1_discovery", True)
        self.controller.interrupt_overload({"event": 1, "state": "error", "reason": "invalid_pose"})
        state = self.controller.snapshot()
        self.assertFalse(state["control_active"])
        self.assertFalse(state["segments"][0]["valid"])
        self.assertEqual(state["segments"][0]["status"], "incomplete")
        self.assertEqual(state["segments"][0]["stop_reason"], "protection_feedback_fault")

    def test_overload_during_discovery_marks_segment_incomplete(self):
        self.prepare_session()
        self.controller.start("mode_1_discovery")
        self.controller.show_mode_explanation("mode_1_discovery")
        self.controller.complete_mode_explanation("mode_1_discovery")
        self.controller.set_control("mode_1_discovery", True)
        self.controller._command_allowed = lambda: False
        self.modes.shutdown()
        self.controller.interrupt_overload({"event": 2})
        state = self.controller.snapshot()
        self.assertFalse(state["control_active"])
        self.assertEqual(state["segments"][0]["status"], "incomplete")
        self.assertEqual(state["segments"][0]["stop_reason"], "effort_overload")

    def test_discovery_rearm_requires_manual_activation_and_creates_new_segment(self):
        self.prepare_session()
        for block_id in ("mode_1_discovery", "mode_2_discovery"):
            with self.subTest(block=block_id):
                self.controller.start(block_id)
                self.set_control(block_id, True)
                self.controller._command_allowed = lambda: False
                self.modes.shutdown()
                self.controller.interrupt_overload({"event": 1, "state": "locked", "reason": "completed"})
                with self.assertRaisesRegex(ExperimentError, "Rearm"):
                    self.controller.set_control(block_id, True)
                self.controller._command_allowed = lambda: True
                state = self.controller.snapshot()
                self.assertFalse(state["control_active"])
                self.assertIsNone(self.modes.active)
                self.assertFalse(self.rosbag.active)
                state = self.controller.set_control(block_id, True)
                self.assertTrue(state["control_active"])
                self.assertEqual([s["name"] for s in state["segments"]], ["rosbag_001", "rosbag_002"])
                self.assertFalse(state["segments"][0]["valid"])
                self.controller.set_control(block_id, False)
                self.controller.end(block_id, True)



if __name__ == "__main__":
    unittest.main()

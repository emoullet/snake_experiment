from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest

from snake_experiment_ui.checkup import CheckupController, CheckupError


class FakeStack:
    def __init__(self):
        self.status = "inactive"
        self.stops = 0

    def start(self):
        self.status = "active"

    def stop(self):
        self.status = "inactive"
        self.stops += 1

    def shutdown(self):
        self.stop()

    def snapshot(self):
        return {"status": self.status, "error": None, "use_simulation": False}


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

    def snapshot(self):
        return {"active_mode": self.active, "status": "active" if self.active else "inactive", "error": None}


class FakeDiagnostics:
    def __init__(self):
        self.profile = SimpleNamespace(
            diagnostic_timeout_sec=5.0,
            expected_revisions={},
            schema_version=1,
            path="profile.yaml",
            digest="digest",
            measurement_window_sec=2.0,
            joystick_deadzone=0.2,
        )
        self.passed = True
        self.activity = True

    def reset_mode_observation(self):
        pass

    def evaluate(self, mode):
        return {"mode": mode, "passed": self.passed, "joystick_activity": self.activity, "checks": []}


class FakeGoTo:
    def __init__(self):
        self.current = None

    def active(self):
        return False

    def start(self, pose_id, pose, context=None, on_finish=None):
        self.current = {
            "motion_id": f"motion-{pose_id}",
            "pose_id": pose_id,
            "status": "succeeded",
            "stop_reason": "target_reached",
            "target": pose,
            "context": context or {},
        }
        if on_finish:
            on_finish(self.current)
        return self.snapshot()

    def snapshot(self):
        return {"active": False, "current": self.current, "configuration": {}}

    def cancel_if_active(self, reason):
        return False

    def update_pose(self, message):
        pass

    def stop(self, reason):
        raise RuntimeError("No active motion")


class CheckupControllerTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        calibration = Path(self.temporary_directory.name) / "latest_calib.json"
        pose = {
            "frame_id": "base_link",
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        }
        calibration.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "poses": {
                        pose_id: pose
                        for pose_id in (
                            "target_1",
                            "target_2",
                            "target_3",
                            "starting_point",
                        )
                    },
                }
            ),
            encoding="utf-8",
        )
        self.stack = FakeStack()
        self.modes = FakeModes()
        self.diagnostics = FakeDiagnostics()
        self.now = 10.0
        self.controller = CheckupController(
            self.stack,
            self.modes,
            self.diagnostics,
            provenance_provider=lambda: {"error": None, "repositories": {".": {"commit": "abc", "dirty": False}}},
            use_simulation=False,
            ros_distro="jazzy",
            monotonic_clock=lambda: self.now,
            utc_clock=lambda: "2026-09-15T10:00:00Z",
            go_to_controller=FakeGoTo(),
            calibration_file=calibration,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def pass_mode(self, mode):
        self.controller.start_mode(mode)
        self.assertEqual(self.controller.snapshot()["workflow"], "awaiting_confirmation")
        self.controller.confirm_mode(mode, True)

    def pass_go_to(self):
        for pose_id in ("target_1", "target_2", "target_3", "starting_point"):
            self.controller.start_go_to(pose_id)

    def test_modes_can_pass_in_any_order_then_validate(self):
        self.controller.start_stack()
        self.pass_mode("snake")
        self.pass_mode("baseline")
        self.pass_go_to()
        self.assertTrue(self.controller.snapshot()["can_validate"])
        state = self.controller.validate()
        self.assertEqual(state["workflow"], "validated")
        self.assertEqual(state["current_view"], "C")
        self.assertEqual(state["stack"]["status"], "inactive")
        self.assertEqual(state["pending_report"]["status"], "PASSED_WITH_WARNINGS")

    def test_validation_is_gated_and_operator_can_reject_then_retry(self):
        self.controller.start_stack()
        with self.assertRaisesRegex(CheckupError, "both pass"):
            self.controller.validate()
        self.controller.start_mode("baseline")
        self.controller.confirm_mode("baseline", False)
        self.assertEqual(self.controller.snapshot()["modes"]["baseline"]["status"], "failed")
        self.controller.retry_mode("baseline")
        self.controller.confirm_mode("baseline", True)
        self.assertEqual(self.controller.snapshot()["modes"]["baseline"]["attempts"], 2)

    def test_validation_requires_all_four_go_to_targets(self):
        self.controller.start_stack()
        self.pass_mode("baseline")
        self.pass_mode("snake")
        for pose_id in ("target_1", "target_2", "target_3"):
            self.controller.start_go_to(pose_id)
        self.assertFalse(self.controller.snapshot()["can_validate"])
        with self.assertRaisesRegex(CheckupError, "starting_point"):
            self.controller.validate()
        self.controller.start_go_to("starting_point")
        self.assertTrue(self.controller.snapshot()["can_validate"])

    def test_diagnostic_timeout_fails_and_stops_mapper(self):
        self.diagnostics.passed = False
        self.controller.start_stack()
        self.controller.start_mode("snake")
        self.now = 16.0
        state = self.controller.snapshot()
        self.assertEqual(state["workflow"], "error")
        self.assertIsNone(self.modes.active)


if __name__ == "__main__":
    unittest.main()

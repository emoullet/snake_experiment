from types import SimpleNamespace
import unittest

from snake_experiment_ui.go_to import GoToController, GoToError, validate_pose


def pose(x=0.0, frame="base_link", quaternion=(0.0, 0.0, 0.0, 1.0)):
    return {
        "frame_id": frame,
        "position": {"x": x, "y": 0.0, "z": 0.0},
        "orientation": dict(zip(("x", "y", "z", "w"), quaternion)),
    }


def message(value):
    return SimpleNamespace(
        header=SimpleNamespace(frame_id=value["frame_id"]),
        pose=SimpleNamespace(
            position=SimpleNamespace(**value["position"]),
            orientation=SimpleNamespace(**value["orientation"]),
        ),
    )


class GoToControllerTest(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.targets = []
        self.passthrough = []
        self.finished = []
        self.controller = GoToController(
            publish_target=self.targets.append,
            publish_passthrough=lambda: self.passthrough.append(True),
            preflight=lambda: None,
            monotonic_clock=lambda: self.now,
            utc_clock=lambda: "2026-09-22T10:00:00Z",
            dwell_sec=0.5,
            timeout_sec=30.0,
        )

    def start(self):
        return self.controller.start(
            "target_1", pose(1.0), on_finish=self.finished.append
        )

    def test_requires_stable_arrival_then_finishes(self):
        self.start()
        self.controller.update_pose(message(pose(1.0)))
        self.assertEqual(self.controller.snapshot()["current"]["status"], "settling")
        self.now += 0.4
        self.controller.update_pose(message(pose(1.02)))
        self.assertEqual(self.controller.snapshot()["current"]["status"], "moving")
        self.controller.update_pose(message(pose(1.0)))
        self.now += 0.5
        self.controller.update_pose(message(pose(1.0)))
        state = self.controller.snapshot()
        self.assertEqual(state["current"]["status"], "succeeded")
        self.assertEqual(len(self.finished), 1)
        self.assertEqual(len(self.passthrough), 1)

    def test_timeout_and_operator_stop_are_reported(self):
        self.start()
        self.now += 30.0
        state = self.controller.snapshot()
        self.assertEqual(state["current"]["status"], "timed_out")
        self.assertEqual(self.finished[-1]["stop_reason"], "timeout")
        self.start()
        self.controller.stop("operator_stop")
        self.assertEqual(self.finished[-1]["status"], "cancelled")

    def test_preflight_and_pose_validation_are_blocking(self):
        unavailable = GoToController(
            publish_target=self.targets.append,
            publish_passthrough=lambda: None,
            preflight=lambda: "Pose target topic missing.",
        )
        with self.assertRaisesRegex(GoToError, "topic missing"):
            unavailable.start("target_1", pose())
        with self.assertRaisesRegex(GoToError, "does not match"):
            validate_pose(pose(frame="map"), "base_link")
        with self.assertRaisesRegex(GoToError, "zero"):
            validate_pose(pose(quaternion=(0.0, 0.0, 0.0, 0.0)), "base_link")


if __name__ == "__main__":
    unittest.main()

from types import SimpleNamespace
import math
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

    def test_feedback_completion_can_be_dispatched_without_waiting_for_callback(self):
        queued = []
        self.controller = GoToController(
            publish_target=self.targets.append,
            publish_passthrough=lambda: self.passthrough.append(True),
            monotonic_clock=lambda: self.now,
            feedback_finish_dispatch=lambda callback, record: queued.append((callback, record)),
        )
        self.start()
        self.controller.update_pose(message(pose(1.0)))
        self.now += .6
        self.controller.update_pose(message(pose(1.0)))
        self.assertEqual(self.controller.snapshot()["current"]["status"], "succeeded")
        self.assertEqual(self.finished, [])
        self.assertEqual(len(queued), 1)
        self.controller.update_pose(message(pose(1.0)))
        self.assertEqual(len(queued), 1)
        callback, record = queued.pop()
        callback(record)
        self.assertEqual(len(self.finished), 1)

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
        self.assertEqual(self.finished[-1]["stop_reason"], "feedback_timeout")
        self.start()
        self.controller.stop("operator_stop")
        self.assertEqual(self.finished[-1]["status"], "cancelled")

    def feedback(self, elapsed, x=0.0, angle=0.0):
        self.now = 10.0 + elapsed
        self.controller.update_pose(message(pose(x, quaternion=(0.0, 0.0, math.sin(angle / 2), math.cos(angle / 2)))))

    def test_long_progressing_motion_and_explicit_total_limit(self):
        for limit in (30.0, 120.0):
            with self.subTest(limit=limit):
                self.setUp()
                self.controller = GoToController(
                    publish_target=self.targets.append,
                    publish_passthrough=lambda: self.passthrough.append(True),
                    monotonic_clock=lambda: self.now, timeout_sec=limit,
                )
                self.start()
                for second in range(int(limit)):
                    self.feedback(second, x=second * .001)
                    self.controller.tick()
                    self.assertTrue(self.controller.snapshot()["active"])
                self.feedback(limit, x=limit * .001)
                state = self.controller.snapshot()
                self.assertEqual(state["current"]["stop_reason"], "total_timeout")
                self.assertEqual(len(self.passthrough), 1)

    def test_stall_window_and_exact_deadline(self):
        self.start()
        self.feedback(0)
        self.feedback(.5)
        self.assertEqual(self.controller.snapshot()["current"]["stall_sec"], 0)
        for second in range(1, 6):
            self.feedback(second)
        self.now = 15.99
        self.controller.tick()
        self.assertTrue(self.controller.snapshot()["active"])
        self.now = 16.0
        self.controller.tick()
        state = self.controller.snapshot()["current"]
        self.assertEqual(state["stop_reason"], "progress_stalled")
        self.assertEqual(state["stall_sec"], 5.0)
        self.assertEqual(len(self.finished), 1)
        self.controller.tick()
        self.controller.update_pose(message(pose(1)))
        self.controller.snapshot()
        self.assertEqual(len(self.finished), 1)
        self.assertEqual(len(self.passthrough), 1)

    def test_recovery_resets_stall_counter(self):
        self.start()
        for second in range(5):
            self.feedback(second)
        self.feedback(5, x=.002)
        self.assertEqual(self.controller.snapshot()["current"]["stall_sec"], 0)
        for second in range(6, 11):
            self.feedback(second, x=.002)
        self.assertTrue(self.controller.snapshot()["active"])
        self.now = 21.0
        self.controller.tick()
        self.assertEqual(self.controller.snapshot()["current"]["stop_reason"], "progress_stalled")

    def test_either_component_can_progress_while_other_is_fixed(self):
        for rotation in (True, False):
            with self.subTest(rotation=rotation):
                self.setUp()
                self.start()
                for second in range(9):
                    self.feedback(second, x=0 if rotation else second * .002,
                                  angle=1 - second * .02 if rotation else 1)
                self.assertTrue(self.controller.snapshot()["active"])

    def test_progress_inside_tolerance_does_not_mask_blocked_component(self):
        self.start()
        for second in range(7):
            self.feedback(second, x=1 - .004 + second * .0006, angle=1)
        self.assertEqual(self.controller.snapshot()["current"]["stop_reason"], "progress_stalled")

    def test_oscillation_with_no_net_progress_stalls(self):
        self.start()
        for half_second in range(13):
            self.feedback(half_second / 2, x=0 if half_second % 2 == 0 else -.002)
        self.assertEqual(self.controller.snapshot()["current"]["stop_reason"], "progress_stalled")

    def test_entering_tolerance_resets_stall_and_allows_stability(self):
        self.start()
        for second in range(6):
            self.feedback(second)
        self.feedback(5.5, x=1)
        self.assertEqual(self.controller.snapshot()["current"]["stall_sec"], 0)
        self.feedback(6.0, x=1)
        self.assertEqual(self.controller.snapshot()["current"]["status"], "succeeded")

    def test_irregular_samples_interpolate_window_boundary(self):
        self.start()
        self.feedback(0)
        self.feedback(.6, x=.006)
        self.feedback(1.4, x=.014)
        state = self.controller.snapshot()["current"]
        self.assertAlmostEqual(state["linear_progress_mps"], .01)
        self.assertEqual(state["stall_sec"], 0)
        self.feedback(1.4, x=.014)
        self.assertTrue(self.controller.snapshot()["active"])

    def test_feedback_loss_including_startup_cannot_be_rescued_by_late_arrival(self):
        for initial_feedback in (False, True):
            with self.subTest(initial_feedback=initial_feedback):
                self.setUp()
                self.start()
                if initial_feedback:
                    self.feedback(0, x=1)
                self.feedback(2, x=1)
                self.assertEqual(self.controller.snapshot()["current"]["stop_reason"], "feedback_timeout")

    def test_invalid_feedback_does_not_refresh_deadline(self):
        invalid = [pose(frame="wrong"), pose(x=float("nan")), pose(x=1e308),
                   pose(quaternion=(0, 0, float("inf"), 1)),
                   pose(quaternion=(1e308, 0, 0, 1)),
                   pose(quaternion=(0, 0, 0, 0)), {}]
        for value in invalid:
            with self.subTest(value=value):
                self.setUp()
                self.start()
                self.feedback(0)
                self.now = 11.5
                self.controller.update_pose(message(value) if value else value)
                self.assertIn("Invalid", self.controller.snapshot()["current"]["error"])
                self.now = 12
                self.controller.tick()
                self.assertEqual(self.controller.snapshot()["current"]["stop_reason"], "feedback_timeout")

    def test_invalid_sample_breaks_stability(self):
        self.start()
        self.feedback(0, x=1)
        self.now = 10.4
        self.controller.update_pose({})
        self.feedback(.6, x=1)
        self.assertTrue(self.controller.snapshot()["active"])
        self.feedback(1.2, x=1)
        self.assertEqual(self.controller.snapshot()["current"]["status"], "succeeded")

    def test_timeout_priority_feedback_then_total_then_stall(self):
        self.controller = GoToController(
            publish_target=self.targets.append,
            publish_passthrough=lambda: None,
            monotonic_clock=lambda: self.now, timeout_sec=6,
        )
        self.start()
        for second in range(5):
            self.feedback(second)
        self.now = 16
        self.controller.tick()
        self.assertEqual(self.controller.snapshot()["current"]["stop_reason"], "feedback_timeout")
        self.now = 10
        self.start()
        for second in range(6):
            self.feedback(second)
        self.now = 16
        self.controller.tick()
        self.assertEqual(self.controller.snapshot()["current"]["stop_reason"], "total_timeout")

    def test_tick_dispatches_completion_outside_ros_callback(self):
        queued = []
        self.controller = GoToController(
            publish_target=self.targets.append, publish_passthrough=lambda: None,
            monotonic_clock=lambda: self.now,
            feedback_finish_dispatch=lambda callback, record: queued.append((callback, record)),
        )
        self.start()
        self.now += 2
        self.controller.tick()
        self.assertEqual(self.finished, [])
        self.assertEqual(len(queued), 1)
        callback, record = queued[0]
        callback(record)
        self.assertEqual(self.finished[0]["stop_reason"], "feedback_timeout")

    def test_configuration_requires_finite_positive_values(self):
        names = ("timeout_sec", "dwell_sec", "linear_tolerance_m", "angular_tolerance_rad",
                 "stall_timeout_sec", "progress_window_sec", "linear_progress_threshold_mps",
                 "angular_progress_threshold_radps", "feedback_timeout_sec")
        for name in names:
            for value in (0, -1, float("nan"), float("inf")):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        GoToController(lambda pose: None, lambda: None, **{name: value})

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

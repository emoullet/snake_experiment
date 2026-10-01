from pathlib import Path
from types import SimpleNamespace
import unittest

from snake_experiment_ui.diagnostics import (
    DiagnosticMonitor,
    DiagnosticProfile,
    evaluate_provenance,
)


PROFILE = Path(__file__).parents[1] / "config" / "system_checkup.yaml"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def vector(**values):
    return SimpleNamespace(**values)


def messages():
    return {
        "/joint_states": SimpleNamespace(name=["j1"], position=[0.0]),
        "/ee_pose": SimpleNamespace(
            header=SimpleNamespace(frame_id="base_link"),
            pose=SimpleNamespace(
                position=vector(x=0.0, y=0.0, z=0.0),
                orientation=vector(x=0.0, y=0.0, z=0.0, w=1.0),
            ),
        ),
        "/joy": SimpleNamespace(axes=[0.5], buttons=[0]),
        "/joystick_cartesian_command": SimpleNamespace(
            twist=SimpleNamespace(
                linear=vector(x=0.0, y=0.0, z=0.0),
                angular=vector(x=0.0, y=0.0, z=0.0),
            )
        ),
        "/cartesian_command": SimpleNamespace(
            twist=SimpleNamespace(
                linear=vector(x=0.0, y=0.0, z=0.0),
                angular=vector(x=0.0, y=0.0, z=0.0),
            )
        ),
        "/mode_request": SimpleNamespace(data="geometric/both"),
    }


class DiagnosticMonitorTest(unittest.TestCase):
    def setUp(self):
        self.profile = DiagnosticProfile(PROFILE)
        self.clock = Clock()
        self.graph = {
            "nodes": [
                "/controller_manager",
                "/robot_state_publisher",
                "/cartesian_manager",
                "/joy_node",
                "/joystick_mapper",
            ],
            "topics": {
                name: [requirement.message_type]
                for name, requirement in {
                    **self.profile.base_topics,
                    **self.profile.mode_topics,
                }.items()
            },
            "controllers": {
                name: "active" for name in self.profile.required_controllers
            },
            "mapper_modes": ["b1", "b2", "b3"],
            "mapper_parameter_names": [],
            "mapper_parameters": {},
        }
        self.monitor = DiagnosticMonitor(
            self.profile, lambda: self.graph, self.clock
        )
        self.graph["mapper_parameter_names"] = list(
            self.monitor.expected_mapper_parameter_names("baseline")
        )
        self.graph["mapper_parameters"] = dict(
            self.profile.modes["baseline"].mapper_parameters
        )

    def record_passing_rates(self, mode_request="geometric/both"):
        topic_messages = messages()
        topic_messages["/mode_request"].data = mode_request
        counts = {
            "/joint_states": 40,
            "/ee_pose": 40,
            "/joy": 10,
            "/joystick_cartesian_command": 10,
            "/cartesian_command": 10,
            "/mode_request": 1,
        }
        for topic, count in counts.items():
            for _ in range(count):
                self.monitor.record(topic, topic_messages[topic], self.clock.now)

    def test_baseline_profile_and_activity_pass(self):
        self.record_passing_rates()
        result = self.monitor.evaluate("baseline")
        self.assertTrue(result["passed"])
        self.assertTrue(result["joystick_activity"])

    def test_mode_request_has_no_rate_check_but_still_requires_a_valid_request(self):
        self.record_passing_rates()
        self.clock.now += self.profile.measurement_window_sec + 0.1
        topic_messages = messages()
        for topic, count in {
            "/joint_states": 40,
            "/ee_pose": 40,
            "/joy": 10,
            "/joystick_cartesian_command": 10,
            "/cartesian_command": 10,
        }.items():
            for _ in range(count):
                self.monitor.record(topic, topic_messages[topic], self.clock.now)

        checks = {
            check["name"]: check
            for check in self.monitor.evaluate("baseline")["checks"]
        }
        self.assertNotIn("topic_rate:/mode_request", checks)
        self.assertIn("topic_rate:/joy", checks)
        for name in (
            "topic_type:/mode_request",
            "message:/mode_request",
            "mode_request",
        ):
            self.assertTrue(checks[name]["passed"])

        self.monitor.reset_mode_observation()
        checks = {
            check["name"]: check
            for check in self.monitor.evaluate("baseline")["checks"]
        }
        self.assertFalse(checks["message:/mode_request"]["passed"])
        self.assertFalse(checks["mode_request"]["passed"])

    def test_snake_requires_only_b1_and_b2(self):
        self.monitor.reset_mode_observation()
        self.graph["mapper_modes"] = ["b1", "b2"]
        self.graph["mapper_parameter_names"] = list(
            self.monitor.expected_mapper_parameter_names("snake")
        )
        self.graph["mapper_parameters"] = dict(
            self.profile.modes["snake"].mapper_parameters
        )
        self.record_passing_rates("geometric/both")
        result = self.monitor.evaluate("snake")
        self.assertTrue(result["passed"])
        mapper_check = next(
            check for check in result["checks"] if check["name"] == "mapper:modes.names"
        )
        self.assertEqual(mapper_check["expected"], ["b1", "b2"])

    def test_wrong_mapper_profile_and_invalid_pose_fail(self):
        invalid = messages()
        invalid["/ee_pose"].pose.position.x = float("nan")
        self.record_passing_rates()
        self.monitor.record("/ee_pose", invalid["/ee_pose"], self.clock.now)
        self.graph["mapper_modes"] = ["b1", "b2"]
        self.graph["mapper_parameters"]["modes.b1.axes.linear_x.scale"] = -1.0
        result = self.monitor.evaluate("baseline")
        failures = {check["name"] for check in result["checks"] if not check["passed"]}
        self.assertIn("mapper:modes.names", failures)
        self.assertIn("mapper:parameter_values", failures)
        self.assertIn("message:/ee_pose", failures)

    def test_revision_constraints_are_optional_and_exact(self):
        provenance = {"repositories": {".": {"commit": "abc", "dirty": True}}}
        self.assertEqual(evaluate_provenance(provenance, {}), [])
        self.assertTrue(evaluate_provenance(provenance, {".": "abc"})[0]["passed"])
        self.assertFalse(evaluate_provenance(provenance, {".": "def"})[0]["passed"])


if __name__ == "__main__":
    unittest.main()

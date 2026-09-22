import unittest
from unittest.mock import patch

from snake_experiment_ui.mode_manager import ModeError, ModeManager


class FakeProcess:
    def __init__(self, command, start_new_session):
        self.command = command
        self.start_new_session = start_new_session
        self.pid = 4242
        self.return_code = None

    def poll(self):
        return self.return_code

    def wait(self, timeout):
        self.return_code = 0
        return 0


class ModeManagerTest(unittest.TestCase):
    def test_activation_is_idempotent_and_publishes_control_mode(self):
        names = []
        requests = []
        processes = []
        mapper_transitions = []

        def create_process(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            names.append("joystick_mapper")
            return process

        manager = ModeManager(
            node_names=lambda: names,
            publish_mode_request=requests.append,
            mapper_transition=lambda: mapper_transitions.append("reset"),
            popen_factory=create_process,
            sleep=lambda _: None,
        )
        first = manager.activate("baseline")
        second = manager.activate("baseline")

        self.assertEqual(first["active_mode"], "baseline")
        self.assertEqual(second["active_mode"], "baseline")
        self.assertEqual(len(processes), 1)
        self.assertEqual(
            processes[0].command,
            [
                "ros2",
                "launch",
                "snake_experiment_ui",
                "joystick_mapper_baseline.launch.py",
            ],
        )
        self.assertEqual(requests, ["geometric/both", "geometric/both"])
        self.assertEqual(mapper_transitions, ["reset"])

    def test_external_mapper_is_rejected(self):
        manager = ModeManager(
            node_names=lambda: ["/joystick_mapper"],
            publish_mode_request=lambda _: None,
        )
        with self.assertRaisesRegex(ModeError, "outside this interface"):
            manager.activate("snake")

    def test_unknown_mode_is_rejected(self):
        manager = ModeManager(
            node_names=lambda: [],
            publish_mode_request=lambda _: None,
        )
        with self.assertRaisesRegex(ModeError, "Unknown"):
            manager.activate("automatic")
        with self.assertRaisesRegex(ModeError, "Unknown"):
            manager.deactivate("automatic")

    @patch("snake_experiment_ui.mode_manager.os.killpg")
    def test_shutdown_stops_owned_process(self, killpg):
        names = []

        def create_process(*args, **kwargs):
            names.append("joystick_mapper")
            return FakeProcess(*args, **kwargs)

        manager = ModeManager(
            node_names=lambda: names,
            publish_mode_request=lambda _: None,
            popen_factory=create_process,
            sleep=lambda _: None,
        )
        manager.activate("snake")
        manager.shutdown()
        killpg.assert_called_once()
        self.assertEqual(manager.snapshot()["status"], "inactive")

    @patch("snake_experiment_ui.mode_manager.os.killpg")
    def test_deactivate_stops_only_the_requested_owned_mode(self, killpg):
        names = []

        def create_process(*args, **kwargs):
            names.append("joystick_mapper")
            return FakeProcess(*args, **kwargs)

        killpg.side_effect = lambda *_: names.clear()
        manager = ModeManager(
            node_names=lambda: names,
            publish_mode_request=lambda _: None,
            popen_factory=create_process,
            sleep=lambda _: None,
        )
        manager.activate("baseline")

        result = manager.deactivate("baseline")
        repeated = manager.deactivate("baseline")

        killpg.assert_called_once()
        self.assertEqual(result["status"], "inactive")
        self.assertIsNone(result["active_mode"])
        self.assertEqual(repeated["status"], "inactive")

    @patch("snake_experiment_ui.mode_manager.os.killpg")
    def test_deactivate_rejects_a_different_mode(self, killpg):
        names = []

        def create_process(*args, **kwargs):
            names.append("joystick_mapper")
            return FakeProcess(*args, **kwargs)

        manager = ModeManager(
            node_names=lambda: names,
            publish_mode_request=lambda _: None,
            popen_factory=create_process,
            sleep=lambda _: None,
        )
        manager.activate("baseline")

        with self.assertRaisesRegex(ModeError, "while active mode is baseline"):
            manager.deactivate("snake")

        killpg.assert_not_called()
        self.assertEqual(manager.snapshot()["active_mode"], "baseline")

    @patch("snake_experiment_ui.mode_manager.os.killpg")
    def test_deactivate_does_not_stop_an_external_mapper(self, killpg):
        manager = ModeManager(
            node_names=lambda: ["/joystick_mapper"],
            publish_mode_request=lambda _: None,
        )

        result = manager.deactivate("baseline")

        killpg.assert_not_called()
        self.assertEqual(result["status"], "inactive")


if __name__ == "__main__":
    unittest.main()

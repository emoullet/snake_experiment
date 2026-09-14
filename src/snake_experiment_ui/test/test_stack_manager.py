import unittest
from unittest.mock import patch

from snake_experiment_ui.stack_manager import StackError, StackManager


class FakeProcess:
    def __init__(self, command, start_new_session):
        self.command = command
        self.start_new_session = start_new_session
        self.pid = 4343
        self.return_code = None

    def poll(self):
        return self.return_code

    def wait(self, timeout):
        self.return_code = 0
        return 0


class StackManagerTest(unittest.TestCase):
    def test_start_is_idempotent_and_uses_simulation(self):
        names = []
        processes = []

        def create_process(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            names.append("cartesian_manager")
            return process

        manager = StackManager(
            node_names=lambda: names,
            popen_factory=create_process,
            sleep=lambda _: None,
        )
        first = manager.start()
        second = manager.start()

        self.assertEqual(first["status"], "active")
        self.assertEqual(second["status"], "active")
        self.assertEqual(len(processes), 1)
        self.assertEqual(
            processes[0].command,
            [
                "ros2",
                "launch",
                "snake_experiment_ui",
                "explorer.launch.py",
                "use_simulation:=true",
            ],
        )

    def test_external_stack_is_not_stopped(self):
        manager = StackManager(node_names=lambda: ["/cartesian_manager"])
        with self.assertRaisesRegex(StackError, "outside this interface"):
            manager.start()
        with self.assertRaisesRegex(StackError, "not started by this interface"):
            manager.stop()

    @patch("snake_experiment_ui.stack_manager.os.killpg")
    def test_stop_terminates_owned_stack(self, killpg):
        names = []

        def create_process(*args, **kwargs):
            names.append("cartesian_manager")
            return FakeProcess(*args, **kwargs)

        manager = StackManager(
            node_names=lambda: names,
            popen_factory=create_process,
            sleep=lambda _: None,
        )
        manager.start()
        result = manager.stop()

        killpg.assert_called_once()
        self.assertEqual(result["status"], "inactive")


if __name__ == "__main__":
    unittest.main()

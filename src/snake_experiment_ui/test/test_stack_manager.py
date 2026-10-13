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
        self.assertEqual(first["robot"], "explorer")
        self.assertIsNone(first["robot_ip"])
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

    def test_kinova_command_and_snapshot_in_both_environments(self):
        for simulated in (True, False):
            with self.subTest(simulated=simulated):
                names = []
                processes = []

                def create_process(*args, **kwargs):
                    process = FakeProcess(*args, **kwargs)
                    processes.append(process)
                    names.append("/cartesian_manager")
                    return process

                manager = StackManager(
                    node_names=lambda: names, robot="kinova", robot_ip="192.168.2.10",
                    use_simulation=simulated, popen_factory=create_process,
                )
                state = manager.start()
                manager.start()
                self.assertEqual(len(processes), 1)
                self.assertEqual(processes[0].command, [
                    "ros2", "launch", "snake_experiment_ui", "kinova.launch.py",
                    f"use_simulation:={str(simulated).lower()}", "robot_ip:=192.168.2.10",
                ])
                self.assertTrue(processes[0].start_new_session)
                self.assertEqual(state["robot"], "kinova")
                self.assertEqual(state["robot_ip"], "192.168.2.10")
                self.assertEqual(state["use_simulation"], simulated)

    def test_session_protection_is_explicit_and_start_hook_is_idempotent(self):
        names, processes, starts = [], [], []
        def create_process(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            names.append("cartesian_manager")
            return process
        manager = StackManager(node_names=lambda: names, robot="kinova", overload_protection=True,
            popen_factory=create_process, before_start=lambda: starts.append("start"))
        manager.start()
        manager.start()
        self.assertIn("overload_protection:=true", processes[0].command)
        self.assertEqual(processes[0].command[-1], "overload_retreat_delay_sec:=1.0")
        self.assertEqual(starts, ["start"])

    def test_retreat_delay_is_forwarded_to_both_robot_launches(self):
        for robot in ("explorer", "kinova"):
            for delay in (0.0, 1.0, 2.5):
                names, processes = [], []
                def create_process(*args, **kwargs):
                    process = FakeProcess(*args, **kwargs)
                    processes.append(process)
                    names.append("cartesian_manager")
                    return process
                manager = StackManager(node_names=lambda: names, robot=robot, overload_protection=True,
                    overload_retreat_delay_sec=delay, popen_factory=create_process)
                manager.start()
                self.assertIn(f"overload_retreat_delay_sec:={delay}", processes[0].command)

    def test_invalid_configuration_cannot_start_a_process(self):
        for value in (-1.0, float("inf"), float("nan")):
            with self.assertRaisesRegex(StackError, "Retreat delay"):
                StackManager(node_names=lambda: [], overload_retreat_delay_sec=value)
        configurations = [{"robot": "unknown"}] + [
            {"robot": "kinova", "robot_ip": address}
            for address in ("", "robot.local", "::1", "256.0.0.1", "192.168.1.10/24",
                            "192.168.1.10 robot_ip:=other", " 192.168.1.10")
        ]
        for configuration in configurations:
            with self.subTest(configuration=configuration):
                with patch("subprocess.Popen") as popen:
                    with self.assertRaises(StackError):
                        StackManager(node_names=lambda: [], **configuration)
                    popen.assert_not_called()

    def test_external_stack_is_not_stopped(self):
        for robot in ("explorer", "kinova"):
            with self.subTest(robot=robot):
                manager = StackManager(node_names=lambda: ["/cartesian_manager"], robot=robot)
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

import unittest
import subprocess
import threading
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
    def test_experiment_stop_returns_before_dds_cleanup_and_start_waits_without_snapshot_lock(self):
        processes, phases = [], []
        entered, release = threading.Event(), threading.Event()
        graph = []
        def create(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            graph[:] = ["cartesian_manager"]
            return process
        def wait(_):
            entered.set()
            release.wait(2)
        manager = StackManager(node_names=lambda: graph, popen_factory=create, sleep=wait)
        manager.start()
        with patch("snake_experiment_ui.stack_manager.os.killpg"):
            state = manager.stop_for_experiment()
        self.assertEqual(state["status"], "inactive")
        self.assertFalse(state["owned"])
        self.assertFalse(entered.is_set())
        outcomes = []
        worker = threading.Thread(target=lambda: outcomes.append(manager.start_for_experiment(phases.append)))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(phases, ["waiting_for_graph"])
            self.assertEqual(len(processes), 1)
            self.assertEqual(manager.snapshot()["status"], "inactive")
        finally:
            graph.clear()
            release.set()
            worker.join(2)
        self.assertEqual(phases, ["waiting_for_graph", "starting"])
        self.assertEqual(len(processes), 2)
        self.assertEqual(outcomes[0]["status"], "active")

    def test_cleanup_timeout_keeps_retry_marker_and_never_launches_or_kills_visible_node(self):
        processes, graph = [], []
        def create(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            graph[:] = ["cartesian_manager"]
            return process
        manager = StackManager(node_names=lambda: graph, popen_factory=create, sleep=lambda _: None,
                               startup_timeout_sec=.001, shutdown_timeout_sec=.001)
        manager.start()
        with patch("snake_experiment_ui.stack_manager.os.killpg") as kill:
            manager.stop_for_experiment()
            with self.assertRaisesRegex(StackError, "still visible"):
                manager.start_for_experiment(lambda _: None)
            self.assertEqual(len(processes), 1)
            self.assertEqual(kill.call_count, 1)
        self.assertFalse(manager.snapshot()["owned"])
        graph.clear()
        self.assertEqual(manager.start_for_experiment(lambda _: None)["status"], "active")
        self.assertEqual(len(processes), 2)

    def test_experiment_start_without_prior_owned_stop_refuses_external_node(self):
        manager = StackManager(node_names=lambda: ["cartesian_manager"])
        with patch("snake_experiment_ui.stack_manager.subprocess.Popen") as popen:
            with self.assertRaisesRegex(StackError, "outside this interface"):
                manager.start_for_experiment(lambda _: None)
        popen.assert_not_called()

    def test_failed_stop_preserves_owned_process_and_resumes_monitoring(self):
        events, graph = [], []
        def create(*args, **kwargs):
            graph[:] = ["cartesian_manager"]
            process = FakeProcess(*args, **kwargs)
            def wait(timeout):
                raise subprocess.TimeoutExpired("ros2", timeout)
            process.wait = wait
            return process
        manager = StackManager(node_names=lambda: graph, popen_factory=create, monitoring_changed=events.append)
        manager.start()
        with patch("snake_experiment_ui.stack_manager.os.killpg"):
            with self.assertRaisesRegex(StackError, "still running"):
                manager.stop_for_experiment()
        self.assertTrue(manager.snapshot()["owned"])
        self.assertEqual(manager.snapshot()["status"], "error")
        self.assertFalse(manager._cleanup_pending)
        self.assertEqual(events, [True, False, True])
        with self.assertRaisesRegex(StackError, "still running"):
            manager.start()

    def test_graph_cleanup_and_new_launch_have_separate_timeout_budgets(self):
        clock = [0.0]
        starts = []
        def names():
            if len(starts) == 1:
                return ["cartesian_manager"] if clock[0] < 14 else []
            if len(starts) == 2:
                return ["cartesian_manager"] if clock[0] >= starts[-1] + 14 else []
            return []
        def create(*args, **kwargs):
            starts.append(clock[0])
            return FakeProcess(*args, **kwargs)
        def wait(_):
            clock[0] += 1
        manager = StackManager(node_names=names, popen_factory=create, sleep=wait, startup_timeout_sec=15)
        with patch("snake_experiment_ui.stack_manager.time.monotonic", side_effect=lambda: clock[0]):
            manager.start()
            with patch("snake_experiment_ui.stack_manager.os.killpg"):
                manager.stop_for_experiment()
            self.assertEqual(manager.start_for_experiment(lambda _: None)["status"], "active")
        self.assertEqual(starts, [0, 14])
        self.assertEqual(clock[0], 28)

    def test_owned_stop_waits_for_stale_graph_before_restart(self):
        processes, waits = [], []
        stale_reads = 0
        def names():
            nonlocal stale_reads
            if not processes:
                return []
            if processes[-1].return_code is None:
                return ["cartesian_manager"]
            stale_reads += 1
            return ["cartesian_manager"] if stale_reads <= 2 else []
        def create_process(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            return process
        manager = StackManager(node_names=names, popen_factory=create_process, sleep=waits.append)
        manager.start()
        with patch("snake_experiment_ui.stack_manager.os.killpg"):
            state = manager.stop_and_wait()
        self.assertFalse(state["owned"])
        self.assertEqual(waits, [.05, .05])
        self.assertTrue(manager.start()["owned"])

    def test_owned_stop_graph_timeout_is_bounded_and_does_not_adopt_node(self):
        processes = []
        def create_process(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            return process
        manager = StackManager(node_names=lambda: ["cartesian_manager"] if processes else [],
                               popen_factory=create_process, sleep=lambda _: None,
                               startup_timeout_sec=.01, shutdown_timeout_sec=.01)
        manager.start()
        with patch("snake_experiment_ui.stack_manager.os.killpg") as kill:
            with self.assertRaisesRegex(StackError, "still visible"):
                manager.stop_and_wait()
        self.assertEqual(kill.call_count, 1)
        self.assertFalse(manager.snapshot()["owned"])
        self.assertEqual(manager.snapshot()["status"], "error")

    def test_stop_and_wait_never_kills_external_graph_node(self):
        manager = StackManager(node_names=lambda: ["cartesian_manager"])
        with patch("snake_experiment_ui.stack_manager.os.killpg") as kill:
            with self.assertRaisesRegex(StackError, "not started by this interface"):
                manager.stop_and_wait()
        kill.assert_not_called()
        self.assertFalse(manager.snapshot()["owned"])

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
        self.assertTrue(first["owned"])
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
                    go_to_linear_tolerance_m=0.001,
                    go_to_angular_tolerance_rad=0.017453292519943295,
                )
                state = manager.start()
                manager.start()
                self.assertEqual(len(processes), 1)
                self.assertEqual(processes[0].command, [
                    "ros2", "launch", "snake_experiment_ui", "kinova.launch.py",
                    f"use_simulation:={str(simulated).lower()}", "robot_ip:=192.168.2.10",
                    "go_to_linear_tolerance_m:=0.001",
                    "go_to_angular_tolerance_rad:=0.017453292519943295",
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
        for value in (0.0, -0.1, float("inf"), float("nan")):
            with self.assertRaisesRegex(StackError, "Go-to angular tolerance"):
                StackManager(
                    node_names=lambda: [], go_to_angular_tolerance_rad=value
                )
        for value in (0.0, -0.001, float("inf"), float("nan")):
            with self.assertRaisesRegex(StackError, "Go-to linear tolerance"):
                StackManager(node_names=lambda: [], go_to_linear_tolerance_m=value)
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

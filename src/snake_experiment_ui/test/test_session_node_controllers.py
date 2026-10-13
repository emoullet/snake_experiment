import threading
import queue
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from snake_experiment_ui.session_node import (
    CONTROLLER_REQUEST_TIMEOUT_SEC,
    SessionInterfaceNode,
)
from snake_experiment_ui.checkup import CheckupController
from snake_experiment_ui.mode_manager import ModeManager
from snake_experiment_ui.experiment import ExperimentController


class ProtectionCallbackConcurrencyTest(unittest.TestCase):
    def test_startup_warning_cleanup_does_not_block_controller_configuration(self):
        experiment = object.__new__(ExperimentController)
        experiment._lock = threading.RLock()
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        def lifecycle():
            with experiment._lock:
                entered.set()
                release.wait(2)
        def cleanup():
            experiment.clear_initial_feedback_warning({"fresh": True, "state": "normal", "blocked": False,
                                                      "pose_valid": True, "overload_valid": True})
            returned.set()
        action = threading.Thread(target=lifecycle)
        action.start()
        self.assertTrue(entered.wait(1))
        worker = threading.Thread(target=cleanup)
        worker.start()
        try:
            self.assertTrue(returned.wait(.2), "Protection worker blocked on workflow lock")
        finally:
            release.set()
            action.join(1)
            worker.join(1)

    def test_worker_skips_only_idle_startup_and_keeps_configuration_running(self):
        startup = {"fresh": True, "state": "normal", "event": 0, "instance": "initial",
                   "reason": "waiting_for_feedback", "blocked": True}
        for kind in ("idle", "restarting", "mapper", "go_to", "acquisition", "stale", "overload", "fault", "cleanup"):
            with self.subTest(kind=kind):
                state = dict(startup)
                if kind == "stale":
                    state.update(fresh=False, state="unavailable", reason="no_fresh_status")
                elif kind == "overload":
                    state.update(event=1, state="locked", reason="overload")
                elif kind == "fault":
                    state.update(state="error", reason="invalid_pose")
                shutdown = Mock()
                shutdown.wait.side_effect = [False, True, True]
                node = SimpleNamespace(
                    _protection_shutdown=shutdown, _protection_events=queue.Queue(),
                    _protection=Mock(robot="kinova"), _stack_manager=Mock(),
                    _experiment=Mock(), _checkup=Mock(), _mode_manager=Mock(),
                    _checkup_go_to=Mock(), _experiment_go_to=Mock(),
                    _repository_root=Path("/tmp"), _protection_cleanup_pending=kind == "cleanup",
                    _protection_restarting=kind == "restarting", _protection_configuration="pending",
                    _protection_parameters=Mock(), _publish_passthrough=Mock(),
                    _protection_calibration_file=Path("/tmp/unused-startup-calibration"),
                    get_logger=Mock(),
                )
                node._protection.snapshot.return_value = state
                node._stack_manager.snapshot.return_value = {"status": "active"}
                node._experiment.protection_context.return_value = {"calibration": {}}
                node._experiment.protection_startup_is_idle.return_value = kind not in ("acquisition", "stale", "overload", "fault")
                node._mode_manager.active_mode.return_value = "snake" if kind == "mapper" else None
                node._checkup_go_to.active.return_value = kind == "go_to"
                node._experiment_go_to.active.return_value = False
                node._protection_parameters.service_is_ready.return_value = False
                with patch("snake_experiment_ui.session_node.EventJournal"):
                    SessionInterfaceNode._protection_worker(node)
                node.get_logger().error.assert_not_called()
                if kind in ("idle", "restarting"):
                    node._experiment.interrupt_overload.assert_not_called()
                    node._checkup.interrupt_overload.assert_not_called()
                    node._publish_passthrough.assert_not_called()
                    node._protection_parameters.service_is_ready.assert_called_once()
                    if kind == "restarting":
                        node._mode_manager.active_mode.assert_not_called()
                else:
                    node._experiment.interrupt_overload.assert_called_once_with(state)
                node._experiment.clear_initial_feedback_warning.assert_called_once_with(state)

    def test_go_to_restoration_does_not_block_feedback_dispatch(self):
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            node = SimpleNamespace(_closing=False, _go_to_finish_executor=executor, get_logger=Mock())
            node._run_go_to_finish = lambda callback, record: SessionInterfaceNode._run_go_to_finish(node, callback, record)
            def restore(record):
                entered.set()
                release.wait(2)
            def feedback():
                SessionInterfaceNode._dispatch_go_to_finish(node, restore, {"status": "succeeded"})
                returned.set()
            thread = threading.Thread(target=feedback)
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(returned.wait(.2), "Feedback waited for mapper restoration")
            finally:
                release.set()
                thread.join(1)

    def test_ros_callbacks_do_not_wait_for_mode_and_checkup_lifecycle_locks(self):
        modes = ModeManager(node_names=lambda: [], publish_mode_request=lambda _: None)
        checkup = object.__new__(CheckupController)
        checkup._lock = threading.RLock()
        checkup._go_to = Mock()
        experiment = object.__new__(ExperimentController)
        experiment._lock = threading.RLock()
        experiment._mapper_local_mode_lock = threading.Lock()
        experiment._mapper_local_mode = None
        experiment.update_ee_pose = Mock()
        node = SimpleNamespace(
            _mapper_client_reset_requested=threading.Event(),
            _refresh_controllers=Mock(), _mode_manager=modes,
            _parameter_list_client=Mock(), _parameter_get_client=Mock(),
            _checkup=checkup, _experiment=experiment, _diagnostics=Mock(),
            _protection=Mock(),
        )
        node._protection.snapshot.return_value = {"blocked": False}
        for mode in ("baseline", "snake"):
            with self.subTest(mode=mode):
                locked, release, callbacks_done = threading.Event(), threading.Event(), threading.Event()
                def lifecycle():
                    with checkup._lock, modes._lock, experiment._lock:
                        modes._active_mode, modes._status = mode, "starting"
                        locked.set()
                        release.wait(2)
                def ros_callbacks():
                    SessionInterfaceNode._refresh_services(node)
                    SessionInterfaceNode._record_ee_pose(node, object())
                    SessionInterfaceNode._record_mapper_local_mode(node, SimpleNamespace(data="b1"))
                    callbacks_done.set()
                action = threading.Thread(target=lifecycle)
                action.start()
                self.assertTrue(locked.wait(1))
                callback = threading.Thread(target=ros_callbacks)
                callback.start()
                try:
                    self.assertTrue(callbacks_done.wait(.2), "ROS executor blocked behind lifecycle locks")
                    checkup._go_to.update_pose.assert_not_called()
                    experiment.update_ee_pose.assert_not_called()
                    self.assertEqual(experiment._mapper_local_mode, "b1")
                    node._parameter_list_client.service_is_ready.assert_not_called()
                finally:
                    release.set()
                    action.join(1)
                    callback.join(1)
        checkup.try_update_ee_pose("fresh pose")
        checkup._go_to.update_pose.assert_called_once_with("fresh pose")
        modes._status, modes._active_mode = "active", "snake"
        self.assertEqual(modes.try_active_mode(), "snake")


class FakeFuture:
    def __init__(self):
        self.callback = None
        self.response = None

    def add_done_callback(self, callback):
        self.callback = callback

    def result(self):
        return self.response

    def complete(self, controllers):
        self.response = SimpleNamespace(
            controller=[SimpleNamespace(name=name, state=state) for name, state in controllers.items()]
        )
        self.callback(self)


class FakeClient:
    def __init__(self, ready=True):
        self.ready = ready
        self.futures = []
        self.removed = []

    def service_is_ready(self):
        return self.ready

    def call_async(self, request):
        future = FakeFuture()
        self.futures.append(future)
        return future

    def remove_pending_request(self, future):
        self.removed.append(future)


class ControllerPollingTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.replacement_client = FakeClient()
        self.node = SimpleNamespace(
            _controller_client=self.client,
            _controller_future=None,
            _controller_future_started_at=None,
            _controller_client_created_at=100.0,
            _controller_client_generation=0,
            _graph_lock=threading.RLock(),
            _graph={"controllers": {}, "nodes": ["/controller_manager"]},
            get_logger=Mock(return_value=SimpleNamespace(warning=Mock())),
            destroy_client=Mock(),
            create_client=Mock(return_value=self.replacement_client),
        )
        self.node._controllers_received = lambda future, generation: (
            SessionInterfaceNode._controllers_received(self.node, future, generation)
        )
        self.node._reconnect_controller_client = lambda now: (
            SessionInterfaceNode._reconnect_controller_client(self.node, now)
        )

    def test_controller_states_are_updated_and_polled_again(self):
        with patch("snake_experiment_ui.session_node.time.monotonic", return_value=100.0):
            SessionInterfaceNode._refresh_controllers(self.node)
        self.client.futures[0].complete({"qontrol_explorer": "active"})
        self.assertEqual(self.node._graph["controllers"], {"qontrol_explorer": "active"})
        self.assertIsNone(self.node._controller_future)

        with patch("snake_experiment_ui.session_node.time.monotonic", return_value=101.0):
            SessionInterfaceNode._refresh_controllers(self.node)
        self.assertEqual(len(self.client.futures), 2)

    def test_stalled_request_is_replaced_and_late_response_ignored(self):
        with patch("snake_experiment_ui.session_node.time.monotonic", return_value=100.0):
            SessionInterfaceNode._refresh_controllers(self.node)
        stalled = self.client.futures[0]
        self.node._graph["controllers"] = {"qontrol_explorer": "active"}

        with patch(
            "snake_experiment_ui.session_node.time.monotonic",
            return_value=100.0 + CONTROLLER_REQUEST_TIMEOUT_SEC,
        ):
            SessionInterfaceNode._refresh_controllers(self.node)
        self.assertEqual(self.client.removed, [stalled])
        self.node.destroy_client.assert_called_once_with(self.client)
        self.assertEqual(self.node._graph["controllers"], {})
        self.assertEqual(len(self.replacement_client.futures), 1)

        stalled.complete({"qontrol_explorer": "active"})
        self.assertEqual(self.node._graph["controllers"], {})
        self.replacement_client.futures[0].complete(
            {
                "joint_state_broadcaster": "active",
                "qontrol_explorer": "active",
                "gripper_controller": "active",
            }
        )
        self.assertEqual(len(self.node._graph["controllers"]), 3)

    def test_unavailable_service_clears_old_controller_states(self):
        self.client.ready = False
        self.node._graph["controllers"] = {"qontrol_explorer": "active"}
        with patch("snake_experiment_ui.session_node.time.monotonic", return_value=101.0):
            SessionInterfaceNode._refresh_controllers(self.node)
        self.assertEqual(self.node._graph["controllers"], {})
        self.assertEqual(self.client.futures, [])

        with patch(
            "snake_experiment_ui.session_node.time.monotonic",
            return_value=100.0 + CONTROLLER_REQUEST_TIMEOUT_SEC,
        ):
            SessionInterfaceNode._refresh_controllers(self.node)
        self.node.destroy_client.assert_called_once_with(self.client)
        self.assertIs(self.node._controller_client, self.replacement_client)


if __name__ == "__main__":
    unittest.main()

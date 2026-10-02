import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from snake_experiment_ui.session_node import (
    CONTROLLER_REQUEST_TIMEOUT_SEC,
    SessionInterfaceNode,
)


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

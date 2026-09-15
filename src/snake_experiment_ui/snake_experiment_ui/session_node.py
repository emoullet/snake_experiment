"""ROS node hosting the independent Snake experiment Panels B-E interface."""

from __future__ import annotations

import os
from pathlib import Path
import threading

from ament_index_python.packages import get_package_share_directory
from controller_manager_msgs.srv import ListControllers
from geometry_msgs.msg import PoseStamped, TwistStamped
import rclpy
from rcl_interfaces.msg import ParameterType
from rcl_interfaces.srv import GetParameters, ListParameters
from rclpy.node import Node
from sensor_msgs.msg import JointState, Joy
from std_msgs.msg import String
import uvicorn

from .checkup import CheckupController
from .diagnostics import DiagnosticMonitor, DiagnosticProfile, collect_git_provenance
from .mode_manager import ModeManager
from .session_web_app import create_session_app
from .stack_manager import StackManager


class SessionInterfaceNode(Node):
    """Bridge Panel B state, ROS diagnostics, and launch ownership to the web UI."""

    def __init__(self) -> None:
        super().__init__("snake_experiment_session_interface")
        self.declare_parameter("host", "127.0.0.1")
        self.declare_parameter("port", 8081)
        self.declare_parameter("stack_use_simulation", False)
        self.declare_parameter("diagnostic_profile", "")
        self.declare_parameter("measurement_window_sec", 2.0)
        self.declare_parameter("repository_root", "")
        self.declare_parameter("mode_startup_timeout_sec", 5.0)
        self.declare_parameter("mode_shutdown_timeout_sec", 5.0)
        self.declare_parameter("stack_startup_timeout_sec", 30.0)
        self.declare_parameter("stack_shutdown_timeout_sec", 10.0)

        share = Path(get_package_share_directory("snake_experiment_ui"))
        configured_profile = str(self.get_parameter("diagnostic_profile").value)
        profile_path = (
            Path(configured_profile) if configured_profile else share / "config/system_checkup.yaml"
        )
        self._profile = DiagnosticProfile(profile_path)
        measurement_window = float(
            self.get_parameter("measurement_window_sec").value
        )
        if measurement_window <= 0:
            raise ValueError("measurement_window_sec must be positive")
        self._profile.measurement_window_sec = measurement_window
        configured_repository = str(self.get_parameter("repository_root").value)
        self._repository_root = (
            Path(configured_repository) if configured_repository else Path.cwd()
        ).resolve()
        self._graph_lock = threading.RLock()
        self._graph = {
            "nodes": [],
            "topics": {},
            "controllers": {},
            "mapper_modes": [],
            "mapper_parameter_names": [],
            "mapper_parameters": {},
        }
        self._controller_future = None
        self._parameter_list_future = None
        self._parameter_get_future = None
        self._mapper_client_generation = 0
        self._mapper_client_reset_requested = threading.Event()

        self._mode_publisher = self.create_publisher(String, "/mode_request", 10)
        self._active_mode_request = None
        self._mode_manager = ModeManager(
            node_names=self._node_names,
            publish_mode_request=self._publish_mode_request,
            mapper_transition=self._request_mapper_client_reset,
            startup_timeout_sec=float(
                self.get_parameter("mode_startup_timeout_sec").value
            ),
            shutdown_timeout_sec=float(
                self.get_parameter("mode_shutdown_timeout_sec").value
            ),
        )
        use_simulation = bool(self.get_parameter("stack_use_simulation").value)
        self._stack_manager = StackManager(
            node_names=self._node_names,
            use_simulation=use_simulation,
            startup_timeout_sec=float(
                self.get_parameter("stack_startup_timeout_sec").value
            ),
            shutdown_timeout_sec=float(
                self.get_parameter("stack_shutdown_timeout_sec").value
            ),
        )
        self._diagnostics = DiagnosticMonitor(self._profile, self._graph_snapshot)

        self._subscriptions = [
            self.create_subscription(
                JointState,
                "/joint_states",
                lambda message: self._diagnostics.record("/joint_states", message),
                50,
            ),
            self.create_subscription(
                PoseStamped,
                "/ee_pose",
                lambda message: self._diagnostics.record("/ee_pose", message),
                50,
            ),
            self.create_subscription(
                Joy,
                "/joy",
                lambda message: self._diagnostics.record("/joy", message),
                50,
            ),
            self.create_subscription(
                TwistStamped,
                "/joystick_cartesian_command",
                lambda message: self._diagnostics.record(
                    "/joystick_cartesian_command", message
                ),
                50,
            ),
            self.create_subscription(
                TwistStamped,
                "/cartesian_command",
                lambda message: self._diagnostics.record("/cartesian_command", message),
                50,
            ),
            self.create_subscription(
                String,
                "/mode_request",
                lambda message: self._diagnostics.record("/mode_request", message),
                10,
            ),
        ]
        self._controller_client = self.create_client(
            ListControllers, "/controller_manager/list_controllers"
        )
        self._parameter_list_client = self.create_client(
            ListParameters, "/joystick_mapper/list_parameters"
        )
        self._parameter_get_client = self.create_client(
            GetParameters, "/joystick_mapper/get_parameters"
        )
        self._graph_timer = self.create_timer(0.5, self._refresh_graph)
        self._service_timer = self.create_timer(1.0, self._refresh_services)
        self._mode_refresh_timer = self.create_timer(1.0, self._refresh_mode_request)

        self._checkup = CheckupController(
            stack_manager=self._stack_manager,
            mode_manager=self._mode_manager,
            diagnostics=self._diagnostics,
            provenance_provider=lambda: collect_git_provenance(self._repository_root),
            use_simulation=use_simulation,
            ros_distro=os.environ.get("ROS_DISTRO", "unknown"),
        )
        app = create_session_app(
            self._checkup,
            static_directory=share / "static",
            template_directory=share / "templates",
        )
        config = uvicorn.Config(
            app,
            host=str(self.get_parameter("host").value),
            port=int(self.get_parameter("port").value),
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        self._server_thread = threading.Thread(
            target=self._server.run,
            name="session-interface-web-server",
            daemon=True,
        )
        self._server_thread.start()
        host = self.get_parameter("host").value
        port = self.get_parameter("port").value
        self.get_logger().info(f"Panels B-E available at http://{host}:{port}")

    def _node_names(self):
        return [
            f"{namespace.rstrip('/')}/{name}" if namespace != "/" else name
            for name, namespace in self.get_node_names_and_namespaces()
        ]

    def _refresh_graph(self) -> None:
        with self._graph_lock:
            self._graph["nodes"] = self._node_names()
            self._graph["topics"] = dict(self.get_topic_names_and_types())

    def _graph_snapshot(self) -> dict:
        with self._graph_lock:
            return {
                key: value.copy() if isinstance(value, (dict, list)) else value
                for key, value in self._graph.items()
            }

    def _refresh_services(self) -> None:
        if self._mapper_client_reset_requested.is_set():
            self._reset_mapper_parameter_clients()
        if self._controller_client.service_is_ready() and self._controller_future is None:
            self._controller_future = self._controller_client.call_async(
                ListControllers.Request()
            )
            self._controller_future.add_done_callback(self._controllers_received)
        active_mode = self._mode_manager.active_mode()
        if (
            active_mode is not None
            and self._parameter_list_client.service_is_ready()
            and self._parameter_list_future is None
        ):
            request = ListParameters.Request()
            request.depth = 0
            self._parameter_list_future = self._parameter_list_client.call_async(request)
            generation = self._mapper_client_generation
            self._parameter_list_future.add_done_callback(
                lambda future, generation=generation: self._parameter_names_received(
                    future, generation
                )
            )
        if (
            active_mode is not None
            and self._parameter_get_client.service_is_ready()
            and self._parameter_get_future is None
        ):
            request = GetParameters.Request()
            request.names = sorted(
                self._profile.modes[active_mode].mapper_parameters
            )
            self._parameter_get_future = self._parameter_get_client.call_async(request)
            generation = self._mapper_client_generation
            self._parameter_get_future.add_done_callback(
                lambda future,
                names=request.names,
                generation=generation: self._mapper_parameters_received(
                    future, names, generation
                )
            )

    def _request_mapper_client_reset(self) -> None:
        with self._graph_lock:
            self._graph["mapper_modes"] = []
            self._graph["mapper_parameter_names"] = []
            self._graph["mapper_parameters"] = {}
        self._mapper_client_reset_requested.set()

    def _reset_mapper_parameter_clients(self) -> None:
        self._mapper_client_generation += 1
        self._mapper_client_reset_requested.clear()
        self._parameter_list_future = None
        self._parameter_get_future = None
        self.destroy_client(self._parameter_list_client)
        self.destroy_client(self._parameter_get_client)
        self._parameter_list_client = self.create_client(
            ListParameters, "/joystick_mapper/list_parameters"
        )
        self._parameter_get_client = self.create_client(
            GetParameters, "/joystick_mapper/get_parameters"
        )

    def _controllers_received(self, future) -> None:
        try:
            response = future.result()
            controllers = {item.name: item.state for item in response.controller}
            with self._graph_lock:
                self._graph["controllers"] = controllers
        except Exception as error:  # service failures are exposed as missing checks
            self.get_logger().warning(f"Unable to list controllers: {error}")
        finally:
            self._controller_future = None

    def _parameter_names_received(self, future, generation) -> None:
        if generation != self._mapper_client_generation:
            return
        try:
            names = list(future.result().result.names)
            with self._graph_lock:
                self._graph["mapper_parameter_names"] = names
        except Exception as error:
            self.get_logger().warning(f"Unable to list mapper parameters: {error}")
        finally:
            if generation == self._mapper_client_generation:
                self._parameter_list_future = None

    def _mapper_parameters_received(self, future, names, generation) -> None:
        if generation != self._mapper_client_generation:
            return
        try:
            values = future.result().values
            parameters = {
                name: self._parameter_value(value)
                for name, value in zip(names, values)
                if value.type != ParameterType.PARAMETER_NOT_SET
            }
            with self._graph_lock:
                self._graph["mapper_parameters"] = parameters
                self._graph["mapper_modes"] = parameters.get("modes.names", [])
        except Exception as error:
            self.get_logger().warning(f"Unable to read mapper parameters: {error}")
        finally:
            if generation == self._mapper_client_generation:
                self._parameter_get_future = None

    @staticmethod
    def _parameter_value(value):
        fields = {
            ParameterType.PARAMETER_BOOL: "bool_value",
            ParameterType.PARAMETER_INTEGER: "integer_value",
            ParameterType.PARAMETER_DOUBLE: "double_value",
            ParameterType.PARAMETER_STRING: "string_value",
            ParameterType.PARAMETER_BYTE_ARRAY: "byte_array_value",
            ParameterType.PARAMETER_BOOL_ARRAY: "bool_array_value",
            ParameterType.PARAMETER_INTEGER_ARRAY: "integer_array_value",
            ParameterType.PARAMETER_DOUBLE_ARRAY: "double_array_value",
            ParameterType.PARAMETER_STRING_ARRAY: "string_array_value",
        }
        field = fields.get(value.type)
        if field is None:
            return None
        observed = getattr(value, field)
        return list(observed) if value.type >= ParameterType.PARAMETER_BYTE_ARRAY else observed

    def _publish_mode_request(self, request: str) -> None:
        self._active_mode_request = request
        message = String()
        message.data = request
        self._mode_publisher.publish(message)
        self.get_logger().info(f"Published control mode request: {request}")

    def _refresh_mode_request(self) -> None:
        if self._active_mode_request is None or self._mode_manager.active_mode() is None:
            return
        message = String()
        message.data = self._active_mode_request
        self._mode_publisher.publish(message)

    def destroy_node(self):
        self._checkup.shutdown()
        self._server.should_exit = True
        if self._server_thread.is_alive():
            self._server_thread.join(timeout=3.0)
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = SessionInterfaceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

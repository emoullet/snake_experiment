"""ROS node hosting the Snake experiment calibration view."""

from __future__ import annotations

from pathlib import Path
import threading

from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import PoseStamped
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import uvicorn

from .calibration import CalibrationState
from .mode_manager import ModeManager
from .stack_manager import StackManager
from .storage import CalibrationStorage
from .web_app import create_app


class TargetCalibrationNode(Node):
    """Bridge ROS state and launch control to the local operator view."""

    def __init__(self) -> None:
        super().__init__("snake_experiment_target_calibration")
        self.declare_parameter("host", "127.0.0.1")
        self.declare_parameter("port", 8080)
        self.declare_parameter("pose_topic", "/ee_pose")
        self.declare_parameter("pose_max_age_sec", 1.0)
        self.declare_parameter("mode_startup_timeout_sec", 5.0)
        self.declare_parameter("mode_shutdown_timeout_sec", 5.0)
        self.declare_parameter("stack_use_simulation", True)
        self.declare_parameter("stack_startup_timeout_sec", 15.0)
        self.declare_parameter("stack_shutdown_timeout_sec", 10.0)

        pose_topic = str(self.get_parameter("pose_topic").value)
        self._mode_publisher = self.create_publisher(String, "/mode_request", 10)
        self._active_mode_request = None
        self._mode_refresh_timer = self.create_timer(1.0, self._refresh_mode_request)
        self._mode_manager = ModeManager(
            node_names=self._node_names,
            publish_mode_request=self._publish_mode_request,
            startup_timeout_sec=float(
                self.get_parameter("mode_startup_timeout_sec").value
            ),
            shutdown_timeout_sec=float(
                self.get_parameter("mode_shutdown_timeout_sec").value
            ),
        )
        self._stack_manager = StackManager(
            node_names=self._node_names,
            use_simulation=bool(self.get_parameter("stack_use_simulation").value),
            startup_timeout_sec=float(
                self.get_parameter("stack_startup_timeout_sec").value
            ),
            shutdown_timeout_sec=float(
                self.get_parameter("stack_shutdown_timeout_sec").value
            ),
        )
        self._calibration_state = CalibrationState(
            max_pose_age_sec=float(self.get_parameter("pose_max_age_sec").value),
            mode_provider=self._mode_manager.active_mode,
        )
        self._pose_subscription = self.create_subscription(
            PoseStamped,
            pose_topic,
            self._calibration_state.update_live_pose,
            10,
        )
        self._storage = CalibrationStorage.from_working_directory(pose_topic)

        share = Path(get_package_share_directory("snake_experiment_ui"))
        app = create_app(
            calibration_state=self._calibration_state,
            mode_manager=self._mode_manager,
            stack_manager=self._stack_manager,
            storage=self._storage,
            static_directory=share / "static",
            template_directory=share / "templates",
            pose_topic=pose_topic,
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
            name="target-calibration-web-server",
            daemon=True,
        )
        self._server_thread.start()
        host = self.get_parameter("host").value
        port = self.get_parameter("port").value
        self.get_logger().info(f"Target calibration available at http://{host}:{port}")

    def _node_names(self):
        return [
            f"{namespace.rstrip('/')}/{name}" if namespace != "/" else name
            for name, namespace in self.get_node_names_and_namespaces()
        ]

    def _publish_mode_request(self, request: str) -> None:
        self._active_mode_request = request
        message = String()
        message.data = request
        self._mode_publisher.publish(message)
        self.get_logger().info(f"Published control mode request: {request}")

    def _refresh_mode_request(self) -> None:
        if (
            self._active_mode_request is None
            or self._mode_manager.active_mode() is None
        ):
            return
        message = String()
        message.data = self._active_mode_request
        self._mode_publisher.publish(message)

    def destroy_node(self):
        self._mode_manager.shutdown()
        self._stack_manager.shutdown()
        self._server.should_exit = True
        if self._server_thread.is_alive():
            self._server_thread.join(timeout=3.0)
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = TargetCalibrationNode()
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

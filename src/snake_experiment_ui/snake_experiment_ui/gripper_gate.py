"""Session gripper relay: discard commands on overload or lost protection state."""
import math
import time

from .overload import ProtectionState
from .robot_preview import robot_definition


class GripperGate:
    """Relay gripper input only while overload protection is healthy."""

    def __init__(self, robot, publish, clock=time.monotonic):
        self.status = ProtectionState(robot, clock)
        self._joint = robot_definition(robot).gripper_joint
        self._publish, self._clock = publish, clock
        self._position, self._position_at = None, None
        self._joint_valid = False
        self._was_blocked = True

    def joints(self, message):
        try:
            value = float(message.position[list(message.name).index(self._joint)])
            if math.isfinite(value):
                self._position, self._position_at = value, self._clock()
                self._joint_valid = True
            else:
                self._joint_valid = False
        except (ValueError, IndexError, TypeError):
            self._joint_valid = False

    def tick(self):
        # Detect a lost gripper stream slightly before the protection status's 0.5 s
        # freshness timeout, so a recently measured hold position is available.
        joint_fresh = self._joint_valid and self._position_at is not None and self._clock() - self._position_at <= 0.4
        blocked = self.status.snapshot()["blocked"] or not joint_fresh
        if blocked and not self._was_blocked:
            if self._position_at is not None and self._clock() - self._position_at <= 0.5:
                self._publish([self._position])
        self._was_blocked = blocked
        return blocked

    def command(self, message):
        if self.tick():
            return
        if len(message.data) == 1 and math.isfinite(message.data[0]):
            self._publish(list(message.data))


def main(args=None):
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from diagnostic_msgs.msg import DiagnosticArray
    from sensor_msgs.msg import JointState
    from std_msgs.msg import Float64MultiArray

    rclpy.init(args=args)
    node = Node("session_gripper_gate")
    node.declare_parameter("robot", "explorer")
    publisher = node.create_publisher(Float64MultiArray, "/_session_control/gripper_commands", 1)

    def publish(values):
        publisher.publish(Float64MultiArray(data=values))

    gate = GripperGate(node.get_parameter("robot").value, publish)
    subscriptions = [
        node.create_subscription(Float64MultiArray, "/gripper_controller/commands", gate.command, 1),
        node.create_subscription(JointState, "/joint_states", gate.joints, 1),
        node.create_subscription(DiagnosticArray, "/cartesian_manager/overload_status",
            lambda message: (gate.status.record(message, node.get_clock().now().nanoseconds * 1e-9), gate.tick()),
            QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)),
    ]
    timer = node.create_timer(0.02, gate.tick)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

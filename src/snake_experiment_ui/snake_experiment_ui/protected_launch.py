"""Session-only controller parameters and protected gripper routing."""
from pathlib import Path
import tempfile

import yaml
from launch.actions import TimerAction, RegisterEventHandler, OpaqueFunction
from launch.event_handlers import OnShutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def protected_controllers(context, robot):
    protected = LaunchConfiguration("overload_protection").perform(context) == "true"
    simulation = LaunchConfiguration("use_simulation").perform(context) == "true"
    arguments = ["qontrol_explorer", "--controller-manager", "/controller_manager"]
    if protected:
        # Load the overlay before configuration/activation; never enable protection
        # after activation, when a user command could already have been accepted.
        with tempfile.NamedTemporaryFile(mode="w", prefix="snake-protection-", suffix=".yaml", delete=False) as stream:
            yaml.safe_dump({"qontrol_explorer": {"ros__parameters": {
                "overload_protection.enabled": True,
                "overload_protection.robot": robot,
                "overload_protection.use_simulation": simulation,
            }}}, stream)
            overlay = stream.name
        arguments += ["--param-file", overlay]
    gripper_arguments = ["gripper_controller", "--controller-manager", "/controller_manager"]
    if protected:
        gripper_arguments += ["--controller-ros-args=--remap", "--controller-ros-args=~/commands:=/_session_control/gripper_commands"]
    actions = [
        TimerAction(period=2.0, actions=[Node(package="controller_manager", executable="spawner", arguments=arguments, output="screen")]),
        Node(package="controller_manager", executable="spawner", arguments=gripper_arguments, output="screen"),
    ]
    if protected:
        actions.append(RegisterEventHandler(OnShutdown(on_shutdown=[OpaqueFunction(function=lambda context: Path(overlay).unlink(missing_ok=True))])))
        actions.append(Node(package="snake_experiment_ui", executable="gripper_gate_node", parameters=[{"robot": robot}], output="screen"))
    return actions

"""Session manager protection and protected gripper routing."""
from pathlib import Path
import yaml
from ament_index_python.packages import get_package_share_directory
from launch.actions import TimerAction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def manager_protection_parameters(robot):
    """Use the exact legacy-controller gains and transport topics from its profile."""
    share = Path(get_package_share_directory("snake_experiment_ui"))
    profile = yaml.safe_load((share / "bringup/cartesian_manager/config" / f"{robot}_params.yaml").read_text())
    controller = profile["qontrol_explorer"]["ros__parameters"]
    return {
        "overload_protection.enabled": ParameterValue(LaunchConfiguration("overload_protection"), value_type=bool),
        "overload_protection.use_simulation": ParameterValue(LaunchConfiguration("use_simulation"), value_type=bool),
        "overload_protection.robot": robot,
        "overload_retreat_strategy": "closest_target_out",
        "overload_protection.controller_linear_gain": float(controller["command_max_linear_velocity"]),
        "overload_protection.controller_angular_gain": float(controller["command_max_angular_velocity"]),
        "overload_protection.linear_gain_topic": controller["topic_max_linear_velocity"],
        "overload_protection.angular_gain_topic": controller["topic_max_angular_velocity"],
        "overload_protection.control_frame_topic": controller["topic_control_frame"],
    }


def protected_controllers(context, robot):
    protected = LaunchConfiguration("overload_protection").perform(context) == "true"
    simulation = LaunchConfiguration("use_simulation").perform(context) == "true"
    arguments = ["qontrol_explorer", "--controller-manager", "/controller_manager"]
    if protected and simulation:
        # Fake hardware does not model contact effort. Keep the public topic for
        # explicit test injections and isolate the legacy controller's estimate.
        arguments += ["--controller-ros-args=--remap", "--controller-ros-args=~/effort_overload:=/_session_control/calculated_effort_overload"]
    gripper_arguments = ["gripper_controller", "--controller-manager", "/controller_manager"]
    if protected:
        gripper_arguments += ["--controller-ros-args=--remap", "--controller-ros-args=~/commands:=/_session_control/gripper_commands"]
    actions = [
        TimerAction(period=2.0, actions=[Node(package="controller_manager", executable="spawner", arguments=arguments, output="screen")]),
        Node(package="controller_manager", executable="spawner", arguments=gripper_arguments, output="screen"),
    ]
    if protected:
        actions.append(Node(package="snake_experiment_ui", executable="gripper_gate_node", parameters=[{"robot": robot}], output="screen"))
    return actions

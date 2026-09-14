"""Launch the experiment joystick mapper for baseline control."""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config_file = PathJoinSubstitution(
        [FindPackageShare("snake_experiment_ui"), "config", "joystick_2d_baseline.yaml"]
    )
    return LaunchDescription(
        [
            Node(
                package="joystick_mapper",
                executable="joystick_mapper_node",
                name="joystick_mapper",
                output="screen",
                parameters=[config_file],
            )
        ]
    )

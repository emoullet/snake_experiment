"""Launch the Snake experiment target calibration interface."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    arguments = [
        DeclareLaunchArgument("host", default_value="127.0.0.1"),
        DeclareLaunchArgument("port", default_value="8080"),
        DeclareLaunchArgument("pose_topic", default_value="/ee_pose"),
        DeclareLaunchArgument("pose_max_age_sec", default_value="1.0"),
        DeclareLaunchArgument("stack_use_simulation", default_value="true"),
        DeclareLaunchArgument("stack_startup_timeout_sec", default_value="15.0"),
        DeclareLaunchArgument("stack_shutdown_timeout_sec", default_value="10.0"),
    ]
    node = Node(
        package="snake_experiment_ui",
        executable="target_calibration_node",
        name="snake_experiment_target_calibration",
        output="screen",
        parameters=[
            {
                "host": LaunchConfiguration("host"),
                "port": LaunchConfiguration("port"),
                "pose_topic": LaunchConfiguration("pose_topic"),
                "pose_max_age_sec": LaunchConfiguration("pose_max_age_sec"),
                "stack_use_simulation": LaunchConfiguration(
                    "stack_use_simulation"
                ),
                "stack_startup_timeout_sec": LaunchConfiguration(
                    "stack_startup_timeout_sec"
                ),
                "stack_shutdown_timeout_sec": LaunchConfiguration(
                    "stack_shutdown_timeout_sec"
                ),
            }
        ],
    )
    return LaunchDescription(arguments + [node])

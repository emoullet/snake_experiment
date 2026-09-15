"""Launch the independent Panels B-E Snake experiment interface."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    arguments = [
        DeclareLaunchArgument("host", default_value="127.0.0.1"),
        DeclareLaunchArgument("port", default_value="8081"),
        DeclareLaunchArgument("stack_use_simulation", default_value="false"),
        DeclareLaunchArgument("diagnostic_profile", default_value=""),
        DeclareLaunchArgument("measurement_window_sec", default_value="2.0"),
        DeclareLaunchArgument("repository_root", default_value=""),
        DeclareLaunchArgument("mode_startup_timeout_sec", default_value="5.0"),
        DeclareLaunchArgument("mode_shutdown_timeout_sec", default_value="5.0"),
        DeclareLaunchArgument("stack_startup_timeout_sec", default_value="30.0"),
        DeclareLaunchArgument("stack_shutdown_timeout_sec", default_value="10.0"),
    ]
    node = Node(
        package="snake_experiment_ui",
        executable="session_interface_node",
        name="snake_experiment_session_interface",
        output="screen",
        parameters=[
            {
                "host": LaunchConfiguration("host"),
                "port": LaunchConfiguration("port"),
                "stack_use_simulation": LaunchConfiguration(
                    "stack_use_simulation"
                ),
                "diagnostic_profile": LaunchConfiguration("diagnostic_profile"),
                "measurement_window_sec": LaunchConfiguration(
                    "measurement_window_sec"
                ),
                "repository_root": LaunchConfiguration("repository_root"),
                "mode_startup_timeout_sec": LaunchConfiguration(
                    "mode_startup_timeout_sec"
                ),
                "mode_shutdown_timeout_sec": LaunchConfiguration(
                    "mode_shutdown_timeout_sec"
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

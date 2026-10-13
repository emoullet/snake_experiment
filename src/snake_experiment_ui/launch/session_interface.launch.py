"""Launch the independent Views B-G Snake experiment interface."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    arguments = [
        DeclareLaunchArgument("host", default_value="127.0.0.1"),
        DeclareLaunchArgument("port", default_value="8081"),
        DeclareLaunchArgument("stack_robot", default_value="explorer", choices=["explorer", "kinova"]),
        DeclareLaunchArgument("stack_robot_ip", default_value="192.168.1.10"),
        DeclareLaunchArgument("stack_use_simulation", default_value="false"),
        DeclareLaunchArgument("diagnostic_profile", default_value=""),
        DeclareLaunchArgument("experiment_profile", default_value=""),
        DeclareLaunchArgument("participant_interface_profile", default_value=""),
        DeclareLaunchArgument("presentation_video_path", default_value=""),
        DeclareLaunchArgument("measurement_window_sec", default_value="2.0"),
        DeclareLaunchArgument("repository_root", default_value=""),
        DeclareLaunchArgument("sessions_root", default_value=""),
        DeclareLaunchArgument(
            "calibration_file", default_value="",
            description="Override calibrations/latest_calib_<stack_robot>.json in the launch directory.",
        ),
        DeclareLaunchArgument("mode_startup_timeout_sec", default_value="5.0"),
        DeclareLaunchArgument("mode_shutdown_timeout_sec", default_value="5.0"),
        DeclareLaunchArgument("stack_startup_timeout_sec", default_value="30.0"),
        DeclareLaunchArgument("stack_shutdown_timeout_sec", default_value="10.0"),
        DeclareLaunchArgument("rosbag_startup_timeout_sec", default_value="5.0"),
        DeclareLaunchArgument("rosbag_shutdown_timeout_sec", default_value="10.0"),
        DeclareLaunchArgument("go_to_timeout_sec", default_value="120.0"),
        DeclareLaunchArgument("go_to_dwell_sec", default_value="0.5"),
        DeclareLaunchArgument("go_to_stall_timeout_sec", default_value="5.0"),
        DeclareLaunchArgument("go_to_progress_window_sec", default_value="1.0"),
        DeclareLaunchArgument("go_to_linear_progress_threshold_mps", default_value="0.0005"),
        DeclareLaunchArgument("go_to_angular_progress_threshold_radps", default_value="0.008726646259971648"),
        DeclareLaunchArgument("go_to_feedback_timeout_sec", default_value="2.0"),
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
                "stack_robot": LaunchConfiguration("stack_robot"),
                "stack_robot_ip": LaunchConfiguration("stack_robot_ip"),
                "stack_use_simulation": LaunchConfiguration(
                    "stack_use_simulation"
                ),
                "diagnostic_profile": LaunchConfiguration("diagnostic_profile"),
                "experiment_profile": LaunchConfiguration("experiment_profile"),
                "participant_interface_profile": LaunchConfiguration("participant_interface_profile"),
                "presentation_video_path": LaunchConfiguration("presentation_video_path"),
                "measurement_window_sec": LaunchConfiguration(
                    "measurement_window_sec"
                ),
                "repository_root": LaunchConfiguration("repository_root"),
                "sessions_root": LaunchConfiguration("sessions_root"),
                "calibration_file": LaunchConfiguration("calibration_file"),
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
                "rosbag_startup_timeout_sec": LaunchConfiguration(
                    "rosbag_startup_timeout_sec"
                ),
                "rosbag_shutdown_timeout_sec": LaunchConfiguration(
                    "rosbag_shutdown_timeout_sec"
                ),
                "go_to_timeout_sec": LaunchConfiguration("go_to_timeout_sec"),
                "go_to_dwell_sec": LaunchConfiguration("go_to_dwell_sec"),
                "go_to_stall_timeout_sec": LaunchConfiguration("go_to_stall_timeout_sec"),
                "go_to_progress_window_sec": LaunchConfiguration("go_to_progress_window_sec"),
                "go_to_linear_progress_threshold_mps": LaunchConfiguration("go_to_linear_progress_threshold_mps"),
                "go_to_angular_progress_threshold_radps": LaunchConfiguration("go_to_angular_progress_threshold_radps"),
                "go_to_feedback_timeout_sec": LaunchConfiguration("go_to_feedback_timeout_sec"),
            }
        ],
    )
    return LaunchDescription(arguments + [node])

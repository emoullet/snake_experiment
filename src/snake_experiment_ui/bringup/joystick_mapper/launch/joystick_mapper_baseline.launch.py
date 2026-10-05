from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression


def generate_launch_description():
    robot = LaunchConfiguration("robot")
    default_config_file = PathJoinSubstitution(
        [
            FindPackageShare("snake_experiment_ui"),
            "bringup",
            "joystick_mapper",
            "config",
            PythonExpression(["'joystick_2d_baseline_' + '", robot, "' + '.yaml'"]),
        ]
    )
    config_file = LaunchConfiguration("config_file")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "config_file",
                default_value=default_config_file,
                description="Path to the joystick mapper parameter file.",
            ),
            DeclareLaunchArgument("robot", default_value="explorer", choices=["explorer", "kinova"]),
            Node(
                package="joystick_mapper",
                executable="joystick_mapper_node",
                name="joystick_mapper",
                output="screen",
                parameters=[config_file],
            ),
        ]
    )

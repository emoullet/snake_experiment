"""Launch the Explorer stack without a joystick mapper."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    LaunchConfiguration,
    PathJoinSubstitution,
    PythonExpression,
)
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    gui = LaunchConfiguration("gui")
    use_simulation = LaunchConfiguration("use_simulation")
    use_actuator_interface = PythonExpression(
        ["'false' if '", use_simulation, "' == 'true' else 'true'"]
    )

    arguments = [
        DeclareLaunchArgument(
            "gui",
            default_value="true",
            description="Start RViz2 automatically.",
        ),
        DeclareLaunchArgument(
            "use_simulation",
            default_value="false",
            description="Launch Gazebo instead of the robot hardware.",
        ),
    ]

    controller_config = PathJoinSubstitution(
        [
            FindPackageShare("snake_experiment_ui"),
            "bringup",
            "cartesian_manager",
            "config",
            "explorer_params.yaml",
        ]
    )
    robot_simulation = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            [FindPackageShare("explorer_bringup"), "/launch/simulation_base.launch.py"]
        ),
        launch_arguments={
            "use_POC2": "true",
            "gui": gui,
            "use_sim_time": use_simulation,
            "rviz_delay": "3.0",
            "extra_controllers_config": controller_config,
            "use_custom_controllers": "true",
        }.items(),
        condition=IfCondition(use_simulation),
    )
    robot_hardware = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            [FindPackageShare("explorer_bringup"), "/launch/hardware_base.launch.py"]
        ),
        launch_arguments={
            "gui": gui,
            "use_sim_time": use_simulation,
            "use_actuator_interface": use_actuator_interface,
            "can_port": "can0",
            "host_id": "45",
            "use_POC2": "true",
            "rviz_delay": "3.0",
            "extra_controllers_config": controller_config,
            "use_custom_controllers": "true",
        }.items(),
        condition=UnlessCondition(use_simulation),
    )

    spawner_qontrol = Node(
        package="controller_manager",
        executable="spawner",
        arguments=["qontrol_explorer", "--controller-manager", "/controller_manager"],
    )
    delayed_spawner_qontrol = TimerAction(period=2.0, actions=[spawner_qontrol])
    spawner_gripper_controller = Node(
        package="controller_manager",
        executable="spawner",
        arguments=["gripper_controller", "--controller-manager", "/controller_manager"],
        output="screen",
    )
    manager_node = Node(
        package="cartesian_manager",
        executable="cartesian_manager_node",
        name="cartesian_manager",
        output="screen",
        parameters=[controller_config],
    )
    joy_node = Node(package="joy", executable="joy_node", name="joy_node")

    return LaunchDescription(
        arguments
        + [
            robot_simulation,
            robot_hardware,
            delayed_spawner_qontrol,
            spawner_gripper_controller,
            manager_node,
            joy_node,
        ]
    )

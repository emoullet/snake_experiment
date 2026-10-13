"""Launch the seven-axis Gen3/Robotiq stack without a joystick mapper."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import Command, FindExecutable, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare

from snake_experiment_ui.protected_launch import protected_controllers, manager_protection_parameters


def generate_launch_description():
    use_simulation = LaunchConfiguration("use_simulation")
    robot_ip = LaunchConfiguration("robot_ip")
    arguments = [
        DeclareLaunchArgument("overload_protection", default_value="false", choices=["true", "false"]),
        DeclareLaunchArgument("overload_retreat_delay_sec", default_value="1.0"),
        DeclareLaunchArgument(
            "use_simulation", default_value="false", choices=["true", "false"],
            description="Use fake hardware instead of the physical robot (not Gazebo).",
        ),
        DeclareLaunchArgument("robot_ip", default_value="192.168.1.10"),
    ]
    description = {
        "robot_description": ParameterValue(Command([
            FindExecutable(name="xacro"), " ",
            PathJoinSubstitution([FindPackageShare("kortex_description"), "robots", "gen3.xacro"]),
            " arm:=gen3 dof:=7 robot_ip:=", robot_ip,
            " use_fake_hardware:=", use_simulation,
            " fake_sensor_commands:=", use_simulation,
            " gripper:=robotiq_2f_85",
            " gripper_joint_name:=robotiq_85_left_knuckle_joint",
            " use_internal_bus_gripper_comm:=true",
            " gripper_max_velocity:=100.0 gripper_max_force:=100.0",
            " use_external_cable:=true",
        ]), value_type=str)
    }
    config = PathJoinSubstitution([
        FindPackageShare("snake_experiment_ui"), "bringup", "cartesian_manager",
        "config", "kinova_params.yaml",
    ])

    def spawner(controller):
        return Node(
            package="controller_manager", executable="spawner",
            arguments=[controller, "--controller-manager", "/controller_manager"],
            output="screen",
        )

    return LaunchDescription(arguments + [
        Node(package="robot_state_publisher", executable="robot_state_publisher",
             parameters=[description], output="screen"),
        Node(package="controller_manager", executable="ros2_control_node",
             parameters=[description, config], output="screen"),
        spawner("joint_state_broadcaster"),
        OpaqueFunction(function=protected_controllers, kwargs={"robot": "kinova"}),
        Node(package="cartesian_manager", executable="cartesian_manager_node",
             name="cartesian_manager",
             parameters=[config, manager_protection_parameters("kinova")], output="screen"),
        Node(package="joy", executable="joy_node", name="joy_node", output="screen"),
        Node(
            package="rviz2", executable="rviz2", name="rviz2", output="screen",
            arguments=["-d", PathJoinSubstitution([
                FindPackageShare("snake_experiment_ui"), "config", "kinova.rviz",
            ])],
            condition=IfCondition(use_simulation),
        ),
    ])

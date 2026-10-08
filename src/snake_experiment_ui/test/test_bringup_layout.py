from pathlib import Path
import importlib.util
import unittest
from unittest.mock import patch

import yaml


PACKAGE_ROOT = Path(__file__).parents[1]
REPOSITORY_ROOT = Path(__file__).parents[3]


class BringupLayoutTest(unittest.TestCase):
    def _load_launch(self, name):
        path = PACKAGE_ROOT / "launch" / name
        if name.startswith("joystick_mapper_"):
            path = PACKAGE_ROOT / "bringup/joystick_mapper/launch" / name
        spec = importlib.util.spec_from_file_location(
            "robot_selection_launch", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_interfaces_forward_robot_selection(self):
        from launch import LaunchContext
        from launch.actions import DeclareLaunchArgument
        from launch.utilities import perform_substitutions

        for name in ("target_calibration.launch.py", "session_interface.launch.py"):
            with self.subTest(launch=name):
                module = self._load_launch(name)
                with patch.object(module, "Node", wraps=module.Node) as node:
                    description = module.generate_launch_description()
                declarations = {
                    action.name: action for action in description.entities
                    if isinstance(action, DeclareLaunchArgument)
                }
                context = LaunchContext()
                self.assertEqual(perform_substitutions(context, declarations["stack_robot"].default_value), "explorer")
                self.assertEqual(perform_substitutions(context, declarations["stack_robot_ip"].default_value), "192.168.1.10")
                context.launch_configurations.update(stack_robot="kinova", stack_robot_ip="192.168.2.10")
                parameters = node.call_args.kwargs["parameters"][0]
                self.assertEqual(parameters["stack_robot"].perform(context), "kinova")
                self.assertEqual(parameters["stack_robot_ip"].perform(context), "192.168.2.10")

    def test_kinova_stack_leaves_mapper_to_interfaces(self):
        module = self._load_launch("kinova.launch.py")
        with patch.object(module, "Node", wraps=module.Node) as node:
            module.generate_launch_description()
        packages = [call.kwargs["package"] for call in node.call_args_list]
        self.assertNotIn("joystick_mapper", packages)
        self.assertIn("joy", packages)
        self.assertIn("cartesian_manager", packages)
        controllers = [
            call.kwargs["arguments"][0] for call in node.call_args_list
            if call.kwargs["executable"] == "spawner"
        ]
        # Other spawners are created at launch execution, after resolving protection.
        from launch import LaunchContext
        from snake_experiment_ui import protected_launch
        context = LaunchContext()
        context.launch_configurations.update(overload_protection="false", use_simulation="true")
        with patch.object(protected_launch, "Node", wraps=protected_launch.Node) as protected_node:
            protected_launch.protected_controllers(context, "kinova")
        controllers += [call.kwargs["arguments"][0] for call in protected_node.call_args_list if call.kwargs["executable"] == "spawner"]
        self.assertCountEqual(controllers, ["joint_state_broadcaster", "qontrol_explorer", "gripper_controller"])

    def test_joystick_profiles_have_one_canonical_location(self):
        for profile in ("baseline", "snake"):
            for robot in ("explorer", "kinova"):
                file_name = f"joystick_2d_{profile}_{robot}.yaml"
                canonical = (
                    PACKAGE_ROOT / "bringup" / "joystick_mapper" / "config" / file_name
                )
                self.assertTrue(canonical.is_file())
                self.assertFalse((PACKAGE_ROOT / "config" / file_name).exists())

    def test_cartesian_manager_launch_matches_submodule(self):
        snapshot = (
            PACKAGE_ROOT
            / "bringup"
            / "cartesian_manager"
            / "launch"
            / "explorer.launch.py"
        )
        submodule = (
            REPOSITORY_ROOT
            / "dependencies"
            / "cartesian_manager"
            / "bringup"
            / "launch"
            / "explorer.launch.py"
        )
        self.assertEqual(snapshot.read_bytes(), submodule.read_bytes())

    def test_mapper_launch_selects_robot_profile_without_parameter_overrides(self):
        from launch import LaunchContext
        from launch.actions import DeclareLaunchArgument
        from launch.utilities import perform_substitutions

        for mode in ("baseline", "snake"):
            module = self._load_launch(f"joystick_mapper_{mode}.launch.py")
            with patch.object(module, "Node", wraps=module.Node) as node:
                description = module.generate_launch_description()
            declarations = {
                action.name: action for action in description.entities
                if isinstance(action, DeclareLaunchArgument)
            }
            self.assertNotIn("ee_frame", declarations)
            self.assertEqual(len(node.call_args.kwargs["parameters"]), 1)
            for robot in ("explorer", "kinova"):
                context = LaunchContext()
                context.launch_configurations["robot"] = robot
                with patch.object(module.FindPackageShare, "perform", return_value="/virtual/share"):
                    selected = perform_substitutions(context, declarations["config_file"].default_value)
                self.assertEqual(Path(selected).name, f"joystick_2d_{mode}_{robot}.yaml")
            context.launch_configurations["config_file"] = "/custom/mapper.yaml"
            self.assertEqual(node.call_args.kwargs["parameters"][0].perform(context), "/custom/mapper.yaml")

    def test_checkup_profiles_reference_matching_robot_parameters(self):
        from snake_experiment_ui.diagnostics import DiagnosticProfile

        for robot, file_name in (("explorer", "system_checkup.yaml"),
                                 ("kinova", "system_checkup_kinova.yaml")):
            profile = DiagnosticProfile(PACKAGE_ROOT / "config" / file_name)
            for mode, requirement in profile.modes.items():
                self.assertEqual(requirement.mapper_config.name, f"joystick_2d_{mode}_{robot}.yaml")
                expected_frame = "ft_frame" if robot == "explorer" else "end_effector_link"
                self.assertEqual(requirement.mapper_parameters["modes.b2.angular_output_frame_id"], expected_frame)

    def test_runtime_uses_experiment_owned_controller_profile(self):
        config = (
            PACKAGE_ROOT
            / "bringup"
            / "cartesian_manager"
            / "config"
            / "explorer_params.yaml"
        )
        parameters = yaml.safe_load(config.read_text(encoding="utf-8"))
        self.assertEqual(
            parameters["gripper_controller"]["ros__parameters"],
            {"joints": ["right_finger_joint"], "interface_name": "position"},
        )
        runtime_launch = (PACKAGE_ROOT / "launch" / "explorer.launch.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('FindPackageShare("snake_experiment_ui")', runtime_launch)
        self.assertIn('"cartesian_manager"', runtime_launch)

    def test_upstream_bringup_directory_was_removed(self):
        self.assertFalse((REPOSITORY_ROOT / "bringup").exists())


if __name__ == "__main__":
    unittest.main()

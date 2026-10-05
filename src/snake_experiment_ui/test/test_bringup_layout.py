from pathlib import Path
import importlib.util
import unittest
from unittest.mock import patch

import yaml


PACKAGE_ROOT = Path(__file__).parents[1]
REPOSITORY_ROOT = Path(__file__).parents[3]


class BringupLayoutTest(unittest.TestCase):
    def _load_launch(self, name):
        spec = importlib.util.spec_from_file_location(
            "robot_selection_launch", PACKAGE_ROOT / "launch" / name
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
        self.assertCountEqual(controllers, ["joint_state_broadcaster", "qontrol_explorer", "gripper_controller"])

    def test_joystick_profiles_have_one_canonical_location(self):
        for profile in ("baseline", "snake"):
            file_name = f"joystick_2d_{profile}.yaml"
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

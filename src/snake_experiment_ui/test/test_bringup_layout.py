from pathlib import Path
import unittest

import yaml


PACKAGE_ROOT = Path(__file__).parents[1]
REPOSITORY_ROOT = Path(__file__).parents[3]


class BringupLayoutTest(unittest.TestCase):
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

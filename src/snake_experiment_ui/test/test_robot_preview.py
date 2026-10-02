import hashlib
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from xml.etree import ElementTree

import yaml

from snake_experiment_ui.robot_preview import (
    ExplorerModelProvider,
    PreviewConfiguration,
    RobotPoseMonitor,
    RobotPreviewError,
    SnakeButtonMonitor,
)


PACKAGE_ROOT = Path(__file__).parents[1]


class PreviewConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.share = Path(self.temporary_directory.name)
        original = PACKAGE_ROOT
        for relative in (
            "bringup/joystick_mapper/config/joystick_2d_baseline.yaml",
            "bringup/joystick_mapper/config/joystick_2d_snake.yaml",
            "bringup/cartesian_manager/config/explorer_params.yaml",
            "config/robot_preview.yaml",
        ):
            destination = self.share / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original / relative, destination)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def profile(self):
        return PreviewConfiguration(self.share / "config/robot_preview.yaml", self.share)

    @staticmethod
    def mapper_parameters(path):
        return yaml.safe_load(path.read_text())["joystick_mapper"]["ros__parameters"]

    @staticmethod
    def edit_mapper(path, edit):
        document = yaml.safe_load(path.read_text())
        edit(document["joystick_mapper"]["ros__parameters"])
        path.write_text(yaml.safe_dump(document))

    def test_installed_profiles_expose_only_needed_mapping_data(self):
        public = self.profile().public
        self.assertEqual(list(public["mapper"]["baseline"]), ["b1", "b2", "b3"])
        self.assertEqual(list(public["mapper"]["snake"]), ["b1", "b2"])
        self.assertEqual(public["mapper"]["baseline"]["b3"]["axes"]["angular_x"]["index"], 0)
        self.assertEqual(public["snake_gain"], 3.0)
        self.assertEqual(public["animation"], {"linear_mm": 100.0, "angular_deg": 20.0, "loop_sec": 2.0})
        self.assertEqual(public["physical_axis_signs"], {"right": 1, "up": 1})
        snake_mapper = self.share / "bringup/joystick_mapper/config/joystick_2d_snake.yaml"
        self.assertEqual(
            self.profile().snake_button_index,
            self.mapper_parameters(snake_mapper)["snake_button_index"],
        )
        self.assertEqual(public["mapper"]["baseline"]["b2"]["angular_frame"], "ft_frame")
        self.assertEqual(public["frames"], {"base": "base_link", "ee": "ft_frame"})
        self.assertNotIn(str(self.share), str(public))

    def test_invalid_mapper_and_animation_are_rejected(self):
        mapper = self.share / "bringup/joystick_mapper/config/joystick_2d_snake.yaml"
        self.edit_mapper(mapper, lambda parameters: parameters["modes"]["names"].append("b3"))
        with self.assertRaises(RobotPreviewError):
            self.profile()
        mapper.write_text((PACKAGE_ROOT / "bringup/joystick_mapper/config/joystick_2d_snake.yaml").read_text())
        preview = self.share / "config/robot_preview.yaml"
        preview.write_text(preview.read_text().replace("linear_mm: 100", "linear_mm: .nan"))
        with self.assertRaises(RobotPreviewError):
            self.profile()

    def test_unmapped_axis_is_rejected(self):
        mapper = self.share / "bringup/joystick_mapper/config/joystick_2d_baseline.yaml"
        self.edit_mapper(
            mapper,
            lambda parameters: parameters["modes"]["b1"]["axes"]["linear_y"].update(index=-1),
        )
        with self.assertRaises(RobotPreviewError):
            self.profile()

    def test_mapper_frame_must_match_cartesian_manager_configuration(self):
        mapper = self.share / "bringup/joystick_mapper/config/joystick_2d_baseline.yaml"
        self.edit_mapper(
            mapper,
            lambda parameters: parameters["modes"]["b2"].update(
                angular_output_frame_id="effector_frame"
            ),
        )
        with self.assertRaisesRegex(RobotPreviewError, "Unsupported baseline/b2 angular frame"):
            self.profile()

    def test_invalid_snake_gain_is_rejected(self):
        manager = self.share / "bringup/cartesian_manager/config/explorer_params.yaml"
        manager.write_text(manager.read_text().replace("gain: 3.0", "gain: -1.0"))
        with self.assertRaises(RobotPreviewError):
            self.profile()

    def test_axis_signs_support_unknown_and_reject_invalid_values(self):
        preview = self.share / "config/robot_preview.yaml"
        preview.write_text(preview.read_text().replace("right: 1", "right: null"))
        self.assertIsNone(self.profile().public["physical_axis_signs"]["right"])
        preview.write_text(preview.read_text().replace("right: null", "right: 0"))
        with self.assertRaises(RobotPreviewError):
            self.profile()

    def test_snake_button_must_be_a_hold_binding(self):
        mapper = self.share / "bringup/joystick_mapper/config/joystick_2d_snake.yaml"
        mapper.write_text(mapper.read_text().replace("snake_button_mode: hold", "snake_button_mode: toggle"))
        with self.assertRaises(RobotPreviewError):
            self.profile()


class ExplorerModelProviderTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.shares = {
            name: self.root / name
            for name in ("explorer_description", "gripper_pincette")
        }
        for share in self.shares.values():
            share.mkdir()
        (self.shares["explorer_description"] / "urdf").mkdir()
        (self.shares["explorer_description"] / "urdf/explorer.urdf.xacro").touch()
        (self.shares["gripper_pincette"] / "description/urdf").mkdir(parents=True)
        (self.shares["gripper_pincette"] / "description/urdf/gripper_pincette.urdf.xacro").touch()
        visual = self.shares["explorer_description"] / "meshes/visual"
        visual.mkdir(parents=True)
        self.mesh = visual / "arm.dae"
        self.mesh.write_text("<COLLADA/>", encoding="utf-8")
        self.uri = self.mesh.as_uri()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def provider(self):
        def process_file(path, mappings):
            self.assertEqual(mappings["use_POC2"], "true")
            self.assertEqual(Path(path).name, "explorer.urdf.xacro")
            xml = (
                "<robot name='explorer'><link name='base'>"
                f"<visual><geometry><mesh filename='{self.uri}'/></geometry>"
                "<material><texture filename='/private/texture.png'/></material></visual>"
                "<collision><geometry><box size='1 1 1'/></geometry></collision>"
                "</link><joint name='joint_1' type='fixed'/><gazebo/></robot>"
            )
            return types.SimpleNamespace(toxml=lambda: xml)

        return ExplorerModelProvider(
            package_share=lambda name: self.shares[name], xacro_processor=process_file
        )

    def test_visual_urdf_uses_opaque_asset_and_removes_non_visual_content(self):
        provider = self.provider()
        xml = provider.urdf()
        root = ElementTree.fromstring(xml)
        self.assertIsNone(root.find("gazebo"))
        self.assertIsNone(root.find(".//collision"))
        self.assertIsNone(root.find(".//texture"))
        asset_url = root.find(".//mesh").get("filename")
        self.assertRegex(asset_url, r"^/participant/3d-preview/assets/[a-f0-9]{20}\.dae$")
        self.assertNotIn(str(self.root), xml)
        self.assertEqual(provider.asset(asset_url.rsplit("/", 1)[-1]), self.mesh)
        with self.assertRaises(RobotPreviewError):
            provider.asset("unknown.dae")

    def test_visual_asset_outside_allowed_package_is_rejected(self):
        outside = self.root / "outside.dae"
        outside.write_text("<COLLADA/>", encoding="utf-8")
        self.uri = outside.as_uri()
        with self.assertRaises(RobotPreviewError):
            self.provider().urdf()

    def test_visual_asset_symlink_cannot_escape_package_source(self):
        external = self.root / "external.dae"
        external.write_text("<COLLADA/>", encoding="utf-8")
        self.mesh.unlink()
        self.mesh.symlink_to(external)
        with self.assertRaises(RobotPreviewError):
            self.provider().urdf()


class RobotPoseMonitorTest(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.monitor = RobotPoseMonitor(clock=lambda: self.now, max_age_sec=0.5)
        self.names = [f"joint_{index}" for index in range(1, 7)]

    def message(self, names=None, positions=None):
        return types.SimpleNamespace(
            name=self.names if names is None else names,
            position=[0.1] * 6 if positions is None else positions,
        )

    def test_complete_pose_freshness_and_gripper(self):
        self.assertEqual(self.monitor.snapshot()["status"], "unavailable")
        self.monitor.record(self.message())
        self.assertEqual(self.monitor.snapshot()["status"], "live")
        self.assertFalse(self.monitor.snapshot()["gripper_available"])
        self.now += 0.6
        self.assertEqual(self.monitor.snapshot()["status"], "stale")
        self.assertIsNone(self.monitor.snapshot()["joints"])
        self.monitor.record(self.message(
            self.names + ["right_finger_joint"], [0.1] * 6 + [0.2]
        ))
        self.assertTrue(self.monitor.snapshot()["gripper_available"])

    def test_rejects_partial_duplicate_and_nonfinite_joint_messages(self):
        invalid = (
            self.message(self.names[:-1], [0.1] * 5),
            self.message(self.names + [self.names[0]], [0.1] * 7),
            self.message(self.names, [0.1] * 5 + [float("nan")]),
            self.message(self.names, [0.1] * 5),
        )
        for message in invalid:
            with self.subTest(message=message):
                self.monitor.record(message)
                self.assertEqual(self.monitor.snapshot()["status"], "unavailable")


class SnakeButtonMonitorTest(unittest.TestCase):
    def test_pressed_released_stale_invalid_and_reset(self):
        now = [10.0]
        monitor = SnakeButtonMonitor(10, clock=lambda: now[0], max_age_sec=0.5)
        self.assertIsNone(monitor.snapshot())
        monitor.record(types.SimpleNamespace(buttons=[0] * 10 + [1]))
        self.assertIs(monitor.snapshot(), True)
        now[0] += 1.2
        monitor.record(types.SimpleNamespace(buttons=[0] * 10 + [1]))
        self.assertIs(monitor.snapshot(), True)
        now[0] += 0.3
        monitor.record(types.SimpleNamespace(buttons=[0] * 11))
        self.assertIs(monitor.snapshot(), False)
        now[0] += 0.51
        self.assertIsNone(monitor.snapshot())
        monitor.record(types.SimpleNamespace(buttons=[0] * 10))
        self.assertIsNone(monitor.snapshot())
        monitor.record(types.SimpleNamespace(buttons=[0] * 10 + [2]))
        self.assertIsNone(monitor.snapshot())
        monitor.record(types.SimpleNamespace(buttons=[0] * 10 + [1]))
        monitor.reset()
        self.assertIsNone(monitor.snapshot())


class PreviewAssetsTest(unittest.TestCase):
    def test_bundle_matches_versioned_source_and_preview_is_separate(self):
        frontend = PACKAGE_ROOT / "frontend/participant_3d"
        hash_value = hashlib.sha256()
        for name in (
            "package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml",
            "src/main.js", "src/participant.js", "src/animation.js",
            "src/mapping.js", "src/kinematics.js",
        ):
            hash_value.update(name.encode("utf-8"))
            hash_value.update((frontend / name).read_bytes())
        for filename in ("participant_3d.bundle.js", "participant_mapping.bundle.js"):
            bundle = (PACKAGE_ROOT / "snake_experiment_ui/static" / filename).read_text(
                encoding="utf-8"
            )
            self.assertTrue(bundle.startswith(f"// source-sha256: {hash_value.hexdigest()}\n"))
        html = (PACKAGE_ROOT / "snake_experiment_ui/templates/participant_3d_preview.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("participant_3d.bundle.js", html)
        self.assertIn('id="preview-mode"', html)
        self.assertIn('id="preview-submode"', html)
        self.assertIn('id="preview-snake-held"', html)
        self.assertIn('v=participant-3d-4', html)
        participant = (PACKAGE_ROOT / "snake_experiment_ui/templates/participant_index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("participant_3d.bundle.js", participant)
        self.assertIn("participant_mapping.bundle.js", participant)
        self.assertIn('id="participant-horizontal-view"', participant)
        self.assertIn('id="participant-vertical-view"', participant)
        self.assertNotIn('id="participant-state-image"', participant)

    def test_session_mode_request_is_not_periodically_republished(self):
        node = (PACKAGE_ROOT / "snake_experiment_ui/session_node.py").read_text(encoding="utf-8")
        self.assertNotIn("_mode_refresh_timer", node)
        self.assertNotIn("_refresh_mode_request", node)
        self.assertIn("publish_mode_request=self._publish_mode_request", node)

    def test_mapper_publishes_latched_local_mode_for_participant_panel(self):
        mapper = (
            PACKAGE_ROOT.parents[1] / "dependencies/input_interfaces/joystick_mapper"
            / "src/joystick_mapper.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn('"/joystick_mapper/active_mode"', mapper)
        self.assertIn("rclcpp::QoS(1).transient_local().reliable()", mapper)
        self.assertIn("publishActiveMode();", mapper)
        self.assertRegex(mapper, r"if \(active_mode_index_ != previous_mode_index_\)[\s\S]*?publishActiveMode\(\);")


if __name__ == "__main__":
    unittest.main()

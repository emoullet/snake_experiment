import hashlib
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from xml.etree import ElementTree

from snake_experiment_ui.robot_preview import (
    ExplorerModelProvider,
    PreviewConfiguration,
    RobotPoseMonitor,
    RobotPreviewError,
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

    def test_installed_profiles_expose_only_needed_mapping_data(self):
        public = self.profile().public
        self.assertEqual(list(public["mapper"]["baseline"]), ["b1", "b2", "b3"])
        self.assertEqual(list(public["mapper"]["snake"]), ["b1", "b2"])
        self.assertEqual(public["mapper"]["baseline"]["b3"]["axes"]["angular_x"]["index"], 0)
        self.assertEqual(public["snake_gain"], 3.0)
        self.assertEqual(public["animation"], {"linear_mm": 40.0, "angular_deg": 7.0, "loop_sec": 4.0})
        self.assertEqual(public["physical_axis_signs"], {"right": None, "up": None})
        self.assertNotIn(str(self.share), str(public))

    def test_invalid_mapper_and_animation_are_rejected(self):
        mapper = self.share / "bringup/joystick_mapper/config/joystick_2d_snake.yaml"
        mapper.write_text(mapper.read_text().replace("names: [b1, b2]", "names: [b1, b2, b3]"))
        with self.assertRaises(RobotPreviewError):
            self.profile()
        mapper.write_text((PACKAGE_ROOT / "bringup/joystick_mapper/config/joystick_2d_snake.yaml").read_text())
        preview = self.share / "config/robot_preview.yaml"
        preview.write_text(preview.read_text().replace("linear_mm: 40", "linear_mm: .nan"))
        with self.assertRaises(RobotPreviewError):
            self.profile()

    def test_unmapped_axis_is_rejected(self):
        mapper = self.share / "bringup/joystick_mapper/config/joystick_2d_baseline.yaml"
        mapper.write_text(mapper.read_text().replace("linear_y: {index: 1, scale: 1.0}", "linear_y: {index: -1, scale: 1.0}"))
        with self.assertRaises(RobotPreviewError):
            self.profile()

    def test_invalid_snake_gain_is_rejected(self):
        manager = self.share / "bringup/cartesian_manager/config/explorer_params.yaml"
        manager.write_text(manager.read_text().replace("gain: 3.0", "gain: -1.0"))
        with self.assertRaises(RobotPreviewError):
            self.profile()

    def test_unverified_signs_remain_unknown(self):
        preview = self.share / "config/robot_preview.yaml"
        preview.write_text(preview.read_text().replace("right: null", "right: 1"))
        self.assertEqual(self.profile().public["physical_axis_signs"]["right"], 1)
        preview.write_text(preview.read_text().replace("right: 1", "right: 0"))
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


class PreviewAssetsTest(unittest.TestCase):
    def test_bundle_matches_versioned_source_and_preview_is_separate(self):
        frontend = PACKAGE_ROOT / "frontend/participant_3d"
        hash_value = hashlib.sha256()
        for name in (
            "package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml",
            "src/main.js", "src/mapping.js", "src/kinematics.js",
        ):
            hash_value.update(name.encode("utf-8"))
            hash_value.update((frontend / name).read_bytes())
        bundle = (PACKAGE_ROOT / "snake_experiment_ui/static/participant_3d.bundle.js").read_text(
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
        self.assertIn('v=participant-3d-2', html)
        participant = (PACKAGE_ROOT / "snake_experiment_ui/templates/participant_index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("participant_3d.bundle.js", participant)


if __name__ == "__main__":
    unittest.main()

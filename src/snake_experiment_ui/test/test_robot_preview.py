import hashlib
from pathlib import Path
import tempfile
import types
import unittest
from xml.etree import ElementTree

from snake_experiment_ui.robot_preview import (
    ExplorerModelProvider,
    RobotPoseMonitor,
    RobotPreviewError,
)


PACKAGE_ROOT = Path(__file__).parents[1]


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
        for name in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "src/main.js"):
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
        participant = (PACKAGE_ROOT / "snake_experiment_ui/templates/participant_index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("participant_3d.bundle.js", participant)


if __name__ == "__main__":
    unittest.main()

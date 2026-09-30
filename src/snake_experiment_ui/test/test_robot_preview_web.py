from pathlib import Path
import tempfile
import unittest

try:
    import httpx
    from fastapi import WebSocketDisconnect
except ImportError:  # pragma: no cover
    httpx = None

from snake_experiment_ui.robot_preview import RobotPreview, RobotPreviewError
from snake_experiment_ui.session_web_app import create_session_app


class FakeCheckup:
    def snapshot(self):
        return {"workflow": "idle", "current_panel": "B"}


class FakeModel:
    def __init__(self, asset):
        self.asset_path = asset

    def urdf(self):
        return "<robot name='explorer'/>"

    def asset(self, asset_id):
        if asset_id != "allowed.dae":
            raise RobotPreviewError("Unknown robot visual asset.")
        return self.asset_path


class FakePose:
    def snapshot(self):
        return {
            "robot": "explorer_poc2", "status": "live",
            "joints": {"joint_1": 0.25}, "gripper_available": False,
        }


@unittest.skipIf(httpx is None, "FastAPI test dependencies are not installed")
class RobotPreviewWebTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        static = root / "static"
        templates = root / "templates"
        static.mkdir()
        templates.mkdir()
        (templates / "participant_3d_preview.html").write_text("3D preview", encoding="utf-8")
        self.asset = root / "allowed.dae"
        self.asset.write_text("<COLLADA/>", encoding="utf-8")
        self.preview = RobotPreview(model=FakeModel(self.asset), pose=FakePose())
        self.app = create_session_app(
            FakeCheckup(), static, templates,
            robot_preview=self.preview,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    async def request(self, path):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    async def test_preview_routes_are_separate_and_read_only(self):
        paths = {route.path for route in self.app.routes}
        self.assertTrue({
            "/participant/3d-preview", "/participant/3d-preview/api/state",
            "/participant/3d-preview/model.urdf", "/participant/3d-preview/assets/{asset_id}",
            "/participant/3d-preview/ws",
        }.issubset(paths))
        self.assertEqual((await self.request("/participant/3d-preview/api/state")).json()["joints"], {"joint_1": 0.25})
        model = await self.request("/participant/3d-preview/model.urdf")
        self.assertEqual(model.status_code, 200)
        self.assertEqual(model.text, "<robot name='explorer'/>")
        self.assertEqual(model.headers["cache-control"], "no-store, max-age=0")
        self.assertEqual((await self.request("/participant/3d-preview/assets/unknown.dae")).status_code, 404)

    async def test_file_routes_return_only_prepared_file_responses(self):
        endpoints = {
            route.path: route.endpoint for route in self.app.routes
            if hasattr(route, "endpoint")
        }
        page = await endpoints["/participant/3d-preview"]()
        self.assertTrue(str(page.path).endswith("participant_3d_preview.html"))
        asset = await endpoints["/participant/3d-preview/assets/{asset_id}"]("allowed.dae")
        self.assertEqual(Path(asset.path), self.asset)
        self.assertEqual(asset.media_type, "model/vnd.collada+xml")

    async def test_websocket_sends_only_preview_pose_changes(self):
        endpoint = next(
            route.endpoint for route in self.app.routes
            if route.path == "/participant/3d-preview/ws"
        )

        class ChangingPose:
            states = ["unavailable", "unavailable", "live", "stale"]

            def snapshot(self):
                status = self.states.pop(0)
                return {
                    "robot": "explorer_poc2", "status": status,
                    "joints": {"joint_1": 0.25} if status == "live" else None,
                    "gripper_available": False,
                }

        class Socket:
            accepted = False
            sent = []

            async def accept(self):
                self.accepted = True

            async def send_json(self, payload):
                self.sent.append(payload)
                if len(self.sent) == 3:
                    raise WebSocketDisconnect()

        self.preview.pose = ChangingPose()
        socket = Socket()
        await endpoint(socket)
        self.assertTrue(socket.accepted)
        self.assertEqual([state["status"] for state in socket.sent], ["unavailable", "live", "stale"])
        self.assertNotIn("participant", str(socket.sent))


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import tempfile
import unittest

try:
    import fastapi  # noqa: F401
    import httpx
except ImportError:  # pragma: no cover - reported by the package dependency check
    httpx = None

from snake_experiment_ui.calibration import CalibrationError


class FakeCalibrationState:
    def __init__(self):
        self.captured = []

    def snapshot(self):
        return {
            "pose_stream": {"received": True, "fresh": True},
            "captures": {},
            "can_capture": True,
            "can_save": False,
            "save_blocker": "Record all seven calibration poses before saving.",
        }

    def capture(self, pose_id):
        if pose_id == "bad":
            raise CalibrationError("Unknown calibration pose: bad")
        self.captured.append(pose_id)

    def calibration_document(self):
        return {"schema_version": 1, "poses": {}}


class FakeModeManager:
    def __init__(self):
        self.mode = None

    def snapshot(self):
        return {"active_mode": self.mode, "status": "active", "error": None}

    def activate(self, mode):
        self.mode = mode


class FakeStackManager:
    def __init__(self):
        self.status = "inactive"

    def snapshot(self):
        return {
            "status": self.status,
            "error": None,
            "use_simulation": True,
        }

    def start(self):
        self.status = "active"

    def stop(self):
        self.status = "inactive"


class FakeStorage:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, document):
        return self.directory / "latest_calib.json"


@unittest.skipIf(httpx is None, "FastAPI test dependencies are not installed")
class WebAppTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from snake_experiment_ui.web_app import create_app

        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        static = root / "static"
        assets = root / "assets"
        templates = root / "templates"
        static.mkdir()
        assets.mkdir()
        templates.mkdir()
        (assets / "app.js").write_text("window.panelA = true;\n", encoding="utf-8")
        (static / "app.js").symlink_to(assets / "app.js")
        (templates / "index.html").write_text("Panel A", encoding="utf-8")
        self.calibration = FakeCalibrationState()
        self.mode = FakeModeManager()
        self.stack = FakeStackManager()
        app = create_app(
            self.calibration,
            self.mode,
            self.stack,
            FakeStorage(root),
            static,
            templates,
            "/ee_pose",
        )
        self.app = app
        self.static_app = next(
            route.app for route in app.routes if route.path == "/static"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    async def request(self, method, path):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return await client.request(method, path)

    async def test_state_and_mode_routes(self):
        response = await self.request("GET", "/api/state")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pose_topic"], "/ee_pose")

        response = await self.request("POST", "/api/modes/snake")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"]["active_mode"], "snake")

    def test_static_assets_follow_colcon_symlinks(self):
        path, stat_result = self.static_app.lookup_path("app.js")
        self.assertEqual(Path(path).name, "app.js")
        self.assertIsNotNone(stat_result)

    async def test_capture_error_is_a_conflict(self):
        response = await self.request("POST", "/api/poses/bad")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Unknown calibration pose", response.json()["detail"])

    async def test_stack_start_and_stop_routes(self):
        response = await self.request("POST", "/api/stack/start")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stack"]["status"], "active")

        response = await self.request("POST", "/api/stack/stop")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stack"]["status"], "inactive")

        response = await self.request("POST", "/api/stack/restart")
        self.assertEqual(response.status_code, 404)

    async def test_save_returns_file_name(self):
        response = await self.request("POST", "/api/calibrations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["file_name"], "latest_calib.json")


if __name__ == "__main__":
    unittest.main()

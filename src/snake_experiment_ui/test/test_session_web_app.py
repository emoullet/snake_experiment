from pathlib import Path
import tempfile
import unittest

try:
    import fastapi  # noqa: F401
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

from snake_experiment_ui.checkup import CheckupError


class FakeCheckup:
    def __init__(self):
        self.workflow = "idle"
        self.confirmations = []

    def snapshot(self):
        return {"workflow": self.workflow, "current_panel": "C" if self.workflow == "validated" else "B"}

    def start_stack(self):
        self.workflow = "stack_ready"
        return self.snapshot()

    def stop_stack(self):
        self.workflow = "idle"
        return self.snapshot()

    def start_mode(self, mode):
        if mode not in ("baseline", "snake"):
            raise CheckupError("Unknown check-up mode")
        self.workflow = "mode_checking"
        return self.snapshot()

    def retry_mode(self, mode):
        return self.start_mode(mode)

    def confirm_mode(self, mode, accepted):
        self.confirmations.append((mode, accepted))
        return self.snapshot()

    def validate(self):
        self.workflow = "validated"
        return self.snapshot()

    def reset(self):
        self.workflow = "idle"
        return self.snapshot()


@unittest.skipIf(httpx is None, "FastAPI test dependencies are not installed")
class SessionWebAppTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from snake_experiment_ui.session_web_app import create_session_app

        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        static = root / "static"
        templates = root / "templates"
        static.mkdir()
        templates.mkdir()
        (static / "session_app.js").write_text("window.session = true;\n", encoding="utf-8")
        (templates / "session_index.html").write_text("Panel B", encoding="utf-8")
        self.checkup = FakeCheckup()
        self.app = create_session_app(self.checkup, static, templates)

    def tearDown(self):
        self.temporary_directory.cleanup()

    async def request(self, method, path, json=None):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=json)

    async def test_stack_mode_confirmation_and_validation_routes(self):
        self.assertEqual((await self.request("POST", "/api/stack/start")).status_code, 200)
        self.assertEqual((await self.request("POST", "/api/checkup/modes/snake/start")).status_code, 200)
        response = await self.request(
            "POST", "/api/checkup/modes/snake/confirm", json={"accepted": True}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.checkup.confirmations, [("snake", True)])
        response = await self.request("POST", "/api/checkup/validate")
        self.assertEqual(response.json()["current_panel"], "C")

    async def test_unknown_mode_and_stack_action_are_reported(self):
        response = await self.request("POST", "/api/checkup/modes/automatic/start")
        self.assertEqual(response.status_code, 409)
        response = await self.request("POST", "/api/stack/restart")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()

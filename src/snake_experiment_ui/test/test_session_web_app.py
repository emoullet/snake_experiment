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


class FakeEnrollment:
    def __init__(self):
        self.parent = None
        self.participant = None
        self.panel = "C"

    def snapshot(self):
        return {"workflow": "participant_ready" if self.participant else "awaiting_parent", "current_panel": self.panel, "participant": self.participant}

    def browse(self, path=""):
        return {"path": path or "/sessions", "directories": [], "parent": None}

    def select_parent(self, path):
        self.parent = path

    def create_folder(self, parent_path, name):
        return {
            "root": "/sessions",
            "path": f"{parent_path}/{name}",
            "parent": parent_path,
            "directories": [],
        }

    def generate_pseudonym(self):
        return {"pseudonym": "A1B2C3"}

    def create_participant(self, form):
        self.participant = form

    def resume_participant(self, pseudonym, acknowledge_mismatch=False):
        self.participant = {"pseudonym": pseudonym, "resumed": True}

    def cancel(self):
        self.participant = None

    def reset(self):
        self.participant = None
        self.parent = None

    def launch(self):
        self.panel = "D"


class FakeExperiment:
    def __init__(self):
        self.panel = "D"
        self.calls = []

    def snapshot(self):
        return {
            "workflow": "ready",
            "current_panel": self.panel,
            "progress": {"workflow": "ready", "blocks": []},
        }

    def start(self, block_id):
        self.calls.append(("start", block_id))
        self.panel = "E"

    def end(self, block_id, confirmed):
        self.calls.append(("end", block_id, confirmed))
        self.panel = "D"

    def abort(self, block_id):
        self.calls.append(("abort", block_id))
        self.panel = "D"


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
        self.enrollment = FakeEnrollment()
        self.experiment = FakeExperiment()
        self.app = create_session_app(
            self.checkup, static, templates, self.enrollment, self.experiment
        )

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
        response = await self.request("GET", "/api/session/browse")
        self.assertEqual(response.status_code, 409)

    async def test_panel_c_routes_and_transition_to_d(self):
        self.checkup.workflow = "validated"
        response = await self.request("GET", "/api/session/browse")
        self.assertEqual(response.json()["path"], "/sessions")
        response = await self.request(
            "POST",
            "/api/session/folders",
            json={"parent_path": "/sessions", "name": "experiment-01"},
        )
        self.assertEqual(response.json()["path"], "/sessions/experiment-01")
        self.assertEqual((await self.request("POST", "/api/session/root", json={"path": "/sessions"})).status_code, 200)
        form = {
            "pseudonym": "A1B2C3", "gathered_consent": True, "handedness": "right",
            "joystick_experience": False, "visual_or_motor_impairment": False,
        }
        response = await self.request("POST", "/api/session/new", json=form)
        self.assertEqual(response.json()["enrollment"]["participant"]["pseudonym"], "A1B2C3")
        response = await self.request("POST", "/api/session/launch")
        self.assertEqual(response.json()["current_panel"], "D")

        response = await self.request(
            "POST", "/api/experiment/blocks/mode_1_discovery/start"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current_panel"], "E")
        response = await self.request(
            "POST",
            "/api/experiment/blocks/mode_1_discovery/end",
            json={"confirmed": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.experiment.calls,
            [("start", "mode_1_discovery"), ("end", "mode_1_discovery", True)],
        )


class SessionTemplateTest(unittest.TestCase):
    def test_participant_fields_use_explicit_choices_without_defaults(self):
        package_root = Path(__file__).parents[1] / "snake_experiment_ui"
        html = (package_root / "templates/session_index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("<select", html)
        self.assertIn('name="gathered_consent" type="checkbox"', html)
        for field in (
            "handedness",
            "joystick_experience",
            "visual_or_motor_impairment",
        ):
            self.assertEqual(html.count(f'name="{field}" type="radio"'), 2)
        self.assertNotIn(" checked", html)

    def test_participant_form_is_reset_for_another_selection(self):
        package_root = Path(__file__).parents[1] / "snake_experiment_ui"
        script = (package_root / "static/session_app.js").read_text(encoding="utf-8")
        self.assertIn("this.resetParticipantForm();", script)
        self.assertIn('["/api/session/cancel", "/api/session/reset"]', script)

    def test_lot_4_panels_and_actions_are_present(self):
        package_root = Path(__file__).parents[1] / "snake_experiment_ui"
        html = (package_root / "templates/session_index.html").read_text(encoding="utf-8")
        script = (package_root / "static/session_app.js").read_text(encoding="utf-8")
        for panel in ("D", "E", "F", "G"):
            self.assertIn(f'data-panel-step="{panel}"', html)
        self.assertIn("data-block-start", script)
        self.assertIn("data-block-end", script)
        self.assertIn("data-block-abort", script)


if __name__ == "__main__":
    unittest.main()

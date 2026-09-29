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

    def start_go_to(self, pose_id):
        self.confirmations.append(("go_to", pose_id))
        return self.snapshot()

    def stop_go_to(self):
        self.confirmations.append(("go_to", "stop"))
        return self.snapshot()

    def browser_disconnected(self):
        pass


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

    def participant_snapshot(self):
        return {"panel": "A", "presentation_status": "showing", "video_available": False}

    def presentation_video_available(self):
        return False

    def show_presentation(self):
        self.calls.append(("presentation", "show"))

    def complete_presentation(self):
        self.calls.append(("presentation", "complete"))

    def start(self, block_id):
        self.calls.append(("start", block_id))
        self.panel = "E"

    def end(self, block_id, confirmed):
        self.calls.append(("end", block_id, confirmed))
        self.panel = "D"

    def abort(self, block_id):
        self.calls.append(("abort", block_id))
        self.panel = "D"

    def set_control(self, block_id, active):
        self.calls.append(("control", block_id, active))

    def restart_stack(self, block_id):
        self.calls.append(("restart", block_id))

    def start_go_to(self, block_id, pose_id):
        self.calls.append(("go_to", block_id, pose_id))

    def stop_go_to(self, block_id):
        self.calls.append(("go_to_stop", block_id))

    def browser_disconnected(self):
        pass

    def prepare_training_trial(self, block_id):
        self.calls.append(("training_prepare", block_id))

    def training_participant_ready(self, block_id):
        self.calls.append(("training_ready", block_id))

    def start_training_attempt(self, block_id):
        self.calls.append(("training_start", block_id))

    def stop_training_attempt(self, block_id):
        self.calls.append(("training_stop", block_id))

    def add_training_incident(self, block_id):
        self.calls.append(("training_incident", block_id))

    def review_training_incidents(self, block_id, descriptions, invalidates_attempt):
        self.calls.append(
            ("training_incident_review", block_id, descriptions, invalidates_attempt)
        )

    def resolve_training_attempt(self, block_id, decision):
        self.calls.append(("training_resolve", block_id, decision))

    def prepare_recording_trial(self, block_id):
        self.calls.append(("recording_prepare", block_id))

    def recording_participant_ready(self, block_id):
        self.calls.append(("recording_ready", block_id))

    def start_recording_attempt(self, block_id):
        self.calls.append(("recording_start", block_id))

    def stop_recording_attempt(self, block_id):
        self.calls.append(("recording_stop", block_id))

    def add_recording_incident(self, block_id):
        self.calls.append(("recording_incident", block_id))

    def review_recording_incidents(self, block_id, descriptions, invalidates_attempt):
        self.calls.append(
            ("recording_incident_review", block_id, descriptions, invalidates_attempt)
        )

    def resolve_recording_attempt(self, block_id, decision):
        self.calls.append(("recording_resolve", block_id, decision))


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
        (templates / "participant_index.html").write_text("Participant A", encoding="utf-8")
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

    async def test_participant_projection_and_presentation_routes(self):
        registered = {route.path for route in self.app.routes}
        self.assertTrue({"/participant", "/participant/api/state", "/participant/ws"}.issubset(registered))
        response = await self.request("GET", "/participant/api/state")
        self.assertEqual(response.json(), {
            "panel": "A", "presentation_status": "showing", "video_available": False,
        })
        self.assertNotIn("participant", response.text)
        self.assertEqual((await self.request("GET", "/participant/video")).status_code, 404)
        self.assertEqual((await self.request("POST", "/api/experiment/presentation/show")).status_code, 409)
        self.checkup.workflow = "validated"
        self.enrollment.panel = "D"
        self.assertEqual((await self.request("POST", "/api/experiment/presentation/show")).status_code, 200)
        self.assertEqual((await self.request("POST", "/api/experiment/presentation/complete")).status_code, 200)
        self.assertEqual(self.experiment.calls[-2:], [("presentation", "show"), ("presentation", "complete")])

    async def test_unknown_mode_and_stack_action_are_reported(self):
        response = await self.request("POST", "/api/checkup/modes/automatic/start")
        self.assertEqual(response.status_code, 409)
        response = await self.request("POST", "/api/stack/restart")
        self.assertEqual(response.status_code, 404)
        response = await self.request("GET", "/api/session/browse")
        self.assertEqual(response.status_code, 409)

    async def test_checkup_go_to_routes(self):
        response = await self.request(
            "POST", "/api/checkup/go-to", json={"pose_id": "target_1"}
        )
        self.assertEqual(response.status_code, 200)
        response = await self.request("POST", "/api/checkup/go-to/stop")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.checkup.confirmations,
            [("go_to", "target_1"), ("go_to", "stop")],
        )
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

    async def test_discovery_control_and_restart_routes(self):
        self.checkup.workflow = "validated"
        self.enrollment.panel = "D"
        response = await self.request(
            "POST",
            "/api/experiment/blocks/mode_1_discovery/control",
            json={"active": True},
        )
        self.assertEqual(response.status_code, 200)
        response = await self.request(
            "POST", "/api/experiment/blocks/mode_1_discovery/restart-stack"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.experiment.calls,
            [
                ("control", "mode_1_discovery", True),
                ("restart", "mode_1_discovery"),
            ],
        )

    async def test_training_routes(self):
        self.checkup.workflow = "validated"
        self.enrollment.panel = "D"
        block = "mode_1_training"
        for action in ("prepare", "ready", "start", "stop"):
            response = await self.request(
                "POST", f"/api/experiment/blocks/{block}/training/{action}"
            )
            self.assertEqual(response.status_code, 200)
        response = await self.request(
            "POST",
            f"/api/experiment/blocks/{block}/training/incidents",
        )
        self.assertEqual(response.status_code, 200)
        descriptions = [{"id": 1, "text": "Minor issue"}]
        response = await self.request(
            "POST",
            f"/api/experiment/blocks/{block}/training/incidents/review",
            json={"descriptions": descriptions, "invalidates_attempt": False},
        )
        self.assertEqual(response.status_code, 200)
        response = await self.request(
            "POST",
            f"/api/experiment/blocks/{block}/training/resolve",
            json={"decision": "retry"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.experiment.calls,
            [
                ("training_prepare", block),
                ("training_ready", block),
                ("training_start", block),
                ("training_stop", block),
                ("training_incident", block),
                ("training_incident_review", block, descriptions, False),
                ("training_resolve", block, "retry"),
            ],
        )

    async def test_experiment_go_to_routes(self):
        self.checkup.workflow = "validated"
        self.enrollment.panel = "D"
        block = "mode_1_training"
        response = await self.request(
            "POST",
            f"/api/experiment/blocks/{block}/go-to",
            json={"pose_id": "target_out_1"},
        )
        self.assertEqual(response.status_code, 200)
        response = await self.request(
            "POST", f"/api/experiment/blocks/{block}/go-to/stop"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.experiment.calls,
            [("go_to", block, "target_out_1"), ("go_to_stop", block)],
        )

    async def test_recording_routes(self):
        self.checkup.workflow = "validated"
        self.enrollment.panel = "D"
        block = "mode_1_recording"
        for action in ("prepare", "ready", "start", "stop"):
            response = await self.request(
                "POST", f"/api/experiment/blocks/{block}/recording/{action}"
            )
            self.assertEqual(response.status_code, 200)
        response = await self.request(
            "POST",
            f"/api/experiment/blocks/{block}/recording/incidents",
        )
        self.assertEqual(response.status_code, 200)
        descriptions = [{"id": 1, "text": "Tracking issue"}]
        response = await self.request(
            "POST",
            f"/api/experiment/blocks/{block}/recording/incidents/review",
            json={"descriptions": descriptions, "invalidates_attempt": True},
        )
        self.assertEqual(response.status_code, 200)
        response = await self.request(
            "POST",
            f"/api/experiment/blocks/{block}/recording/resolve",
            json={"decision": "advance_with_deviation"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.experiment.calls,
            [
                ("recording_prepare", block),
                ("recording_ready", block),
                ("recording_start", block),
                ("recording_stop", block),
                ("recording_incident", block),
                ("recording_incident_review", block, descriptions, True),
                ("recording_resolve", block, "advance_with_deviation"),
            ],
        )


class SessionTemplateTest(unittest.TestCase):
    def test_participant_panel_a_and_operator_step_are_present(self):
        package_root = Path(__file__).resolve().parents[1] / "snake_experiment_ui"
        operator = (package_root / "templates/session_index.html").read_text(encoding="utf-8")
        participant = (package_root / "templates/participant_index.html").read_text(encoding="utf-8")
        script = (package_root / "static/participant_app.js").read_text(encoding="utf-8")
        self.assertIn('id="participant-waiting"', participant)
        self.assertIn('id="participant-presentation"', participant)
        self.assertIn('id="participant-video"', participant)
        self.assertIn('controls preload="metadata"', participant)
        self.assertIn('data-presentation="show"', operator)
        self.assertIn('data-presentation="complete"', operator)
        self.assertIn('new WebSocket(', script)
        self.assertNotIn('innerHTML', script)

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
        self.assertIn("data-block-control", script)
        self.assertIn("data-restart-stack", script)
        self.assertIn("data-trial-action", script)
        self.assertIn("Prepare next trial and move to start", script)
        self.assertIn(
            "Prepare the next trial and move the robot to its calibrated start pose?",
            script,
        )
        self.assertNotIn('data-trial-action="ready"', script)
        self.assertIn(
            '["awaiting_start_pose", "ready"].includes(workflow)',
            script,
        )
        self.assertIn('<h2 id="participant-heading">Participant ready</h2>', html)
        self.assertIn("data-trial-incident-review-form", script)
        self.assertIn("data-incident-occurrence", script)
        self.assertIn("Signal incident occurrence", script)
        self.assertIn("Validate attempt", script)
        self.assertIn("Invalidate attempt", script)
        self.assertLess(
            script.index('<p class="step-number">Trial issue</p>'),
            script.index('<p class="step-number">Go to</p>'),
        )
        self.assertIn("data-incident-input", script)
        self.assertIn('if (current.tagName === "TEXTAREA") return;', script)
        self.assertIn("this.patchChildren(container, range.createContextualFragment(markup));", script)
        self.assertIn('data-dom-key="trial:${trial.id}"', script)
        self.assertIn('data-dom-key="segment:${this.escape(segment.name)}"', script)
        self.assertIn('data-dom-key="description:${this.escape(key)}"', script)
        self.assertIn('data-dom-key="incident:${incidentPrefix}:${incident.id}"', script)
        self.assertIn('data-dom-key="diagnostic:${this.escape(item.name)}"', script)
        self.assertIn('data-dom-key="participant:${this.escape(item.pseudonym)}"', script)
        self.assertIn('data-dom-key="block:${this.escape(block.id)}"', script)
        self.assertIn('data-dom-key="folder:${this.escape(item.path)}"', script)
        self.assertNotIn("pointerInteractionActive", script)
        self.assertNotIn("renderDeferred", script)
        self.assertNotIn("incidentDrafts", script)
        self.assertNotIn(".innerHTML =", script)
        self.assertIn("data-checkup-go-to", html)
        self.assertIn("data-block-go-to", script)
        self.assertIn("Stop motion", script)
        self.assertIn("renderRecording", script)
        self.assertIn("End recordings", script)
        self.assertIn("Continue with deviation", script)

    def test_successful_checkup_go_to_has_explicit_visual_feedback(self):
        package_root = Path(__file__).resolve().parents[1]
        html = (package_root / "snake_experiment_ui/templates/session_index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "snake_experiment_ui/static/session_app.js").read_text(
            encoding="utf-8"
        )
        style = (package_root / "snake_experiment_ui/static/session_style.css").read_text(
            encoding="utf-8"
        )
        web_app = (package_root / "snake_experiment_ui/session_web_app.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('button.classList.toggle("is-complete", succeeded)', script)
        self.assertIn('button.classList.toggle("is-timed-out", timedOut)', script)
        self.assertIn('"starting point reached"', script)
        self.assertIn("target ${button.dataset.checkupGoTo.replace", script)
        self.assertIn("successfully reached", script)
        self.assertIn(".goto-grid button.is-complete", style)
        self.assertIn(".goto-grid button.is-timed-out", style)
        self.assertIn("session_style.css?v=participant-lot-1", html)
        self.assertIn("session_app.js?v=participant-lot-1", html)
        self.assertIn('"Cache-Control": "no-store, max-age=0"', web_app)
        self.assertIn("NoCacheStaticFiles", web_app)
        self.assertLess(
            html.index('data-checkup-go-to="starting_point"'),
            html.index('data-checkup-go-to="target_1"'),
        )

    def test_trial_panels_use_sticky_two_column_layout_and_starting_point_first(self):
        package_root = Path(__file__).parents[1] / "snake_experiment_ui"
        script = (package_root / "static/session_app.js").read_text(encoding="utf-8")
        style = (package_root / "static/session_style.css").read_text(encoding="utf-8")
        trial_render = script[
            script.index("  renderTrialPanel(") : script.index("\n  title(value)")
        ]
        self.assertIn('class="trial-workspace"', trial_render)
        self.assertIn('class="trial-main-column"', trial_render)
        self.assertIn('class="card trial-progress-card"', trial_render)
        self.assertLess(
            trial_render.index('class="trial-hero-heading"'),
            trial_render.index('class="trial-hero-status"'),
        )
        self.assertLess(
            trial_render.index('class="trial-hero-status"'),
            trial_render.index('class="trial-workspace"'),
        )
        self.assertIn(
            '<dl class="participant-summary trial-process-summary" aria-label="Process status">${processItems}</dl>',
            trial_render,
        )
        self.assertLess(trial_render.index('["Mode", block.mode]'), trial_render.index('["Stack", experiment.stack.status]'))
        self.assertNotIn("Development configuration", trial_render)
        self.assertNotIn("Provisional", trial_render)
        self.assertNotIn("These thresholds are development values", trial_render)
        self.assertLess(
            trial_render.index("Current trial"),
            trial_render.index("Incident occurrences"),
        )
        self.assertLess(
            trial_render.index("Incident occurrences"),
            trial_render.index("Calibrated start positioning"),
        )
        self.assertLess(
            trial_render.index('data-block-go-to="starting_point"'),
            trial_render.index('data-block-go-to="target_out_${target}"'),
        )
        self.assertIn(".trial-progress-card { position: sticky", style)
        self.assertIn(".trial-progress-card .segment-list", style)
        self.assertIn(".trial-hero-card { display: grid; grid-template-columns:", style)
        self.assertIn(".trial-process-summary { display: grid; width: 100%; grid-template-columns: repeat(4,", style)
        self.assertIn(".trial-hero-card { grid-template-columns: 1fr; }", style)
        self.assertIn("@media (max-width: 900px)", style)


if __name__ == "__main__":
    unittest.main()

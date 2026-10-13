import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from snake_experiment_ui.overload import ParticipantOverloadWarning
from snake_experiment_ui.session_web_app import create_session_app


def protection(**changes):
    return {
        "instance": "manager-1", "event": 1, "state": "translating",
        "reason": "overload", "blocked": True, "fresh": True,
        "overloaded": True, "overload_valid": True, "pose_valid": True,
        "phase_history": [], **changes,
    }


class ParticipantOverloadWarningTest(unittest.TestCase):
    def assertWarning(self, result, phase):
        self.assertEqual(set(result), {"phase", "episode_id"})
        self.assertEqual(result["phase"], phase)
        self.assertRegex(result["episode_id"], r"^[a-f0-9]{32}$")

    def test_retreat_phases_and_disabled_or_failed_retreat(self):
        for state, reason, expected in (
            ("waiting_retreat", "overload", "waiting_retreat"),
            ("translating", "overload", "retreating"),
            ("rotating", "overload", "retreating"),
            ("waiting_clear", "waiting_for_clear_force", "waiting_clear"),
            ("locked", "completed", "retreat_complete"),
            ("locked", "retreat_disabled", "paused"),
            ("error", "no_compatible_calibration", "paused"),
            ("error", "invalid_calibration_pose", "paused"),
        ):
            with self.subTest(state=state, reason=reason):
                projector = ParticipantOverloadWarning()
                raw = protection(state=state, reason=reason, overloaded=False)
                before = copy.deepcopy(raw)
                self.assertWarning(projector.snapshot(raw, True), expected)
                self.assertEqual(raw, before)

    def test_signal_or_history_identifies_an_episode_without_current_force(self):
        projector = ParticipantOverloadWarning()
        self.assertWarning(projector.snapshot(protection(state="error", reason="operator_stop"), True), "paused")
        for historical in ("waiting_retreat", "translating", "waiting_clear", "rotating", "locked"):
            with self.subTest(historical=historical):
                projector = ParticipantOverloadWarning()
                raw = protection(
                    state="error", reason="invalid_feedback", overloaded=False,
                    phase_history=[{"state": historical, "elapsed_sec": 0.1}],
                )
                self.assertWarning(projector.snapshot(raw, True), "paused")

    def test_telemetry_fault_or_startup_alone_never_announces_overload(self):
        for raw in (
            {},
            protection(fresh=False),
            protection(event=0),
            protection(state="error", reason="invalid_pose", overloaded=False),
            protection(state="error", reason="invalid_overload_feedback", overloaded=False),
            protection(state="error", reason="invalid_feedback", overloaded=True, overload_valid=False),
            protection(state="error", reason="invalid_feedback", overloaded=False, phase_history="invalid"),
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(ParticipantOverloadWarning().snapshot(raw, True))

    def test_loss_of_freshness_preserves_warning_until_rearm_or_stack_stop(self):
        projector = ParticipantOverloadWarning()
        self.assertWarning(projector.snapshot(protection(), True), "retreating")
        self.assertWarning(projector.snapshot(protection(overloaded=False), True), "retreating")
        self.assertWarning(projector.snapshot({"fresh": False, "blocked": True}, True), "unavailable")
        self.assertWarning(projector.snapshot(protection(overload_valid=False), True), "paused")
        normal = protection(state="normal", blocked=False, reason="rearmed", overloaded=False)
        self.assertIsNone(projector.snapshot(normal, True))
        self.assertIsNone(projector.snapshot({"fresh": False, "blocked": True}, True))
        projector.snapshot(protection(), True)
        self.assertIsNone(projector.snapshot(protection(), False))
        self.assertIsNone(projector.snapshot({"fresh": False, "blocked": True}, True))

    def test_new_manager_or_episode_does_not_inherit_old_overload(self):
        for changed in ({"instance": "manager-2"}, {"event": 2}):
            projector = ParticipantOverloadWarning()
            projector.snapshot(protection(), True)
            raw = protection(state="error", reason="invalid_pose", overloaded=False, **changed)
            self.assertIsNone(projector.snapshot(raw, True))

    def test_episode_id_stays_stable_and_changes_only_for_new_episode(self):
        projector = ParticipantOverloadWarning()
        first = projector.snapshot(protection(state="waiting_retreat"), True)["episode_id"]
        for phase in ("translating", "waiting_clear", "rotating", "locked"):
            self.assertEqual(projector.snapshot(protection(state=phase), True)["episode_id"], first)
        self.assertEqual(projector.snapshot({"fresh": False, "blocked": True}, True)["episode_id"], first)
        second = projector.snapshot(protection(event=2), True)["episode_id"]
        self.assertNotEqual(first, second)
        third = projector.snapshot(protection(instance="manager-2", event=2), True)["episode_id"]
        self.assertNotIn(third, (first, second))


class ParticipantOverloadProjectionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.raw = protection(target="private-target", original_target={"private_pose": 42})
        self.stack = {"status": "active"}
        self.public = {"view": "waiting", "video_available": False}
        self.app = create_session_app(
            SimpleNamespace(snapshot=lambda: {}), Path(self.directory.name), Path(self.directory.name),
            experiment=SimpleNamespace(participant_snapshot=lambda: self.public),
            protection_snapshot=lambda: self.raw, stack_snapshot=lambda: self.stack,
            overload_sound_duration_sec=1.25,
        )

    def tearDown(self):
        self.directory.cleanup()

    async def test_http_projection_is_minimal_on_all_participant_screens(self):
        endpoint = next(route.endpoint for route in self.app.routes if route.path == "/participant/api/state")
        for view in ("waiting", "A", "B", "C"):
            self.public["view"] = view
            result = await endpoint()
            self.assertEqual(result["view"], view)
            warning = result["overload_warning"]
            self.assertEqual(set(warning), {"phase", "episode_id", "duration_sec"})
            self.assertEqual(warning["phase"], "retreating")
            self.assertEqual(warning["duration_sec"], 1.25)
            self.assertNotIn("overload_warning", self.public)
            for private in ("private-target", "private_pose", "original_target", "manager-1"):
                self.assertNotIn(private, json.dumps(result))
        self.stack["status"] = "inactive"
        self.assertIsNone((await endpoint())["overload_warning"])

    async def test_websocket_reports_phases_without_changing_participant_view(self):
        messages = []
        class Socket:
            async def accept(self): pass
            async def send_json(self, message): messages.append(message)
        next_states = iter([
            protection(state="rotating", overloaded=False),
            protection(state="waiting_clear", reason="waiting_for_clear_force", overloaded=False),
            protection(state="locked", reason="completed", overloaded=False),
            protection(state="normal", blocked=False, reason="rearmed", overloaded=False),
            None,
        ])
        async def tick(_):
            raw = next(next_states)
            if raw is None:
                self.app.state.shutting_down = True
            else:
                self.raw = raw
        endpoint = next(route.endpoint for route in self.app.routes if route.path == "/participant/ws")
        with patch("snake_experiment_ui.session_web_app.asyncio.sleep", side_effect=tick):
            await endpoint(Socket())
        self.assertEqual([message["overload_warning"]["phase"] if message["overload_warning"] else None
                          for message in messages], ["retreating", "waiting_clear", "retreat_complete", None])
        warnings = [message["overload_warning"] for message in messages if message["overload_warning"]]
        self.assertEqual(len({warning["episode_id"] for warning in warnings}), 1)
        self.assertTrue(all(warning["duration_sec"] == 1.25 for warning in warnings))
        self.assertTrue(all(message["view"] == "waiting" for message in messages))

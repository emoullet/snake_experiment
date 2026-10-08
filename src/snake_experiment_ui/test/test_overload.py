import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest

from snake_experiment_ui.calibration import CalibrationError
from snake_experiment_ui.gripper_gate import GripperGate
from snake_experiment_ui.overload import ProtectionState, ProtectionError, recovery_parameters, EventJournal, protection_action_state
from snake_experiment_ui.session_web_app import create_session_app


def calibration(robot):
    return {"schema_version": 1, "reference_frame": "base_link",
            "robot": {"id": robot, "ee_frame": "ft_frame" if robot == "explorer" else "end_effector_link"},
            "poses": {f"target_out_{i}": {"frame_id": "base_link", "position": {"x": .1*i, "y": 0., "z": .4},
                "orientation": {"x": 0., "y": 0., "z": 0., "w": 2.}} for i in (1, 2, 3)}}


def status(robot="kinova", state="normal", event=0, **changes):
    values = {"instance": "test-controller", "enabled": "true", "blocked": "false" if state == "normal" else "true",
              "force_valid": "true", "pose_valid": "true", "reason": "overload" if event else "none",
              "event": str(event), "target": "target_out_1" if event else "", "flipped": "false",
              "force_source": "fake_hardware_no_contact", "force_norm_N": "0",
              "initial_pose": "null", "original_target": "null", "adapted_target": "null"}
    values.update(changes)
    return NS(header=NS(stamp=NS(sec=10, nanosec=0)), status=[NS(name="overload_protection", hardware_id=robot,
        message=state, values=[NS(key=k, value=v) for k, v in values.items()])])


@pytest.mark.parametrize("robot", ["explorer", "kinova"])
def test_targets_are_complete_normalized_and_robot_specific(robot):
    result = recovery_parameters(calibration(robot), robot)
    assert len(result["overload_protection.out_positions"]) == 9
    assert result["overload_protection.out_orientations"] == [0., 0., 0., 1.]*3
    assert result["overload_protection.ee_frame"] == ("ft_frame" if robot == "explorer" else "end_effector_link")


@pytest.mark.parametrize("corruption", ["robot", "frame", "pose_frame", "missing", "nan", "quaternion", "overflow"])
def test_invalid_calibrations_cannot_install_partial_targets(corruption):
    document = calibration("kinova")
    pose = document["poses"]["target_out_2"]
    if corruption == "robot": document["robot"]["id"] = "explorer"
    if corruption == "frame": document["reference_frame"] = "world"
    if corruption == "pose_frame": pose["frame_id"] = "world"
    if corruption == "missing": del document["poses"]["target_out_3"]
    if corruption == "nan": pose["position"]["x"] = float("nan")
    if corruption == "quaternion": pose["orientation"]["w"] = 0
    if corruption == "overflow": pose["orientation"]["w"] = 1e308
    with pytest.raises(CalibrationError): recovery_parameters(document, "kinova")


def test_queued_startup_and_telemetry_states_do_not_override_live_protection():
    clear = {"state": "normal", "blocked": False, "fresh": True, "event": 0}
    startup = {"state": "normal", "blocked": True, "fresh": True, "event": 0}
    stale = {"state": "unavailable", "blocked": True, "fresh": False, "event": 0}
    assert protection_action_state(startup, clear) is clear
    assert protection_action_state(stale, clear) is clear
    assert protection_action_state(clear, stale) is stale
    episode = {"state": "locked", "blocked": True, "fresh": True, "event": 1}
    assert protection_action_state(episode, clear) is episode
    assert protection_action_state(episode, stale) is episode


def test_status_fail_closed_and_freshness():
    tick = [0.]
    monitor = ProtectionState("kinova", lambda: tick[0])
    with pytest.raises(ProtectionError, match="absent ou périmé"): monitor.require_clear()
    assert not monitor.record(status(), ros_now=10.1)["blocked"]
    monitor.require_clear()
    tick[0] = .51
    assert monitor.snapshot()["blocked"]
    with pytest.raises(ProtectionError, match="absent ou périmé"): monitor.require_clear()
    for invalid in (status(robot="explorer"), status(enabled="false"), status(force_valid="false"), status(force_norm_N="nan"),
                    status(state="locked", blocked="false"), NS(status=[])):
        assert monitor.record(invalid)["blocked"]
    assert monitor.record(status(), ros_now=11)["blocked"]


def test_gripper_drops_cached_commands_and_holds_measured_position():
    tick = [0.]
    published = []
    gate = GripperGate("kinova", published.append, lambda: tick[0])
    gate.command(NS(data=[.8]))
    assert published == []
    gate.status.record(status())
    gate.joints(NS(name=["robotiq_85_left_knuckle_joint"], position=[.3]))
    gate.command(NS(data=[.8]))
    assert published == [[.8]]
    gate.status.record(status(state="translating", event=1))
    gate.tick()
    gate.command(NS(data=[0.]))
    assert published == [[.8], [.3]]
    gate.status.record(status(event=1))
    gate.tick()
    assert published == [[.8], [.3]]  # no replay on rearm
    gate.command(NS(data=[float("nan")]))
    assert published == [[.8], [.3]]
    gate.command(NS(data=[0.]))
    assert published[-1] == [0.]
    tick[0] = .51
    gate.command(NS(data=[.8]))
    assert published[-1] == [0.]


def test_event_journal_deduplicates_transitions_and_distinguishes_restarts(tmp_path):
    journal = EventJournal()
    path = tmp_path / "safety/overload_events.jsonl"
    journal.append(path, {"event": 0})
    assert not path.exists()
    event = {"instance": "a", "event": 1, "state": "translating", "reason": "overload"}
    journal.append(path, event)
    journal.append(path, event)
    journal.append(path, dict(event, state="locked", reason="completed"))
    journal.append(path, dict(event, instance="b"))
    assert len(path.read_text().splitlines()) == 3


def test_api_guards_commands_but_allows_stop_and_incident_review(tmp_path):
    class Checkup:
        def snapshot(self): return {"current_view": "B", "workflow": "stack_ready"}
        def start_mode(self, mode): raise AssertionError("Forbidden callback executed")
        def start_go_to(self, pose): raise AssertionError("Forbidden callback executed")
        def stop_go_to(self): return self.snapshot()
    state = {"blocked": True, "state": "locked"}
    def guard(): raise ProtectionError("locked")
    actions = []
    app = create_session_app(Checkup(), tmp_path, tmp_path, protection_snapshot=lambda: state,
        protection_guard=guard, protection_action=actions.append)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ("/api/checkup/modes/baseline/start", "/api/checkup/go-to", "/api/experiment/blocks/x/recording/start", "/api/experiment/blocks/x/training/prepare"):
                response = await client.post(path, json={"pose_id": "target_1"})
                assert response.status_code == 409
            assert (await client.post("/api/checkup/go-to/stop")).status_code == 200
            assert (await client.post("/api/protection/stop")).status_code == 200
            assert (await client.get("/api/state")).json()["protection"] == state
            assert actions == ["stop"]
    asyncio.run(scenario())


def test_gripper_holds_before_joint_feedback_expires():
    tick = [0.]
    published = []
    gate = GripperGate("kinova", published.append, lambda: tick[0])
    gate.status.record(status())
    gate.joints(NS(name=["robotiq_85_left_knuckle_joint"], position=[.2]))
    gate.command(NS(data=[.8]))
    tick[0] = .42
    gate.tick()
    assert published == [[.8], [.2]]
    gate.command(NS(data=[0.]))
    assert published == [[.8], [.2]]

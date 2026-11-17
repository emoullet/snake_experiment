from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from snake_experiment_ui.input_sources import enabled_input_topics
from snake_experiment_ui.diagnostics import DiagnosticMonitor, DiagnosticProfile
from snake_experiment_ui.experiment import _apply_input_topic_policy, _topic_policy


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("robot", ["explorer", "kinova"])
def test_robot_sources(robot):
    topics = enabled_input_topics(ROOT / "bringup/cartesian_manager/config" / f"{robot}_params.yaml")
    assert "/hand_joystick_cartesian_command" in topics
    assert "/mouse_joystick_cartesian_command" in topics
    assert ("/tablet_cartesian_command" in topics) == (robot == "kinova")


def test_sources_custom_disabled_and_invalid(tmp_path):
    path = tmp_path / "profile.yaml"
    params = {"inputs": {"sources": ["joystick", "hand_joystick"],
                         "joystick": {"enabled": False}},
              "topics": {"hand_joystick_command": "/custom"}}
    def write():
        path.write_text(yaml.safe_dump({"cartesian_manager": {"ros__parameters": params}}))
    write()
    assert enabled_input_topics(path) == ("/custom",)
    params["inputs"]["hand_joystick"] = {"enabled": False}
    write()
    with pytest.raises(ValueError, match="no enabled"):
        enabled_input_topics(path)
    params["inputs"]["sources"] = ["unknown"]
    write()
    with pytest.raises(ValueError, match="unknown"):
        enabled_input_topics(path)


def test_diagnostic_group_without_joy():
    profile = DiagnosticProfile(ROOT / "config/system_checkup.yaml")
    source = "/hand_joystick_cartesian_command"
    graph = {"nodes": [*profile.required_nodes, "/joystick_mapper"],
             "controllers": {name: "active" for name in profile.required_controllers},
             "topics": {}, "mapper_modes": list(profile.modes["baseline"].mapper_modes),
             "mapper_parameters": dict(profile.modes["baseline"].mapper_parameters)}
    monitor = DiagnosticMonitor(profile, lambda: graph, lambda: 100.0, input_topics=(source, "/mouse_joystick_cartesian_command"))
    graph["mapper_parameter_names"] = list(monitor.expected_mapper_parameter_names("baseline"))
    graph["topics"] = {topic: [req.message_type] for topic, req in {**profile.base_topics, **profile.mode_topics}.items()}
    graph["topics"][source] = ["geometry_msgs/msg/TwistStamped"]
    vector = lambda x=0.0: SimpleNamespace(x=x, y=0.0, z=0.0)
    command = SimpleNamespace(twist=SimpleNamespace(linear=vector(0.1), angular=vector()))
    for _ in range(10):
        monitor.record(source, command)
    check = next(c for c in monitor.evaluate("baseline")["checks"] if c["name"] == "input_command:any")
    assert check["passed"]
    assert monitor.evaluate("baseline")["joystick_activity"]
    command.twist.linear.x = float("nan")
    monitor.record(source, command)
    assert not next(c for c in monitor.evaluate("baseline")["checks"] if c["name"] == "input_command:any")["passed"]
    monitor.reset_mode_observation()
    assert not monitor.evaluate("baseline")["joystick_activity"]


def test_bag_any_of_preserves_legacy():
    settings = {"rosbag_topics": ["/joy", "/hand", "/mouse"],
                "rosbag_optional_topics": ["/joy", "/hand", "/mouse"],
                "rosbag_required_any_topics": ["/hand", "/mouse"]}
    record = _topic_policy(settings)
    result = {"valid": True, "message_counts": {"/hand": 1}}
    assert _apply_input_topic_policy(result, record)["valid"]
    assert record["observed_input_topics"] == ["/hand"]
    result["message_counts"] = {"/joy": 1}
    assert not _apply_input_topic_policy(result, record)["valid"]
    assert record["input_topics_error"]
    assert _apply_input_topic_policy(result, {})["valid"]

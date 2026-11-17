"""Resolve the cartesian manager's enabled input topics."""

from pathlib import Path

import yaml


SOURCES = ("joystick", "tablet", "visual_servoing", "hand_joystick", "face_joystick", "mouse_joystick")


def enabled_input_topics(path: Path) -> tuple[str, ...]:
    try:
        params = yaml.safe_load(Path(path).read_text())["cartesian_manager"]["ros__parameters"]
        inputs = params["inputs"]
        sources = inputs["sources"]
        if not isinstance(sources, list) or not sources or len(sources) != len(set(sources)):
            raise ValueError("sources must be a nonempty unique list")
        topics = []
        for source in sources:
            if source not in SOURCES:
                raise ValueError(f"unknown input source: {source}")
            enabled = inputs.get(source, {}).get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError(f"invalid enabled flag for {source}")
            if enabled:
                topic = params.get("topics", {}).get(f"{source}_command", f"/{source}_cartesian_command")
                if not isinstance(topic, str) or not topic.startswith("/") or topic == "/":
                    raise ValueError(f"invalid command topic for {source}")
                if topic not in topics:
                    topics.append(topic)
        if not topics:
            raise ValueError("no enabled input sources")
        return tuple(topics)
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"Invalid cartesian input configuration {path}: {error}") from error

"""Video locations for the participant-facing interface."""

from pathlib import Path

import yaml


class ParticipantProfileError(ValueError):
    """The participant interface configuration is invalid."""


def video_is_available(path: Path | str | None) -> bool:
    """Only existing MP4 files can be shown in the participant interface."""
    if not path:
        return False
    candidate = Path(path).expanduser()
    return candidate.suffix.lower() == ".mp4" and candidate.is_file()


class ParticipantProfile:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise ParticipantProfileError(
                f"Participant interface profile is unreadable: {self.path}"
            ) from error
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ParticipantProfileError("Unsupported participant interface profile schema.")
        videos = data.get("videos")
        explanations = videos.get("mode_explanation") if isinstance(videos, dict) else None
        if not isinstance(videos, dict) or not isinstance(explanations, dict):
            raise ParticipantProfileError("Participant interface video paths are incomplete.")
        values = (
            videos.get("experiment_presentation"),
            explanations.get("baseline"),
            explanations.get("snake"),
        )
        if any(not isinstance(value, str) for value in values):
            raise ParticipantProfileError("Participant interface video paths must be strings.")
        if any(value.strip() and Path(value).suffix.lower() != ".mp4" for value in values):
            raise ParticipantProfileError("Participant interface videos must be MP4 files.")
        self.data = data

    def video_path(self, kind: str, mode: str | None = None) -> Path | None:
        if kind == "experiment_presentation" and mode is None:
            value = self.data["videos"][kind]
        elif kind == "mode_explanation" and mode in ("baseline", "snake"):
            value = self.data["videos"][kind][mode]
        else:
            raise ParticipantProfileError("Unknown participant video slot.")
        if not value.strip():
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.path.parent / path
        return path.resolve()

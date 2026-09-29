"""Media locations for the participant-facing interface."""

from pathlib import Path

import yaml


class ParticipantProfileError(ValueError):
    """The participant interface configuration is invalid."""


STATE_NAMES = {"baseline": ("b1", "b2", "b3"), "snake": ("b1", "b2")}


def video_is_available(path: Path | str | None) -> bool:
    """Only existing MP4 files can be shown in the participant interface."""
    if not path:
        return False
    candidate = Path(path).expanduser()
    return candidate.suffix.lower() == ".mp4" and candidate.is_file()


def state_image_is_available(path: Path | str | None) -> bool:
    """Only existing PNG files can be shown as state explanations."""
    if not path:
        return False
    candidate = Path(path).expanduser()
    return candidate.suffix.lower() == ".png" and candidate.is_file()


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
        images = data.get("state_images")
        if images is None:
            images = {
                mode: {state: "" for state in states}
                for mode, states in STATE_NAMES.items()
            }
        if not isinstance(images, dict) or any(
            not isinstance(images.get(mode), dict)
            or any(
                not isinstance(images[mode].get(state), str)
                or (
                    images[mode][state].strip()
                    and Path(images[mode][state]).suffix.lower() != ".png"
                )
                for state in states
            )
            for mode, states in STATE_NAMES.items()
        ):
            raise ParticipantProfileError(
                "Participant state images need PNG paths for baseline b1/b2/b3 and snake b1/b2."
            )
        data["state_images"] = images
        self.data = data

    def _resolve_media_path(self, value: str) -> Path | None:
        if not value.strip():
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.path.parent / path
        return path.resolve()

    def video_path(self, kind: str, mode: str | None = None) -> Path | None:
        if kind == "experiment_presentation" and mode is None:
            value = self.data["videos"][kind]
        elif kind == "mode_explanation" and mode in ("baseline", "snake"):
            value = self.data["videos"][kind][mode]
        else:
            raise ParticipantProfileError("Unknown participant video slot.")
        return self._resolve_media_path(value)

    def state_image_path(self, mode: str, state: str) -> Path | None:
        if state not in STATE_NAMES.get(mode, ()):
            raise ParticipantProfileError("Unknown participant state image slot.")
        return self._resolve_media_path(self.data["state_images"][mode][state])

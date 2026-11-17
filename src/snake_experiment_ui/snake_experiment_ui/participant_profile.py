"""Media locations for the participant-facing interface."""

from pathlib import Path
import math

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


class ParticipantProfile:
    """Validated media and display settings for the participant interface."""

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
        self.default_language = data.get("default_language", "english")
        if self.default_language not in ("english", "french"):
            raise ParticipantProfileError("default_language must be english or french.")
        self.mapping_description = data.get("mapping_description", "demo")
        if self.mapping_description not in ("demo", "text"):
            raise ParticipantProfileError("mapping_description must be demo or text.")
        self.mapping_text = data.get("mapping_text", {})
        if self.mapping_description == "text" or "mapping_text" in data:
            for robot in ("explorer", "kinova"):
                for mode, submodes in STATE_NAMES.items():
                    for submode in submodes:
                        try:
                            labels = self.mapping_text[robot][mode][submode]
                        except (KeyError, TypeError):
                            raise ParticipantProfileError(f"Missing mapping_text for {robot}/{mode}/{submode}.") from None
                        if not isinstance(labels, list) or len(labels) != 2 or any(not isinstance(label, str) or not label.strip() for label in labels):
                            raise ParticipantProfileError(f"mapping_text for {robot}/{mode}/{submode} must contain two nonempty strings.")
        self.mapping_text_french = data.get("mapping_text_french", {})
        if "mapping_text_french" in data:
            for robot in ("explorer", "kinova"):
                for mode, submodes in STATE_NAMES.items():
                    for submode in submodes:
                        try:
                            labels = self.mapping_text_french[robot][mode][submode]
                        except (KeyError, TypeError):
                            raise ParticipantProfileError(f"Missing mapping_text_french for {robot}/{mode}/{submode}.") from None
                        if not isinstance(labels, list) or len(labels) != 2 or any(not isinstance(label, str) or not label.strip() for label in labels):
                            raise ParticipantProfileError(f"mapping_text_french for {robot}/{mode}/{submode} must contain two nonempty strings.")
        preview = data.get("preview", {})
        if not isinstance(preview, dict) or preview.get("default_view", "view_1") not in (
            "view_1", "view_2", "view_3", "view_4", "view_5", "view_6"
        ):
            raise ParticipantProfileError("Participant preview default_view must be view_1 through view_6.")
        self.preview_default_view = preview.get("default_view", "view_1")
        sounds = data.get("sounds", {})
        overload = sounds.get("overload", {}) if isinstance(sounds, dict) else None
        if not isinstance(overload, dict):
            raise ParticipantProfileError("Participant overload sound configuration must be a mapping.")
        duration = overload.get("duration_sec", 3.0)
        try:
            valid_duration = type(duration) in (int, float) and math.isfinite(duration) and duration > 0
        except OverflowError:
            valid_duration = False
        if not valid_duration:
            raise ParticipantProfileError("Participant overload sound duration_sec must be finite and positive.")
        self.overload_sound_duration_sec = float(duration)
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

"""Configurable, ROS-agnostic diagnostics for the Panel B system check-up."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Dict, Iterable, Mapping, Optional

import yaml


class DiagnosticProfileError(RuntimeError):
    """Raised when the diagnostic profile is incomplete or invalid."""


@dataclass(frozen=True)
class TopicRequirement:
    message_type: str
    min_rate_hz: float


@dataclass(frozen=True)
class ModeRequirement:
    mode_request: str
    mapper_modes: tuple[str, ...]
    mapper_config: Path
    mapper_config_digest: str
    mapper_parameters: Mapping[str, object]


class DiagnosticProfile:
    """Validated representation of the versioned check-up YAML profile."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        raw_bytes = self.path.read_bytes()
        raw = yaml.safe_load(raw_bytes) or {}
        if int(raw.get("schema_version", 0)) != 1:
            raise DiagnosticProfileError("Unsupported diagnostic profile schema.")
        self.schema_version = 1
        self.digest = hashlib.sha256(raw_bytes).hexdigest()
        self.measurement_window_sec = float(raw["measurement_window_sec"])
        self.diagnostic_timeout_sec = float(raw["diagnostic_timeout_sec"])
        self.joystick_deadzone = float(raw["joystick_deadzone"])
        if self.measurement_window_sec <= 0 or self.diagnostic_timeout_sec <= 0:
            raise DiagnosticProfileError("Diagnostic time windows must be positive.")
        if not 0 <= self.joystick_deadzone < 1:
            raise DiagnosticProfileError("joystick_deadzone must be in [0, 1).")
        self.required_nodes = tuple(str(value) for value in raw["required_nodes"])
        self.required_controllers = tuple(
            str(value) for value in raw["required_controllers"]
        )
        self.base_topics = self._topics(raw["base_topics"])
        self.mode_topics = self._topics(raw["mode_topics"])
        self.modes = {}
        for name, values in raw["modes"].items():
            mapper_config = (self.path.parent / str(values["mapper_config"])).resolve()
            mapper_bytes = mapper_config.read_bytes()
            mapper_raw = yaml.safe_load(mapper_bytes) or {}
            try:
                ros_parameters = mapper_raw["joystick_mapper"]["ros__parameters"]
            except (KeyError, TypeError) as error:
                raise DiagnosticProfileError(
                    f"Invalid mapper configuration: {mapper_config}"
                ) from error
            self.modes[name] = ModeRequirement(
                mode_request=str(values["mode_request"]),
                mapper_modes=tuple(str(value) for value in values["mapper_modes"]),
                mapper_config=mapper_config,
                mapper_config_digest=hashlib.sha256(mapper_bytes).hexdigest(),
                mapper_parameters=self._flatten_parameters(ros_parameters),
            )
        if set(self.modes) != {"baseline", "snake"}:
            raise DiagnosticProfileError("Profile must define baseline and snake modes.")
        self.expected_revisions = {
            str(path): str(revision)
            for path, revision in (raw.get("expected_revisions") or {}).items()
            if revision is not None and str(revision).strip()
        }

    @staticmethod
    def _topics(raw: Mapping[str, Mapping]) -> Dict[str, TopicRequirement]:
        result = {}
        for name, values in raw.items():
            rate = float(values["min_rate_hz"])
            if rate < 0:
                raise DiagnosticProfileError(f"Negative topic rate for {name}.")
            result[str(name)] = TopicRequirement(str(values["type"]), rate)
        return result

    @classmethod
    def _flatten_parameters(cls, values: Mapping, prefix: str = "") -> Dict:
        flattened = {}
        for name, value in values.items():
            parameter_name = f"{prefix}.{name}" if prefix else str(name)
            if isinstance(value, Mapping):
                flattened.update(cls._flatten_parameters(value, parameter_name))
            else:
                flattened[parameter_name] = value
        return flattened


def _normalise_node_name(name: str) -> str:
    return "/" + name.strip("/")


def _finite(values: Iterable[float]) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError):
        return False


class DiagnosticMonitor:
    """Collect telemetry and evaluate it against a DiagnosticProfile."""

    def __init__(
        self,
        profile: DiagnosticProfile,
        graph_provider: Callable[[], Mapping],
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profile = profile
        self._graph_provider = graph_provider
        self._clock = monotonic_clock
        self._lock = threading.RLock()
        self._samples: Dict[str, deque[float]] = {
            name: deque()
            for name in set(profile.base_topics) | set(profile.mode_topics)
        }
        self._message_validity: Dict[str, tuple[bool, Optional[str]]] = {}
        self._last_joy_axes: Optional[tuple[float, ...]] = None
        self._last_joy_buttons: Optional[tuple[int, ...]] = None
        self._joystick_activity = False
        self._latest_mode_request: Optional[str] = None

    def reset_mode_observation(self) -> None:
        with self._lock:
            self._joystick_activity = False
            self._last_joy_axes = None
            self._last_joy_buttons = None
            self._latest_mode_request = None
            for topic in self.profile.mode_topics:
                self._samples[topic].clear()
                self._message_validity.pop(topic, None)

    def record(self, topic: str, message, received_at: Optional[float] = None) -> None:
        """Record one typed ROS-like message; plain test doubles are supported."""
        now = self._clock() if received_at is None else received_at
        with self._lock:
            if topic in self._samples:
                self._samples[topic].append(now)
                self._prune(topic, now)
            valid, error = self._validate_message(topic, message)
            self._message_validity[topic] = (valid, error)
            if topic == "/joy":
                axes = tuple(float(value) for value in getattr(message, "axes", ()))
                buttons = tuple(int(value) for value in getattr(message, "buttons", ()))
                moved = any(abs(value) > self.profile.joystick_deadzone for value in axes)
                if self._last_joy_axes is not None:
                    moved = moved or any(
                        abs(current - previous) > self.profile.joystick_deadzone
                        for current, previous in zip(axes, self._last_joy_axes)
                    )
                changed_button = (
                    self._last_joy_buttons is not None
                    and buttons != self._last_joy_buttons
                )
                self._joystick_activity |= moved or changed_button
                self._last_joy_axes = axes
                self._last_joy_buttons = buttons
            elif topic == "/mode_request":
                self._latest_mode_request = str(getattr(message, "data", ""))

    def _prune(self, topic: str, now: float) -> None:
        cutoff = now - self.profile.measurement_window_sec
        samples = self._samples[topic]
        while samples and samples[0] < cutoff:
            samples.popleft()

    @staticmethod
    def _validate_message(topic: str, message) -> tuple[bool, Optional[str]]:
        if topic == "/joint_states":
            names = tuple(getattr(message, "name", ()))
            positions = tuple(getattr(message, "position", ()))
            if not names or len(names) != len(positions):
                return False, "Joint names and positions are missing or inconsistent."
            if not _finite(positions):
                return False, "Joint positions contain a non-finite value."
        elif topic == "/ee_pose":
            frame_id = str(getattr(getattr(message, "header", None), "frame_id", ""))
            pose = getattr(message, "pose", None)
            position = getattr(pose, "position", None)
            orientation = getattr(pose, "orientation", None)
            values = [
                getattr(position, key, float("nan")) for key in ("x", "y", "z")
            ] + [
                getattr(orientation, key, float("nan"))
                for key in ("x", "y", "z", "w")
            ]
            if not frame_id.strip():
                return False, "End-effector pose has no frame ID."
            if not _finite(values):
                return False, "End-effector pose contains a non-finite value."
        elif topic in ("/joystick_cartesian_command", "/cartesian_command"):
            twist = getattr(message, "twist", None)
            linear = getattr(twist, "linear", None)
            angular = getattr(twist, "angular", None)
            values = [
                getattr(vector, key, float("nan"))
                for vector in (linear, angular)
                for key in ("x", "y", "z")
            ]
            if not _finite(values):
                return False, "Cartesian command contains a non-finite value."
        elif topic == "/joy":
            if not _finite(getattr(message, "axes", ())):
                return False, "Joystick axes contain a non-finite value."
        return True, None

    def _rate(self, topic: str, now: float) -> float:
        self._prune(topic, now)
        return len(self._samples[topic]) / self.profile.measurement_window_sec

    def evaluate(self, mode: str) -> dict:
        """Return detailed pass/fail evidence for the selected mode."""
        if mode not in self.profile.modes:
            raise DiagnosticProfileError(f"Unknown profile mode: {mode}")
        graph = dict(self._graph_provider())
        nodes = {_normalise_node_name(name) for name in graph.get("nodes", ())}
        topics = {
            str(name): tuple(types) for name, types in graph.get("topics", {}).items()
        }
        controllers = dict(graph.get("controllers", {}))
        mapper_names = tuple(graph.get("mapper_modes", ()))
        mapper_parameter_names = set(graph.get("mapper_parameter_names", ()))
        mapper_parameters = dict(graph.get("mapper_parameters", {}))
        now = self._clock()
        checks = []

        for node in (*self.profile.required_nodes, "/joystick_mapper"):
            passed = _normalise_node_name(node) in nodes
            checks.append(self._check(f"node:{node}", passed, "present", passed))
        for controller in self.profile.required_controllers:
            actual = controllers.get(controller)
            checks.append(
                self._check(
                    f"controller:{controller}", actual == "active", "active", actual
                )
            )

        requirements = dict(self.profile.base_topics)
        requirements.update(self.profile.mode_topics)
        for topic, requirement in requirements.items():
            observed_types = topics.get(topic, ())
            type_ok = requirement.message_type in observed_types
            checks.append(
                self._check(
                    f"topic_type:{topic}",
                    type_ok,
                    requirement.message_type,
                    list(observed_types),
                )
            )
            if requirement.min_rate_hz > 0:
                rate = self._rate(topic, now)
                checks.append(
                    self._check(
                        f"topic_rate:{topic}",
                        rate >= requirement.min_rate_hz,
                        requirement.min_rate_hz,
                        round(rate, 3),
                        unit="Hz",
                    )
                )
            valid, error = self._message_validity.get(
                topic, (False, "No message received.")
            )
            checks.append(
                self._check(f"message:{topic}", valid, "valid", error or "valid")
            )

        mode_requirement = self.profile.modes[mode]
        checks.append(
            self._check(
                "mapper:modes.names",
                mapper_names == mode_requirement.mapper_modes,
                list(mode_requirement.mapper_modes),
                list(mapper_names),
            )
        )
        expected_parameter_names = self.expected_mapper_parameter_names(mode)
        missing_parameters = sorted(expected_parameter_names - mapper_parameter_names)
        checks.append(
            self._check(
                "mapper:mode_parameters",
                not missing_parameters,
                "all configured",
                missing_parameters or "all present",
            )
        )
        mismatches = []
        for name, expected in mode_requirement.mapper_parameters.items():
            observed = mapper_parameters.get(name)
            if not self._parameter_matches(expected, observed):
                mismatches.append(
                    {"parameter": name, "expected": expected, "observed": observed}
                )
        checks.append(
            self._check(
                "mapper:parameter_values",
                not mismatches,
                "selected YAML profile",
                mismatches or "all values match",
            )
        )
        checks.append(
            self._check(
                "mode_request",
                self._latest_mode_request == mode_requirement.mode_request,
                mode_requirement.mode_request,
                self._latest_mode_request,
            )
        )
        return {
            "mode": mode,
            "passed": all(check["passed"] for check in checks),
            "joystick_activity": self._joystick_activity,
            "mapper_config": str(mode_requirement.mapper_config),
            "mapper_config_sha256": mode_requirement.mapper_config_digest,
            "checks": checks,
        }

    def expected_mapper_parameter_names(self, mode: str) -> set[str]:
        return set(self.profile.modes[mode].mapper_parameters)

    @staticmethod
    def _parameter_matches(expected, observed) -> bool:
        if isinstance(expected, float) and isinstance(observed, (int, float)):
            return math.isclose(expected, float(observed), rel_tol=1e-9, abs_tol=1e-12)
        if isinstance(expected, list) and isinstance(observed, (list, tuple)):
            return expected == list(observed)
        return expected == observed

    @staticmethod
    def _check(name, passed, expected, observed, unit=None) -> dict:
        result = {
            "name": name,
            "passed": bool(passed),
            "expected": expected,
            "observed": observed,
        }
        if unit:
            result["unit"] = unit
        return result


def collect_git_provenance(repository_root: Path) -> dict:
    """Collect main-repository and recursive submodule revisions without writes."""
    root = Path(repository_root).resolve()

    def run(*arguments: str, cwd: Path = root) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    entries = {}
    try:
        entries["."] = {
            "commit": run("rev-parse", "HEAD"),
            "dirty": bool(run("status", "--porcelain")),
        }
        output = run("submodule", "status", "--recursive")
        for line in output.splitlines():
            fields = line.lstrip(" +-U").split()
            if len(fields) < 2:
                continue
            path = fields[1]
            submodule_root = root / path
            entries[path] = {
                "commit": run("rev-parse", "HEAD", cwd=submodule_root),
                "dirty": bool(run("status", "--porcelain", cwd=submodule_root)),
            }
    except (OSError, subprocess.CalledProcessError) as error:
        return {"error": str(error), "repositories": entries}
    return {"error": None, "repositories": entries}


def evaluate_provenance(provenance: Mapping, expected: Mapping[str, str]) -> list[dict]:
    repositories = provenance.get("repositories", {})
    return [
        {
            "path": path,
            "expected": revision,
            "observed": repositories.get(path, {}).get("commit"),
            "passed": repositories.get(path, {}).get("commit") == revision,
        }
        for path, revision in expected.items()
    ]


def stable_json_digest(value: Mapping) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

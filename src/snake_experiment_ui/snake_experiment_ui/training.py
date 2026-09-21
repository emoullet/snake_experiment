"""Pure training-trial generation and Cartesian pose error helpers."""

from __future__ import annotations

import math
from typing import Iterable, Optional


class TrainingError(RuntimeError):
    """An operator-correctable training workflow error."""


def build_trials(mode: str, settings: dict) -> list[dict]:
    """Build globally numbered trials from configurable cycles and targets."""
    cycles = int(settings["cycles"])
    sequence = list(settings["target_sequence"])
    pattern = settings["trial_folder_pattern"]
    trials = []
    trial_id = 1
    for cycle in range(1, cycles + 1):
        for target_start, target_end in zip(sequence, sequence[1:]):
            trials.append(
                {
                    "id": trial_id,
                    "cycle": cycle,
                    "target_start": int(target_start),
                    "target_end": int(target_end),
                    "folder": pattern.format(
                        mode=mode,
                        trial_id=trial_id,
                        cycle=cycle,
                        target_start=target_start,
                        target_end=target_end,
                    ),
                    "status": "pending",
                    "prepared_at_utc": None,
                    "ready_at_utc": None,
                    "completed_at_utc": None,
                    "decision": None,
                    "attempts": [],
                }
            )
            trial_id += 1
    return trials


def validate_training_settings(
    settings: dict, expected_folder: Optional[str] = None
) -> None:
    """Validate configurable trial sequencing and folder patterns."""
    cycles = settings.get("cycles")
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles <= 0:
        raise TrainingError("Trial cycles must be a positive integer.")
    sequence = settings.get("target_sequence")
    if (
        not isinstance(sequence, list)
        or len(sequence) < 2
        or any(isinstance(item, bool) or item not in (1, 2, 3) for item in sequence)
    ):
        raise TrainingError(
            "Trial target_sequence must contain at least two targets from 1, 2, 3."
        )
    _validate_pattern(
        settings.get("trial_folder_pattern"),
        {
            "mode": "snake",
            "trial_id": 1,
            "cycle": 1,
            "target_start": 1,
            "target_end": 2,
        },
        "trial_folder_pattern",
        expected_folder or "snake_training_trial_001_01_1_2",
    )
    _validate_pattern(
        settings.get("attempt_pattern"),
        {"attempt": 1},
        "attempt_pattern",
        "attempt_001",
    )


def validate_thresholds(thresholds: dict) -> None:
    """Validate positive development thresholds in operator-facing units."""
    required = (
        "linear_mm",
        "angular_deg",
        "start_linear_mm",
        "start_angular_deg",
        "success_dwell_sec",
        "pose_freshness_sec",
    )
    if not isinstance(thresholds, dict):
        raise TrainingError("Training success_thresholds are missing.")
    for name in required:
        value = thresholds.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TrainingError(f"Training threshold {name} must be numeric.")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise TrainingError(f"Training threshold {name} must be positive and finite.")
    if not isinstance(thresholds.get("provisional"), bool):
        raise TrainingError("Training threshold provisional flag must be boolean.")


def effective_thresholds(thresholds: dict) -> dict:
    """Return thresholds with SI values used by the monitor."""
    validate_thresholds(thresholds)
    result = dict(thresholds)
    result.update(
        {
            "linear_m": float(thresholds["linear_mm"]) / 1000.0,
            "angular_rad": math.radians(float(thresholds["angular_deg"])),
            "start_linear_m": float(thresholds["start_linear_mm"]) / 1000.0,
            "start_angular_rad": math.radians(float(thresholds["start_angular_deg"])),
        }
    )
    return result


def pose_error(observed: dict, target: dict) -> tuple[float, float]:
    """Return Euclidean distance and shortest quaternion angle in radians."""
    observed_frame = str(observed.get("frame_id", "")).strip()
    target_frame = str(target.get("frame_id", "")).strip()
    if not observed_frame or not target_frame:
        raise TrainingError("Current and calibrated poses require a reference frame.")
    if observed_frame != target_frame:
        raise TrainingError("Current and calibrated poses use different reference frames.")
    observed_position = _finite_values(observed.get("position", {}), ("x", "y", "z"))
    target_position = _finite_values(target.get("position", {}), ("x", "y", "z"))
    differences = zip(observed_position, target_position)
    linear = math.sqrt(sum((current - expected) ** 2 for current, expected in differences))
    observed_quaternion = _normalised_quaternion(observed.get("orientation", {}))
    target_quaternion = _normalised_quaternion(target.get("orientation", {}))
    dot = abs(sum(a * b for a, b in zip(observed_quaternion, target_quaternion)))
    angular = 2.0 * math.acos(max(-1.0, min(1.0, dot)))
    return linear, angular


def _finite_values(container: dict, names: Iterable[str]) -> tuple[float, ...]:
    try:
        values = tuple(float(container[name]) for name in names)
    except (KeyError, TypeError, ValueError) as error:
        raise TrainingError("Pose values are missing or invalid.") from error
    if not all(math.isfinite(value) for value in values):
        raise TrainingError("Pose contains a non-finite value.")
    return values


def _normalised_quaternion(container: dict) -> tuple[float, ...]:
    values = _finite_values(container, ("x", "y", "z", "w"))
    norm = math.sqrt(sum(value * value for value in values))
    if norm < 1e-9:
        raise TrainingError("Pose quaternion is invalid.")
    return tuple(value / norm for value in values)


def _validate_pattern(pattern, values: dict, name: str, expected: str) -> None:
    try:
        observed = str(pattern).format(**values)
    except (AttributeError, KeyError, ValueError) as error:
        raise TrainingError(f"Invalid training {name}.") from error
    if observed != expected:
        raise TrainingError(f"Training {name} must produce {expected}.")

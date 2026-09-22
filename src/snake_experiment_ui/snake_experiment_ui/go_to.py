"""ROS-agnostic supervision for calibrated Cartesian pose motions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import threading
import time
from typing import Callable, Optional
import uuid

from .training import TrainingError, pose_error


ACTIVE_STATES = ("preparing", "moving", "settling")


class GoToError(RuntimeError):
    """An operator-correctable Cartesian motion error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def pose_document(message) -> dict:
    """Convert a PoseStamped-like object or dictionary to the stored pose shape."""
    if isinstance(message, dict):
        return json.loads(json.dumps(message))
    return {
        "frame_id": str(message.header.frame_id),
        "position": {
            "x": float(message.pose.position.x),
            "y": float(message.pose.position.y),
            "z": float(message.pose.position.z),
        },
        "orientation": {
            "x": float(message.pose.orientation.x),
            "y": float(message.pose.orientation.y),
            "z": float(message.pose.orientation.z),
            "w": float(message.pose.orientation.w),
        },
    }


def validate_pose(pose: dict, expected_frame: str) -> dict:
    """Validate and normalize a calibration pose before commanding motion."""
    value = pose_document(pose)
    frame = str(value.get("frame_id", "")).strip()
    if frame != expected_frame:
        raise GoToError(
            f"Pose frame '{frame or '<empty>'}' does not match '{expected_frame}'."
        )
    try:
        position = [float(value["position"][key]) for key in ("x", "y", "z")]
        orientation = [
            float(value["orientation"][key]) for key in ("x", "y", "z", "w")
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise GoToError("Pose position or orientation is incomplete.") from error
    if not all(math.isfinite(item) for item in position + orientation):
        raise GoToError("Pose contains a non-finite value.")
    norm = math.sqrt(sum(item * item for item in orientation))
    if norm <= 1.0e-9:
        raise GoToError("Pose orientation quaternion is zero.")
    value["frame_id"] = frame
    value["orientation"] = {
        key: orientation[index] / norm
        for index, key in enumerate(("x", "y", "z", "w"))
    }
    return value


class GoToController:
    """Publish one pose target and infer completion from end-effector feedback."""

    def __init__(
        self,
        publish_target: Callable[[dict], None],
        publish_passthrough: Callable[[], None],
        preflight: Callable[[], Optional[str]] = lambda: None,
        expected_frame: str = "base_link",
        linear_tolerance_m: float = 0.005,
        angular_tolerance_rad: float = math.radians(5.0),
        dwell_sec: float = 0.5,
        timeout_sec: float = 30.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], str] = _utc_now,
    ) -> None:
        if min(linear_tolerance_m, angular_tolerance_rad, dwell_sec, timeout_sec) <= 0:
            raise ValueError("Go-to tolerances and time limits must be positive.")
        self._publish_target = publish_target
        self._publish_passthrough = publish_passthrough
        self._preflight = preflight
        self._expected_frame = expected_frame
        self._linear_tolerance_m = linear_tolerance_m
        self._angular_tolerance_rad = angular_tolerance_rad
        self._dwell_sec = dwell_sec
        self._timeout_sec = timeout_sec
        self._clock = monotonic_clock
        self._utc_clock = utc_clock
        self._lock = threading.RLock()
        self._current: Optional[dict] = None
        self._started_monotonic: Optional[float] = None
        self._dwell_started_monotonic: Optional[float] = None
        self._on_finish: Optional[Callable[[dict], None]] = None

    def active(self) -> bool:
        return bool(self.snapshot()["active"])

    def start(
        self,
        pose_id: str,
        pose: dict,
        context: Optional[dict] = None,
        on_finish: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        self.snapshot()
        with self._lock:
            if self._current and self._current["status"] in ACTIVE_STATES:
                raise GoToError("Another Cartesian motion is already active.")
            preflight_error = self._preflight()
            if preflight_error:
                raise GoToError(preflight_error)
            target = validate_pose(pose, self._expected_frame)
            now = self._clock()
            self._current = {
                "motion_id": str(uuid.uuid4()),
                "pose_id": str(pose_id),
                "target": target,
                "context": json.loads(json.dumps(context or {})),
                "status": "preparing",
                "started_at_utc": self._utc_clock(),
                "completed_at_utc": None,
                "stop_reason": None,
                "error": None,
                "linear_error_m": None,
                "linear_error_mm": None,
                "angular_error_rad": None,
                "angular_error_deg": None,
                "dwell_sec": 0.0,
                "timeout_sec": self._timeout_sec,
            }
            self._started_monotonic = now
            self._dwell_started_monotonic = None
            self._on_finish = on_finish
            try:
                self._publish_target(target)
            except Exception as error:
                self._current["status"] = "error"
                self._current["error"] = f"Unable to publish pose target: {error}"
                self._current["completed_at_utc"] = self._utc_clock()
                self._current["stop_reason"] = "publish_failure"
                callback = self._take_finish_callback()
                if callback:
                    callback(self._public_current())
                raise GoToError(self._current["error"]) from error
            self._current["status"] = "moving"
            return self.snapshot()

    def update_pose(self, message) -> None:
        callback = None
        record = None
        with self._lock:
            if not self._current or self._current["status"] not in ACTIVE_STATES:
                return
            try:
                observed = pose_document(message)
                linear, angular = pose_error(observed, self._current["target"])
            except (AttributeError, TypeError, ValueError, TrainingError) as error:
                self._current["error"] = f"Invalid /ee_pose feedback: {error}"
                self._dwell_started_monotonic = None
                self._current["dwell_sec"] = 0.0
                return
            self._current.update(
                {
                    "linear_error_m": linear,
                    "linear_error_mm": linear * 1000.0,
                    "angular_error_rad": angular,
                    "angular_error_deg": math.degrees(angular),
                    "error": None,
                }
            )
            within = (
                linear <= self._linear_tolerance_m
                and angular <= self._angular_tolerance_rad
            )
            now = self._clock()
            if not within:
                self._current["status"] = "moving"
                self._dwell_started_monotonic = None
                self._current["dwell_sec"] = 0.0
                return
            if self._dwell_started_monotonic is None:
                self._dwell_started_monotonic = now
            self._current["status"] = "settling"
            self._current["dwell_sec"] = max(
                0.0, now - self._dwell_started_monotonic
            )
            if self._current["dwell_sec"] >= self._dwell_sec:
                callback, record = self._finish_locked("succeeded", "target_reached")
        if callback:
            callback(record)

    def stop(self, reason: str = "operator_stop") -> dict:
        callback = None
        record = None
        with self._lock:
            if not self._current or self._current["status"] not in ACTIVE_STATES:
                raise GoToError("No Cartesian motion is currently active.")
            callback, record = self._finish_locked("cancelled", reason)
        if callback:
            callback(record)
        return self.snapshot()

    def cancel_if_active(self, reason: str) -> bool:
        callback = None
        record = None
        with self._lock:
            if not self._current or self._current["status"] not in ACTIVE_STATES:
                return False
            callback, record = self._finish_locked("cancelled", reason)
        if callback:
            callback(record)
        return True

    def snapshot(self) -> dict:
        callback = None
        record = None
        with self._lock:
            callback, record = self._refresh_timeout()
            result = {
                "active": bool(
                    self._current and self._current["status"] in ACTIVE_STATES
                ),
                "current": self._public_current(),
                "configuration": {
                    "expected_frame": self._expected_frame,
                    "linear_tolerance_m": self._linear_tolerance_m,
                    "angular_tolerance_rad": self._angular_tolerance_rad,
                    "dwell_sec": self._dwell_sec,
                    "timeout_sec": self._timeout_sec,
                },
            }
        if callback:
            callback(record)
        return result

    def _refresh_timeout(self):
        if (
            not self._current
            or self._current["status"] not in ACTIVE_STATES
            or self._started_monotonic is None
            or self._clock() - self._started_monotonic < self._timeout_sec
        ):
            return None, None
        return self._finish_locked("timed_out", "timeout")

    def _finish_locked(self, status: str, reason: str):
        try:
            self._publish_passthrough()
        except Exception as error:
            status = "error"
            self._current["error"] = f"Unable to cancel Cartesian behaviour: {error}"
        self._current["status"] = status
        self._current["stop_reason"] = reason
        self._current["completed_at_utc"] = self._utc_clock()
        callback = self._take_finish_callback()
        record = self._public_current()
        self._started_monotonic = None
        self._dwell_started_monotonic = None
        return callback, record

    def _take_finish_callback(self):
        callback = self._on_finish
        self._on_finish = None
        return callback

    def _public_current(self):
        return json.loads(json.dumps(self._current)) if self._current else None

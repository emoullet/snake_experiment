"""ROS-agnostic supervision for calibrated Cartesian pose motions."""

from __future__ import annotations

from collections import deque
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
    if not math.isfinite(norm):
        raise GoToError("Pose orientation quaternion norm is non-finite.")
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
        timeout_sec: float = 120.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], str] = _utc_now,
        feedback_finish_dispatch: Callable = lambda callback, record: callback(record),
        stall_timeout_sec: float = 5.0,
        progress_window_sec: float = 1.0,
        linear_progress_threshold_mps: float = 0.0005,
        angular_progress_threshold_radps: float = math.radians(0.5),
        feedback_timeout_sec: float = 2.0,
    ) -> None:
        limits = (linear_tolerance_m, angular_tolerance_rad, dwell_sec, timeout_sec,
                  stall_timeout_sec, progress_window_sec, linear_progress_threshold_mps,
                  angular_progress_threshold_radps, feedback_timeout_sec)
        if any(not math.isfinite(value) or value <= 0 for value in limits):
            raise ValueError("Go-to tolerances and time limits must be finite and positive.")
        self._publish_target = publish_target
        self._publish_passthrough = publish_passthrough
        self._preflight = preflight
        self._expected_frame = expected_frame
        self._linear_tolerance_m = linear_tolerance_m
        self._angular_tolerance_rad = angular_tolerance_rad
        self._dwell_sec = dwell_sec
        self._timeout_sec = timeout_sec
        self._stall_timeout_sec = stall_timeout_sec
        self._progress_window_sec = progress_window_sec
        self._linear_progress_threshold_mps = linear_progress_threshold_mps
        self._angular_progress_threshold_radps = angular_progress_threshold_radps
        self._feedback_timeout_sec = feedback_timeout_sec
        self._last_feedback_monotonic = None
        self._stall_started_monotonic = None
        self._progress_samples = deque()
        self._clock = monotonic_clock
        self._utc_clock = utc_clock
        self._feedback_finish_dispatch = feedback_finish_dispatch
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
                "linear_progress_mps": None,
                "angular_progress_radps": None,
                "stall_sec": 0.0,
            }
            self._started_monotonic = now
            self._last_feedback_monotonic = None
            self._stall_started_monotonic = None
            self._progress_samples.clear()
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

    def tick(self) -> None:
        """Supervise deadlines without relying on a browser or pose messages."""
        with self._lock:
            callback, record = self._refresh_timeout()
        if callback:
            self._feedback_finish_dispatch(callback, record)

    def update_pose(self, message) -> None:
        with self._lock:
            callback, record = self._refresh_timeout()
            if self._current and self._current["status"] in ACTIVE_STATES:
                callback, record = self._update_pose_locked(message)
        if callback:
            self._feedback_finish_dispatch(callback, record)

    def _update_pose_locked(self, message):
        try:
            observed = validate_pose(pose_document(message), self._expected_frame)
            linear, angular = pose_error(observed, self._current["target"])
            if not math.isfinite(linear) or not math.isfinite(angular):
                raise GoToError("Pose error is non-finite.")
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError, TrainingError, GoToError) as error:
            self._current["error"] = f"Invalid /ee_pose feedback: {error}"
            self._dwell_started_monotonic = None
            self._current["dwell_sec"] = 0.0
            # Do not bridge a stability interval across an invalid sample.
            self._current["status"] = "moving"
            return None, None
        now = self._clock()
        self._last_feedback_monotonic = now
        self._current.update({
            "linear_error_m": linear,
            "linear_error_mm": linear * 1000.0,
            "angular_error_rad": angular,
            "angular_error_deg": math.degrees(angular),
            "error": None,
        })
        self._update_progress(now, linear, angular)
        within = linear <= self._linear_tolerance_m and angular <= self._angular_tolerance_rad
        if not within:
            self._current["status"] = "moving"
            self._dwell_started_monotonic = None
            self._current["dwell_sec"] = 0.0
            return None, None
        if self._dwell_started_monotonic is None:
            self._dwell_started_monotonic = now
        self._current["status"] = "settling"
        self._current["dwell_sec"] = max(0.0, now - self._dwell_started_monotonic)
        if self._current["dwell_sec"] >= self._dwell_sec:
            return self._finish_locked("succeeded", "target_reached")
        return None, None

    def _update_progress(self, now, linear, angular):
        samples = self._progress_samples
        sample = (now, linear, angular)
        # Multiple callbacks at the same clock instant must not divide by zero.
        if samples and samples[-1][0] == now:
            samples[-1] = sample
        else:
            samples.append(sample)
        boundary = now - self._progress_window_sec
        while len(samples) > 1 and samples[1][0] <= boundary:
            samples.popleft()
        rates = None
        if len(samples) >= 2 and samples[0][0] <= boundary:
            before, after = samples[0], samples[1]
            fraction = (boundary - before[0]) / (after[0] - before[0])
            baseline = [before[i] + fraction * (after[i] - before[i]) for i in (1, 2)]
            rates = ((baseline[0] - linear) / self._progress_window_sec,
                     (baseline[1] - angular) / self._progress_window_sec)
            self._current["linear_progress_mps"], self._current["angular_progress_radps"] = rates
        within = linear <= self._linear_tolerance_m and angular <= self._angular_tolerance_rad
        progressing = rates is not None and (
            (linear > self._linear_tolerance_m and rates[0] >= self._linear_progress_threshold_mps)
            or (angular > self._angular_tolerance_rad and rates[1] >= self._angular_progress_threshold_radps)
        )
        if within or progressing:
            self._stall_started_monotonic = None
        elif rates is not None and self._stall_started_monotonic is None:
            self._stall_started_monotonic = now
        self._current["stall_sec"] = (0.0 if self._stall_started_monotonic is None
                                      else max(0.0, now - self._stall_started_monotonic))

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
                    "stall_timeout_sec": self._stall_timeout_sec,
                    "progress_window_sec": self._progress_window_sec,
                    "linear_progress_threshold_mps": self._linear_progress_threshold_mps,
                    "angular_progress_threshold_radps": self._angular_progress_threshold_radps,
                    "feedback_timeout_sec": self._feedback_timeout_sec,
                },
            }
        if callback:
            callback(record)
        return result

    def _refresh_timeout(self):
        if (not self._current or self._current["status"] not in ACTIVE_STATES
                or self._started_monotonic is None):
            return None, None
        now = self._clock()
        self._current["stall_sec"] = (0.0 if self._stall_started_monotonic is None
                                      else max(0.0, now - self._stall_started_monotonic))
        feedback_origin = (self._last_feedback_monotonic if self._last_feedback_monotonic is not None
                           else self._started_monotonic)
        if now - feedback_origin >= self._feedback_timeout_sec:
            return self._finish_locked("timed_out", "feedback_timeout")
        if now - self._started_monotonic >= self._timeout_sec:
            return self._finish_locked("timed_out", "total_timeout")
        if self._current["stall_sec"] >= self._stall_timeout_sec:
            return self._finish_locked("timed_out", "progress_stalled")
        return None, None

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
        self._stall_started_monotonic = None
        self._last_feedback_monotonic = None
        self._progress_samples.clear()
        return callback, record

    def _take_finish_callback(self):
        callback = self._on_finish
        self._on_finish = None
        return callback

    def _public_current(self):
        return json.loads(json.dumps(self._current)) if self._current else None

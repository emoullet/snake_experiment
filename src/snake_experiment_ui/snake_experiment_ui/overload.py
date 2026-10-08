"""Fail-closed ROS status, validated retreat targets, and session event journal."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time

from .calibration import validate_calibration_robot, CalibrationError
from .robot_preview import robot_definition


class ProtectionError(RuntimeError):
    pass


def protection_action_state(queued, current):
    """Retain latched controller episodes; decide telemetry loss from live state.

    Startup and unavailable snapshots in the transition queue are historical.
    Their freshness must not override a subsequently received healthy status.
    A real controller episode still needs cleanup even if processing was delayed.
    """
    if queued.get("blocked") and queued.get("event", 0) > 0 and queued.get("state") != "unavailable":
        return queued
    return current


def recovery_parameters(document, robot):
    """Validate all three poses together, without transforming unknown frames."""
    validate_calibration_robot(document, robot)
    definition = robot_definition(robot)
    if document.get("schema_version") != 1 or document.get("reference_frame") != definition.base_frame:
        raise CalibrationError("Retreat calibration must use the robot base frame.")
    positions, orientations = [], []
    try:
        for index in (1, 2, 3):
            pose = document["poses"][f"target_out_{index}"]
            if pose["frame_id"] != definition.base_frame:
                raise CalibrationError("Retreat poses use an incompatible frame.")
            position = [float(pose["position"][key]) for key in "xyz"]
            orientation = [float(pose["orientation"][key]) for key in "xyzw"]
            if not all(math.isfinite(v) for v in position + orientation):
                raise CalibrationError("Retreat pose contains non-finite values.")
            norm = math.sqrt(sum(v*v for v in orientation))
            if not math.isfinite(norm) or norm < 1e-9:
                raise CalibrationError("Retreat quaternion is invalid.")
            if not math.isfinite(sum(v*v for v in position)):
                raise CalibrationError("Retreat position cannot be represented safely.")
            positions.extend(position)
            orientations.extend(v/norm for v in orientation)
    except (KeyError, TypeError, ValueError) as error:
        raise CalibrationError("Retreat calibration requires three valid out poses.") from error
    return {
        "overload_protection.reference_frame": definition.base_frame,
        "overload_protection.ee_frame": definition.ee_frame,
        "overload_protection.out_positions": positions,
        "overload_protection.out_orientations": orientations,
    }


class ProtectionState:
    """Never accept a disabled, malformed, wrong-robot or stale status as clear."""
    def __init__(self, robot, clock=time.monotonic):
        self.robot, self._clock = robot, clock
        self._lock = threading.RLock()
        self._at = None
        self._value = {}

    def record(self, message, ros_now=None):
        try:
            status = next(s for s in message.status if s.name == "overload_protection")
            values = {v.key: v.value for v in status.values}
            stamp = message.header.stamp.sec + message.header.stamp.nanosec * 1e-9
            if (status.hardware_id != self.robot or values["enabled"] != "true"
                or status.message not in ("normal", "translating", "waiting_clear", "rotating", "locked", "error")
                or values["blocked"] not in ("true", "false")
                or (status.message == "normal") != (values["blocked"] == "false")
                or (ros_now is not None and not 0 <= ros_now - stamp <= 0.5)):
                raise ValueError("Invalid protection status")
            value = {"instance": values.get("instance", ""), "state": status.message, "blocked": values["blocked"] == "true",
                     "force_valid": values["force_valid"] == "true", "pose_valid": values["pose_valid"] == "true",
                     "reason": values["reason"], "event": int(values["event"]),
                     "target": values["target"], "flipped": values["flipped"] == "true",
                     "force_estimation_verified": values.get("force_estimation_verified", "true") == "true",
                     "force_source": values["force_source"], "rearm_ready": values.get("rearm_ready") == "true", "controller_stamp": stamp}
            for key in ("initial_pose", "original_target", "adapted_target"):
                value[key] = json.loads(values[key])
            value["phase_history"] = json.loads(values.get("phase_history", "[]"))
            force = float(values["force_norm_N"])
            value["force_norm_N"] = force if math.isfinite(force) else None
            if not math.isfinite(force) or force < 0:
                value["force_valid"] = False
            if not value["force_valid"] or not value["pose_valid"]:
                value["blocked"] = True
            with self._lock:
                self._value, self._at = value, self._clock()
        except (StopIteration, AttributeError, KeyError, ValueError, TypeError):
            with self._lock:
                self._value, self._at = {}, None
        return self.snapshot()

    def invalidate(self):
        with self._lock:
            self._value, self._at = {}, None

    def snapshot(self):
        with self._lock:
            fresh = self._at is not None and self._clock() - self._at <= 0.5
            return {**self._value, "fresh": fresh, "blocked": not fresh or self._value.get("blocked", True),
                    "state": self._value.get("state", "unavailable") if fresh else "unavailable",
                    "reason": self._value.get("reason", "no_fresh_status") if fresh else "no_fresh_status"}

    def require_clear(self):
        state = self.snapshot()
        if state["state"] == "unavailable":
            raise ProtectionError("État de protection absent ou périmé : commandes verrouillées. Vérifier la stack et la réception de /qontrol_explorer/overload_status.")
        if state["blocked"]:
            raise ProtectionError("Commandes verrouillées par la protection de surcharge. Vérifier l’alarme puis réarmer explicitement.")


class EventJournal:
    """Write transitions only, separately from recorded experimental trials."""
    def __init__(self):
        self._lock = threading.Lock()
        self._previous = None

    def append(self, path, state, context=None):
        # Event zero describes startup, not an overload episode.
        if not state.get("event"):
            return
        identity = (str(path), state.get("instance"), state.get("event"), state.get("state"), state.get("reason"))
        with self._lock:
            if identity == self._previous:
                return
            record = {"at_utc": datetime.now(timezone.utc).isoformat(), "robot": state.get("robot"),
                      **state, "context": context or {}}
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, allow_nan=False) + "\n")
            self._previous = identity

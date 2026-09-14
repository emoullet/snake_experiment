"""Thread-safe calibration state and pose validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import threading
import time
from typing import Callable, Dict, Optional


POSE_IDS = (
    "target_1",
    "target_out_1",
    "target_2",
    "target_out_2",
    "target_3",
    "target_out_3",
    "starting_point",
)


class CalibrationError(RuntimeError):
    """An operator-correctable calibration error."""


@dataclass(frozen=True)
class PoseRecord:
    """Serializable end-effector pose captured from ROS."""

    frame_id: str
    stamp_sec: int
    stamp_nanosec: int
    received_at_utc: str
    captured_at_utc: str
    control_mode: str
    position: Dict[str, float]
    orientation: Dict[str, float]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CalibrationState:
    """Own the latest ROS pose and the seven calibration captures."""

    def __init__(
        self,
        max_pose_age_sec: float,
        mode_provider: Callable[[], Optional[str]],
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], str] = _utc_now,
    ) -> None:
        if max_pose_age_sec <= 0.0:
            raise ValueError("max_pose_age_sec must be positive")
        self._max_pose_age_sec = max_pose_age_sec
        self._mode_provider = mode_provider
        self._monotonic_clock = monotonic_clock
        self._utc_clock = utc_clock
        self._lock = threading.RLock()
        self._latest_pose = None
        self._latest_received_monotonic: Optional[float] = None
        self._latest_received_utc: Optional[str] = None
        self._captures: Dict[str, PoseRecord] = {}

    def update_live_pose(self, pose_message) -> None:
        """Store the newest PoseStamped without mutating it."""
        with self._lock:
            self._latest_pose = pose_message
            self._latest_received_monotonic = self._monotonic_clock()
            self._latest_received_utc = self._utc_clock()

    def _pose_age(self) -> Optional[float]:
        if self._latest_received_monotonic is None:
            return None
        return max(0.0, self._monotonic_clock() - self._latest_received_monotonic)

    def _pose_is_fresh(self) -> bool:
        age = self._pose_age()
        return age is not None and age <= self._max_pose_age_sec

    @staticmethod
    def _normalised_orientation(orientation) -> Dict[str, float]:
        values = (
            float(orientation.x),
            float(orientation.y),
            float(orientation.z),
            float(orientation.w),
        )
        if not all(math.isfinite(value) for value in values):
            raise CalibrationError("The current orientation contains a non-finite value.")
        norm = math.sqrt(sum(value * value for value in values))
        if norm < 1e-9:
            raise CalibrationError("The current orientation quaternion is invalid.")
        return {
            "x": values[0] / norm,
            "y": values[1] / norm,
            "z": values[2] / norm,
            "w": values[3] / norm,
        }

    def capture(self, pose_id: str) -> Dict:
        """Capture the latest fresh pose under one of the required identifiers."""
        if pose_id not in POSE_IDS:
            raise CalibrationError(f"Unknown calibration pose: {pose_id}")
        mode = self._mode_provider()
        if mode not in ("baseline", "snake"):
            raise CalibrationError("Activate a control mode before recording a pose.")

        with self._lock:
            if self._latest_pose is None:
                raise CalibrationError("No end-effector pose has been received yet.")
            if not self._pose_is_fresh():
                raise CalibrationError("The end-effector pose is stale. Check /ee_pose.")

            message = self._latest_pose
            frame_id = str(message.header.frame_id).strip()
            if not frame_id:
                raise CalibrationError("The current pose has no frame ID.")
            position_values = (
                float(message.pose.position.x),
                float(message.pose.position.y),
                float(message.pose.position.z),
            )
            if not all(math.isfinite(value) for value in position_values):
                raise CalibrationError("The current position contains a non-finite value.")

            record = PoseRecord(
                frame_id=frame_id,
                stamp_sec=int(message.header.stamp.sec),
                stamp_nanosec=int(message.header.stamp.nanosec),
                received_at_utc=self._latest_received_utc or self._utc_clock(),
                captured_at_utc=self._utc_clock(),
                control_mode=mode,
                position={
                    "x": position_values[0],
                    "y": position_values[1],
                    "z": position_values[2],
                },
                orientation=self._normalised_orientation(message.pose.orientation),
            )
            self._captures[pose_id] = record
            return asdict(record)

    def _save_error(self) -> Optional[str]:
        missing = [pose_id for pose_id in POSE_IDS if pose_id not in self._captures]
        if missing:
            return "Record all seven calibration poses before saving."
        frames = {record.frame_id for record in self._captures.values()}
        if len(frames) != 1:
            return "All calibration poses must use the same frame."
        return None

    def calibration_document(self) -> Dict:
        """Return a validated, versioned document ready for storage."""
        mode = self._mode_provider()
        if mode not in ("baseline", "snake"):
            raise CalibrationError("Activate a control mode before saving calibration.")
        with self._lock:
            error = self._save_error()
            if error:
                raise CalibrationError(error)
            frame_id = next(iter(self._captures.values())).frame_id
            return {
                "schema_version": 1,
                "saved_at_utc": self._utc_clock(),
                "pose_topic": None,
                "reference_frame": frame_id,
                "active_mode": mode,
                "poses": {
                    pose_id: asdict(self._captures[pose_id]) for pose_id in POSE_IDS
                },
            }

    def snapshot(self) -> Dict:
        """Return JSON-compatible UI state."""
        with self._lock:
            age = self._pose_age()
            fresh = self._pose_is_fresh()
            error = self._save_error()
            current_pose = None
            if self._latest_pose is not None:
                message = self._latest_pose
                current_pose = {
                    "frame_id": str(message.header.frame_id),
                    "stamp_sec": int(message.header.stamp.sec),
                    "stamp_nanosec": int(message.header.stamp.nanosec),
                    "received_at_utc": self._latest_received_utc,
                    "position": {
                        "x": self._finite_or_none(message.pose.position.x),
                        "y": self._finite_or_none(message.pose.position.y),
                        "z": self._finite_or_none(message.pose.position.z),
                    },
                    "orientation": {
                        "x": self._finite_or_none(message.pose.orientation.x),
                        "y": self._finite_or_none(message.pose.orientation.y),
                        "z": self._finite_or_none(message.pose.orientation.z),
                        "w": self._finite_or_none(message.pose.orientation.w),
                    },
                }
            return {
                "pose_stream": {
                    "received": self._latest_pose is not None,
                    "fresh": fresh,
                    "age_sec": round(age, 3) if age is not None else None,
                    "frame_id": (
                        str(self._latest_pose.header.frame_id)
                        if self._latest_pose is not None
                        else None
                    ),
                    "current_pose": current_pose,
                },
                "captures": {
                    pose_id: (
                        asdict(self._captures[pose_id])
                        if pose_id in self._captures
                        else None
                    )
                    for pose_id in POSE_IDS
                },
                "can_capture": fresh
                and self._mode_provider() in ("baseline", "snake"),
                "can_save": error is None
                and self._mode_provider() in ("baseline", "snake"),
                "save_blocker": error,
            }

    @staticmethod
    def _finite_or_none(value) -> Optional[float]:
        numeric_value = float(value)
        return numeric_value if math.isfinite(numeric_value) else None

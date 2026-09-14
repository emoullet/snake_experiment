"""Atomic JSON storage for experiment calibration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid


class CalibrationStorage:
    """Maintain the current calibration and archive its previous versions."""

    def __init__(self, launch_directory: str | Path, pose_topic: str) -> None:
        self.launch_directory = Path(launch_directory).expanduser().resolve()
        self.directory = self.launch_directory / "calibrations"
        self.archive_directory = self.directory / "calib_archives"
        self.latest_path = self.directory / "latest_calib.json"
        self.pose_topic = pose_topic
        self._save_lock = threading.Lock()

    @classmethod
    def from_working_directory(cls, pose_topic: str) -> "CalibrationStorage":
        """Capture the process working directory as the storage root."""
        return cls(Path.cwd(), pose_topic)

    def save(self, document: dict) -> Path:
        """Atomically replace the current calibration and archive the old one."""
        timestamp = self._timestamp()
        calibration_id = f"cal-{timestamp}-{uuid.uuid4().hex}"
        final_document = dict(document)
        final_document["calibration_id"] = calibration_id
        final_document["pose_topic"] = self.pose_topic

        with self._save_lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise RuntimeError(
                    f"Unable to create calibration directory: {error}"
                ) from error
            temporary_path = self._write_temporary(final_document)
            archived_path = None
            try:
                if self.latest_path.exists():
                    self.archive_directory.mkdir(parents=True, exist_ok=True)
                    archived_path = self._next_archive_path()
                    os.replace(self.latest_path, archived_path)
                    os.chmod(archived_path, 0o600)
                os.replace(temporary_path, self.latest_path)
                temporary_path = None
            except OSError as error:
                rollback_error = self._restore_latest(archived_path)
                detail = f"Unable to save calibration: {error}"
                if rollback_error is not None:
                    detail += f"; unable to restore previous calibration: {rollback_error}"
                raise RuntimeError(detail) from error
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        return self.latest_path

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")

    def _next_archive_path(self) -> Path:
        while True:
            path = self.archive_directory / (
                f"archived-{self._timestamp()}-{uuid.uuid4().hex}.json"
            )
            if not path.exists():
                return path

    def _write_temporary(self, document: dict) -> Path:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=".calibration-",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                json.dump(document, output, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_path, 0o600)
            return temporary_path
        except (OSError, TypeError, ValueError) as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise RuntimeError(f"Unable to prepare calibration: {error}") from error

    def _restore_latest(self, archived_path: Path | None) -> OSError | None:
        if archived_path is None or not archived_path.exists():
            return None
        try:
            os.replace(archived_path, self.latest_path)
        except OSError as error:
            return error
        return None

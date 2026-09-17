"""Owned rosbag2 recorder lifecycle and metadata inspection."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Callable, Iterable, Optional

import yaml


class RosbagError(RuntimeError):
    """An operator-correctable rosbag recorder error."""


class RosbagManager:
    """Start and stop one recorder process owned by the session interface."""

    def __init__(
        self,
        node_names: Callable[[], Iterable[str]],
        startup_timeout_sec: float = 5.0,
        shutdown_timeout_sec: float = 10.0,
        node_name: str = "snake_experiment_discovery_recorder",
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._node_names = node_names
        self._startup_timeout_sec = startup_timeout_sec
        self._shutdown_timeout_sec = shutdown_timeout_sec
        self._node_name = node_name
        self._popen_factory = popen_factory
        self._sleep = sleep
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._output: Optional[Path] = None
        self._topics: list[str] = []
        self._storage = "mcap"
        self._status = "inactive"
        self._error: Optional[str] = None

    def _recorder_visible(self) -> bool:
        expected = f"/{self._node_name}"
        return any(
            f"/{str(name).strip('/')}" == expected for name in self._node_names()
        )

    def _refresh_process_state(self) -> None:
        if self._process is None:
            return
        return_code = self._process.poll()
        if return_code is not None:
            self._process = None
            if self._status not in ("stopping", "inactive"):
                self._status = "error"
                self._error = f"Rosbag recorder exited with status {return_code}."

    def snapshot(self) -> dict:
        with self._lock:
            self._refresh_process_state()
            return {
                "status": self._status,
                "error": self._error,
                "output": str(self._output) if self._output else None,
                "topics": list(self._topics),
                "storage": self._storage,
                "owned": self._process is not None,
            }

    def start(self, output: Path, topics: list[str], storage: str = "mcap") -> dict:
        with self._lock:
            self._refresh_process_state()
            if self._process is not None or self._status == "active":
                raise RosbagError("A recorder owned by this interface is already active.")
            if self._recorder_visible():
                raise RosbagError(
                    f"A recorder named /{self._node_name} is running outside this interface."
                )
            output = Path(output).resolve()
            if output.exists():
                raise RosbagError(f"Rosbag output already exists: {output}")
            if not topics or any(not str(topic).startswith("/") for topic in topics):
                raise RosbagError("Rosbag topics must be non-empty absolute topic names.")
            if storage not in ("mcap", "sqlite3"):
                raise RosbagError(f"Unsupported rosbag storage: {storage}")
            output.parent.mkdir(parents=True, exist_ok=True)
            self._output = output
            self._topics = list(topics)
            self._storage = storage
            self._status = "starting"
            self._error = None
            command = [
                "ros2",
                "bag",
                "record",
                "--storage",
                storage,
                "--output",
                str(output),
                "--disable-keyboard-controls",
                "--node-name",
                self._node_name,
                "--topics",
                *topics,
            ]
            try:
                self._process = self._popen_factory(command, start_new_session=True)
            except OSError as error:
                self._status = "error"
                self._error = f"Unable to start rosbag recorder: {error}"
                raise RosbagError(self._error) from error

            deadline = time.monotonic() + self._startup_timeout_sec
            while time.monotonic() < deadline:
                self._refresh_process_state()
                if self._process is None:
                    raise RosbagError(self._error or "Rosbag recorder failed to start.")
                if self._recorder_visible():
                    self._status = "active"
                    return self.snapshot()
                self._sleep(0.05)
            self._error = f"Recorder node /{self._node_name} did not appear in time."
            self._stop_locked(preserve_error=True)
            raise RosbagError(self._error)

    def stop(self) -> dict:
        with self._lock:
            self._refresh_process_state()
            if self._process is None:
                if self._recorder_visible():
                    raise RosbagError("The visible recorder is not owned by this interface.")
                if self._status == "active":
                    self._status = "error"
                    self._error = "Owned recorder process disappeared."
                    raise RosbagError(self._error)
                self._status = "inactive"
                return self.inspect(self._output, self._topics, self._storage)
            self._stop_locked()
            return self.inspect(self._output, self._topics, self._storage)

    def _stop_locked(self, preserve_error: bool = False) -> None:
        process = self._process
        if process is None:
            if not preserve_error:
                self._status = "inactive"
            return
        self._status = "stopping"
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=self._shutdown_timeout_sec)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        finally:
            self._process = None
            self._status = "error" if preserve_error else "inactive"
            if not preserve_error:
                self._error = None

    @staticmethod
    def inspect(output: Optional[Path], topics: list[str], storage: str) -> dict:
        counts = {topic: 0 for topic in topics}
        metadata_path = Path(output) / "metadata.yaml" if output else None
        metadata_error = None
        if metadata_path and metadata_path.is_file():
            try:
                metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
                information = metadata.get("rosbag2_bagfile_information", {})
                storage = information.get("storage_identifier", storage)
                for item in information.get("topics_with_message_count", []):
                    topic = item.get("topic_metadata", {}).get("name")
                    if topic in counts:
                        counts[topic] = int(item.get("message_count", 0))
            except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError) as error:
                metadata_error = f"Unable to read rosbag metadata: {error}"
        else:
            metadata_error = "Rosbag metadata.yaml was not created."
        missing = [topic for topic, count in counts.items() if count <= 0]
        return {
            "output": str(output) if output else None,
            "storage": storage,
            "message_counts": counts,
            "valid": not missing and metadata_error is None,
            "missing_topics": missing,
            "metadata_error": metadata_error,
        }

    def shutdown(self) -> Optional[dict]:
        with self._lock:
            self._refresh_process_state()
            if self._process is None:
                return None
            self._stop_locked()
            return self.inspect(self._output, self._topics, self._storage)

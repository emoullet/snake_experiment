"""Exclusive lifecycle management for joystick mapper launch files."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Callable, Iterable, Optional


MODES = {
    "baseline": {
        "launch_file": "joystick_mapper_baseline.launch.py",
        "mode_request": "geometric/both",
    },
    "snake": {
        "launch_file": "joystick_mapper_snake.launch.py",
        "mode_request": "geometric/both",
    },
}


class ModeError(RuntimeError):
    """An operator-correctable mode lifecycle error."""


class ModeManager:
    """Start exactly one owned joystick mapper and select its control mode."""

    def __init__(
        self,
        node_names: Callable[[], Iterable[str]],
        publish_mode_request: Callable[[str], None],
        mapper_transition: Callable[[], None] = lambda: None,
        startup_timeout_sec: float = 5.0,
        shutdown_timeout_sec: float = 5.0,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._node_names = node_names
        self._publish_mode_request = publish_mode_request
        self._mapper_transition = mapper_transition
        self._startup_timeout_sec = startup_timeout_sec
        self._shutdown_timeout_sec = shutdown_timeout_sec
        self._popen_factory = popen_factory
        self._sleep = sleep
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._active_mode: Optional[str] = None
        self._status = "inactive"
        self._error: Optional[str] = None

    def _mapper_visible(self) -> bool:
        for name in self._node_names():
            normalised = name.rstrip("/")
            if normalised == "joystick_mapper" or normalised.endswith("/joystick_mapper"):
                return True
        return False

    def _refresh_process_state(self) -> None:
        if self._process is None:
            return
        return_code = self._process.poll()
        if return_code is not None:
            self._process = None
            if self._status not in ("stopping", "inactive"):
                self._status = "error"
                self._error = f"Joystick mapper exited with status {return_code}."
            self._active_mode = None

    def active_mode(self) -> Optional[str]:
        """Return the active owned mode, if the process is healthy."""
        with self._lock:
            self._refresh_process_state()
            return self._active_mode if self._status == "active" else None

    def snapshot(self) -> dict:
        """Return JSON-compatible lifecycle state."""
        with self._lock:
            self._refresh_process_state()
            return {
                "active_mode": self._active_mode,
                "status": self._status,
                "error": self._error,
            }

    def activate(self, mode: str) -> dict:
        """Activate baseline or snake mode and return lifecycle state."""
        if mode not in MODES:
            raise ModeError(f"Unknown control mode: {mode}")
        with self._lock:
            self._refresh_process_state()
            if self._status == "active" and self._active_mode == mode:
                self._publish_mode_request(MODES[mode]["mode_request"])
                return self.snapshot()

            if self._process is None and self._mapper_visible():
                raise ModeError(
                    "A joystick_mapper node is already running outside this interface."
                )
            if self._process is not None:
                self._stop_locked()
                deadline = time.monotonic() + self._shutdown_timeout_sec
                while self._mapper_visible() and time.monotonic() < deadline:
                    self._sleep(0.05)
                if self._mapper_visible():
                    self._status = "error"
                    self._error = "The previous joystick mapper did not leave the ROS graph."
                    raise ModeError(self._error)

            # Parameter service clients can retain discovery state for the previous
            # server because both profiles use the same ROS node and service names.
            self._mapper_transition()
            self._status = "starting"
            self._error = None
            command = [
                "ros2",
                "launch",
                "snake_experiment_ui",
                MODES[mode]["launch_file"],
            ]
            try:
                self._process = self._popen_factory(command, start_new_session=True)
            except OSError as error:
                self._status = "error"
                self._error = f"Unable to start joystick mapper: {error}"
                raise ModeError(self._error) from error

            deadline = time.monotonic() + self._startup_timeout_sec
            while time.monotonic() < deadline:
                self._refresh_process_state()
                if self._process is None:
                    raise ModeError(self._error or "Joystick mapper failed to start.")
                if self._mapper_visible():
                    self._active_mode = mode
                    self._status = "active"
                    self._publish_mode_request(MODES[mode]["mode_request"])
                    return self.snapshot()
                self._sleep(0.05)

            self._status = "error"
            self._error = "Joystick mapper did not appear in the ROS graph in time."
            self._stop_locked(preserve_error=True)
            raise ModeError(self._error)

    def deactivate(self, mode: str) -> dict:
        """Stop the owned mapper for the requested active mode."""
        if mode not in MODES:
            raise ModeError(f"Unknown control mode: {mode}")
        with self._lock:
            self._refresh_process_state()
            if self._active_mode is None and self._process is None:
                self._status = "inactive"
                self._error = None
                return self.snapshot()
            if self._active_mode != mode:
                active_mode = self._active_mode or "none"
                raise ModeError(
                    f"Cannot deactivate {mode} mode while active mode is {active_mode}."
                )

            self._stop_locked()
            deadline = time.monotonic() + self._shutdown_timeout_sec
            while self._mapper_visible() and time.monotonic() < deadline:
                self._sleep(0.05)
            if self._mapper_visible():
                self._status = "error"
                self._error = "Joystick mapper did not leave the ROS graph in time."
                raise ModeError(self._error)
            return self.snapshot()

    def _stop_locked(self, preserve_error: bool = False) -> None:
        if self._process is None:
            if not preserve_error:
                self._status = "inactive"
                self._error = None
            self._active_mode = None
            return
        process = self._process
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
            self._active_mode = None
            if preserve_error:
                self._status = "error"
            else:
                self._status = "inactive"
                self._error = None

    def shutdown(self) -> None:
        """Stop the owned mapper, if any."""
        with self._lock:
            self._stop_locked()

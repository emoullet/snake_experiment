"""Lifecycle management for the experiment stack launch process."""

from __future__ import annotations

from ipaddress import AddressValueError, IPv4Address
import os
import signal
import subprocess
import threading
import time
from typing import Callable, Iterable, Optional


class StackError(RuntimeError):
    """An operator-correctable stack lifecycle error."""


class StackManager:
    """Start and stop the experiment stack owned by View A."""

    def __init__(
        self,
        node_names: Callable[[], Iterable[str]],
        use_simulation: bool = True,
        startup_timeout_sec: float = 15.0,
        shutdown_timeout_sec: float = 10.0,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
        robot: str = "explorer",
        robot_ip: str = "192.168.1.10",
        overload_protection: bool = False,
        before_start: Callable[[], None] = lambda: None,
    ) -> None:
        if robot not in ("explorer", "kinova"):
            raise StackError(f"Unknown experiment robot: {robot}")
        if robot == "kinova":
            try:
                IPv4Address(robot_ip)
            except AddressValueError as error:
                raise StackError(f"Invalid Kinova IPv4 address: {robot_ip}") from error
        self._before_start = before_start
        self._overload_protection = overload_protection
        self._robot = robot
        self._robot_ip = robot_ip
        self._node_names = node_names
        self._use_simulation = use_simulation
        self._startup_timeout_sec = startup_timeout_sec
        self._shutdown_timeout_sec = shutdown_timeout_sec
        self._popen_factory = popen_factory
        self._sleep = sleep
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._status = "inactive"
        self._error: Optional[str] = None

    def _stack_visible(self) -> bool:
        for name in self._node_names():
            normalised = name.rstrip("/")
            if normalised == "cartesian_manager" or normalised.endswith(
                "/cartesian_manager"
            ):
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
                self._error = f"Experiment stack exited with status {return_code}."

    def snapshot(self) -> dict:
        """Return JSON-compatible stack state."""
        with self._lock:
            self._refresh_process_state()
            return {
                "status": self._status,
                "error": self._error,
                "use_simulation": self._use_simulation,
                "robot": self._robot,
                "robot_ip": self._robot_ip if self._robot == "kinova" else None,
            }

    def start(self) -> dict:
        """Start the stack and wait until cartesian_manager is visible."""
        with self._lock:
            self._refresh_process_state()
            if self._status == "active":
                return self.snapshot()
            if self._process is None and self._stack_visible():
                raise StackError(
                    "A cartesian_manager node is already running outside this interface."
                )

            self._before_start()
            self._status = "starting"
            self._error = None
            command = [
                "ros2",
                "launch",
                "snake_experiment_ui",
                f"{self._robot}.launch.py",
                f"use_simulation:={str(self._use_simulation).lower()}",
            ]
            if self._robot == "kinova":
                command.append(f"robot_ip:={self._robot_ip}")
            if self._overload_protection:
                command.append("overload_protection:=true")
            try:
                self._process = self._popen_factory(command, start_new_session=True)
            except OSError as error:
                self._status = "error"
                self._error = f"Unable to start the experiment stack: {error}"
                raise StackError(self._error) from error

            deadline = time.monotonic() + self._startup_timeout_sec
            while time.monotonic() < deadline:
                self._refresh_process_state()
                if self._process is None:
                    raise StackError(self._error or "Experiment stack failed to start.")
                if self._stack_visible():
                    self._status = "active"
                    return self.snapshot()
                self._sleep(0.05)

            self._status = "error"
            self._error = "cartesian_manager did not appear in the ROS graph in time."
            self._stop_locked(preserve_error=True)
            raise StackError(self._error)

    def stop(self) -> dict:
        """Stop the stack if it was started by this view."""
        with self._lock:
            self._refresh_process_state()
            if self._process is None:
                if self._stack_visible():
                    raise StackError(
                        "The running experiment stack was not started by this interface."
                    )
                self._status = "inactive"
                self._error = None
                return self.snapshot()
            self._stop_locked()
            return self.snapshot()

    def _stop_locked(self, preserve_error: bool = False) -> None:
        process = self._process
        if process is None:
            if not preserve_error:
                self._status = "inactive"
                self._error = None
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
            if preserve_error:
                self._status = "error"
            else:
                self._status = "inactive"
                self._error = None

    def shutdown(self) -> None:
        """Stop the owned stack, if any."""
        with self._lock:
            self._stop_locked()

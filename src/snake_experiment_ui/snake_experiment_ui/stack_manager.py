"""Lifecycle management for the experiment stack launch process."""

from __future__ import annotations

from ipaddress import AddressValueError, IPv4Address
import math
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
        overload_retreat_delay_sec: float = 1.0,
        go_to_linear_tolerance_m: Optional[float] = None,
        go_to_angular_tolerance_rad: Optional[float] = None,
        before_start: Callable[[], None] = lambda: None,
        monitoring_changed: Callable[[bool], None] = lambda active: None,
    ) -> None:
        if robot not in ("explorer", "kinova"):
            raise StackError(f"Unknown experiment robot: {robot}")
        if robot == "kinova":
            try:
                IPv4Address(robot_ip)
            except AddressValueError as error:
                raise StackError(f"Invalid Kinova IPv4 address: {robot_ip}") from error
        self._before_start = before_start
        self._monitoring_changed = monitoring_changed
        self._overload_protection = overload_protection
        if not math.isfinite(overload_retreat_delay_sec) or overload_retreat_delay_sec < 0:
            raise StackError("Retreat delay must be finite and non-negative.")
        self._overload_retreat_delay_sec = overload_retreat_delay_sec
        if go_to_linear_tolerance_m is not None and (
            not math.isfinite(go_to_linear_tolerance_m)
            or go_to_linear_tolerance_m <= 0
        ):
            raise StackError("Go-to linear tolerance must be finite and positive.")
        self._go_to_linear_tolerance_m = go_to_linear_tolerance_m
        if go_to_angular_tolerance_rad is not None and (
            not math.isfinite(go_to_angular_tolerance_rad)
            or go_to_angular_tolerance_rad <= 0
        ):
            raise StackError("Go-to angular tolerance must be finite and positive.")
        self._go_to_angular_tolerance_rad = go_to_angular_tolerance_rad
        self._robot = robot
        self._robot_ip = robot_ip
        self._node_names = node_names
        self._use_simulation = use_simulation
        self._startup_timeout_sec = startup_timeout_sec
        self._shutdown_timeout_sec = shutdown_timeout_sec
        self._popen_factory = popen_factory
        self._sleep = sleep
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._cleanup_pending = False
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
                "owned": self._process is not None,
                "error": self._error,
                "use_simulation": self._use_simulation,
                "robot": self._robot,
                "robot_ip": self._robot_ip if self._robot == "kinova" else None,
            }

    def start(self) -> dict:
        """Start the stack and wait until cartesian_manager is visible."""
        with self._lifecycle_lock:
            with self._lock:
                self._refresh_process_state()
                if self._status == "active":
                    return self.snapshot()
                if self._process is not None:
                    raise StackError("The owned stack process is still running; stop it before starting again.")
                if self._process is None and self._stack_visible():
                    raise StackError(
                        "A cartesian_manager node is already running outside this interface."
                    )

            self._before_start()
            self._monitoring_changed(True)
            with self._lock:
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
            if self._go_to_linear_tolerance_m is not None:
                command.append(
                    "go_to_linear_tolerance_m:="
                    f"{self._go_to_linear_tolerance_m}"
                )
            if self._go_to_angular_tolerance_rad is not None:
                command.append(
                    "go_to_angular_tolerance_rad:="
                    f"{self._go_to_angular_tolerance_rad}"
                )
            if self._overload_protection:
                command.append("overload_protection:=true")
                command.append(f"overload_retreat_delay_sec:={self._overload_retreat_delay_sec}")
            try:
                with self._lock:
                    self._process = self._popen_factory(command, start_new_session=True)
            except OSError as error:
                with self._lock:
                    self._status = "error"
                    self._error = f"Unable to start the experiment stack: {error}"
                raise StackError(self._error) from error

            deadline = time.monotonic() + self._startup_timeout_sec
            while time.monotonic() < deadline:
                with self._lock:
                    self._refresh_process_state()
                    if self._process is None:
                        raise StackError(self._error or "Experiment stack failed to start.")
                if self._stack_visible():
                    with self._lock:
                        self._status = "active"
                    return self.snapshot()
                self._sleep(0.05)

            with self._lock:
                self._status = "error"
                self._error = "cartesian_manager did not appear in the ROS graph in time."
            self._stop_locked(preserve_error=True)
            raise StackError(self._error)

    def stop(self) -> dict:
        """Stop the stack if it was started by this view."""
        with self._lifecycle_lock:
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

    def stop_for_experiment(self) -> dict:
        """Stop owned processes now; defer DDS cleanup to the next start."""
        with self._lifecycle_lock:
            owned = self.snapshot()["owned"]
            state = self.stop()
            if owned:
                self._cleanup_pending = True
            return state

    def start_for_experiment(self, phase_changed: Callable[[str], None]) -> dict:
        """Wait for a previously stopped owned graph without holding snapshot locks."""
        with self._lifecycle_lock:
            if self._cleanup_pending:
                phase_changed("waiting_for_graph")
                self._wait_for_graph_cleanup()
                self._cleanup_pending = False
            phase_changed("starting")
            return self.start()

    def _wait_for_graph_cleanup(self) -> None:
        deadline = time.monotonic() + max(self._startup_timeout_sec, self._shutdown_timeout_sec)
        while self._stack_visible():
            if time.monotonic() >= deadline:
                with self._lock:
                    self._status = "error"
                    self._error = "Owned stack stopped, but cartesian_manager is still visible in the ROS graph."
                raise StackError(self._error)
            self._sleep(0.05)

    def stop_and_wait(self) -> dict:
        """Stop the owned launch, then wait for its ROS graph entries to expire.

        Kept separate from stop() so existing panel lifecycles stay unchanged.
        A still-visible external node is never killed or adopted.
        """
        with self._lifecycle_lock:
            self.stop_for_experiment()
            self._wait_for_graph_cleanup()
            self._cleanup_pending = False
            return self.snapshot()

    def _stop_locked(self, preserve_error: bool = False) -> None:
        with self._lock:
            process = self._process
        if process is None:
            with self._lock:
                if not preserve_error:
                    self._status = "inactive"
                    self._error = None
            return
        with self._lock:
            self._status = "stopping"
        self._monitoring_changed(False)
        try:
            for stop_signal, timeout in ((signal.SIGINT, self._shutdown_timeout_sec), (signal.SIGTERM, 1.0)):
                try:
                    os.killpg(process.pid, stop_signal)
                    process.wait(timeout=timeout)
                    break
                except ProcessLookupError:
                    break
                except subprocess.TimeoutExpired:
                    continue
        except OSError as error:
            with self._lock:
                self._status = "error"
                self._error = f"Unable to stop the owned experiment stack: {error}"
            self._monitoring_changed(True)
            raise StackError(self._error) from error
        with self._lock:
            if process.poll() is None:
                self._status = "error"
                self._error = "Experiment stack did not stop; its owned process is still running."
                self._monitoring_changed(True)
                raise StackError(self._error)
            self._process = None
            if preserve_error:
                self._status = "error"
            else:
                self._status = "inactive"
                self._error = None

    def shutdown(self) -> None:
        """Stop the owned stack, if any."""
        with self._lifecycle_lock:
            self._stop_locked()

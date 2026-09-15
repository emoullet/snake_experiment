"""Panel B workflow, gating rules, and in-memory report construction."""

from __future__ import annotations

from datetime import datetime, timezone
import platform
import threading
import time
from typing import Callable, Optional
import uuid

from .diagnostics import evaluate_provenance


class CheckupError(RuntimeError):
    """An operator-correctable Panel B workflow error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CheckupController:
    """Coordinate the stack, both control-mode checks, and final report."""

    def __init__(
        self,
        stack_manager,
        mode_manager,
        diagnostics,
        provenance_provider: Callable[[], dict],
        use_simulation: bool,
        ros_distro: str,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._stack = stack_manager
        self._modes = mode_manager
        self._diagnostics = diagnostics
        self._provenance_provider = provenance_provider
        self._use_simulation = use_simulation
        self._ros_distro = ros_distro
        self._clock = monotonic_clock
        self._utc_clock = utc_clock
        self._lock = threading.RLock()
        self._reset_state()

    def _reset_state(self) -> None:
        self._workflow = "idle"
        self._started_at_utc: Optional[str] = None
        self._active_mode: Optional[str] = None
        self._diagnostic_deadline: Optional[float] = None
        self._error: Optional[str] = None
        self._pending_report: Optional[dict] = None
        self._provenance: Optional[dict] = None
        self._mode_results = {
            mode: {
                "status": "not_tested",
                "attempts": 0,
                "operator_confirmed": None,
                "evidence": None,
                "error": None,
            }
            for mode in ("baseline", "snake")
        }

    def start_stack(self) -> dict:
        with self._lock:
            if self._workflow == "validated":
                raise CheckupError("Reset the completed check-up before starting again.")
            self._stack.start()
            if self._started_at_utc is None:
                self._started_at_utc = self._utc_clock()
                self._provenance = self._provenance_provider()
            self._workflow = "stack_ready"
            self._error = None
            return self.snapshot()

    def stop_stack(self) -> dict:
        with self._lock:
            self._stop_active_mode()
            self._stack.stop()
            if self._workflow != "validated":
                self._workflow = "idle"
            return self.snapshot()

    def start_mode(self, mode: str) -> dict:
        if mode not in self._mode_results:
            raise CheckupError(f"Unknown check-up mode: {mode}")
        with self._lock:
            if self._stack.snapshot()["status"] != "active":
                raise CheckupError("Start the experiment stack before testing a mode.")
            self._diagnostics.reset_mode_observation()
            self._modes.activate(mode)
            result = self._mode_results[mode]
            result.update(
                {
                    "status": "checking",
                    "attempts": result["attempts"] + 1,
                    "operator_confirmed": None,
                    "evidence": None,
                    "error": None,
                }
            )
            self._active_mode = mode
            self._diagnostic_deadline = (
                self._clock() + self._diagnostics.profile.diagnostic_timeout_sec
            )
            self._workflow = "mode_checking"
            self._error = None
            return self.snapshot()

    def retry_mode(self, mode: str) -> dict:
        with self._lock:
            if mode not in self._mode_results:
                raise CheckupError(f"Unknown check-up mode: {mode}")
            self._stop_active_mode()
            return self.start_mode(mode)

    def confirm_mode(self, mode: str, accepted: bool) -> dict:
        with self._lock:
            self._refresh()
            if mode != self._active_mode or self._workflow != "awaiting_confirmation":
                raise CheckupError(
                    f"{mode} is not awaiting an operator confirmation."
                )
            result = self._mode_results[mode]
            result["operator_confirmed"] = bool(accepted)
            if not accepted:
                result["status"] = "failed"
                result["error"] = "Operator rejected the observed robot behaviour."
                self._workflow = "error"
                self._error = result["error"]
                self._stop_active_mode()
                return self.snapshot()

            result["status"] = "passed"
            result["error"] = None
            self._stop_active_mode()
            if all(value["status"] == "passed" for value in self._mode_results.values()):
                self._workflow = "ready_to_validate"
            else:
                self._workflow = "stack_ready"
            self._error = None
            return self.snapshot()

    def validate(self) -> dict:
        with self._lock:
            self._refresh()
            if not all(
                result["status"] == "passed"
                for result in self._mode_results.values()
            ):
                raise CheckupError("Baseline and Snake must both pass before validation.")
            provenance = self._provenance or self._provenance_provider()
            constraints = evaluate_provenance(
                provenance, self._diagnostics.profile.expected_revisions
            )
            failures = [item for item in constraints if not item["passed"]]
            if failures:
                paths = ", ".join(item["path"] for item in failures)
                raise CheckupError(f"Git revision constraint failed for: {paths}.")
            completed_at = self._utc_clock()
            self._pending_report = {
                "schema_version": 1,
                "checkup_id": str(uuid.uuid4()),
                "started_at_utc": self._started_at_utc,
                "completed_at_utc": completed_at,
                "status": "PASSED_WITH_WARNINGS",
                "environment": "simulation" if self._use_simulation else "hardware",
                "platform": {
                    "ros_distro": self._ros_distro,
                    "operating_system": platform.platform(),
                },
                "diagnostic_profile": {
                    "schema_version": self._diagnostics.profile.schema_version,
                    "path": str(self._diagnostics.profile.path),
                    "sha256": self._diagnostics.profile.digest,
                    "measurement_window_sec": (
                        self._diagnostics.profile.measurement_window_sec
                    ),
                    "diagnostic_timeout_sec": (
                        self._diagnostics.profile.diagnostic_timeout_sec
                    ),
                    "joystick_deadzone": self._diagnostics.profile.joystick_deadzone,
                },
                "git_provenance": provenance,
                "revision_constraints": constraints,
                "modes": self._mode_results,
                "go_to": {
                    "available": False,
                    "blocking": False,
                    "warning": "Go-to functions are not implemented yet.",
                    "targets": ["target_1", "target_2", "target_3", "starting_point"],
                },
            }
            self._stop_active_mode()
            self._stack.stop()
            self._workflow = "validated"
            self._error = None
            return self.snapshot()

    def reset(self) -> dict:
        with self._lock:
            self._stop_active_mode()
            self._stack.stop()
            self._reset_state()
            return self.snapshot()

    def _stop_active_mode(self) -> None:
        active = self._modes.active_mode()
        if active is not None:
            self._modes.deactivate(active)
        self._active_mode = None
        self._diagnostic_deadline = None

    def _refresh(self) -> None:
        if self._workflow not in (
            "mode_checking",
            "awaiting_joystick",
            "awaiting_confirmation",
        ):
            return
        if self._active_mode is None:
            return
        if self._workflow == "awaiting_confirmation":
            return
        evidence = self._diagnostics.evaluate(self._active_mode)
        result = self._mode_results[self._active_mode]
        result["evidence"] = evidence
        if evidence["passed"]:
            if evidence["joystick_activity"]:
                result["status"] = "awaiting_confirmation"
                self._workflow = "awaiting_confirmation"
            else:
                result["status"] = "awaiting_joystick"
                self._workflow = "awaiting_joystick"
            return
        if self._diagnostic_deadline is not None and self._clock() >= self._diagnostic_deadline:
            failed = [
                check["name"] for check in evidence["checks"] if not check["passed"]
            ]
            message = "Automatic diagnostics failed: " + ", ".join(failed)
            result["status"] = "failed"
            result["error"] = message
            self._workflow = "error"
            self._error = message
            self._stop_active_mode()

    def snapshot(self) -> dict:
        with self._lock:
            self._refresh()
            return {
                "workflow": self._workflow,
                "current_panel": "C" if self._workflow == "validated" else "B",
                "error": self._error,
                "stack": self._stack.snapshot(),
                "mode_process": self._modes.snapshot(),
                "active_mode": self._active_mode,
                "modes": self._mode_results,
                "can_validate": all(
                    result["status"] == "passed"
                    for result in self._mode_results.values()
                ),
                "go_to": {
                    "available": False,
                    "blocking": False,
                    "targets": ["target_1", "target_2", "target_3", "starting_point"],
                },
                "pending_report": self._pending_report,
                "diagnostic_profile": {
                    "path": str(self._diagnostics.profile.path),
                    "sha256": self._diagnostics.profile.digest,
                    "measurement_window_sec": (
                        self._diagnostics.profile.measurement_window_sec
                    ),
                },
            }

    def shutdown(self) -> None:
        with self._lock:
            self._stop_active_mode()
            self._stack.shutdown()

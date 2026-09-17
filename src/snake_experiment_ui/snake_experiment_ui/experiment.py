"""Panel D experiment block orchestration and durable progress."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Callable, Optional

import yaml


MODES = ("baseline", "snake")
PHASE_PANELS = {"discovery": "E", "training": "F", "recording": "G"}
REQUIRED_SEQUENCE = (
    ("mode_1_discovery", "mode_1", "discovery"),
    ("mode_2_discovery", "mode_2", "discovery"),
    ("mode_1_training", "mode_1", "training"),
    ("mode_1_recording", "mode_1", "recording"),
    ("mode_2_training", "mode_2", "training"),
    ("mode_2_recording", "mode_2", "recording"),
)


class ExperimentError(RuntimeError):
    """An operator-correctable Panel D-G error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(value, target, indent=2, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


class ExperimentProfile:
    """Validated, versioned description of the six Panel D blocks."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise ExperimentError(f"Unable to read experiment profile: {error}") from error
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ExperimentError("experiment.yaml must use schema_version 1.")
        sequence = data.get("block_sequence")
        if not isinstance(sequence, list):
            raise ExperimentError("experiment.yaml block_sequence must be a list.")
        observed = tuple(
            (item.get("id"), item.get("mode_role"), item.get("phase"))
            for item in sequence
            if isinstance(item, dict)
        )
        if observed != REQUIRED_SEQUENCE:
            raise ExperimentError("experiment.yaml must contain the required six-block order.")
        phases = data.get("phases")
        if not isinstance(phases, dict):
            raise ExperimentError("experiment.yaml phases are missing.")
        for phase in PHASE_PANELS:
            settings = phases.get(phase)
            if not isinstance(settings, dict):
                raise ExperimentError(f"experiment.yaml phase '{phase}' is missing.")
            topics = settings.get("rosbag_topics")
            if topics != ["/joy", "/ee_pose", "/joint_states"]:
                raise ExperimentError(f"Invalid rosbag_topics for phase '{phase}'.")
        if phases["training"].get("cycles") != 2:
            raise ExperimentError("Training must define exactly 2 cycles.")
        if phases["recording"].get("cycles") != 10:
            raise ExperimentError("Recording must define exactly 10 cycles.")
        for phase in ("training", "recording"):
            if phases[phase].get("target_sequence") != [1, 2, 3, 1]:
                raise ExperimentError(f"Invalid target sequence for phase '{phase}'.")
        thresholds = data.get("success_thresholds")
        if not isinstance(thresholds, dict) or set(thresholds) != {
            "linear_m",
            "angular_rad",
        } or any(value is not None for value in thresholds.values()):
            raise ExperimentError("Success thresholds must be explicitly null.")
        self.data = data
        self.sha256 = _sha256(self.path)

    def blocks(self, plan: str) -> list[dict]:
        assigned = plan.split("->")
        if len(assigned) != 2 or set(assigned) != set(MODES):
            raise ExperimentError(f"Unsupported experimental plan: {plan}")
        role_modes = {"mode_1": assigned[0], "mode_2": assigned[1]}
        blocks = []
        for index, item in enumerate(self.data["block_sequence"]):
            resolved_mode = role_modes[item["mode_role"]]
            phase = item["phase"]
            blocks.append(
                {
                    "id": item["id"],
                    "order": index + 1,
                    "mode_role": item["mode_role"],
                    "mode": resolved_mode,
                    "phase": phase,
                    "panel": PHASE_PANELS[phase],
                    "folder": f"{resolved_mode}_{phase}",
                    "settings": dict(self.data["phases"][phase]),
                    "success_thresholds": dict(self.data["success_thresholds"]),
                    "status": "not_started",
                    "attempts": 0,
                    "started_at_utc": None,
                    "updated_at_utc": None,
                    "completed_at_utc": None,
                    "error": None,
                }
            )
        return blocks


class ExperimentController:
    """Run one owned stack and one owned mapper through the prescribed blocks."""

    def __init__(
        self,
        profile: ExperimentProfile,
        stack_manager,
        mode_manager,
        utc_clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._source_profile = profile
        self._profile = profile
        self._stack = stack_manager
        self._modes = mode_manager
        self._utc_clock = utc_clock
        self._lock = threading.RLock()
        self._participant: Optional[dict] = None
        self._progress: Optional[dict] = None
        self._current_panel = "D"
        self._error: Optional[str] = None

    def prepare(self, participant: dict) -> dict:
        """Load or create durable progress without starting a ROS process."""
        with self._lock:
            folder = Path(participant["folder"]).resolve()
            if not folder.is_dir():
                raise ExperimentError(f"Participant folder not found: {folder}")
            profile_path = folder / "experimental_environment" / "experiment.yaml"
            try:
                manifest = json.loads(
                    (folder / "manifest.json").read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ExperimentError("Participant manifest is unreadable.") from error
            profile_is_manifested = any(
                item.get("path") == "experimental_environment/experiment.yaml"
                for item in manifest.get("files", [])
                if isinstance(item, dict)
            )
            late_initialization = not profile_is_manifested
            if not profile_path.is_file():
                if profile_is_manifested:
                    raise ExperimentError(
                        "The snapshotted experiment profile is missing from this session."
                    )
                profile_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self._source_profile.path, profile_path)
            profile = ExperimentProfile(profile_path)
            if late_initialization:
                self._record_late_initialization(folder, profile_path)
            self._profile = profile
            progress_path = folder / "experiment_progress.json"
            if progress_path.is_file():
                try:
                    progress = json.loads(progress_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    raise ExperimentError("experiment_progress.json is unreadable.") from error
                self._validate_progress(progress, participant)
                changed = False
                for block in progress["blocks"]:
                    if block["status"] in ("starting", "running"):
                        block["status"] = "interrupted"
                        block["updated_at_utc"] = self._utc_clock()
                        block["error"] = "Interface restarted while the block was active."
                        self._write_block(folder, block)
                        changed = True
                if changed:
                    progress["workflow"] = "interrupted"
                    progress["current_block"] = None
                    progress["updated_at_utc"] = self._utc_clock()
                    _atomic_json(progress_path, progress)
            else:
                now = self._utc_clock()
                progress = {
                    "schema_version": 1,
                    "pseudonym": participant["pseudonym"],
                    "experimental_plan": participant["experimental_plan"],
                    "profile": {
                        "path": "experimental_environment/experiment.yaml",
                        "sha256": self._profile.sha256,
                    },
                    "created_at_utc": now,
                    "updated_at_utc": now,
                    "workflow": "ready",
                    "current_block": None,
                    "late_initialization": late_initialization,
                    "warnings": (
                        ["Experiment profile and progress were initialized for a legacy session."]
                        if late_initialization
                        else []
                    ),
                    "blocks": self._profile.blocks(participant["experimental_plan"]),
                }
                _atomic_json(progress_path, progress)
            self._participant = dict(participant)
            self._progress = progress
            self._current_panel = "D"
            self._error = None
            return self.snapshot()

    def start(self, block_id: str) -> dict:
        with self._lock:
            progress, folder = self._require_prepared()
            if progress["current_block"] is not None:
                raise ExperimentError("Another experiment block is already active.")
            block = self._block(block_id)
            expected = self._next_block()
            if expected is None:
                raise ExperimentError("The experiment sequence is already complete.")
            if block["id"] != expected["id"]:
                raise ExperimentError(f"The next required block is {expected['id']}.")
            if block["status"] not in ("not_started", "interrupted", "error"):
                raise ExperimentError(f"Block {block_id} cannot be started from {block['status']}.")
            block_folder = folder / block["folder"]
            if block["status"] == "not_started" and block_folder.exists():
                raise ExperimentError(f"Block folder already exists unexpectedly: {block['folder']}")
            block_folder.mkdir(parents=False, exist_ok=True)
            block["attempts"] += 1
            now = self._utc_clock()
            block["started_at_utc"] = block["started_at_utc"] or now
            block["updated_at_utc"] = now
            block["status"] = "starting"
            block["error"] = None
            progress["workflow"] = "starting_block"
            progress["current_block"] = block_id
            self._persist(folder)
            self._write_block(folder, block)
            try:
                self._stack.start()
                self._modes.activate(block["mode"])
            except Exception as error:
                active_mode = self._modes.active_mode()
                if active_mode:
                    try:
                        self._modes.deactivate(active_mode)
                    except Exception:
                        pass
                block["status"] = "error"
                block["error"] = str(error)
                block["updated_at_utc"] = self._utc_clock()
                progress["workflow"] = "error"
                progress["current_block"] = None
                self._error = f"Unable to start block {block_id}: {error}"
                self._persist(folder)
                self._write_block(folder, block)
                raise ExperimentError(self._error) from error
            block["status"] = "running"
            block["updated_at_utc"] = self._utc_clock()
            progress["workflow"] = "block_running"
            self._current_panel = block["panel"]
            self._error = None
            self._persist(folder)
            self._write_block(folder, block)
            return self.snapshot()

    def end(self, block_id: str, confirmed: bool) -> dict:
        with self._lock:
            if not confirmed:
                raise ExperimentError("Explicit confirmation is required to end a block.")
            progress, folder = self._require_prepared()
            block = self._running_block(block_id)
            try:
                self._modes.deactivate(block["mode"])
            except Exception as error:
                block["error"] = f"Unable to stop mapper: {error}"
                block["updated_at_utc"] = self._utc_clock()
                self._persist(folder)
                self._write_block(folder, block)
                raise ExperimentError(block["error"]) from error
            now = self._utc_clock()
            block["status"] = "completed"
            block["completed_at_utc"] = now
            block["updated_at_utc"] = now
            block["error"] = None
            progress["current_block"] = None
            self._current_panel = "D"
            if self._next_block() is None:
                try:
                    self._stack.stop()
                except Exception as error:
                    progress["workflow"] = "error"
                    self._error = f"Sequence completed, but stack shutdown failed: {error}"
                    self._persist(folder)
                    self._write_block(folder, block)
                    raise ExperimentError(self._error) from error
                progress["workflow"] = "sequence_completed"
            else:
                progress["workflow"] = "ready"
            self._error = None
            self._persist(folder)
            self._write_block(folder, block)
            return self.snapshot()

    def abort(self, block_id: str) -> dict:
        with self._lock:
            progress, folder = self._require_prepared()
            block = self._running_block(block_id)
            try:
                self._modes.deactivate(block["mode"])
            except Exception as error:
                raise ExperimentError(f"Unable to stop mapper: {error}") from error
            block["status"] = "interrupted"
            block["updated_at_utc"] = self._utc_clock()
            block["error"] = "Stopped by the operator; resume this block to continue."
            progress["workflow"] = "interrupted"
            progress["current_block"] = None
            self._current_panel = "D"
            self._error = None
            self._persist(folder)
            self._write_block(folder, block)
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            if self._progress is None:
                return {
                    "workflow": "not_prepared",
                    "current_panel": "D",
                    "error": None,
                    "participant": None,
                    "profile": None,
                    "progress": None,
                    "stack": self._stack.snapshot(),
                    "mapper": self._modes.snapshot(),
                }
            stack_state = self._stack.snapshot()
            mapper_state = self._modes.snapshot()
            if (
                self._progress.get("workflow") == "block_running"
                and self._progress.get("current_block")
            ):
                block = self._block(self._progress["current_block"])
                process_failed = (
                    stack_state.get("status") != "active"
                    or mapper_state.get("status") != "active"
                    or mapper_state.get("active_mode") != block["mode"]
                )
                if process_failed:
                    folder = Path(self._participant["folder"])
                    if mapper_state.get("active_mode") == block["mode"]:
                        try:
                            self._modes.deactivate(block["mode"])
                            mapper_state = self._modes.snapshot()
                        except Exception:
                            pass
                    block["status"] = "interrupted"
                    block["updated_at_utc"] = self._utc_clock()
                    block["error"] = "A required owned process stopped unexpectedly."
                    self._progress["workflow"] = "interrupted"
                    self._progress["current_block"] = None
                    self._current_panel = "D"
                    self._error = block["error"]
                    self._persist(folder)
                    self._write_block(folder, block)
            return {
                "workflow": self._progress["workflow"],
                "current_panel": self._current_panel,
                "error": self._error,
                "participant": {
                    "pseudonym": self._participant["pseudonym"],
                    "experimental_plan": self._participant["experimental_plan"],
                    "folder": self._participant["folder"],
                },
                "profile": {
                    "path": str(self._profile.path),
                    "sha256": self._profile.sha256,
                    "schema_version": self._profile.data["schema_version"],
                    "configuration": json.loads(json.dumps(self._profile.data)),
                },
                "progress": json.loads(json.dumps(self._progress)),
                "stack": stack_state,
                "mapper": mapper_state,
            }

    def shutdown(self) -> None:
        with self._lock:
            if self._progress is not None and self._progress.get("current_block"):
                folder = Path(self._participant["folder"])
                block = self._block(self._progress["current_block"])
                block["status"] = "interrupted"
                block["updated_at_utc"] = self._utc_clock()
                block["error"] = "Interface stopped while the block was active."
                self._progress["workflow"] = "interrupted"
                self._progress["current_block"] = None
                self._persist(folder)
                self._write_block(folder, block)
            self._modes.shutdown()
            self._stack.shutdown()
            self._current_panel = "D"

    def _require_prepared(self) -> tuple[dict, Path]:
        if self._progress is None or self._participant is None:
            raise ExperimentError("Prepare a participant from Panel C first.")
        return self._progress, Path(self._participant["folder"])

    def _block(self, block_id: str) -> dict:
        if self._progress is None:
            raise ExperimentError("Experiment progress is not prepared.")
        for block in self._progress["blocks"]:
            if block["id"] == block_id:
                return block
        raise ExperimentError(f"Unknown experiment block: {block_id}")

    def _next_block(self) -> Optional[dict]:
        if self._progress is None:
            return None
        return next(
            (block for block in self._progress["blocks"] if block["status"] != "completed"),
            None,
        )

    def _running_block(self, block_id: str) -> dict:
        block = self._block(block_id)
        if self._progress["current_block"] != block_id or block["status"] != "running":
            raise ExperimentError(f"Block {block_id} is not currently running.")
        return block

    def _persist(self, folder: Path) -> None:
        self._progress["updated_at_utc"] = self._utc_clock()
        _atomic_json(folder / "experiment_progress.json", self._progress)

    def _write_block(self, participant_folder: Path, block: dict) -> None:
        block_folder = participant_folder / block["folder"]
        block_folder.mkdir(parents=True, exist_ok=True)
        _atomic_json(block_folder / "block.json", block)

    def _validate_progress(self, progress: dict, participant: dict) -> None:
        if progress.get("schema_version") != 1:
            raise ExperimentError("Unsupported experiment progress schema.")
        if progress.get("pseudonym") != participant["pseudonym"]:
            raise ExperimentError("Experiment progress belongs to another participant.")
        if progress.get("experimental_plan") != participant["experimental_plan"]:
            raise ExperimentError("Experiment progress uses another experimental plan.")
        expected = self._profile.blocks(participant["experimental_plan"])
        blocks = progress.get("blocks")
        if (
            not isinstance(blocks, list)
            or not all(isinstance(item, dict) for item in blocks)
            or [item.get("id") for item in blocks] != [
                item["id"] for item in expected
            ]
        ):
            raise ExperimentError("Experiment progress has an invalid block sequence.")
        if progress.get("profile", {}).get("sha256") != self._profile.sha256:
            raise ExperimentError("Saved experiment progress does not match its profile snapshot.")
        valid_statuses = {
            "not_started",
            "starting",
            "running",
            "completed",
            "interrupted",
            "error",
        }
        if any(block.get("status") not in valid_statuses for block in blocks):
            raise ExperimentError("Experiment progress contains an invalid block status.")
        current = progress.get("current_block")
        if current is not None and current not in {block["id"] for block in blocks}:
            raise ExperimentError("Experiment progress references an unknown active block.")

    def _record_late_initialization(self, folder: Path, profile_path: Path) -> None:
        manifest_path = folder / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ExperimentError("Participant manifest is unreadable.") from error
        relative = "experimental_environment/experiment.yaml"
        files = [item for item in manifest.get("files", []) if item.get("path") != relative]
        files.append({"path": relative, "sha256": _sha256(profile_path)})
        manifest["files"] = files
        history = list(manifest.get("late_initializations", []))
        history.append(
            {
                "at_utc": self._utc_clock(),
                "component": "experiment_profile_and_progress",
                "warning": "Initialized when Panel D was first opened for a legacy session.",
            }
        )
        manifest["late_initializations"] = history
        manifest["experiment"] = {
            "profile": relative,
            "progress": "experiment_progress.json",
        }
        _atomic_json(manifest_path, manifest)

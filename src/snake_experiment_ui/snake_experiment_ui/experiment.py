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
import time
from typing import Callable, Optional

import yaml

from .go_to import GoToError
from .participant_profile import STATE_NAMES, state_image_is_available
from .training import (
    TrainingError,
    build_trials,
    effective_thresholds,
    pose_error,
    validate_thresholds,
    validate_training_settings,
)


MODES = ("baseline", "snake")
PHASE_PANELS = {"discovery": "E", "training": "F", "recording": "G"}
TRIAL_PHASES = ("training", "recording")
REQUIRED_SEQUENCE = (
    ("mode_1_discovery", "mode_1", "discovery"),
    ("mode_2_discovery", "mode_2", "discovery"),
    ("mode_1_training", "mode_1", "training"),
    ("mode_1_recording", "mode_1", "recording"),
    ("mode_2_training", "mode_2", "training"),
    ("mode_2_recording", "mode_2", "recording"),
)
DEFAULT_TRAINING_SETTINGS = {
    "cycles": 2,
    "target_sequence": [1, 2, 3, 1],
    "rosbag_topics": ["/joy", "/ee_pose", "/joint_states"],
    "trial_folder_pattern": (
        "{mode}_training_trial_{trial_id:03d}_{cycle:02d}_"
        "{target_start}_{target_end}"
    ),
    "attempt_pattern": "attempt_{attempt:03d}",
}
DEFAULT_RECORDING_SETTINGS = {
    "cycles": 10,
    "target_sequence": [1, 2, 3, 1],
    "rosbag_topics": ["/joy", "/ee_pose", "/joint_states"],
    "trial_folder_pattern": (
        "{mode}_trial_{trial_id:03d}_{cycle:02d}_"
        "{target_start}_{target_end}"
    ),
    "attempt_pattern": "attempt_{attempt:03d}",
}
DEFAULT_TRAINING_THRESHOLDS = {
    "provisional": True,
    "linear_mm": 5.0,
    "angular_deg": 5.0,
    "start_linear_mm": 5.0,
    "start_angular_deg": 5.0,
    "success_dwell_sec": 0.5,
    "pose_freshness_sec": 0.5,
}


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

    def __init__(self, path: Path, allow_legacy: bool = False) -> None:
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
        default_instructions = {
            "placeholder": True,
            "text": (
                "TODO — Replace this placeholder with the approved standardized "
                "discovery instructions before running participant sessions."
            ),
        }
        instructions = phases["discovery"].get("instructions")
        if instructions is None and allow_legacy:
            phases["discovery"]["instructions"] = default_instructions
        elif (
            not isinstance(instructions, dict)
            or not isinstance(instructions.get("placeholder"), bool)
            or not str(instructions.get("text", "")).strip()
        ):
            raise ExperimentError("Discovery instructions are missing or invalid.")
        if allow_legacy:
            for key, value in DEFAULT_TRAINING_SETTINGS.items():
                phases["training"].setdefault(key, json.loads(json.dumps(value)))
            for key, value in DEFAULT_RECORDING_SETTINGS.items():
                phases["recording"].setdefault(key, json.loads(json.dumps(value)))
        try:
            validate_training_settings(phases["training"])
            validate_training_settings(
                phases["recording"],
                "snake_trial_001_01_1_2",
            )
        except TrainingError as error:
            raise ExperimentError(str(error)) from error
        thresholds = data.get("success_thresholds")
        if allow_legacy and (
            not isinstance(thresholds, dict)
            or set(thresholds) == {"linear_m", "angular_rad"}
        ):
            thresholds = dict(DEFAULT_TRAINING_THRESHOLDS)
            data["success_thresholds"] = thresholds
        try:
            validate_thresholds(thresholds)
        except TrainingError as error:
            raise ExperimentError(str(error)) from error
        rosbag = data.get("rosbag")
        if rosbag is None and allow_legacy:
            rosbag = {"storage": "mcap", "segment_pattern": "rosbag_{index:03d}"}
            data["rosbag"] = rosbag
        if not isinstance(rosbag, dict) or rosbag.get("storage") != "mcap":
            raise ExperimentError("experiment.yaml rosbag storage must be 'mcap'.")
        pattern = rosbag.get("segment_pattern")
        try:
            examples = [str(pattern).format(index=index) for index in (1, 999)]
        except (AttributeError, KeyError, ValueError) as error:
            raise ExperimentError("Invalid rosbag segment_pattern.") from error
        if examples != ["rosbag_001", "rosbag_999"]:
            raise ExperimentError("rosbag segment_pattern must produce rosbag_001 names.")
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
                    "settings": json.loads(json.dumps(self.data["phases"][phase])),
                    "success_thresholds": dict(self.data["success_thresholds"]),
                    "status": "not_started",
                    "attempts": 0,
                    "started_at_utc": None,
                    "updated_at_utc": None,
                    "completed_at_utc": None,
                    "error": None,
                    "control_active": False,
                    "mode_explanation": {
                        "status": "pending", "shown_at_utc": None,
                        "completed_at_utc": None, "video_missing": None,
                    },
                    "current_segment": None,
                    "segments": [],
                    "restart": {"status": "idle", "error": None},
                    "go_to_history": [],
                    # Schema 1 retains the training_* storage keys for backward
                    # compatibility; they carry the shared trial workflow for G.
                    "training_workflow": (
                        "awaiting_prepare" if phase in TRIAL_PHASES else None
                    ),
                    "training_trials": (
                        build_trials(resolved_mode, self.data["phases"][phase])
                        if phase in TRIAL_PHASES
                        else []
                    ),
                    "current_trial_id": None,
                    "training_deviations": [],
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
        rosbag_manager,
        utc_clock: Callable[[], str] = _utc_now,
        monotonic_clock: Callable[[], float] = time.monotonic,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
        go_to_controller=None,
        presentation_video: Optional[Path] = None,
        mode_explanation_videos: Optional[dict] = None,
        state_images: Optional[dict] = None,
        snake_button_provider: Optional[Callable[[], Optional[bool]]] = None,
    ) -> None:
        self._source_profile = profile
        self._profile = profile
        self._stack = stack_manager
        self._modes = mode_manager
        self._rosbag = rosbag_manager
        self._utc_clock = utc_clock
        self._monotonic_clock = monotonic_clock
        self._thread_factory = thread_factory
        self._go_to = go_to_controller
        self._presentation_video = Path(presentation_video).expanduser().resolve() if presentation_video else None
        self._mode_explanation_videos = {
            mode: Path(path).expanduser().resolve() if path else None
            for mode, path in (mode_explanation_videos or {}).items()
            if mode in MODES
        }
        self._state_images = {
            mode: {
                state: Path(path).expanduser().resolve() if path else None
                for state, path in (state_images or {}).get(mode, {}).items()
                if state in STATE_NAMES[mode]
            }
            for mode in MODES
        }
        self._mapper_local_mode: Optional[str] = None
        self._snake_button_provider = snake_button_provider or (lambda: None)
        self._lock = threading.RLock()
        self._participant: Optional[dict] = None
        self._progress: Optional[dict] = None
        self._current_panel = "D"
        self._error: Optional[str] = None
        self._calibration: Optional[dict] = None
        self._latest_pose: Optional[dict] = None
        self._latest_pose_monotonic: Optional[float] = None

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
            profile = ExperimentProfile(profile_path, allow_legacy=True)
            if late_initialization:
                self._record_late_initialization(folder, profile_path)
            self._profile = profile
            self._calibration = self._load_calibration(folder)
            progress_path = folder / "experiment_progress.json"
            if progress_path.is_file():
                try:
                    progress = json.loads(progress_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    raise ExperimentError("experiment_progress.json is unreadable.") from error
                self._validate_progress(progress, participant)
                changed = False
                if "presentation" not in progress:
                    progress["presentation"] = {
                        "status": "legacy_skipped", "shown_at_utc": None,
                        "completed_at_utc": None, "video_missing": None,
                    }
                    changed = True
                recovered_active = False
                migrated_trial_phases = set()
                for block in progress["blocks"]:
                    changed = self._ensure_lot5_fields(block) or changed
                    if "go_to_history" not in block:
                        block["go_to_history"] = []
                        changed = True
                    migrated = self._ensure_trial_fields(block)
                    changed = migrated or changed
                    if migrated and block.get("phase") in TRIAL_PHASES:
                        migrated_trial_phases.add(block["phase"])
                    if block["status"] in ("starting", "running"):
                        if block.get("current_segment"):
                            segment = self._segment(block, block["current_segment"])
                            segment["status"] = "interrupted"
                            segment["stopped_at_utc"] = self._utc_clock()
                            segment["stop_reason"] = "interface_restart"
                            segment["valid"] = False
                        if block["phase"] in TRIAL_PHASES:
                            self._recover_training_attempt(
                                block, folder, "interface_restart"
                            )
                        block["control_active"] = False
                        block["current_segment"] = None
                        block["status"] = "interrupted"
                        block["updated_at_utc"] = self._utc_clock()
                        block["error"] = "Interface restarted while the block was active."
                        self._write_block(folder, block)
                        changed = True
                        recovered_active = True
                if changed:
                    for phase in sorted(migrated_trial_phases):
                        lot = 6 if phase == "training" else 7
                        warning = (
                            f"LOT {lot} {phase} fields were initialised with the "
                            "effective development profile."
                        )
                        progress.setdefault("warnings", [])
                        if warning not in progress["warnings"]:
                            progress["warnings"].append(warning)
                    if recovered_active:
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
                    "presentation": {
                        "status": "legacy_skipped" if late_initialization else "pending",
                        "shown_at_utc": None,
                        "completed_at_utc": None, "video_missing": None,
                    },
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

    def show_presentation(self) -> dict:
        with self._lock:
            progress, folder = self._require_prepared()
            if progress["current_block"] is not None:
                raise ExperimentError("Stop the active block before showing the presentation.")
            presentation = progress["presentation"]
            if presentation["status"] == "legacy_skipped":
                raise ExperimentError("This legacy session does not require presentation validation.")
            if presentation["status"] == "pending":
                presentation["status"] = "showing"
                presentation["shown_at_utc"] = self._utc_clock()
                self._persist(folder)
            return self.snapshot()

    def complete_presentation(self) -> dict:
        with self._lock:
            progress, folder = self._require_prepared()
            presentation = progress["presentation"]
            if presentation["status"] != "showing":
                raise ExperimentError("Display the experiment presentation before validating it.")
            presentation["status"] = "completed"
            presentation["completed_at_utc"] = self._utc_clock()
            presentation["video_missing"] = not self.presentation_video_available()
            if presentation["video_missing"]:
                warning = "Experiment presentation video was unavailable at manual validation."
                if warning not in progress["warnings"]:
                    progress["warnings"].append(warning)
            self._persist(folder)
            return self.snapshot()

    def presentation_video_available(self) -> bool:
        from .participant_profile import video_is_available

        return video_is_available(self._presentation_video)

    def mode_explanation_video_available(self, mode: str) -> bool:
        from .participant_profile import video_is_available

        return mode in MODES and video_is_available(
            self._mode_explanation_videos.get(mode)
        )

    def state_image_available(self, mode: str, state: str) -> bool:
        return state in STATE_NAMES.get(mode, ()) and state_image_is_available(
            self._state_images.get(mode, {}).get(state)
        )

    def record_mapper_local_mode(self, state: str) -> None:
        """Record the mapper's reported local mode, never infer it from joystick input."""
        with self._lock:
            self._mapper_local_mode = state if state in {"b1", "b2", "b3"} else None

    def clear_mapper_local_mode(self) -> None:
        with self._lock:
            self._mapper_local_mode = None

    def show_mode_explanation(self, block_id: str) -> dict:
        with self._lock:
            _, folder = self._require_prepared()
            block = self._running_block(block_id)
            if block["phase"] != "discovery":
                raise ExperimentError("Mode explanation is available only during discovery.")
            explanation = block["mode_explanation"]
            if explanation["status"] == "pending":
                explanation["status"] = "showing"
                explanation["shown_at_utc"] = self._utc_clock()
                block["updated_at_utc"] = self._utc_clock()
                self._persist(folder)
                self._write_block(folder, block)
            return self.snapshot()

    def complete_mode_explanation(self, block_id: str) -> dict:
        with self._lock:
            progress, folder = self._require_prepared()
            block = self._running_block(block_id)
            if block["phase"] != "discovery":
                raise ExperimentError("Mode explanation is available only during discovery.")
            explanation = block["mode_explanation"]
            if explanation["status"] != "showing":
                raise ExperimentError("Show the mode explanation before validating it.")
            explanation["status"] = "completed"
            explanation["completed_at_utc"] = self._utc_clock()
            explanation["video_missing"] = not self.mode_explanation_video_available(block["mode"])
            if explanation["video_missing"]:
                warning = f"{block['mode'].title()} explanation video was unavailable at manual validation."
                if warning not in progress["warnings"]:
                    progress["warnings"].append(warning)
            block["updated_at_utc"] = self._utc_clock()
            self._persist(folder)
            self._write_block(folder, block)
            return self.snapshot()

    def participant_snapshot(self) -> dict:
        """Public, read-only projection with no participant or session metadata."""
        with self._lock:
            presentation = self._progress.get("presentation", {}) if self._progress else {}
            status = presentation.get("status", "pending")
            active = (
                self._block(self._progress["current_block"])
                if self._progress and self._progress.get("current_block")
                else None
            )
            if active and active["phase"] == "discovery":
                explanation = active["mode_explanation"]
                visible = explanation["status"] in ("showing", "completed")
                return {
                    "panel": "B" if visible else "waiting",
                    "presentation_status": status,
                    "video_available": (
                        self.mode_explanation_video_available(active["mode"])
                        if visible else False
                    ),
                    "mode": active["mode"] if visible else None,
                    "explanation_status": explanation["status"],
                }
            if active and active["phase"] in TRIAL_PHASES and active["status"] == "running":
                self._refresh_training_live(active)
                workflow = active.get("training_workflow")
                trial = next(
                    (
                        item for item in active.get("training_trials", [])
                        if item.get("id") == active.get("current_trial_id")
                    ),
                    None,
                )
                mapper = self._modes.snapshot()
                state = (
                    self._mapper_local_mode
                    if mapper.get("status") == "active"
                    and mapper.get("active_mode") == active["mode"]
                    and self._mapper_local_mode in STATE_NAMES[active["mode"]]
                    else None
                )
                recording = workflow == "recording"
                live = active.get("training_live", {})
                target_error = live.get("target_error") if recording and live.get("pose_fresh") else None
                latest_completed = max(
                    (
                        item for item in active.get("training_trials", [])
                        if item.get("status") == "completed"
                    ),
                    key=lambda item: item["id"],
                    default=None,
                )
                reached = workflow in ("awaiting_prepare", "ready_to_end") and latest_completed is not None
                return {
                    "panel": "C",
                    "phase": active["phase"],
                    "mode": active["mode"],
                    "task": (
                        f"Go to target {trial['target_end']}" if recording and trial
                        else "Preparing trial" if trial and workflow in ("awaiting_start_pose", "ready")
                        else "Waiting for next trial"
                    ),
                    "state": "Recording" if recording else "Target reached" if reached else "Waiting",
                    "linear_mm": target_error.get("linear_mm") if target_error else None,
                    "angular_deg": target_error.get("angular_deg") if target_error else None,
                    "local_mode": state,
                    "snake_button_held": (
                        self._snake_button_provider()
                        if active["mode"] == "snake" and state is not None
                        else None
                    ),
                    "state_image_available": bool(
                        state and self.state_image_available(active["mode"], state)
                    ),
                }
            any_explanation = bool(
                self._progress and any(
                    block["phase"] == "discovery"
                    and block["mode_explanation"]["status"] != "pending"
                    for block in self._progress["blocks"]
                )
            )
            return {
                "panel": "A" if status in ("showing", "completed") and not any_explanation else "waiting",
                "presentation_status": status,
                "video_available": self.presentation_video_available(),
                "mode": None,
                "explanation_status": "pending",
            }

    def start(self, block_id: str) -> dict:
        with self._lock:
            progress, folder = self._require_prepared()
            if progress["presentation"]["status"] not in ("completed", "legacy_skipped"):
                raise ExperimentError("Validate the experiment presentation before starting a block.")
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
            if block["status"] == "interrupted" and block["phase"] in TRIAL_PHASES:
                # Recover sessions interrupted by older versions while a trial
                # was selected but before any acquisition had started.
                self._reset_unstarted_trial(block, folder)
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
                if block["phase"] != "discovery":
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
            block["control_active"] = block["phase"] != "discovery"
            block["updated_at_utc"] = self._utc_clock()
            progress["workflow"] = "block_running"
            self._current_panel = block["panel"]
            self._error = None
            self._persist(folder)
            self._write_block(folder, block)
            return self.snapshot()

    def start_go_to(self, block_id: str, pose_id: str) -> dict:
        """Suspend trial control and command a calibrated start pose."""
        allowed = ("target_out_1", "target_out_2", "target_out_3", "starting_point")
        if pose_id not in allowed:
            raise ExperimentError(f"Unknown trial Go-to pose: {pose_id}")
        with self._lock:
            _, folder = self._require_prepared()
            block = self._running_block(block_id)
            if block["phase"] not in TRIAL_PHASES:
                raise ExperimentError("Go-to is available only on Panels F and G.")
            if self._go_to is None:
                raise ExperimentError("Cartesian Go-to is not configured.")
            if block.get("training_workflow") in (
                "recording",
                "stopping_success",
                "incident_review_required",
            ):
                raise ExperimentError(
                    "Go-to is unavailable during acquisition or incident review."
                )
            if self._stack.snapshot().get("status") != "active":
                raise ExperimentError("Restart the stack before using Go-to.")
            if self._modes.active_mode() != block["mode"]:
                raise ExperimentError("The block mapper is not active.")
            try:
                pose = self._calibration_pose(pose_id)
                self._modes.deactivate(block["mode"])
                block["control_active"] = False
                motion = self._go_to.start(
                    pose_id,
                    pose,
                    context={"scope": "experiment", "block_id": block_id},
                    on_finish=lambda record: self._finish_go_to(block_id, record),
                )
            except Exception as error:
                try:
                    if self._stack.snapshot().get("status") == "active":
                        self._modes.activate(block["mode"])
                        block["control_active"] = True
                except Exception as restore_error:
                    block["error"] = f"Unable to restore mapper: {restore_error}"
                self._persist(folder)
                self._write_block(folder, block)
                if isinstance(error, (ExperimentError, TrainingError, GoToError)):
                    raise ExperimentError(str(error)) from error
                raise
            current = motion["current"]
            block["go_to_history"].append(json.loads(json.dumps(current)))
            block["error"] = None
            self._persist(folder)
            self._write_block(folder, block)
            return self.snapshot()

    def stop_go_to(self, block_id: str) -> dict:
        with self._lock:
            self._running_block(block_id)
            if self._go_to is None:
                raise ExperimentError("Cartesian Go-to is not configured.")
            try:
                self._go_to.stop("operator_stop")
            except GoToError as error:
                raise ExperimentError(str(error)) from error
            return self.snapshot()

    def browser_disconnected(self) -> None:
        if self._go_to is not None:
            self._go_to.cancel_if_active("browser_disconnect")

    def _finish_go_to(self, block_id: str, record: dict) -> None:
        with self._lock:
            if self._progress is None or self._participant is None:
                return
            block = self._block(block_id)
            history = block.setdefault("go_to_history", [])
            replacement = json.loads(json.dumps(record))
            history_index = None
            for index, item in enumerate(history):
                if item.get("motion_id") == record.get("motion_id"):
                    history[index] = replacement
                    history_index = index
                    break
            else:
                history.append(replacement)
                history_index = len(history) - 1
            no_restore_reasons = {
                "backend_shutdown",
                "block_abort",
                "block_end",
                "restart_stack",
                "stack_stop",
            }
            if (
                record.get("stop_reason") not in no_restore_reasons
                and self._progress.get("current_block") == block_id
                and block.get("status") == "running"
                and self._stack.snapshot().get("status") == "active"
            ):
                try:
                    self._modes.activate(block["mode"])
                    block["control_active"] = True
                except Exception as error:
                    replacement["mapper_restore_error"] = str(error)
                    history[history_index] = replacement
                    block["control_active"] = False
                    block["error"] = f"Unable to restore mapper after Go-to: {error}"
                    self._error = block["error"]
            folder = Path(self._participant["folder"])
            self._persist(folder)
            self._write_block(folder, block)

    def end(self, block_id: str, confirmed: bool) -> dict:
        with self._lock:
            if not confirmed:
                raise ExperimentError("Explicit confirmation is required to end a block.")
            progress, folder = self._require_prepared()
            block = self._running_block(block_id)
            if self._go_to is not None:
                self._go_to.cancel_if_active("block_end")
            if block["phase"] == "discovery":
                if block["mode_explanation"]["status"] != "completed":
                    raise ExperimentError("Validate the mode explanation before ending discovery.")
                if block["control_active"]:
                    self._deactivate_discovery(block, folder, "end_block")
                if not self._can_end(block):
                    block["error"] = (
                        "Discovery requires at least one valid segment with messages "
                        "on every expected topic."
                    )
                    self._persist(folder)
                    self._write_block(folder, block)
                    raise ExperimentError(block["error"])
            elif block["phase"] in TRIAL_PHASES:
                if not self._trial_can_end(block):
                    block["error"] = "Resolve every trial before ending the block."
                    self._persist(folder)
                    self._write_block(folder, block)
                    raise ExperimentError(block["error"])
                try:
                    self._modes.deactivate(block["mode"])
                except Exception as error:
                    block["error"] = f"Unable to stop mapper: {error}"
                    self._persist(folder)
                    self._write_block(folder, block)
                    raise ExperimentError(block["error"]) from error
            else:
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
            if self._go_to is not None:
                self._go_to.cancel_if_active("block_abort")
            cleanup_errors = []
            if block["phase"] == "discovery":
                if block["control_active"]:
                    try:
                        self._deactivate_discovery(block, folder, "operator_abort")
                    except Exception as error:
                        cleanup_errors.append(str(error))
                try:
                    self._stack.stop()
                except Exception as error:
                    cleanup_errors.append(f"stack: {error}")
            elif block["phase"] in TRIAL_PHASES:
                if block.get("training_workflow") in ("recording", "stopping_success"):
                    try:
                        self._finish_training_attempt(
                            block, folder, "operator_abort", successful=False
                        )
                    except Exception as error:
                        cleanup_errors.append(str(error))
                active_mode = self._modes.active_mode()
                if active_mode:
                    try:
                        self._modes.deactivate(active_mode)
                    except Exception as error:
                        cleanup_errors.append(f"mapper: {error}")
                try:
                    self._stack.stop()
                except Exception as error:
                    cleanup_errors.append(f"stack: {error}")
                self._reset_unstarted_trial(block, folder)
            else:
                try:
                    self._modes.deactivate(block["mode"])
                except Exception as error:
                    raise ExperimentError(f"Unable to stop mapper: {error}") from error
            block["status"] = "interrupted"
            block["updated_at_utc"] = self._utc_clock()
            block["error"] = "Stopped by the operator; resume this block to continue."
            if cleanup_errors:
                block["error"] += " Cleanup warning: " + "; ".join(cleanup_errors)
            progress["workflow"] = "interrupted"
            progress["current_block"] = None
            self._current_panel = "D"
            self._error = block["error"] if cleanup_errors else None
            self._persist(folder)
            self._write_block(folder, block)
            return self.snapshot()

    def set_control(self, block_id: str, active: bool) -> dict:
        """Activate or deactivate coupled discovery control and recording."""
        with self._lock:
            _, folder = self._require_prepared()
            block = self._running_block(block_id)
            if block["phase"] != "discovery":
                raise ExperimentError("Control toggling is available only during discovery.")
            if active and block["mode_explanation"]["status"] != "completed":
                raise ExperimentError("Validate the mode explanation before activating control.")
            if bool(block["control_active"]) == bool(active):
                return self.snapshot()
            if active:
                self._activate_discovery(block, folder)
            else:
                self._deactivate_discovery(block, folder, "operator_toggle")
            return self.snapshot()

    def prepare_training_trial(self, block_id: str) -> dict:
        """Select the next training trial and move to its calibrated start pose."""
        return self._prepare_trial(block_id, "training")

    def prepare_recording_trial(self, block_id: str) -> dict:
        """Select the next recording trial and move to its calibrated start pose."""
        return self._prepare_trial(block_id, "recording")

    def _prepare_trial(self, block_id: str, phase: str) -> dict:
        with self._lock:
            _, folder = self._require_prepared()
            block = self._trial_block(block_id, phase)
            self._require_trial_processes(block)
            if block["training_workflow"] != "awaiting_prepare":
                raise ExperimentError("Finish the current trial step first.")
            trial = next(
                (
                    item
                    for item in block["training_trials"]
                    if item["status"] == "pending"
                ),
                None,
            )
            if trial is None:
                raise ExperimentError("No unresolved trial remains.")
            block["current_trial_id"] = trial["id"]
            block["training_workflow"] = "awaiting_start_pose"
            trial["status"] = "positioning"
            trial["prepared_at_utc"] = self._utc_clock()
            trial["ready_at_utc"] = None
            block["training_live"] = self._empty_training_live()
            self._refresh_training_live(block)
            self._persist_training(folder, block, trial)
            # Preparing a trial also performs the manual repositioning step that
            # used to follow it.  start_go_to() owns mapper suspension/restoration
            # and records the motion in the block history.
            return self.start_go_to(
                block_id,
                f"target_out_{trial['target_start']}",
            )

    def training_participant_ready(self, block_id: str) -> dict:
        """Record participant readiness after a valid calibrated start pose."""
        return self._trial_participant_ready(block_id, "training")

    def recording_participant_ready(self, block_id: str) -> dict:
        """Record participant readiness for an official recording trial."""
        return self._trial_participant_ready(block_id, "recording")

    def _trial_participant_ready(self, block_id: str, phase: str) -> dict:
        with self._lock:
            _, folder = self._require_prepared()
            block = self._trial_block(block_id, phase)
            self._require_trial_processes(block)
            if block["training_workflow"] != "awaiting_start_pose":
                raise ExperimentError("Prepare a trial before confirming readiness.")
            self._refresh_training_live(block)
            if not block["training_live"].get("start_within_thresholds"):
                raise ExperimentError("Move the robot to the calibrated start pose first.")
            trial = self._current_training_trial(block)
            trial["status"] = "ready"
            trial["ready_at_utc"] = self._utc_clock()
            block["training_workflow"] = "ready"
            self._persist_training(folder, block, trial)
            return self.snapshot()

    def start_training_attempt(self, block_id: str) -> dict:
        """Start one owned MCAP attempt after rechecking the start pose."""
        return self._start_trial_attempt(block_id, "training")

    def start_recording_attempt(self, block_id: str) -> dict:
        """Start one owned official MCAP attempt after the pose check."""
        return self._start_trial_attempt(block_id, "recording")

    def _start_trial_attempt(self, block_id: str, phase: str) -> dict:
        with self._lock:
            _, folder = self._require_prepared()
            block = self._trial_block(block_id, phase)
            self._require_trial_processes(block)
            workflow = block["training_workflow"]
            if workflow not in ("awaiting_start_pose", "ready"):
                raise ExperimentError("Prepare a trial before recording.")
            self._refresh_training_live(block)
            if not block["training_live"].get("start_within_thresholds"):
                raise ExperimentError("The robot left the calibrated start pose.")
            trial = self._current_training_trial(block)
            if workflow == "awaiting_start_pose":
                trial["ready_at_utc"] = self._utc_clock()
            attempt_number = len(trial["attempts"]) + 1
            attempt_name = block["settings"]["attempt_pattern"].format(
                attempt=attempt_number
            )
            output = folder / block["folder"] / trial["folder"] / attempt_name
            attempt = {
                "number": attempt_number,
                "name": attempt_name,
                "status": "starting",
                "started_at_utc": self._utc_clock(),
                "stopped_at_utc": None,
                "stop_reason": None,
                "target_start": trial["target_start"],
                "target_end": trial["target_end"],
                "thresholds": effective_thresholds(block["success_thresholds"]),
                "start_error": dict(block["training_live"].get("start_error") or {}),
                "final_error": None,
                "minimum_error": {"linear_m": None, "angular_rad": None},
                "success_dwell_sec": 0.0,
                "success_dwell_started_monotonic": None,
                "incidents": [],
                "technical_outcome": None,
                "incident_review": {
                    "status": "not_required",
                    "invalidates_attempt": None,
                    "reviewed_at_utc": None,
                },
                "storage": self._profile.data["rosbag"]["storage"],
                "topics": list(block["settings"]["rosbag_topics"]),
                "message_counts": {},
                "missing_topics": list(block["settings"]["rosbag_topics"]),
                "metadata_error": None,
                "valid": False,
                "decision": None,
            }
            trial["attempts"].append(attempt)
            try:
                self._rosbag.start(
                    output,
                    list(block["settings"]["rosbag_topics"]),
                    self._profile.data["rosbag"]["storage"],
                )
            except Exception as error:
                attempt["status"] = "invalid"
                attempt["stopped_at_utc"] = self._utc_clock()
                attempt["stop_reason"] = "recorder_start_failure"
                attempt["metadata_error"] = str(error)
                trial["status"] = "decision_required"
                block["training_workflow"] = "decision_required"
                block["error"] = f"Unable to start trial recorder: {error}"
                self._persist_training(folder, block, trial, attempt)
                raise ExperimentError(block["error"]) from error
            attempt["status"] = "recording"
            trial["status"] = "recording"
            block["training_workflow"] = "recording"
            block["error"] = None
            block["training_live"] = self._empty_training_live()
            self._persist_training(folder, block, trial, attempt)
            return self.snapshot()

    def stop_training_attempt(self, block_id: str) -> dict:
        """Stop and invalidate the active attempt at the operator's request."""
        return self._stop_trial_attempt(block_id, "training")

    def stop_recording_attempt(self, block_id: str) -> dict:
        """Stop and invalidate the active official recording attempt."""
        return self._stop_trial_attempt(block_id, "recording")

    def _stop_trial_attempt(self, block_id: str, phase: str) -> dict:
        with self._lock:
            _, folder = self._require_prepared()
            block = self._trial_block(block_id, phase)
            if block["training_workflow"] != "recording":
                raise ExperimentError("No trial attempt is currently recording.")
            self._finish_training_attempt(
                block, folder, "operator_stop", successful=False
            )
            return self.snapshot()

    def add_training_incident(self, block_id: str) -> dict:
        """Append a timestamped incident occurrence to the active attempt."""
        return self._add_trial_incident(block_id, "training")

    def add_recording_incident(self, block_id: str) -> dict:
        """Append an occurrence to the active official recording attempt."""
        return self._add_trial_incident(block_id, "recording")

    def _add_trial_incident(self, block_id: str, phase: str) -> dict:
        with self._lock:
            _, folder = self._require_prepared()
            block = self._trial_block(block_id, phase)
            if block["training_workflow"] != "recording":
                raise ExperimentError("Incidents can be recorded only during acquisition.")
            trial = self._current_training_trial(block)
            attempt = self._current_training_attempt(trial)
            occurrence_id = max(
                (incident.get("id", 0) for incident in attempt["incidents"]),
                default=0,
            ) + 1
            attempt["incidents"].append(
                {
                    "id": occurrence_id,
                    "at_utc": self._utc_clock(),
                    "text": None,
                    "described_at_utc": None,
                }
            )
            self._persist_training(folder, block, trial, attempt)
            return self.snapshot()

    def review_training_incidents(
        self, block_id: str, descriptions: list[dict], invalidates_attempt: bool
    ) -> dict:
        """Describe training incidents and resolve their global validity impact."""
        return self._review_trial_incidents(
            block_id, descriptions, invalidates_attempt, "training"
        )

    def review_recording_incidents(
        self, block_id: str, descriptions: list[dict], invalidates_attempt: bool
    ) -> dict:
        """Describe recording incidents and resolve their validity impact."""
        return self._review_trial_incidents(
            block_id, descriptions, invalidates_attempt, "recording"
        )

    def _review_trial_incidents(
        self,
        block_id: str,
        descriptions: list[dict],
        invalidates_attempt: bool,
        phase: str,
    ) -> dict:
        with self._lock:
            _, folder = self._require_prepared()
            block = self._trial_block(block_id, phase)
            if block["training_workflow"] != "incident_review_required":
                raise ExperimentError("No incident review is currently required.")
            trial = self._current_training_trial(block)
            attempt = self._current_training_attempt(trial)
            incidents = attempt.get("incidents", [])
            expected_ids = {incident["id"] for incident in incidents}
            observed_ids = [item.get("id") for item in descriptions]
            if len(observed_ids) != len(set(observed_ids)):
                raise ExperimentError("Incident description identifiers must be unique.")
            if set(observed_ids) != expected_ids:
                raise ExperimentError("Every incident occurrence must be described exactly once.")
            values = {}
            for item in descriptions:
                value = str(item.get("text", "")).strip()
                if not value:
                    raise ExperimentError("Incident descriptions must not be empty.")
                if len(value) > 2000:
                    raise ExperimentError(
                        "Incident descriptions must not exceed 2000 characters."
                    )
                values[item["id"]] = value
            described_at = self._utc_clock()
            for incident in incidents:
                incident["text"] = values[incident["id"]]
                incident["described_at_utc"] = described_at
            attempt["incident_review"] = {
                "status": "complete",
                "invalidates_attempt": bool(invalidates_attempt),
                "reviewed_at_utc": described_at,
            }
            self._apply_trial_attempt_outcome(
                block,
                trial,
                attempt,
                incident_invalidates=bool(invalidates_attempt),
            )
            self._persist_training(folder, block, trial, attempt)
            return self.snapshot()

    def resolve_training_attempt(self, block_id: str, decision: str) -> dict:
        """Retry an invalid trial or accept it as a protocol deviation."""
        return self._resolve_trial_attempt(block_id, decision, "training")

    def resolve_recording_attempt(self, block_id: str, decision: str) -> dict:
        """Resolve an invalid official recording attempt."""
        return self._resolve_trial_attempt(block_id, decision, "recording")

    def _resolve_trial_attempt(
        self, block_id: str, decision: str, phase: str
    ) -> dict:
        with self._lock:
            _, folder = self._require_prepared()
            block = self._trial_block(block_id, phase)
            if block["training_workflow"] != "decision_required":
                raise ExperimentError("No invalid trial attempt requires a decision.")
            if decision not in ("retry", "advance_with_deviation"):
                raise ExperimentError("Unknown training resolution decision.")
            trial = self._current_training_trial(block)
            attempt = self._current_training_attempt(trial)
            attempt["decision"] = decision
            trial["decision"] = decision
            if decision == "retry":
                trial["status"] = "pending"
            else:
                trial["status"] = "completed_with_deviation"
                trial["completed_at_utc"] = self._utc_clock()
                if trial["id"] not in block["training_deviations"]:
                    block["training_deviations"].append(trial["id"])
            block["current_trial_id"] = None
            block["training_workflow"] = (
                "ready_to_end" if self._trial_can_end(block) else "awaiting_prepare"
            )
            block["training_live"] = self._empty_training_live()
            block["error"] = None
            self._persist_training(folder, block, trial, attempt)
            return self.snapshot()

    def update_ee_pose(self, message) -> None:
        """Update live training errors and schedule success finalisation."""
        worker = None
        with self._lock:
            if self._go_to is not None:
                self._go_to.update_pose(message)
            try:
                self._latest_pose = self._pose_document(message)
            except (AttributeError, TypeError, ValueError):
                self._latest_pose = None
            self._latest_pose_monotonic = self._monotonic_clock()
            if (
                self._progress is None
                or self._progress.get("workflow") != "block_running"
                or not self._progress.get("current_block")
            ):
                return
            block = self._block(self._progress["current_block"])
            if block["phase"] not in TRIAL_PHASES:
                return
            self._refresh_training_live(block)
            if block["training_workflow"] != "recording":
                return
            trial = self._current_training_trial(block)
            attempt = self._current_training_attempt(trial)
            target_error = block["training_live"].get("target_error")
            if not target_error:
                attempt["success_dwell_started_monotonic"] = None
                attempt["success_dwell_sec"] = 0.0
                return
            linear = target_error["linear_m"]
            angular = target_error["angular_rad"]
            attempt["final_error"] = dict(target_error)
            minimum = attempt["minimum_error"]
            minimum["linear_m"] = (
                linear if minimum["linear_m"] is None else min(minimum["linear_m"], linear)
            )
            minimum["angular_rad"] = (
                angular
                if minimum["angular_rad"] is None
                else min(minimum["angular_rad"], angular)
            )
            thresholds = effective_thresholds(block["success_thresholds"])
            within = (
                linear <= thresholds["linear_m"]
                and angular <= thresholds["angular_rad"]
            )
            now = self._monotonic_clock()
            if not within:
                attempt["success_dwell_started_monotonic"] = None
                attempt["success_dwell_sec"] = 0.0
                return
            if attempt["success_dwell_started_monotonic"] is None:
                attempt["success_dwell_started_monotonic"] = now
            attempt["success_dwell_sec"] = max(
                0.0, now - attempt["success_dwell_started_monotonic"]
            )
            if attempt["success_dwell_sec"] >= thresholds["success_dwell_sec"]:
                block["training_workflow"] = "stopping_success"
                attempt["status"] = "stopping_success"
                worker = self._thread_factory(
                    target=self._finish_training_success_worker,
                    args=(block["id"], trial["id"], attempt["number"]),
                    daemon=True,
                )
        if worker is not None:
            worker.start()

    def restart_stack(self, block_id: str) -> dict:
        """Restart the owned stack and restore the previous control state."""
        with self._lock:
            progress, folder = self._require_prepared()
            block = self._running_block(block_id)
            if self._go_to is not None:
                self._go_to.cancel_if_active("restart_stack")
            was_active = bool(block["control_active"])
            restore_control = was_active or block["phase"] in TRIAL_PHASES
            block["restart"] = {"status": "restarting", "error": None}
            block["updated_at_utc"] = self._utc_clock()
            self._persist(folder)
            self._write_block(folder, block)
            try:
                if block["phase"] == "discovery":
                    if was_active:
                        self._deactivate_discovery(block, folder, "restart_stack")
                elif block["phase"] in TRIAL_PHASES:
                    if block.get("training_workflow") in (
                        "recording",
                        "stopping_success",
                    ):
                        self._finish_training_attempt(
                            block, folder, "restart_stack", successful=False
                        )
                    active_mode = self._modes.active_mode()
                    if active_mode:
                        self._modes.deactivate(active_mode)
                    block["control_active"] = False
                else:
                    active_mode = self._modes.active_mode()
                    if active_mode:
                        self._modes.deactivate(active_mode)
                    block["control_active"] = False
                self._stack.stop()
                self._stack.start()
                if restore_control:
                    if block["phase"] == "discovery":
                        self._activate_discovery(block, folder)
                    else:
                        self._modes.activate(block["mode"])
                        block["control_active"] = True
            except Exception as error:
                active_mode = self._modes.active_mode()
                if active_mode:
                    try:
                        self._modes.deactivate(active_mode)
                    except Exception:
                        pass
                try:
                    self._rosbag.shutdown()
                except Exception:
                    pass
                block["control_active"] = False
                block["current_segment"] = None
                block["restart"] = {"status": "error", "error": str(error)}
                block["error"] = f"Unable to restart stack: {error}"
                progress["workflow"] = "block_running"
                self._error = block["error"]
                self._persist(folder)
                self._write_block(folder, block)
                raise ExperimentError(block["error"]) from error
            block["restart"] = {"status": "complete", "error": None}
            block["error"] = None
            self._error = None
            self._persist(folder)
            self._write_block(folder, block)
            return self.snapshot()

    def _finish_training_success_worker(
        self, block_id: str, trial_id: int, attempt_number: int
    ) -> None:
        with self._lock:
            if self._progress is None or self._progress.get("current_block") != block_id:
                return
            block = self._block(block_id)
            if block.get("current_trial_id") != trial_id:
                return
            trial = self._current_training_trial(block)
            attempt = self._current_training_attempt(trial)
            if (
                attempt["number"] != attempt_number
                or block.get("training_workflow") != "stopping_success"
            ):
                return
            folder = Path(self._participant["folder"])
            try:
                self._finish_training_attempt(
                    block, folder, "success_threshold", successful=True
                )
            except Exception as error:
                block["error"] = f"Unable to finalise successful trial attempt: {error}"
                self._error = block["error"]
                self._persist(folder)
                self._write_block(folder, block)

    def _finish_training_attempt(
        self, block: dict, folder: Path, reason: str, successful: bool
    ) -> None:
        trial = self._current_training_trial(block)
        attempt = self._current_training_attempt(trial)
        try:
            result = self._rosbag.stop()
        except Exception as error:
            result = {
                "storage": self._profile.data["rosbag"]["storage"],
                "message_counts": {
                    topic: 0 for topic in block["settings"]["rosbag_topics"]
                },
                "missing_topics": list(block["settings"]["rosbag_topics"]),
                "metadata_error": str(error),
                "valid": False,
            }
        data_valid = bool(result.get("valid"))
        technical_completed = bool(successful and data_valid)
        attempt["stopped_at_utc"] = self._utc_clock()
        technical_stop_reason = (
            reason if not successful or data_valid else "invalid_data"
        )
        attempt["stop_reason"] = technical_stop_reason
        attempt["storage"] = result.get("storage", attempt["storage"])
        attempt["message_counts"] = dict(result.get("message_counts", {}))
        attempt["missing_topics"] = list(
            result.get("missing_topics", attempt["topics"])
        )
        attempt["metadata_error"] = result.get("metadata_error")
        attempt["valid"] = False
        attempt["success_dwell_started_monotonic"] = None
        attempt["technical_outcome"] = {
            "successful_motion": bool(successful),
            "data_valid": data_valid,
            "completed": technical_completed,
            "stop_reason": technical_stop_reason,
            "evaluated_at_utc": self._utc_clock(),
        }
        pending_incidents = [
            incident
            for incident in attempt.get("incidents", [])
            if not str(incident.get("text") or "").strip()
        ]
        if pending_incidents:
            attempt["status"] = "incident_review_required"
            attempt["incident_review"] = {
                "status": "required",
                "invalidates_attempt": None,
                "reviewed_at_utc": None,
            }
            trial["status"] = "incident_review_required"
            block["training_workflow"] = "incident_review_required"
            block["error"] = (
                "Describe every incident occurrence and decide whether the "
                "incidents invalidate this attempt."
            )
        else:
            if attempt.get("incidents"):
                attempt["incident_review"] = {
                    "status": "complete",
                    "invalidates_attempt": False,
                    "reviewed_at_utc": attempt.get("stopped_at_utc"),
                }
            self._apply_trial_attempt_outcome(
                block, trial, attempt, incident_invalidates=False
            )
        block["training_live"] = self._empty_training_live()
        self._persist_training(folder, block, trial, attempt)

    def _apply_trial_attempt_outcome(
        self,
        block: dict,
        trial: dict,
        attempt: dict,
        *,
        incident_invalidates: bool,
    ) -> None:
        technical = attempt.get("technical_outcome") or {}
        completed = bool(technical.get("completed")) and not incident_invalidates
        attempt["status"] = "succeeded" if completed else "invalid"
        attempt["valid"] = completed
        if incident_invalidates:
            attempt["stop_reason"] = "incident_invalidated"
        else:
            attempt["stop_reason"] = technical.get(
                "stop_reason", attempt.get("stop_reason")
            )
        if completed:
            trial["status"] = "completed"
            trial["completed_at_utc"] = self._utc_clock()
            trial["decision"] = "automatic_success"
            block["current_trial_id"] = None
            block["training_workflow"] = (
                "ready_to_end" if self._trial_can_end(block) else "awaiting_prepare"
            )
            block["error"] = None
        else:
            trial["status"] = "decision_required"
            block["training_workflow"] = "decision_required"
            block["error"] = (
                "The attempt is invalid. Retry it or continue with a deviation."
            )

    def _refresh_training_live(self, block: dict) -> None:
        live = self._empty_training_live()
        if self._latest_pose is None or self._latest_pose_monotonic is None:
            live["error"] = "No end-effector pose has been received."
            block["training_live"] = live
            return
        age = max(0.0, self._monotonic_clock() - self._latest_pose_monotonic)
        live["pose_age_sec"] = age
        thresholds = effective_thresholds(block["success_thresholds"])
        live["pose_fresh"] = age <= thresholds["pose_freshness_sec"]
        if not live["pose_fresh"]:
            live["error"] = "The end-effector pose is stale."
            block["training_live"] = live
            return
        if block.get("current_trial_id") is None:
            block["training_live"] = live
            return
        trial = self._current_training_trial(block)
        try:
            start_target = self._calibration_pose(f"target_out_{trial['target_start']}")
            start_linear, start_angular = pose_error(self._latest_pose, start_target)
            live["start_error"] = self._error_document(start_linear, start_angular)
            live["start_within_thresholds"] = (
                start_linear <= thresholds["start_linear_m"]
                and start_angular <= thresholds["start_angular_rad"]
            )
            target = self._calibration_pose(f"target_{trial['target_end']}")
            target_linear, target_angular = pose_error(self._latest_pose, target)
            live["target_error"] = self._error_document(target_linear, target_angular)
            live["target_within_thresholds"] = (
                target_linear <= thresholds["linear_m"]
                and target_angular <= thresholds["angular_rad"]
            )
        except TrainingError as error:
            live["error"] = str(error)
        block["training_live"] = live

    @staticmethod
    def _empty_training_live() -> dict:
        return {
            "pose_fresh": False,
            "pose_age_sec": None,
            "start_error": None,
            "start_within_thresholds": False,
            "target_error": None,
            "target_within_thresholds": False,
            "error": None,
        }

    @staticmethod
    def _error_document(linear: float, angular: float) -> dict:
        return {
            "linear_m": linear,
            "linear_mm": linear * 1000.0,
            "angular_rad": angular,
            "angular_deg": angular * 180.0 / 3.141592653589793,
        }

    @staticmethod
    def _pose_document(message) -> dict:
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

    def _calibration_pose(self, name: str) -> dict:
        if self._calibration is None:
            raise TrainingError("Participant calibration is not loaded.")
        try:
            return self._calibration["poses"][name]
        except (KeyError, TypeError) as error:
            raise TrainingError(f"Calibration pose {name} is missing.") from error

    @staticmethod
    def _load_calibration(folder: Path) -> dict:
        path = folder / "calibration" / "latest_calib.json"
        try:
            calibration = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ExperimentError("Participant calibration is unreadable.") from error
        if not isinstance(calibration, dict) or calibration.get("schema_version") != 1:
            raise ExperimentError("Participant calibration must use schema_version 1.")
        poses = calibration.get("poses")
        required = {
            *(f"target_{index}" for index in (1, 2, 3)),
            *(f"target_out_{index}" for index in (1, 2, 3)),
        }
        if not isinstance(poses, dict) or not required.issubset(poses):
            raise ExperimentError("Participant calibration is missing training poses.")
        try:
            for name in required:
                pose_error(poses[name], poses[name])
        except TrainingError as error:
            raise ExperimentError(f"Invalid participant calibration: {error}") from error
        return calibration

    def _trial_block(self, block_id: str, expected_phase: str) -> dict:
        block = self._running_block(block_id)
        if block["phase"] != expected_phase or expected_phase not in TRIAL_PHASES:
            panel = PHASE_PANELS.get(expected_phase, "F/G")
            raise ExperimentError(
                f"{expected_phase.title()} actions are available only on Panel {panel}."
            )
        return block

    def _require_trial_processes(self, block: dict) -> None:
        if self._go_to is not None and self._go_to.active():
            raise ExperimentError("Wait for the active Go-to to finish or stop it first.")
        stack = self._stack.snapshot()
        mapper = self._modes.snapshot()
        if (
            stack.get("status") != "active"
            or mapper.get("status") != "active"
            or mapper.get("active_mode") != block["mode"]
        ):
            raise ExperimentError("Restart the stack before continuing this trial block.")

    @staticmethod
    def _trial_can_end(block: dict) -> bool:
        trials = block.get("training_trials", [])
        return bool(trials) and all(
            trial.get("status") in ("completed", "completed_with_deviation")
            for trial in trials
        )

    @staticmethod
    def _current_training_trial(block: dict) -> dict:
        trial_id = block.get("current_trial_id")
        for trial in block.get("training_trials", []):
            if trial.get("id") == trial_id:
                return trial
        raise ExperimentError("No trial is currently selected.")

    @staticmethod
    def _current_training_attempt(trial: dict) -> dict:
        if not trial.get("attempts"):
            raise ExperimentError("No attempt exists for the current trial.")
        return trial["attempts"][-1]

    def _persist_training(
        self,
        folder: Path,
        block: dict,
        trial: dict,
        attempt: Optional[dict] = None,
    ) -> None:
        block["updated_at_utc"] = self._utc_clock()
        self._persist(folder)
        self._write_block(folder, block)
        self._write_trial(folder, block, trial)
        if attempt is not None:
            self._write_attempt(folder, block, trial, attempt)

    @staticmethod
    def _write_trial(folder: Path, block: dict, trial: dict) -> None:
        path = folder / block["folder"] / trial["folder"] / "trial.json"
        _atomic_json(path, trial)

    @staticmethod
    def _write_attempt(folder: Path, block: dict, trial: dict, attempt: dict) -> None:
        path = (
            folder
            / block["folder"]
            / trial["folder"]
            / attempt["name"]
            / "attempt.json"
        )
        _atomic_json(path, attempt)

    def _activate_discovery(self, block: dict, folder: Path) -> None:
        if self._stack.snapshot().get("status") != "active":
            raise ExperimentError("Restart the experiment stack before activating control.")
        segment = self._new_segment(block, folder)
        topics = list(block["settings"]["rosbag_topics"])
        storage = self._profile.data["rosbag"]["storage"]
        try:
            self._rosbag.start(folder / block["folder"] / segment["name"], topics, storage)
            self._modes.activate(block["mode"])
        except Exception as error:
            active_mode = self._modes.active_mode()
            if active_mode:
                try:
                    self._modes.deactivate(active_mode)
                except Exception:
                    pass
            try:
                result = self._rosbag.shutdown()
            except Exception as stop_error:
                result = {
                    "valid": False,
                    "message_counts": {topic: 0 for topic in topics},
                    "missing_topics": topics,
                    "metadata_error": str(stop_error),
                }
            self._finish_segment(segment, result, "activation_failure", "failed")
            block["control_active"] = False
            block["current_segment"] = None
            block["error"] = f"Unable to activate discovery: {error}"
            self._persist(folder)
            self._write_block(folder, block)
            raise ExperimentError(block["error"]) from error
        segment["status"] = "recording"
        segment["started_at_utc"] = self._utc_clock()
        block["control_active"] = True
        block["error"] = None
        self._error = None
        self._persist(folder)
        self._write_block(folder, block)

    def _deactivate_discovery(self, block: dict, folder: Path, reason: str) -> None:
        segment = self._segment(block, block.get("current_segment"))
        errors = []
        try:
            result = self._rosbag.stop()
        except Exception as error:
            errors.append(f"recorder: {error}")
            result = {
                "valid": False,
                "message_counts": {
                    topic: 0 for topic in block["settings"]["rosbag_topics"]
                },
                "missing_topics": list(block["settings"]["rosbag_topics"]),
                "metadata_error": str(error),
            }
        active_mode = self._modes.active_mode()
        if active_mode:
            try:
                self._modes.deactivate(active_mode)
            except Exception as error:
                errors.append(f"mapper: {error}")
        self._finish_segment(
            segment,
            result,
            reason,
            "complete" if result.get("valid") else "incomplete",
        )
        block["control_active"] = False
        block["current_segment"] = None
        block["error"] = "; ".join(errors) if errors else None
        self._persist(folder)
        self._write_block(folder, block)
        if errors:
            raise ExperimentError("Unable to deactivate discovery cleanly: " + "; ".join(errors))

    def _new_segment(self, block: dict, folder: Path) -> dict:
        pattern = self._profile.data["rosbag"]["segment_pattern"]
        index = 1
        block_folder = folder / block["folder"]
        existing_names = {item.get("name") for item in block["segments"]}
        while True:
            name = pattern.format(index=index)
            if name not in existing_names and not (block_folder / name).exists():
                break
            index += 1
        segment = {
            "index": index,
            "name": name,
            "status": "starting",
            "storage": self._profile.data["rosbag"]["storage"],
            "topics": list(block["settings"]["rosbag_topics"]),
            "started_at_utc": None,
            "stopped_at_utc": None,
            "stop_reason": None,
            "message_counts": {},
            "missing_topics": list(block["settings"]["rosbag_topics"]),
            "metadata_error": None,
            "valid": False,
        }
        block["segments"].append(segment)
        block["current_segment"] = name
        return segment

    def _finish_segment(self, segment: dict, result: Optional[dict], reason: str, status: str) -> None:
        result = result or {}
        segment["status"] = status
        segment["stopped_at_utc"] = self._utc_clock()
        segment["stop_reason"] = reason
        segment["storage"] = result.get("storage", segment["storage"])
        segment["message_counts"] = dict(result.get("message_counts", {}))
        segment["missing_topics"] = list(result.get("missing_topics", segment["topics"]))
        segment["metadata_error"] = result.get("metadata_error")
        segment["valid"] = bool(result.get("valid"))

    @staticmethod
    def _can_end(block: dict) -> bool:
        return any(segment.get("valid") for segment in block.get("segments", []))

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
                    "recorder": self._rosbag.snapshot(),
                    "control_active": False,
                    "current_segment": None,
                    "segments": [],
                    "can_end": False,
                    "restart": {"status": "idle", "error": None},
                    "training": None,
                    "recording": None,
                    "go_to": {"available": False, "motion": None, "history": []},
                }
            stack_state = self._stack.snapshot()
            mapper_state = self._modes.snapshot()
            recorder_state = self._rosbag.snapshot()
            if (
                self._progress.get("workflow") == "block_running"
                and self._progress.get("current_block")
            ):
                block = self._block(self._progress["current_block"])
                if block["phase"] == "discovery":
                    process_failed = stack_state.get("status") != "active" or (
                        block["control_active"]
                        and (
                            mapper_state.get("status") != "active"
                            or mapper_state.get("active_mode") != block["mode"]
                            or recorder_state.get("status") != "active"
                        )
                    )
                    if process_failed:
                        folder = Path(self._participant["folder"])
                        if block["control_active"]:
                            try:
                                self._deactivate_discovery(
                                    block, folder, "process_failure"
                                )
                            except Exception:
                                block["control_active"] = False
                                block["current_segment"] = None
                        block["error"] = "A required owned process stopped unexpectedly."
                        self._error = block["error"]
                        self._persist(folder)
                        self._write_block(folder, block)
                        mapper_state = self._modes.snapshot()
                        recorder_state = self._rosbag.snapshot()
                    process_failed = False
                elif block["phase"] in TRIAL_PHASES:
                    go_to_active = bool(self._go_to and self._go_to.active())
                    if not go_to_active:
                        mapper_state = self._modes.snapshot()
                    stack_or_mapper_failed = (
                        stack_state.get("status") != "active"
                        or (
                            not go_to_active
                            and (
                                mapper_state.get("status") != "active"
                                or mapper_state.get("active_mode") != block["mode"]
                            )
                        )
                    )
                    recorder_failed = (
                        block.get("training_workflow") == "recording"
                        and recorder_state.get("status") != "active"
                    )
                    if stack_or_mapper_failed or recorder_failed:
                        folder = Path(self._participant["folder"])
                        if block.get("training_workflow") in (
                            "recording",
                            "stopping_success",
                        ):
                            try:
                                self._finish_training_attempt(
                                    block,
                                    folder,
                                    "process_failure",
                                    successful=False,
                                )
                            except Exception:
                                pass
                        if stack_or_mapper_failed:
                            block["control_active"] = False
                        block["error"] = (
                            "A required owned process stopped unexpectedly. "
                            + (
                                "Restart the stack."
                                if stack_or_mapper_failed
                                else "Resolve the invalid attempt."
                            )
                        )
                        self._error = block["error"]
                        self._persist(folder)
                        self._write_block(folder, block)
                        mapper_state = self._modes.snapshot()
                        recorder_state = self._rosbag.snapshot()
                    process_failed = False
                else:
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
            active_block = None
            if self._progress.get("current_block"):
                active_block = self._block(self._progress["current_block"])
                if active_block["phase"] in TRIAL_PHASES:
                    self._refresh_training_live(active_block)
            can_end = False
            if active_block:
                if active_block["phase"] == "discovery":
                    can_end = (
                        active_block["mode_explanation"]["status"] == "completed"
                        and self._can_end(active_block)
                    )
                elif active_block["phase"] in TRIAL_PHASES:
                    can_end = self._trial_can_end(active_block)
            training_state = None
            if active_block and active_block["phase"] in TRIAL_PHASES:
                trials = active_block.get("training_trials", [])
                current_trial = next(
                    (
                        trial
                        for trial in trials
                        if trial.get("id") == active_block.get("current_trial_id")
                    ),
                    None,
                )
                current_attempt = (
                    current_trial["attempts"][-1]
                    if current_trial and current_trial.get("attempts")
                    else None
                )
                resolved = sum(
                    trial.get("status")
                    in ("completed", "completed_with_deviation")
                    for trial in trials
                )
                training_state = {
                    "workflow": active_block.get("training_workflow"),
                    "trials": json.loads(json.dumps(trials)),
                    "current_trial_id": active_block.get("current_trial_id"),
                    "current_trial": json.loads(json.dumps(current_trial)),
                    "current_attempt": json.loads(json.dumps(current_attempt)),
                    "incident_review_required": bool(
                        active_block.get("training_workflow")
                        == "incident_review_required"
                    ),
                    "incident_descriptions_missing": sum(
                        not str(incident.get("text") or "").strip()
                        for incident in (
                            current_attempt.get("incidents", [])
                            if current_attempt
                            else []
                        )
                    ),
                    "technical_outcome": json.loads(
                        json.dumps(
                            current_attempt.get("technical_outcome")
                            if current_attempt
                            else None
                        )
                    ),
                    "live": json.loads(
                        json.dumps(active_block.get("training_live", {}))
                    ),
                    "stability": {
                        "duration_sec": (
                            current_attempt.get("success_dwell_sec", 0.0)
                            if current_attempt
                            else 0.0
                        ),
                        "required_sec": active_block["success_thresholds"][
                            "success_dwell_sec"
                        ],
                    },
                    "progress": {"resolved": resolved, "total": len(trials)},
                    "deviations": list(
                        active_block.get("training_deviations", [])
                    ),
                    "thresholds": effective_thresholds(
                        active_block["success_thresholds"]
                    ),
                    "go_to_available": bool(
                        self._go_to
                        and active_block.get("training_workflow")
                        not in (
                            "recording",
                            "stopping_success",
                            "incident_review_required",
                        )
                    ),
                }
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
                "presentation_video_available": self.presentation_video_available(),
                "mode_explanation_video_available": (
                    self.mode_explanation_video_available(active_block["mode"])
                    if active_block and active_block["phase"] == "discovery"
                    else False
                ),
                "stack": stack_state,
                "mapper": mapper_state,
                "recorder": recorder_state,
                "control_active": bool(active_block and active_block["control_active"]),
                "current_segment": (
                    active_block.get("current_segment") if active_block else None
                ),
                "segments": (
                    json.loads(json.dumps(active_block.get("segments", [])))
                    if active_block
                    else []
                ),
                "can_end": bool(can_end),
                "restart": (
                    dict(active_block.get("restart", {}))
                    if active_block
                    else {"status": "idle", "error": None}
                ),
                "go_to": {
                    "available": bool(
                        active_block
                        and active_block["phase"] in TRIAL_PHASES
                        and active_block.get("training_workflow")
                        not in (
                            "recording",
                            "stopping_success",
                            "incident_review_required",
                        )
                        and stack_state.get("status") == "active"
                    ),
                    "motion": self._go_to.snapshot() if self._go_to else None,
                    "history": (
                        json.loads(json.dumps(active_block.get("go_to_history", [])))
                        if active_block
                        else []
                    ),
                    "targets": [
                        "target_out_1",
                        "target_out_2",
                        "target_out_3",
                        "starting_point",
                    ],
                },
                "training": (
                    training_state
                    if active_block and active_block["phase"] == "training"
                    else None
                ),
                "recording": (
                    training_state
                    if active_block and active_block["phase"] == "recording"
                    else None
                ),
            }

    def shutdown(self) -> None:
        with self._lock:
            if self._go_to is not None:
                self._go_to.cancel_if_active("backend_shutdown")
            if self._progress is not None and self._progress.get("current_block"):
                folder = Path(self._participant["folder"])
                block = self._block(self._progress["current_block"])
                if block["phase"] == "discovery" and block["control_active"]:
                    try:
                        self._deactivate_discovery(
                            block, folder, "interface_shutdown"
                        )
                    except Exception:
                        pass
                if block["phase"] in TRIAL_PHASES and block.get(
                    "training_workflow"
                ) in ("recording", "stopping_success"):
                    try:
                        self._finish_training_attempt(
                            block, folder, "interface_shutdown", successful=False
                        )
                    except Exception:
                        pass
                block["status"] = "interrupted"
                block["updated_at_utc"] = self._utc_clock()
                block["error"] = "Interface stopped while the block was active."
                self._progress["workflow"] = "interrupted"
                self._progress["current_block"] = None
                self._persist(folder)
                self._write_block(folder, block)
            self._rosbag.shutdown()
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

    @staticmethod
    def _segment(block: dict, name: Optional[str]) -> dict:
        if name is None:
            raise ExperimentError("No discovery segment is currently active.")
        for segment in block.get("segments", []):
            if segment.get("name") == name:
                return segment
        raise ExperimentError(f"Unknown discovery segment: {name}")

    @staticmethod
    def _ensure_lot5_fields(block: dict) -> bool:
        changed = False
        defaults = {
            "control_active": False,
            "current_segment": None,
            "segments": [],
            "restart": {"status": "idle", "error": None},
            "go_to_history": [],
        }
        for key, value in defaults.items():
            if key not in block:
                block[key] = value
                changed = True
        return changed

    def _ensure_trial_fields(self, block: dict) -> bool:
        phase = block.get("phase")
        if phase not in TRIAL_PHASES:
            return False
        changed = False
        settings = block.setdefault("settings", {})
        for key, value in self._profile.data["phases"][phase].items():
            if key not in settings:
                settings[key] = json.loads(json.dumps(value))
                changed = True
        thresholds = block.get("success_thresholds")
        try:
            validate_thresholds(thresholds)
        except TrainingError:
            block["success_thresholds"] = dict(
                self._profile.data["success_thresholds"]
            )
            changed = True
        defaults = {
            "training_workflow": "awaiting_prepare",
            "training_trials": build_trials(block["mode"], settings),
            "current_trial_id": None,
            "training_deviations": [],
            "training_live": self._empty_training_live(),
        }
        for key, value in defaults.items():
            is_empty_placeholder = (
                (key == "training_workflow" and block.get(key) is None)
                or (key == "training_trials" and not block.get(key))
            )
            if key not in block or is_empty_placeholder:
                block[key] = value
                changed = True
        for trial in block.get("training_trials", []):
            for attempt in trial.get("attempts", []):
                changed = self._ensure_attempt_incident_fields(attempt) or changed
        return changed

    @staticmethod
    def _ensure_attempt_incident_fields(attempt: dict) -> bool:
        """Normalise legacy incidents without changing their descriptions."""
        changed = False
        seen_ids = set()
        for index, incident in enumerate(attempt.setdefault("incidents", []), start=1):
            incident_id = incident.get("id")
            if not isinstance(incident_id, int) or incident_id < 1 or incident_id in seen_ids:
                incident_id = index
                while incident_id in seen_ids:
                    incident_id += 1
                incident["id"] = incident_id
                changed = True
            seen_ids.add(incident_id)
            if "text" not in incident:
                incident["text"] = None
                changed = True
            if "described_at_utc" not in incident:
                incident["described_at_utc"] = (
                    incident.get("at_utc")
                    if str(incident.get("text") or "").strip()
                    else None
                )
                changed = True
        if "technical_outcome" not in attempt:
            completed = bool(
                attempt.get("status") == "succeeded" and attempt.get("valid")
            )
            attempt["technical_outcome"] = (
                {
                    "successful_motion": completed,
                    "data_valid": bool(attempt.get("valid")),
                    "completed": completed,
                    "stop_reason": attempt.get("stop_reason"),
                    "evaluated_at_utc": attempt.get("stopped_at_utc"),
                }
                if attempt.get("stopped_at_utc")
                else None
            )
            changed = True
        if "incident_review" not in attempt:
            incidents = attempt.get("incidents", [])
            described = bool(incidents) and all(
                str(incident.get("text") or "").strip() for incident in incidents
            )
            required = bool(incidents) and not described and attempt.get("stopped_at_utc")
            attempt["incident_review"] = {
                "status": "required" if required else "complete" if described else "not_required",
                "invalidates_attempt": False if described else None,
                "reviewed_at_utc": (
                    attempt.get("stopped_at_utc") if described else None
                ),
            }
            changed = True
        return changed

    def _recover_training_attempt(
        self, block: dict, folder: Path, reason: str
    ) -> None:
        trial_id = block.get("current_trial_id")
        if trial_id is None:
            block["training_workflow"] = "awaiting_prepare"
            return
        trial = self._current_training_trial(block)
        if trial.get("attempts"):
            attempt = trial["attempts"][-1]
            self._ensure_attempt_incident_fields(attempt)
            if (
                block.get("training_workflow") == "incident_review_required"
                or attempt.get("status") == "incident_review_required"
            ):
                trial["status"] = "incident_review_required"
                block["training_workflow"] = "incident_review_required"
                self._write_attempt(folder, block, trial, attempt)
                self._write_trial(folder, block, trial)
                return
            if attempt.get("status") in ("starting", "recording", "stopping_success"):
                attempt["stopped_at_utc"] = self._utc_clock()
                attempt["stop_reason"] = reason
                attempt["valid"] = False
                attempt["technical_outcome"] = {
                    "successful_motion": False,
                    "data_valid": False,
                    "completed": False,
                    "stop_reason": reason,
                    "evaluated_at_utc": attempt["stopped_at_utc"],
                }
                pending_incidents = [
                    incident
                    for incident in attempt.get("incidents", [])
                    if not str(incident.get("text") or "").strip()
                ]
                if pending_incidents:
                    attempt["status"] = "incident_review_required"
                    attempt["incident_review"] = {
                        "status": "required",
                        "invalidates_attempt": None,
                        "reviewed_at_utc": None,
                    }
                    trial["status"] = "incident_review_required"
                    block["training_workflow"] = "incident_review_required"
                else:
                    attempt["status"] = "invalid"
                    trial["status"] = "decision_required"
                    block["training_workflow"] = "decision_required"
                self._write_attempt(folder, block, trial, attempt)
                self._write_trial(folder, block, trial)
                return
        trial["status"] = "pending"
        block["current_trial_id"] = None
        block["training_workflow"] = "awaiting_prepare"

    def _reset_unstarted_trial(self, block: dict, folder: Path) -> bool:
        """Requeue a selected trial when no acquisition needs resolution."""
        if block.get("training_workflow") not in ("awaiting_start_pose", "ready"):
            return False
        trial_id = block.get("current_trial_id")
        if trial_id is not None:
            trial = self._current_training_trial(block)
            if trial.get("status") in ("positioning", "ready"):
                trial["status"] = "pending"
                trial["prepared_at_utc"] = None
                trial["ready_at_utc"] = None
                self._write_trial(folder, block, trial)
        block["current_trial_id"] = None
        block["training_workflow"] = "awaiting_prepare"
        block["training_live"] = self._empty_training_live()
        return True

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
        presentation = progress.get("presentation")
        if presentation is not None and (
            not isinstance(presentation, dict)
            or presentation.get("status") not in
            {"pending", "showing", "completed", "legacy_skipped"}
        ):
            raise ExperimentError("Experiment progress has an invalid presentation state.")

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

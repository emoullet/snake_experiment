"""Panel C pseudonymised enrolment and resumable session preparation."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import string
import tempfile
import threading
from typing import Callable, Optional


CSV_FIELDS = [
    "session_date",
    "pseudonym",
    "gathered_consent",
    "handedness",
    "joystick_experience",
    "visual_or_motor_impairment",
    "starting_time",
    "experimental_plan",
    "state",
]
PLANS = ("baseline->snake", "snake->baseline")


class EnrollmentError(RuntimeError):
    """An operator-correctable Panel C error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class EnrollmentController:
    """Create, inspect, resume, and launch a participant session."""

    def __init__(
        self,
        sessions_root: Path,
        calibration_file: Path,
        bringup_root: Path,
        checkup_report_provider: Callable[[], Optional[dict]],
        provenance_provider: Callable[[], dict],
        stack_manager,
        mode_manager,
        experiment_profile: Optional[Path] = None,
        experiment_prepare: Optional[Callable[[dict], dict]] = None,
        utc_clock: Callable[[], str] = _utc_now,
        token_choice: Callable = secrets.choice,
    ) -> None:
        self._sessions_root = Path(sessions_root).resolve()
        self._calibration_file = Path(calibration_file).resolve()
        self._bringup_root = Path(bringup_root).resolve()
        self._checkup_report_provider = checkup_report_provider
        self._provenance_provider = provenance_provider
        self._stack = stack_manager
        self._modes = mode_manager
        self._experiment_profile = (
            Path(experiment_profile).resolve() if experiment_profile else None
        )
        self._experiment_prepare = experiment_prepare
        self._utc_clock = utc_clock
        self._choice = token_choice
        self._lock = threading.RLock()
        self._selected_parent: Optional[Path] = None
        self._participant: Optional[dict] = None
        self._created_here = False
        self._workflow = "awaiting_parent"
        self._error: Optional[str] = None
        self._warnings: list[str] = []
        self._resume_candidates: list[dict] = []
        self._mismatches: list[dict] = []
        self._current_panel = "C"

    @property
    def sessions_root(self) -> Path:
        return self._sessions_root

    def _bounded(self, value: str | Path) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self._sessions_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise EnrollmentError(f"Folder is not accessible: {candidate}") from error
        if not resolved.is_dir():
            raise EnrollmentError(f"Not a folder: {resolved}")
        try:
            resolved.relative_to(self._sessions_root)
        except ValueError as error:
            raise EnrollmentError("The selected folder is outside sessions_root.") from error
        return resolved

    def browse(self, path: str = "") -> dict:
        with self._lock:
            folder = self._bounded(path or self._sessions_root)
            entries = []
            for child in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
                try:
                    resolved = child.resolve(strict=True)
                    resolved.relative_to(self._sessions_root)
                except (OSError, ValueError):
                    continue
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(resolved)})
            parent = None
            if folder != self._sessions_root:
                parent = str(folder.parent)
            return {
                "root": str(self._sessions_root),
                "path": str(folder),
                "parent": parent,
                "directories": entries,
            }

    def create_folder(self, parent_path: str, name: str) -> dict:
        """Create one folder below sessions_root and browse into it."""
        with self._lock:
            parent = self._bounded(parent_path or self._sessions_root)
            folder_name = str(name).strip()
            if (
                not folder_name
                or folder_name in (".", "..")
                or folder_name.startswith(".")
                or len(folder_name) > 100
                or Path(folder_name).name != folder_name
                or "/" in folder_name
                or "\\" in folder_name
                or any(ord(character) < 32 for character in folder_name)
            ):
                raise EnrollmentError(
                    "Folder name must be a visible single name of at most 100 characters."
                )
            destination = parent / folder_name
            try:
                destination.mkdir()
            except FileExistsError as error:
                raise EnrollmentError(
                    f"A file or folder named '{folder_name}' already exists."
                ) from error
            except OSError as error:
                raise EnrollmentError(
                    f"Unable to create folder '{folder_name}': {error}"
                ) from error
            return self.browse(str(destination))

    def select_parent(self, path: str) -> dict:
        with self._lock:
            if self._participant is not None:
                raise EnrollmentError("Cancel the current participant before changing folder.")
            self._selected_parent = self._bounded(path)
            self._ensure_csv()
            self._scan()
            self._workflow = "parent_selected"
            self._error = None
            return self.snapshot()

    def generate_pseudonym(self) -> dict:
        with self._lock:
            if self._selected_parent is None:
                raise EnrollmentError("Select an experiment folder first.")
            existing = {row["pseudonym"] for row in self._read_rows()}
            existing.update(item.name for item in self._selected_parent.iterdir())
            for _ in range(1000):
                token = "".join(self._choice(string.ascii_uppercase + string.digits) for _ in range(6))
                if (
                    any(char.isalpha() for char in token)
                    and any(char.isdigit() for char in token)
                    and token not in existing
                ):
                    return {"pseudonym": token}
            raise EnrollmentError("Unable to generate a unique pseudonym.")

    def create_participant(self, form: dict) -> dict:
        with self._lock:
            if self._selected_parent is None:
                raise EnrollmentError("Select an experiment folder first.")
            if self._participant is not None:
                raise EnrollmentError("A participant is already selected.")
            report = self._checkup_report_provider()
            if not report:
                raise EnrollmentError("A validated Panel B report is required.")
            row = self._validate_form(form)
            rows = self._read_rows()
            if any(item["pseudonym"] == row["pseudonym"] for item in rows):
                raise EnrollmentError("This pseudonym already exists in experiment_state.csv.")
            participant_folder = self._selected_parent / row["pseudonym"]
            if participant_folder.exists():
                raise EnrollmentError("A folder already exists for this pseudonym.")

            now = self._utc_clock()
            row.update(
                {
                    "session_date": now.split("T", 1)[0],
                    "starting_time": now,
                    "experimental_plan": self._choose_plan(rows),
                    "state": "partial",
                }
            )
            temporary = Path(
                tempfile.mkdtemp(prefix=f".{row['pseudonym']}-", dir=self._selected_parent)
            )
            try:
                manifest = self._populate_environment(temporary, row, report, now)
                temporary.rename(participant_folder)
                try:
                    self._write_rows(rows + [row])
                except Exception:
                    shutil.rmtree(participant_folder)
                    raise
            except Exception:
                if temporary.exists():
                    shutil.rmtree(temporary)
                raise

            self._participant = {
                **row,
                "folder": str(participant_folder),
                "manifest": manifest,
                "resumed": False,
            }
            self._created_here = True
            self._mismatches = []
            self._workflow = "participant_ready"
            self._error = None
            self._scan()
            return self.snapshot()

    def resume_participant(self, pseudonym: str, acknowledge_mismatch: bool = False) -> dict:
        with self._lock:
            if self._selected_parent is None:
                raise EnrollmentError("Select an experiment folder first.")
            if self._participant is not None:
                raise EnrollmentError("A participant is already selected.")
            self._scan()
            candidate = next(
                (item for item in self._resume_candidates if item["pseudonym"] == pseudonym),
                None,
            )
            if candidate is None:
                raise EnrollmentError("This participant is not a resumable partial session.")
            row = next(item for item in self._read_rows() if item["pseudonym"] == pseudonym)
            folder = self._selected_parent / pseudonym
            try:
                manifest = json.loads(
                    (folder / "manifest.json").read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise EnrollmentError(
                    f"Participant manifest is unreadable: {pseudonym}"
                ) from error
            mismatches = self._compare_environment(manifest)
            self._mismatches = mismatches
            if mismatches and not acknowledge_mismatch:
                raise EnrollmentError("The saved environment differs from the current environment. Acknowledge the warning to resume.")
            report = self._checkup_report_provider()
            if not report:
                raise EnrollmentError("A validated Panel B report is required.")
            manifest = self._append_checkup_report(folder, manifest, report)
            self._participant = {
                **row,
                "folder": str(folder),
                "manifest": manifest,
                "resumed": True,
            }
            self._created_here = False
            self._workflow = "participant_ready"
            self._error = None
            return self.snapshot()

    def cancel(self) -> dict:
        with self._lock:
            if self._current_panel == "D":
                raise EnrollmentError("The participant cannot be cancelled after launch.")
            if self._participant is not None and self._created_here:
                pseudonym = self._participant["pseudonym"]
                folder = (self._selected_parent / pseudonym).resolve()
                if folder.parent != self._selected_parent or not folder.name == pseudonym:
                    raise EnrollmentError("Refusing to remove an unexpected participant path.")
                rows = [row for row in self._read_rows() if row["pseudonym"] != pseudonym]
                self._write_rows(rows)
                if folder.exists():
                    shutil.rmtree(folder)
            self._participant = None
            self._created_here = False
            self._mismatches = []
            self._workflow = "parent_selected" if self._selected_parent else "awaiting_parent"
            self._error = None
            if self._selected_parent:
                self._scan()
            return self.snapshot()

    def reset(self) -> dict:
        with self._lock:
            if self._current_panel == "D":
                raise EnrollmentError("The session cannot be reset after launch.")
            self.cancel()
            self._selected_parent = None
            self._resume_candidates = []
            self._warnings = []
            self._workflow = "awaiting_parent"
            return self.snapshot()

    def launch(self) -> dict:
        with self._lock:
            if self._current_panel == "D":
                raise EnrollmentError("The experiment is already launched.")
            if self._participant is None:
                raise EnrollmentError("Create or resume a participant before launching.")
            self._workflow = "launching"
            self._error = None
            try:
                if self._experiment_prepare is not None:
                    self._experiment_prepare(self._participant)
            except Exception as error:
                self._workflow = "error"
                self._error = f"Unable to prepare experiment: {error}"
                raise EnrollmentError(self._error) from error
            self._workflow = "experiment_ready"
            self._current_panel = "D"
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "workflow": self._workflow,
                "current_panel": self._current_panel,
                "error": self._error,
                "sessions_root": str(self._sessions_root),
                "selected_parent": str(self._selected_parent) if self._selected_parent else None,
                "calibration_file": str(self._calibration_file),
                "participant": self._participant,
                "created_here": self._created_here,
                "resume_candidates": self._resume_candidates,
                "warnings": self._warnings,
                "mismatches": self._mismatches,
                "can_launch": self._participant is not None,
            }

    def _csv_path(self) -> Path:
        if self._selected_parent is None:
            raise EnrollmentError("No experiment folder is selected.")
        return self._selected_parent / "experiment_state.csv"

    def _ensure_csv(self) -> None:
        path = self._csv_path()
        if not path.exists():
            self._write_rows([])

    def _read_rows(self) -> list[dict]:
        path = self._csv_path()
        try:
            with path.open("r", encoding="utf-8", newline="") as source:
                reader = csv.DictReader(source)
                if reader.fieldnames != CSV_FIELDS:
                    raise EnrollmentError(
                        "experiment_state.csv has an invalid header. Expected: "
                        + ", ".join(CSV_FIELDS)
                    )
                rows = list(reader)
        except UnicodeError as error:
            raise EnrollmentError("experiment_state.csv is not valid UTF-8.") from error
        for row in rows:
            if not row["pseudonym"] or row["state"] not in ("partial", "complete"):
                raise EnrollmentError("experiment_state.csv contains an invalid participant row.")
            if row["experimental_plan"] not in PLANS:
                raise EnrollmentError("experiment_state.csv contains an invalid experimental plan.")
        pseudonyms = [row["pseudonym"] for row in rows]
        if len(pseudonyms) != len(set(pseudonyms)):
            raise EnrollmentError("experiment_state.csv contains duplicate pseudonyms.")
        return rows

    def _write_rows(self, rows: list[dict]) -> None:
        path = self._csv_path()
        descriptor, temporary_name = tempfile.mkstemp(prefix=".experiment-state-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _scan(self) -> None:
        rows = self._read_rows()
        warnings = []
        candidates = []
        row_names = {row["pseudonym"] for row in rows}
        for row in rows:
            folder = self._selected_parent / row["pseudonym"]
            if not folder.is_dir():
                warnings.append(f"Missing participant folder: {row['pseudonym']}")
                continue
            manifest = folder / "manifest.json"
            if row["state"] == "partial":
                if manifest.is_file():
                    try:
                        saved = json.loads(manifest.read_text(encoding="utf-8"))
                        if saved.get("pseudonym") != row["pseudonym"]:
                            raise ValueError("pseudonym mismatch")
                    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                        warnings.append(
                            f"Partial session has an invalid manifest: {row['pseudonym']}"
                        )
                    else:
                        candidates.append(
                            {
                                "pseudonym": row["pseudonym"],
                                "session_date": row["session_date"],
                                "starting_time": row["starting_time"],
                                "experimental_plan": row["experimental_plan"],
                            }
                        )
                else:
                    warnings.append(f"Partial session has no manifest: {row['pseudonym']}")
        for folder in self._selected_parent.iterdir():
            if folder.is_dir() and folder.name not in row_names and not folder.name.startswith("."):
                warnings.append(f"Orphan participant folder: {folder.name}")
        self._warnings = warnings
        self._resume_candidates = candidates

    def _validate_form(self, form: dict) -> dict:
        required = (
            "pseudonym",
            "gathered_consent",
            "handedness",
            "joystick_experience",
            "visual_or_motor_impairment",
        )
        if any(key not in form or form[key] is None for key in required):
            raise EnrollmentError("All participant fields are required.")
        pseudonym = str(form["pseudonym"]).strip().upper()
        if (
            len(pseudonym) != 6
            or not set(pseudonym) <= set(string.ascii_uppercase + string.digits)
            or not any(char.isalpha() for char in pseudonym)
            or not any(char.isdigit() for char in pseudonym)
        ):
            raise EnrollmentError("Pseudonym must contain six capital letters/digits, including both kinds.")
        if form["gathered_consent"] is not True:
            raise EnrollmentError("Participant consent must be gathered before enrolment.")
        if form["handedness"] not in ("left", "right"):
            raise EnrollmentError("Handedness must be left or right.")
        for key in ("joystick_experience", "visual_or_motor_impairment"):
            if not isinstance(form[key], bool):
                raise EnrollmentError(f"{key} must be true or false.")
        return {
            "session_date": "",
            "pseudonym": pseudonym,
            "gathered_consent": "true",
            "handedness": form["handedness"],
            "joystick_experience": str(form["joystick_experience"]).lower(),
            "visual_or_motor_impairment": str(form["visual_or_motor_impairment"]).lower(),
            "starting_time": "",
            "experimental_plan": "",
            "state": "partial",
        }

    def _choose_plan(self, rows: list[dict]) -> str:
        counts = {plan: 0 for plan in PLANS}
        for row in rows:
            counts[row["experimental_plan"]] += 1
        minimum = min(counts.values())
        candidates = [plan for plan, count in counts.items() if count == minimum]
        return self._choice(candidates)

    def _bringup_files(self):
        if not self._bringup_root.is_dir():
            raise EnrollmentError(f"Packaged bringup directory not found: {self._bringup_root}")
        for path in sorted(self._bringup_root.rglob("*")):
            if path.is_file() and "config" in path.relative_to(self._bringup_root).parts:
                yield path

    def _populate_environment(self, folder: Path, row: dict, report: dict, now: str) -> dict:
        if not self._calibration_file.is_file():
            raise EnrollmentError(f"Calibration file not found: {self._calibration_file}")
        environment = folder / "experimental_environment"
        bringup_destination = environment / "bringup"
        calibration_destination = folder / "calibration" / "latest_calib.json"
        checkup_id = report.get("checkup_id", "panel-b-checkup")
        checkup_destination = folder / "checkups" / f"{checkup_id}.json"
        files = []
        for source in self._bringup_files():
            relative = source.relative_to(self._bringup_root)
            destination = bringup_destination / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            files.append({"path": str(Path("experimental_environment/bringup") / relative), "sha256": _sha256(destination)})
        if self._experiment_profile is not None:
            if not self._experiment_profile.is_file():
                raise EnrollmentError(
                    f"Experiment profile not found: {self._experiment_profile}"
                )
            experiment_destination = environment / "experiment.yaml"
            experiment_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._experiment_profile, experiment_destination)
            files.append(
                {
                    "path": "experimental_environment/experiment.yaml",
                    "sha256": _sha256(experiment_destination),
                }
            )
        calibration_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._calibration_file, calibration_destination)
        checkup_destination.parent.mkdir(parents=True, exist_ok=True)
        checkup_destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "created_at_utc": now,
            "pseudonym": row["pseudonym"],
            "experimental_plan": row["experimental_plan"],
            "state": "partial",
            "active_checkup_report": str(Path("checkups") / checkup_destination.name),
            "checkup_reports": [str(Path("checkups") / checkup_destination.name)],
            "git_provenance": self._provenance_provider(),
            "experiment": {
                "profile": "experimental_environment/experiment.yaml",
                "progress": "experiment_progress.json",
            },
            "files": files + [
                {"path": "calibration/latest_calib.json", "sha256": _sha256(calibration_destination)},
                {"path": str(Path("checkups") / checkup_destination.name), "sha256": _sha256(checkup_destination)},
            ],
        }
        (folder / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest

    def _append_checkup_report(self, folder: Path, manifest: dict, report: dict) -> dict:
        checkup_id = report.get("checkup_id")
        if not checkup_id:
            raise EnrollmentError("The Panel B report has no checkup identifier.")
        checkup_id = str(checkup_id)
        if Path(checkup_id).name != checkup_id or checkup_id in (".", ".."):
            raise EnrollmentError("The Panel B checkup identifier is unsafe.")
        relative = str(Path("checkups") / f"{checkup_id}.json")
        destination = folder / relative
        if destination.exists():
            raise EnrollmentError(f"Check-up report already exists: {checkup_id}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        updated = dict(manifest)
        history = list(updated.get("checkup_reports", []))
        history.append(relative)
        updated["checkup_reports"] = history
        updated["active_checkup_report"] = relative
        files = list(updated.get("files", []))
        files.append({"path": relative, "sha256": _sha256(destination)})
        updated["files"] = files
        temporary = folder / ".manifest.json.tmp"
        try:
            temporary.write_text(
                json.dumps(updated, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, folder / "manifest.json")
        except Exception:
            destination.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            raise
        return updated

    def _current_environment_hashes(self) -> dict[str, str]:
        values = {}
        for source in self._bringup_files():
            relative = Path("experimental_environment/bringup") / source.relative_to(self._bringup_root)
            values[str(relative)] = _sha256(source)
        if self._calibration_file.is_file():
            values["calibration/latest_calib.json"] = _sha256(self._calibration_file)
        if self._experiment_profile is not None and self._experiment_profile.is_file():
            values["experimental_environment/experiment.yaml"] = _sha256(
                self._experiment_profile
            )
        return values

    def _compare_environment(self, manifest: dict) -> list[dict]:
        saved = {
            item["path"]: item["sha256"]
            for item in manifest.get("files", [])
            if item.get("path", "").startswith("experimental_environment/bringup/")
            or item.get("path") == "experimental_environment/experiment.yaml"
            or item.get("path") == "calibration/latest_calib.json"
        }
        current = self._current_environment_hashes()
        # Sessions created before LOT 4 legitimately have no experiment profile.
        # It is snapshotted, with a manifest warning, when Panel D is first opened.
        if "experimental_environment/experiment.yaml" not in saved:
            current.pop("experimental_environment/experiment.yaml", None)
        mismatches = []
        for path in sorted(set(saved) | set(current)):
            if saved.get(path) != current.get(path):
                mismatches.append({"path": path, "saved": saved.get(path), "current": current.get(path)})
        return mismatches

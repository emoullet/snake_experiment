"""FastAPI surface for the independent Panels B-G interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .checkup import CheckupError
from .diagnostics import DiagnosticProfileError
from .enrollment import EnrollmentError
from .experiment import ExperimentError
from .mode_manager import ModeError
from .rosbag_manager import RosbagError
from .stack_manager import StackError
from .training import TrainingError


class Confirmation(BaseModel):
    accepted: bool


class FolderSelection(BaseModel):
    path: str


class FolderCreation(BaseModel):
    parent_path: str
    name: str


class ParticipantForm(BaseModel):
    pseudonym: str
    gathered_consent: bool
    handedness: str
    joystick_experience: bool
    visual_or_motor_impairment: bool


class ResumeRequest(BaseModel):
    pseudonym: str
    acknowledge_mismatch: bool = False


class EndBlockRequest(BaseModel):
    confirmed: bool


class ControlRequest(BaseModel):
    active: bool


class IncidentRequest(BaseModel):
    text: str


class TrainingResolutionRequest(BaseModel):
    decision: str


def create_session_app(
    checkup,
    static_directory: Path,
    template_directory: Path,
    enrollment=None,
    experiment=None,
):
    """Create the Panel B-G app around injectable workflow controllers."""
    app = FastAPI(title="Snake Experiment Session Interface", version="1.0")
    app.mount(
        "/session-static",
        StaticFiles(directory=static_directory, follow_symlink=True),
        name="session-static",
    )

    def action(callback):
        try:
            return callback()
        except (
            CheckupError,
            DiagnosticProfileError,
            EnrollmentError,
            ExperimentError,
            ModeError,
            RosbagError,
            StackError,
            TrainingError,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.get("/", include_in_schema=False)
    async def session_interface():
        return FileResponse(template_directory / "session_index.html")

    @app.get("/api/state")
    async def state():
        return combined_snapshot()

    def combined_snapshot():
        state = checkup.snapshot()
        if enrollment is not None and state.get("current_panel") != "B":
            panel_c = enrollment.snapshot()
            state["current_panel"] = panel_c["current_panel"]
            state["enrollment"] = panel_c
            if experiment is not None and panel_c["current_panel"] != "C":
                panel_d = experiment.snapshot()
                state["current_panel"] = panel_d["current_panel"]
                state["experiment"] = panel_d
        return state

    def enrollment_action(callback):
        require_panel_c()
        action(callback)
        return combined_snapshot()

    def require_panel_c():
        if checkup.snapshot().get("current_panel") == "B":
            raise HTTPException(
                status_code=409,
                detail="Validate Panel B before using session enrolment.",
            )

    def require_panel_b():
        if checkup.snapshot().get("current_panel") != "B":
            raise HTTPException(status_code=409, detail="Panel B is already complete.")

    @app.post("/api/stack/{command}")
    async def stack(command: str):
        require_panel_b()
        if command == "start":
            return action(checkup.start_stack)
        if command == "stop":
            return action(checkup.stop_stack)
        raise HTTPException(status_code=404, detail="Unknown stack action.")

    @app.post("/api/checkup/modes/{mode}/start")
    async def start_mode(mode: str):
        require_panel_b()
        return action(lambda: checkup.start_mode(mode))

    @app.post("/api/checkup/modes/{mode}/retry")
    async def retry_mode(mode: str):
        require_panel_b()
        return action(lambda: checkup.retry_mode(mode))

    @app.post("/api/checkup/modes/{mode}/confirm")
    async def confirm_mode(mode: str, confirmation: Confirmation):
        require_panel_b()
        return action(lambda: checkup.confirm_mode(mode, confirmation.accepted))

    @app.post("/api/checkup/validate")
    async def validate():
        require_panel_b()
        return action(checkup.validate)

    @app.post("/api/checkup/reset")
    async def reset():
        require_panel_b()
        return action(checkup.reset)

    @app.get("/api/session/browse")
    async def browse(path: str = ""):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Panel C is not configured.")
        require_panel_c()
        return action(lambda: enrollment.browse(path))

    @app.post("/api/session/root")
    async def select_root(selection: FolderSelection):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Panel C is not configured.")
        return enrollment_action(lambda: enrollment.select_parent(selection.path))

    @app.post("/api/session/folders")
    async def create_folder(request: FolderCreation):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Panel C is not configured.")
        require_panel_c()
        return action(
            lambda: enrollment.create_folder(request.parent_path, request.name)
        )

    @app.post("/api/session/pseudonym/regenerate")
    async def regenerate_pseudonym():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Panel C is not configured.")
        require_panel_c()
        return action(enrollment.generate_pseudonym)

    @app.post("/api/session/new")
    async def create_participant(form: ParticipantForm):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Panel C is not configured.")
        return enrollment_action(lambda: enrollment.create_participant(form.dict()))

    @app.post("/api/session/resume")
    async def resume_participant(request: ResumeRequest):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Panel C is not configured.")
        return enrollment_action(
            lambda: enrollment.resume_participant(
                request.pseudonym, request.acknowledge_mismatch
            )
        )

    @app.post("/api/session/cancel")
    async def cancel_participant():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Panel C is not configured.")
        return enrollment_action(enrollment.cancel)

    @app.post("/api/session/reset")
    async def reset_session():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Panel C is not configured.")
        return enrollment_action(enrollment.reset)

    @app.post("/api/session/launch")
    async def launch_session():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Panel C is not configured.")
        return enrollment_action(enrollment.launch)

    def experiment_action(callback):
        if experiment is None:
            raise HTTPException(status_code=404, detail="Panel D is not configured.")
        if checkup.snapshot().get("current_panel") == "B":
            raise HTTPException(status_code=409, detail="Validate Panel B first.")
        if enrollment is None or enrollment.snapshot().get("current_panel") == "C":
            raise HTTPException(
                status_code=409,
                detail="Prepare a participant from Panel C first.",
            )
        action(callback)
        return combined_snapshot()

    @app.post("/api/experiment/blocks/{block_id}/start")
    async def start_experiment_block(block_id: str):
        return experiment_action(lambda: experiment.start(block_id))

    @app.post("/api/experiment/blocks/{block_id}/end")
    async def end_experiment_block(block_id: str, request: EndBlockRequest):
        return experiment_action(lambda: experiment.end(block_id, request.confirmed))

    @app.post("/api/experiment/blocks/{block_id}/abort")
    async def abort_experiment_block(block_id: str):
        return experiment_action(lambda: experiment.abort(block_id))

    @app.post("/api/experiment/blocks/{block_id}/control")
    async def set_experiment_control(block_id: str, request: ControlRequest):
        return experiment_action(
            lambda: experiment.set_control(block_id, request.active)
        )

    @app.post("/api/experiment/blocks/{block_id}/restart-stack")
    async def restart_experiment_stack(block_id: str):
        return experiment_action(lambda: experiment.restart_stack(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/prepare")
    async def prepare_training_trial(block_id: str):
        return experiment_action(lambda: experiment.prepare_training_trial(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/ready")
    async def confirm_training_ready(block_id: str):
        return experiment_action(
            lambda: experiment.training_participant_ready(block_id)
        )

    @app.post("/api/experiment/blocks/{block_id}/training/start")
    async def start_training_attempt(block_id: str):
        return experiment_action(lambda: experiment.start_training_attempt(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/stop")
    async def stop_training_attempt(block_id: str):
        return experiment_action(lambda: experiment.stop_training_attempt(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/incidents")
    async def add_training_incident(block_id: str, request: IncidentRequest):
        return experiment_action(
            lambda: experiment.add_training_incident(block_id, request.text)
        )

    @app.post("/api/experiment/blocks/{block_id}/training/resolve")
    async def resolve_training_attempt(
        block_id: str, request: TrainingResolutionRequest
    ):
        return experiment_action(
            lambda: experiment.resolve_training_attempt(
                block_id, request.decision
            )
        )

    @app.post("/api/experiment/blocks/{block_id}/recording/prepare")
    async def prepare_recording_trial(block_id: str):
        return experiment_action(lambda: experiment.prepare_recording_trial(block_id))

    @app.post("/api/experiment/blocks/{block_id}/recording/ready")
    async def confirm_recording_ready(block_id: str):
        return experiment_action(
            lambda: experiment.recording_participant_ready(block_id)
        )

    @app.post("/api/experiment/blocks/{block_id}/recording/start")
    async def start_recording_attempt(block_id: str):
        return experiment_action(lambda: experiment.start_recording_attempt(block_id))

    @app.post("/api/experiment/blocks/{block_id}/recording/stop")
    async def stop_recording_attempt(block_id: str):
        return experiment_action(lambda: experiment.stop_recording_attempt(block_id))

    @app.post("/api/experiment/blocks/{block_id}/recording/incidents")
    async def add_recording_incident(block_id: str, request: IncidentRequest):
        return experiment_action(
            lambda: experiment.add_recording_incident(block_id, request.text)
        )

    @app.post("/api/experiment/blocks/{block_id}/recording/resolve")
    async def resolve_recording_attempt(
        block_id: str, request: TrainingResolutionRequest
    ):
        return experiment_action(
            lambda: experiment.resolve_recording_attempt(
                block_id, request.decision
            )
        )

    @app.websocket("/ws")
    async def state_websocket(websocket: WebSocket):
        await websocket.accept()
        previous = None
        try:
            while True:
                current = combined_snapshot()
                serialised = json.dumps(current, sort_keys=True)
                if serialised != previous:
                    await websocket.send_json(current)
                    previous = serialised
                await asyncio.sleep(0.25)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app

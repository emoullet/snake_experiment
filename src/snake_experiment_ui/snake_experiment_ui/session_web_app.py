"""FastAPI surface for the independent Views B-G interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .checkup import CheckupError
from .diagnostics import DiagnosticProfileError
from .enrollment import EnrollmentError
from .experiment import ExperimentError
from .mode_manager import ModeError
from .participant_profile import state_image_is_available, video_is_available
from .rosbag_manager import RosbagError
from .robot_preview import RobotPreviewError
from .stack_manager import StackError
from .training import TrainingError


NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


class NoCacheStaticFiles(StaticFiles):
    """Serve operator assets without retaining stale mixed UI versions."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE_HEADERS)
        return response


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


class IncidentDescription(BaseModel):
    id: int
    text: str


class IncidentReviewRequest(BaseModel):
    descriptions: list[IncidentDescription]
    invalidates_attempt: bool


class TrainingResolutionRequest(BaseModel):
    decision: str


class GoToRequest(BaseModel):
    pose_id: str


def create_session_app(
    checkup,
    static_directory: Path,
    template_directory: Path,
    enrollment=None,
    experiment=None,
    presentation_video="",
    mode_explanation_videos=None,
    state_images=None,
    robot_preview=None,
):
    """Create the View B-G app around injectable workflow controllers."""
    app = FastAPI(title="Snake Experiment Session Interface", version="1.0")
    websocket_clients = 0

    app.mount(
        "/session-static",
        NoCacheStaticFiles(directory=static_directory, follow_symlink=True),
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
        return FileResponse(
            template_directory / "session_index.html", headers=NO_CACHE_HEADERS
        )

    @app.get("/api/state")
    async def state():
        return combined_snapshot()

    @app.get("/participant", include_in_schema=False)
    async def participant_interface():
        return FileResponse(template_directory / "participant_index.html", headers=NO_CACHE_HEADERS)

    @app.get("/participant/3d-preview", include_in_schema=False)
    async def participant_3d_preview():
        return FileResponse(
            template_directory / "participant_3d_preview.html", headers=NO_CACHE_HEADERS
        )

    @app.get("/participant/3d-preview/api/state", include_in_schema=False)
    async def participant_3d_state():
        if robot_preview is None:
            raise HTTPException(status_code=503, detail="Robot preview is unavailable.")
        return robot_preview.snapshot()

    @app.get("/participant/3d-preview/api/config", include_in_schema=False)
    async def participant_3d_config():
        if robot_preview is None:
            raise HTTPException(status_code=503, detail="Robot preview is unavailable.")
        try:
            return robot_preview.configuration()
        except RobotPreviewError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/participant/3d-preview/model.urdf", include_in_schema=False)
    async def participant_3d_model():
        if robot_preview is None:
            raise HTTPException(status_code=503, detail="Robot preview is unavailable.")
        try:
            model = robot_preview.model.urdf()
        except RobotPreviewError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return Response(model, media_type="application/xml", headers=NO_CACHE_HEADERS)

    @app.get("/participant/3d-preview/assets/{asset_id}", include_in_schema=False)
    async def participant_3d_asset(asset_id: str):
        if robot_preview is None:
            raise HTTPException(status_code=503, detail="Robot preview is unavailable.")
        try:
            asset = robot_preview.model.asset(asset_id)
        except RobotPreviewError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        media_type = "model/stl" if asset.suffix.lower() == ".stl" else "model/vnd.collada+xml"
        return FileResponse(asset, media_type=media_type, headers=NO_CACHE_HEADERS)

    @app.websocket("/participant/3d-preview/ws")
    async def participant_3d_websocket(websocket: WebSocket):
        if robot_preview is None:
            await websocket.close(code=1013)
            return
        await websocket.accept()
        previous = None
        try:
            while True:
                current = robot_preview.snapshot()
                serialised = json.dumps(current, sort_keys=True)
                if serialised != previous:
                    await websocket.send_json(current)
                    previous = serialised
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, RuntimeError):
            return

    @app.get("/participant/api/state")
    async def participant_state():
        return experiment.participant_snapshot() if experiment is not None else {
            "view": "waiting", "presentation_status": "pending", "video_available": False,
        }

    @app.get("/participant/video", include_in_schema=False)
    async def participant_video():
        video = Path(presentation_video).expanduser().resolve() if presentation_video else None
        if experiment is None or not video_is_available(video):
            raise HTTPException(status_code=404, detail="Presentation video is unavailable.")
        return FileResponse(video, media_type="video/mp4", headers=NO_CACHE_HEADERS)

    @app.get("/participant/video/mode/{mode}", include_in_schema=False)
    async def mode_explanation_video(mode: str):
        state = experiment.participant_snapshot() if experiment is not None else {}
        paths = mode_explanation_videos or {}
        video = paths.get(mode) if mode in ("baseline", "snake") else None
        if (
            state.get("view") != "B"
            or state.get("mode") != mode
            or not video_is_available(video)
        ):
            raise HTTPException(status_code=404, detail="Mode explanation video is unavailable.")
        return FileResponse(video, media_type="video/mp4", headers=NO_CACHE_HEADERS)

    @app.get("/participant/state-image/{mode}/{state}", include_in_schema=False)
    async def participant_state_image(mode: str, state: str):
        public = experiment.participant_snapshot() if experiment is not None else {}
        image = (state_images or {}).get(mode, {}).get(state)
        if (
            public.get("view") != "C"
            or public.get("mode") != mode
            or public.get("local_mode") != state
            or not state_image_is_available(image)
        ):
            raise HTTPException(status_code=404, detail="State explanation image is unavailable.")
        return FileResponse(image, media_type="image/png", headers=NO_CACHE_HEADERS)

    @app.websocket("/participant/ws")
    async def participant_websocket(websocket: WebSocket):
        await websocket.accept()
        previous = None
        try:
            while True:
                current = experiment.participant_snapshot() if experiment is not None else {
                    "view": "waiting", "presentation_status": "pending", "video_available": False,
                }
                serialised = json.dumps(current, sort_keys=True)
                if serialised != previous:
                    await websocket.send_json(current)
                    previous = serialised
                await asyncio.sleep(0.25)
        except (WebSocketDisconnect, RuntimeError):
            return

    def combined_snapshot():
        state = checkup.snapshot()
        if enrollment is not None and state.get("current_view") != "B":
            view_c = enrollment.snapshot()
            state["current_view"] = view_c["current_view"]
            state["enrollment"] = view_c
            if experiment is not None and view_c["current_view"] != "C":
                view_d = experiment.snapshot()
                state["current_view"] = view_d["current_view"]
                state["experiment"] = view_d
        return state

    def enrollment_action(callback):
        require_view_c()
        action(callback)
        return combined_snapshot()

    def require_view_c():
        if checkup.snapshot().get("current_view") == "B":
            raise HTTPException(
                status_code=409,
                detail="Validate View B before using session enrolment.",
            )

    def require_view_b():
        if checkup.snapshot().get("current_view") != "B":
            raise HTTPException(status_code=409, detail="View B is already complete.")

    @app.post("/api/stack/{command}")
    async def stack(command: str):
        require_view_b()
        if command == "start":
            return action(checkup.start_stack)
        if command == "stop":
            return action(checkup.stop_stack)
        raise HTTPException(status_code=404, detail="Unknown stack action.")

    @app.post("/api/checkup/modes/{mode}/start")
    async def start_mode(mode: str):
        require_view_b()
        return action(lambda: checkup.start_mode(mode))

    @app.post("/api/checkup/modes/{mode}/retry")
    async def retry_mode(mode: str):
        require_view_b()
        return action(lambda: checkup.retry_mode(mode))

    @app.post("/api/checkup/modes/{mode}/confirm")
    async def confirm_mode(mode: str, confirmation: Confirmation):
        require_view_b()
        return action(lambda: checkup.confirm_mode(mode, confirmation.accepted))

    @app.post("/api/checkup/validate")
    async def validate():
        require_view_b()
        return action(checkup.validate)

    @app.post("/api/checkup/reset")
    async def reset():
        require_view_b()
        return action(checkup.reset)

    @app.post("/api/checkup/go-to")
    async def start_checkup_go_to(request: GoToRequest):
        require_view_b()
        return action(lambda: checkup.start_go_to(request.pose_id))

    @app.post("/api/checkup/go-to/stop")
    async def stop_checkup_go_to():
        require_view_b()
        return action(checkup.stop_go_to)

    @app.get("/api/session/browse")
    async def browse(path: str = ""):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        require_view_c()
        return action(lambda: enrollment.browse(path))

    @app.post("/api/session/root")
    async def select_root(selection: FolderSelection):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(lambda: enrollment.select_parent(selection.path))

    @app.post("/api/session/folders")
    async def create_folder(request: FolderCreation):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        require_view_c()
        return action(
            lambda: enrollment.create_folder(request.parent_path, request.name)
        )

    @app.post("/api/session/pseudonym/regenerate")
    async def regenerate_pseudonym():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        require_view_c()
        return action(enrollment.generate_pseudonym)

    @app.post("/api/session/new")
    async def create_participant(form: ParticipantForm):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(lambda: enrollment.create_participant(form.dict()))

    @app.post("/api/session/resume")
    async def resume_participant(request: ResumeRequest):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(
            lambda: enrollment.resume_participant(
                request.pseudonym, request.acknowledge_mismatch
            )
        )

    @app.post("/api/session/cancel")
    async def cancel_participant():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(enrollment.cancel)

    @app.post("/api/session/reset")
    async def reset_session():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(enrollment.reset)

    @app.post("/api/session/launch")
    async def launch_session():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(enrollment.launch)

    def experiment_action(callback):
        if experiment is None:
            raise HTTPException(status_code=404, detail="View D is not configured.")
        if checkup.snapshot().get("current_view") == "B":
            raise HTTPException(status_code=409, detail="Validate View B first.")
        if enrollment is None or enrollment.snapshot().get("current_view") == "C":
            raise HTTPException(
                status_code=409,
                detail="Prepare a participant from View C first.",
            )
        action(callback)
        return combined_snapshot()

    @app.post("/api/experiment/presentation/show")
    async def show_presentation():
        return experiment_action(lambda: experiment.show_presentation())

    @app.post("/api/experiment/presentation/complete")
    async def complete_presentation():
        return experiment_action(lambda: experiment.complete_presentation())

    @app.post("/api/experiment/blocks/{block_id}/start")
    async def start_experiment_block(block_id: str):
        return experiment_action(lambda: experiment.start(block_id))

    @app.post("/api/experiment/blocks/{block_id}/explanation/show")
    async def show_mode_explanation(block_id: str):
        return experiment_action(lambda: experiment.show_mode_explanation(block_id))

    @app.post("/api/experiment/blocks/{block_id}/explanation/complete")
    async def complete_mode_explanation(block_id: str):
        return experiment_action(lambda: experiment.complete_mode_explanation(block_id))

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

    @app.post("/api/experiment/blocks/{block_id}/go-to")
    async def start_experiment_go_to(block_id: str, request: GoToRequest):
        return experiment_action(
            lambda: experiment.start_go_to(block_id, request.pose_id)
        )

    @app.post("/api/experiment/blocks/{block_id}/go-to/stop")
    async def stop_experiment_go_to(block_id: str):
        return experiment_action(lambda: experiment.stop_go_to(block_id))

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
    async def add_training_incident(block_id: str):
        return experiment_action(lambda: experiment.add_training_incident(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/incidents/review")
    async def review_training_incidents(
        block_id: str, request: IncidentReviewRequest
    ):
        return experiment_action(
            lambda: experiment.review_training_incidents(
                block_id,
                [item.dict() for item in request.descriptions],
                request.invalidates_attempt,
            )
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
    async def add_recording_incident(block_id: str):
        return experiment_action(lambda: experiment.add_recording_incident(block_id))

    @app.post("/api/experiment/blocks/{block_id}/recording/incidents/review")
    async def review_recording_incidents(
        block_id: str, request: IncidentReviewRequest
    ):
        return experiment_action(
            lambda: experiment.review_recording_incidents(
                block_id,
                [item.dict() for item in request.descriptions],
                request.invalidates_attempt,
            )
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
        nonlocal websocket_clients
        await websocket.accept()
        websocket_clients += 1
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
        finally:
            websocket_clients = max(0, websocket_clients - 1)
            if websocket_clients == 0:
                try:
                    checkup.browser_disconnected()
                except Exception:
                    pass
                if experiment is not None:
                    try:
                        experiment.browser_disconnected()
                    except Exception:
                        pass

    return app

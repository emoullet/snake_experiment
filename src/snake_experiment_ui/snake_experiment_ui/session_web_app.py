"""FastAPI surface for the independent Views B-G interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .overload import ParticipantOverloadWarning, ProtectionError
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
    """Boolean confirmation submitted by an operator action."""

    accepted: bool


class FolderSelection(BaseModel):
    """Selected filesystem directory for a participant session."""

    path: str


class FolderCreation(BaseModel):
    """Parent directory and name for a new session folder."""

    parent_path: str
    name: str


class ParticipantForm(BaseModel):
    """Participant metadata collected during enrollment."""

    pseudonym: str
    gathered_consent: bool
    handedness: str
    joystick_experience: bool
    visual_or_motor_impairment: bool


class ResumeRequest(BaseModel):
    """Participant identity and optional mismatch acknowledgement."""

    pseudonym: str
    acknowledge_mismatch: bool = False


class EndBlockRequest(BaseModel):
    """Explicit confirmation for completing an experiment block."""

    confirmed: bool


class ControlRequest(BaseModel):
    """Requested enabled state for participant control and recording."""

    active: bool


class IncidentDescription(BaseModel):
    """Operator description associated with one reported incident."""

    id: int
    text: str


class IncidentReviewRequest(BaseModel):
    """Incident descriptions and validity decision for an attempt."""

    descriptions: list[IncidentDescription]
    invalidates_attempt: bool


class TrainingResolutionRequest(BaseModel):
    """Operator decision to retry a trial or accept a recorded deviation."""

    decision: str


class GoToRequest(BaseModel):
    """Identifier of a calibrated target requested by the operator."""

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
    protection_snapshot=None,
    protection_guard=None,
    protection_action=None,
    preview_default_view="view_1",
    stack_snapshot=None,
    overload_sound_duration_sec=3.0,
):
    """Create the View B-G app around injectable workflow controllers."""
    app = FastAPI(title="Snake Experiment Session Interface", version="1.0")
    websocket_clients = 0
    app.state.shutting_down = False
    participant_warning = ParticipantOverloadWarning()

    @app.middleware("http")
    async def protect_commands(request, call_next):
        if app.state.shutting_down and request.method == "POST":
            return JSONResponse(status_code=503, content={"detail": "Session interface is shutting down."})
        path = request.url.path
        commands = (
            path == "/api/experiment/stack/restart"
            or path.startswith("/api/checkup/modes/") and path.endswith(("/start", "/retry"))
            or path == "/api/checkup/go-to"
            or path.startswith("/api/experiment/blocks/") and path.endswith(("/start", "/control", "/restart-stack", "/go-to", "/ready", "/prepare"))
        )
        # Discovery starts only the stack and explanation workflow. It does not
        # activate a mapper or recorder, so it can bootstrap an inactive stack
        # whose protection status cannot yet be fresh.
        discovery_bootstrap = False
        parts = path.split("/")
        if (request.method == "POST" and experiment is not None
                and len(parts) == 6 and parts[1:4] == ["api", "experiment", "blocks"]
                and parts[5] == "start"):
            snapshot = await asyncio.to_thread(experiment.snapshot)
            block = next((item for item in (snapshot.get("progress") or {}).get("blocks", [])
                          if item["id"] == parts[4]), None)
            discovery_bootstrap = bool(block and block["phase"] == "discovery"
                                       and snapshot.get("stack", {}).get("status") == "inactive")
        if request.method == "POST" and commands and not discovery_bootstrap and protection_guard is not None:
            try:
                protection_guard()
            except ProtectionError as error:
                return JSONResponse(status_code=409, content={"detail": str(error)})
        return await call_next(request)

    @app.post("/api/protection/{command}")
    async def protection_command(command: str):
        if protection_action is None or command not in ("stop", "rearm"):
            raise HTTPException(status_code=404, detail="Unknown protection action.")
        await asyncio.to_thread(action, lambda: protection_action(command))
        return await asyncio.to_thread(combined_snapshot)

    app.mount(
        "/session-static",
        NoCacheStaticFiles(directory=static_directory, follow_symlink=True),
        name="session-static",
    )

    def action(callback):
        try:
            return callback()
        except (
            ProtectionError,
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
        return await asyncio.to_thread(combined_snapshot)

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
            return {**robot_preview.configuration(), "camera": {"default_view": preview_default_view}}
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
            while not app.state.shutting_down:
                current = robot_preview.snapshot()
                serialised = json.dumps(current, sort_keys=True)
                if serialised != previous:
                    await websocket.send_json(current)
                    previous = serialised
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, RuntimeError):
            return

    def participant_projection():
        state = dict(experiment.participant_snapshot()) if experiment is not None else {
            "view": "waiting", "presentation_status": "pending", "video_available": False,
        }
        state["overload_warning"] = None
        if protection_snapshot is not None:
            protection = protection_snapshot()
            stack = stack_snapshot() if stack_snapshot is not None else checkup.snapshot().get("stack", {})
            warning = participant_warning.snapshot(protection, stack.get("status") == "active")
            state["overload_warning"] = {**warning, "duration_sec": overload_sound_duration_sec} if warning else None
        return state

    @app.get("/participant/api/state")
    async def participant_state():
        return await asyncio.to_thread(participant_projection)

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
            while not app.state.shutting_down:
                current = await asyncio.to_thread(participant_projection)
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
        if protection_snapshot is not None:
            state["protection"] = protection_snapshot()
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
    def stack(command: str):
        require_view_b()
        if command == "start":
            return action(checkup.start_stack)
        if command == "stop":
            return action(checkup.stop_stack)
        raise HTTPException(status_code=404, detail="Unknown stack action.")

    @app.post("/api/checkup/modes/{mode}/start")
    def start_mode(mode: str):
        require_view_b()
        return action(lambda: checkup.start_mode(mode))

    @app.post("/api/checkup/modes/{mode}/retry")
    def retry_mode(mode: str):
        require_view_b()
        return action(lambda: checkup.retry_mode(mode))

    @app.post("/api/checkup/modes/{mode}/confirm")
    def confirm_mode(mode: str, confirmation: Confirmation):
        require_view_b()
        return action(lambda: checkup.confirm_mode(mode, confirmation.accepted))

    @app.post("/api/checkup/validate")
    def validate():
        require_view_b()
        return action(checkup.validate)

    @app.post("/api/checkup/reset")
    def reset():
        require_view_b()
        return action(checkup.reset)

    @app.post("/api/checkup/go-to")
    def start_checkup_go_to(request: GoToRequest):
        require_view_b()
        return action(lambda: checkup.start_go_to(request.pose_id))

    @app.post("/api/checkup/go-to/stop")
    def stop_checkup_go_to():
        require_view_b()
        return action(checkup.stop_go_to)

    @app.get("/api/session/browse")
    async def browse(path: str = ""):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        require_view_c()
        return action(lambda: enrollment.browse(path))

    @app.post("/api/session/root")
    def select_root(selection: FolderSelection):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(lambda: enrollment.select_parent(selection.path))

    @app.post("/api/session/folders")
    def create_folder(request: FolderCreation):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        require_view_c()
        return action(
            lambda: enrollment.create_folder(request.parent_path, request.name)
        )

    @app.post("/api/session/pseudonym/regenerate")
    def regenerate_pseudonym():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        require_view_c()
        return action(enrollment.generate_pseudonym)

    @app.post("/api/session/new")
    def create_participant(form: ParticipantForm):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(lambda: enrollment.create_participant(form.dict()))

    @app.post("/api/session/resume")
    def resume_participant(request: ResumeRequest):
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(
            lambda: enrollment.resume_participant(
                request.pseudonym, request.acknowledge_mismatch
            )
        )

    @app.post("/api/session/cancel")
    def cancel_participant():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(enrollment.cancel)

    @app.post("/api/session/reset")
    def reset_session():
        if enrollment is None:
            raise HTTPException(status_code=404, detail="View C is not configured.")
        return enrollment_action(enrollment.reset)

    @app.post("/api/session/launch")
    def launch_session():
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

    @app.post("/api/experiment/stack/start")
    def start_experiment_stack():
        return experiment_action(lambda: experiment.manage_experiment_stack("start"))

    @app.post("/api/experiment/stack/stop")
    def stop_experiment_stack():
        return experiment_action(lambda: experiment.manage_experiment_stack("stop"))

    @app.post("/api/experiment/stack/restart")
    def restart_experiment_stack_between_blocks():
        return experiment_action(lambda: experiment.manage_experiment_stack("restart"))

    @app.post("/api/experiment/presentation/show")
    def show_presentation():
        return experiment_action(lambda: experiment.show_presentation())

    @app.post("/api/experiment/presentation/complete")
    def complete_presentation():
        return experiment_action(lambda: experiment.complete_presentation())

    @app.post("/api/experiment/blocks/{block_id}/start")
    def start_experiment_block(block_id: str):
        return experiment_action(lambda: experiment.start(block_id))

    @app.post("/api/experiment/blocks/{block_id}/explanation/show")
    def show_mode_explanation(block_id: str):
        return experiment_action(lambda: experiment.show_mode_explanation(block_id))

    @app.post("/api/experiment/blocks/{block_id}/explanation/complete")
    def complete_mode_explanation(block_id: str):
        return experiment_action(lambda: experiment.complete_mode_explanation(block_id))

    @app.post("/api/experiment/blocks/{block_id}/end")
    def end_experiment_block(block_id: str, request: EndBlockRequest):
        return experiment_action(lambda: experiment.end(block_id, request.confirmed))

    @app.post("/api/experiment/blocks/{block_id}/abort")
    def abort_experiment_block(block_id: str):
        return experiment_action(lambda: experiment.abort(block_id))

    @app.post("/api/experiment/blocks/{block_id}/control")
    def set_experiment_control(block_id: str, request: ControlRequest):
        return experiment_action(
            lambda: experiment.set_control(block_id, request.active)
        )

    @app.post("/api/experiment/blocks/{block_id}/restart-stack")
    def restart_experiment_stack(block_id: str):
        return experiment_action(lambda: experiment.restart_stack(block_id))

    @app.post("/api/experiment/blocks/{block_id}/go-to")
    def start_experiment_go_to(block_id: str, request: GoToRequest):
        return experiment_action(
            lambda: experiment.start_go_to(block_id, request.pose_id)
        )

    @app.post("/api/experiment/blocks/{block_id}/go-to/stop")
    def stop_experiment_go_to(block_id: str):
        return experiment_action(lambda: experiment.stop_go_to(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/prepare")
    def prepare_training_trial(block_id: str):
        return experiment_action(lambda: experiment.prepare_training_trial(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/ready")
    def confirm_training_ready(block_id: str):
        return experiment_action(
            lambda: experiment.training_participant_ready(block_id)
        )

    @app.post("/api/experiment/blocks/{block_id}/training/start")
    def start_training_attempt(block_id: str):
        return experiment_action(lambda: experiment.start_training_attempt(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/stop")
    def stop_training_attempt(block_id: str):
        return experiment_action(lambda: experiment.stop_training_attempt(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/incidents")
    def add_training_incident(block_id: str):
        return experiment_action(lambda: experiment.add_training_incident(block_id))

    @app.post("/api/experiment/blocks/{block_id}/training/incidents/review")
    def review_training_incidents(
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
    def resolve_training_attempt(
        block_id: str, request: TrainingResolutionRequest
    ):
        return experiment_action(
            lambda: experiment.resolve_training_attempt(
                block_id, request.decision
            )
        )

    @app.post("/api/experiment/blocks/{block_id}/recording/prepare")
    def prepare_recording_trial(block_id: str):
        return experiment_action(lambda: experiment.prepare_recording_trial(block_id))

    @app.post("/api/experiment/blocks/{block_id}/recording/ready")
    def confirm_recording_ready(block_id: str):
        return experiment_action(
            lambda: experiment.recording_participant_ready(block_id)
        )

    @app.post("/api/experiment/blocks/{block_id}/recording/start")
    def start_recording_attempt(block_id: str):
        return experiment_action(lambda: experiment.start_recording_attempt(block_id))

    @app.post("/api/experiment/blocks/{block_id}/recording/stop")
    def stop_recording_attempt(block_id: str):
        return experiment_action(lambda: experiment.stop_recording_attempt(block_id))

    @app.post("/api/experiment/blocks/{block_id}/recording/incidents")
    def add_recording_incident(block_id: str):
        return experiment_action(lambda: experiment.add_recording_incident(block_id))

    @app.post("/api/experiment/blocks/{block_id}/recording/incidents/review")
    def review_recording_incidents(
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
    def resolve_recording_attempt(
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
            while not app.state.shutting_down:
                current = await asyncio.to_thread(combined_snapshot)
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

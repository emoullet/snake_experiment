"""FastAPI surface for View A."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .calibration import CalibrationError
from .mode_manager import ModeError
from .stack_manager import StackError


def create_app(
    calibration_state,
    mode_manager,
    stack_manager,
    storage,
    static_directory: Path,
    template_directory: Path,
    pose_topic: str,
) -> FastAPI:
    """Create the View A application around injectable services."""
    app = FastAPI(title="Snake Experiment Calibration", version="1.0")
    # Colcon's --symlink-install places data files outside the installed share
    # directory. Allow those package-managed links so the browser can load the
    # view's JavaScript and CSS during development builds.
    app.mount(
        "/static",
        StaticFiles(directory=static_directory, follow_symlink=True),
        name="static",
    )

    def snapshot() -> dict:
        state = calibration_state.snapshot()
        state["mode"] = mode_manager.snapshot()
        state["stack"] = stack_manager.snapshot()
        state["pose_topic"] = pose_topic
        state["calibration_directory"] = str(storage.directory)
        return state

    def operator_action(action: Callable[[], object]):
        try:
            return action()
        except (CalibrationError, ModeError, StackError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.get("/", include_in_schema=False)
    async def calibration_home():
        return FileResponse(template_directory / "index.html")

    @app.get("/api/state")
    async def get_state():
        return snapshot()

    @app.post("/api/modes/{mode}")
    async def activate_mode(mode: str):
        operator_action(lambda: mode_manager.activate(mode))
        return snapshot()

    @app.post("/api/modes/{mode}/deactivate")
    async def deactivate_mode(mode: str):
        operator_action(lambda: mode_manager.deactivate(mode))
        return snapshot()

    @app.post("/api/stack/{action}")
    async def control_stack(action: str):
        if action == "start":
            operator_action(stack_manager.start)
        elif action == "stop":
            operator_action(stack_manager.stop)
        else:
            raise HTTPException(status_code=404, detail="Unknown stack action.")
        return snapshot()

    @app.post("/api/poses/{pose_id}")
    async def capture_pose(pose_id: str):
        operator_action(lambda: calibration_state.capture(pose_id))
        return snapshot()

    @app.post("/api/calibrations")
    async def save_calibration():
        document = operator_action(calibration_state.calibration_document)
        path = operator_action(lambda: storage.save(document))
        return {
            "file_name": path.name,
            "state": snapshot(),
        }

    @app.websocket("/ws")
    async def state_websocket(websocket: WebSocket):
        await websocket.accept()
        previous = None
        try:
            while True:
                current = snapshot()
                serialised = json.dumps(current, sort_keys=True)
                if serialised != previous:
                    await websocket.send_json(current)
                    previous = serialised
                await asyncio.sleep(0.25)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app

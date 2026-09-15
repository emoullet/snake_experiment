"""FastAPI surface for the independent Panels B-E interface."""

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
from .mode_manager import ModeError
from .stack_manager import StackError


class Confirmation(BaseModel):
    accepted: bool


def create_session_app(checkup, static_directory: Path, template_directory: Path):
    """Create the Panel B-E app around an injectable CheckupController."""
    app = FastAPI(title="Snake Experiment Session Interface", version="1.0")
    app.mount(
        "/session-static",
        StaticFiles(directory=static_directory, follow_symlink=True),
        name="session-static",
    )

    def action(callback):
        try:
            return callback()
        except (CheckupError, DiagnosticProfileError, ModeError, StackError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.get("/", include_in_schema=False)
    async def session_interface():
        return FileResponse(template_directory / "session_index.html")

    @app.get("/api/state")
    async def state():
        return checkup.snapshot()

    @app.post("/api/stack/{command}")
    async def stack(command: str):
        if command == "start":
            return action(checkup.start_stack)
        if command == "stop":
            return action(checkup.stop_stack)
        raise HTTPException(status_code=404, detail="Unknown stack action.")

    @app.post("/api/checkup/modes/{mode}/start")
    async def start_mode(mode: str):
        return action(lambda: checkup.start_mode(mode))

    @app.post("/api/checkup/modes/{mode}/retry")
    async def retry_mode(mode: str):
        return action(lambda: checkup.retry_mode(mode))

    @app.post("/api/checkup/modes/{mode}/confirm")
    async def confirm_mode(mode: str, confirmation: Confirmation):
        return action(lambda: checkup.confirm_mode(mode, confirmation.accepted))

    @app.post("/api/checkup/validate")
    async def validate():
        return action(checkup.validate)

    @app.post("/api/checkup/reset")
    async def reset():
        return action(checkup.reset)

    @app.websocket("/ws")
    async def state_websocket(websocket: WebSocket):
        await websocket.accept()
        previous = None
        try:
            while True:
                current = checkup.snapshot()
                serialised = json.dumps(current, sort_keys=True)
                if serialised != previous:
                    await websocket.send_json(current)
                    previous = serialised
                await asyncio.sleep(0.25)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app

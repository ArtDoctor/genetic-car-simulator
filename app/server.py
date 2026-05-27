from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from typing import Any

from fastapi import Depends, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .sim import SimulationManager

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SESSION_COOKIE_NAME = "gcs_session"
SESSION_TTL_SECONDS = 2 * 60 * 60
SESSION_CLEANUP_SECONDS = 5 * 60
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass
class SessionEntry:
    manager: SimulationManager
    last_seen: float


class SessionStore:
    """In-memory per-visitor simulation managers.

    The old server used one global SimulationManager, so every browser fought over
    the same population, road, and running state. Keeping a manager per visitor is
    the smallest backend change because the existing Python simulation stays as-is;
    the browser just gets a session cookie and all API/WebSocket traffic resolves
    to that visitor's manager.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionEntry] = {}
        self._lock = asyncio.Lock()

    def _valid_session_id(self, session_id: str | None) -> str | None:
        if session_id and SESSION_ID_RE.fullmatch(session_id):
            return session_id
        return None

    def _new_session_id(self) -> str:
        return token_urlsafe(32)

    def _set_cookie(self, response: Response, session_id: str) -> None:
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
        )

    async def get_for_http(self, request: Request, response: Response) -> tuple[str, SimulationManager]:
        session_id = self._valid_session_id(request.cookies.get(SESSION_COOKIE_NAME))
        if session_id is None:
            session_id = self._new_session_id()

        now = time.time()
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                entry = SessionEntry(manager=SimulationManager(), last_seen=now)
                self._sessions[session_id] = entry
            else:
                entry.last_seen = now

        self._set_cookie(response, session_id)
        await entry.manager.ensure_loop()
        return session_id, entry.manager

    async def get_for_websocket(self, websocket: WebSocket) -> tuple[str, SimulationManager]:
        session_id = self._valid_session_id(websocket.cookies.get(SESSION_COOKIE_NAME))
        session_id = session_id or self._valid_session_id(websocket.query_params.get("session"))
        if session_id is None:
            session_id = self._new_session_id()

        now = time.time()
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                entry = SessionEntry(manager=SimulationManager(), last_seen=now)
                self._sessions[session_id] = entry
            else:
                entry.last_seen = now

        await entry.manager.ensure_loop()
        return session_id, entry.manager

    async def touch(self, session_id: str) -> None:
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                entry.last_seen = time.time()

    async def cleanup_once(self) -> None:
        cutoff = time.time() - SESSION_TTL_SECONDS
        expired: list[SessionEntry] = []
        async with self._lock:
            for session_id, entry in list(self._sessions.items()):
                if entry.last_seen < cutoff:
                    expired.append(self._sessions.pop(session_id))

        for entry in expired:
            await entry.manager.close()

    async def cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(SESSION_CLEANUP_SECONDS)
            await self.cleanup_once()

    async def close_all(self) -> None:
        async with self._lock:
            entries = list(self._sessions.values())
            self._sessions.clear()
        for entry in entries:
            await entry.manager.close()


sessions = SessionStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(sessions.cleanup_loop())
    app.state.cleanup_task = cleanup_task
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        await sessions.close_all()


app = FastAPI(title="Genetic Car Simulator Prototype", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class SpeedPayload(BaseModel):
    speed: float


class EvolvePayload(BaseModel):
    elite_count: int = 2
    copy_count: int = 1
    mutation_rate: float = 0.22


class MapPayload(BaseModel):
    preset: str
    seed: int | None = None


class AutoEvolvePayload(BaseModel):
    enabled: bool
    elite_count: int = 2
    copy_count: int = 1
    mutation_rate: float = 0.22


async def current_manager(request: Request, response: Response) -> SimulationManager:
    _, manager = await sessions.get_for_http(request, response)
    return manager


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/session")
async def create_session(request: Request, response: Response) -> dict[str, str]:
    session_id, _ = await sessions.get_for_http(request, response)
    return {"sessionId": session_id}


@app.get("/api/state")
async def state(manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    return await manager.snapshot()


@app.get("/api/road")
async def road(manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    return manager.road.to_dict()


@app.get("/api/random-car")
async def random_car(seed: int | None = None, manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    gene = await manager.random_car(seed=seed)
    return gene.to_dict()


@app.post("/api/randomize")
async def randomize(seed: int | None = None, manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    await manager.randomize(seed=seed)
    return await manager.snapshot()


@app.post("/api/start")
async def start(manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    await manager.start()
    return await manager.snapshot()


@app.post("/api/pause")
async def pause(manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    await manager.pause(True)
    return await manager.snapshot()


@app.post("/api/resume")
async def resume(manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    await manager.pause(False)
    return await manager.snapshot()


@app.post("/api/speed")
async def speed(payload: SpeedPayload, manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    await manager.set_speed(payload.speed)
    return await manager.snapshot()


@app.post("/api/map")
async def set_map(payload: MapPayload, manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    await manager.set_map(payload.preset, payload.seed)
    return await manager.snapshot()


@app.post("/api/evolve")
async def evolve(payload: EvolvePayload, manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    await manager.evolve(payload.elite_count, payload.copy_count, payload.mutation_rate)
    return await manager.snapshot()


@app.post("/api/auto-evolve")
async def auto_evolve(payload: AutoEvolvePayload, manager: SimulationManager = Depends(current_manager)) -> dict[str, Any]:
    await manager.set_auto_evolve(payload.enabled, payload.elite_count, payload.copy_count, payload.mutation_rate)
    return await manager.snapshot()


@app.websocket("/ws/sim")
async def sim_ws(websocket: WebSocket) -> None:
    session_id, manager = await sessions.get_for_websocket(websocket)
    await websocket.accept()
    try:
        while True:
            await sessions.touch(session_id)
            await websocket.send_json(await manager.snapshot())
            await asyncio.sleep(1 / 30)
    except WebSocketDisconnect:
        return


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run("app.server:app", host="0.0.0.0", port=int(os.getenv("PORT", "18473")), reload=True)

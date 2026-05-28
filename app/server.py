from __future__ import annotations

import asyncio
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from typing import Any

from fastapi import Depends, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .leaderboard import LeaderboardStore
from .sim_process import MAX_RUNNING_SIMULATIONS, SimulationCapacityError, SimulationProcess

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SESSION_COOKIE_NAME = "gcs_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", str(24 * 60 * 60)))
SESSION_CLEANUP_SECONDS = int(os.getenv("SESSION_CLEANUP_SECONDS", str(10 * 60)))
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass
class SessionEntry:
    manager: SimulationProcess
    last_seen: float
    active_websockets: int = 0


class SessionStore:
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

    async def get_for_http(self, request: Request, response: Response) -> tuple[str, SimulationProcess]:
        session_id = self._valid_session_id(request.cookies.get(SESSION_COOKIE_NAME))
        if session_id is None:
            session_id = self._new_session_id()

        now = time.time()
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                entry = SessionEntry(manager=SimulationProcess(), last_seen=now)
                self._sessions[session_id] = entry
            else:
                entry.last_seen = now

        self._set_cookie(response, session_id)
        await entry.manager.ensure_loop()
        return session_id, entry.manager

    async def get_for_websocket(self, websocket: WebSocket) -> tuple[str, SimulationProcess]:
        session_id = self._valid_session_id(websocket.cookies.get(SESSION_COOKIE_NAME))
        session_id = session_id or self._valid_session_id(websocket.query_params.get("session"))
        if session_id is None:
            session_id = self._new_session_id()

        now = time.time()
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                entry = SessionEntry(manager=SimulationProcess(), last_seen=now)
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

    async def websocket_connected(self, session_id: str) -> None:
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                entry.active_websockets += 1
                entry.last_seen = time.time()

    async def websocket_disconnected(self, session_id: str) -> None:
        manager_to_pause: SimulationProcess | None = None
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                entry.active_websockets = max(0, entry.active_websockets - 1)
                entry.last_seen = time.time()
                if entry.active_websockets == 0:
                    manager_to_pause = entry.manager

        if manager_to_pause is not None:
            with suppress(Exception):
                await manager_to_pause.pause(True)

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
leaderboard = LeaderboardStore()


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


app = FastAPI(title="Genetic Car Simulator", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(SimulationCapacityError)
async def simulation_capacity_error(_: Request, exc: SimulationCapacityError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc), "maxRunningSimulations": MAX_RUNNING_SIMULATIONS},
    )


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


class ImportCarPayload(BaseModel):
    gene: dict[str, Any]
    index: int


class LeaderboardNamePayload(BaseModel):
    display_name: str


async def current_manager(request: Request, response: Response) -> SimulationProcess:
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
async def state(manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    return await manager.snapshot()


@app.get("/api/road")
async def road(manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    return await manager.road()


@app.get("/api/leaderboard")
async def leaderboard_state(request: Request, response: Response) -> dict[str, Any]:
    session_id, _ = await sessions.get_for_http(request, response)
    return await leaderboard.snapshot(session_id)


@app.get("/api/random-car")
async def random_car(manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    return await manager.random_car()


@app.post("/api/randomize")
async def randomize(manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    await manager.randomize()
    return await manager.snapshot()


@app.post("/api/start")
async def start(manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    await manager.start()
    return await manager.snapshot()


@app.post("/api/pause")
async def pause(manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    await manager.pause(True)
    return await manager.snapshot()


@app.post("/api/resume")
async def resume(manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    await manager.pause(False)
    return await manager.snapshot()


@app.post("/api/speed")
async def speed(payload: SpeedPayload, manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    await manager.set_speed(payload.speed)
    return await manager.snapshot()


@app.post("/api/map")
async def set_map(payload: MapPayload, manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    await manager.set_map(payload.preset, payload.seed)
    return await manager.snapshot()


@app.post("/api/evolve")
async def evolve(payload: EvolvePayload, manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    await manager.evolve(payload.elite_count, payload.copy_count, payload.mutation_rate)
    return await manager.snapshot()


@app.post("/api/import-car")
async def import_car(payload: ImportCarPayload, manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    await manager.import_car(payload.gene, payload.index)
    return await manager.snapshot()


@app.post("/api/leaderboard/name")
async def set_leaderboard_name(payload: LeaderboardNamePayload, request: Request, response: Response) -> dict[str, Any]:
    session_id, _ = await sessions.get_for_http(request, response)
    await leaderboard.set_display_name(session_id, payload.display_name)
    return await leaderboard.snapshot(session_id)


@app.post("/api/auto-evolve")
async def auto_evolve(payload: AutoEvolvePayload, manager: SimulationProcess = Depends(current_manager)) -> dict[str, Any]:
    await manager.set_auto_evolve(payload.enabled, payload.elite_count, payload.copy_count, payload.mutation_rate)
    return await manager.snapshot()


@app.websocket("/ws/sim")
async def sim_ws(websocket: WebSocket) -> None:
    session_id, manager = await sessions.get_for_websocket(websocket)
    await websocket.accept()
    await sessions.websocket_connected(session_id)
    last_touch = 0.0
    last_leaderboard_record = 0.0
    try:
        while True:
            now = time.time()
            if now - last_touch > 5.0:
                await sessions.touch(session_id)
                last_touch = now
            snapshot = await manager.snapshot()
            if now - last_leaderboard_record > 2.0:
                with suppress(Exception):
                    await leaderboard.record_snapshot(session_id, snapshot)
                last_leaderboard_record = now
            await websocket.send_json(snapshot)
            await asyncio.sleep(1 / 30)
    except WebSocketDisconnect:
        return
    except Exception:
        return
    finally:
        await sessions.websocket_disconnected(session_id)


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run("app.server:app", host="0.0.0.0", port=int(os.getenv("PORT", "18473")), reload=True)

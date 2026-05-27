from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .sim import SimulationManager

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

app = FastAPI(title="Genetic Car Simulator Prototype")
manager = SimulationManager()
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class SpeedPayload(BaseModel):
    speed: float


class EvolvePayload(BaseModel):
    elite_count: int = 2
    copy_count: int = 1
    mutation_rate: float = 0.22


@app.on_event("startup")
async def startup() -> None:
    await manager.ensure_loop()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
async def state() -> dict[str, Any]:
    return await manager.snapshot()


@app.get("/api/road")
async def road() -> dict[str, Any]:
    return manager.road.to_dict()


@app.get("/api/random-car")
async def random_car(seed: int | None = None) -> dict[str, Any]:
    gene = await manager.random_car(seed=seed)
    return gene.to_dict()


@app.post("/api/randomize")
async def randomize(seed: int | None = None) -> dict[str, Any]:
    await manager.randomize(seed=seed)
    return await manager.snapshot()


@app.post("/api/start")
async def start() -> dict[str, Any]:
    await manager.start()
    return await manager.snapshot()


@app.post("/api/pause")
async def pause() -> dict[str, Any]:
    await manager.pause(True)
    return await manager.snapshot()


@app.post("/api/resume")
async def resume() -> dict[str, Any]:
    await manager.pause(False)
    return await manager.snapshot()


@app.post("/api/speed")
async def speed(payload: SpeedPayload) -> dict[str, Any]:
    await manager.set_speed(payload.speed)
    return await manager.snapshot()


@app.post("/api/evolve")
async def evolve(payload: EvolvePayload) -> dict[str, Any]:
    await manager.evolve(payload.elite_count, payload.copy_count, payload.mutation_rate)
    return await manager.snapshot()


@app.websocket("/ws/sim")
async def sim_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(await manager.snapshot())
            await asyncio.sleep(1 / 24)
    except WebSocketDisconnect:
        return


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.server:app", host="0.0.0.0", port=8000, reload=True)

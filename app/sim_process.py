from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import queue
import time
import traceback
from multiprocessing.context import BaseContext
from typing import Any
from uuid import uuid4

from .sim import SimulationManager

COMMAND_TIMEOUT_SECONDS = 15.0
SHUTDOWN_TIMEOUT_SECONDS = 3.0
DEFAULT_MAX_RUNNING_SIMULATIONS = max(1, int((os.cpu_count() or 1) * 0.6))
MAX_RUNNING_SIMULATIONS = max(1, int(os.getenv("SIM_MAX_RUNNING", str(DEFAULT_MAX_RUNNING_SIMULATIONS))))
SIM_SLOT_WAIT_SECONDS = float(os.getenv("SIM_SLOT_WAIT_SECONDS", "0.25"))
_running_slots = asyncio.BoundedSemaphore(MAX_RUNNING_SIMULATIONS)


class SimulationCapacityError(RuntimeError):
    pass


def _multiprocessing_context() -> BaseContext:
    methods = mp.get_all_start_methods()
    return mp.get_context("fork" if "fork" in methods else methods[0])


async def _execute(manager: SimulationManager, command: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if command == "snapshot":
        return await manager.snapshot()
    if command == "road":
        return manager.road.to_dict()
    if command == "random_car":
        return (await manager.random_car(*args, **kwargs)).to_dict()
    if command == "start":
        await manager.start()
        return None
    if command == "pause":
        await manager.pause(*args, **kwargs)
        return None
    if command == "set_speed":
        await manager.set_speed(*args, **kwargs)
        return None
    if command == "randomize":
        await manager.randomize(*args, **kwargs)
        return None
    if command == "set_map":
        await manager.set_map(*args, **kwargs)
        return None
    if command == "evolve":
        await manager.evolve(*args, **kwargs)
        return None
    if command == "set_auto_evolve":
        await manager.set_auto_evolve(*args, **kwargs)
        return None
    if command == "import_car":
        await manager.import_car(*args, **kwargs)
        return None
    raise ValueError(f"unknown simulation command: {command}")


async def _worker_loop(command_queue: mp.Queue, response_queue: mp.Queue) -> None:
    manager = SimulationManager()
    await manager.ensure_loop()
    try:
        while True:
            try:
                request_id, command, args, kwargs = await asyncio.to_thread(command_queue.get, True, 0.1)
            except queue.Empty:
                continue

            if command == "__close__":
                await manager.close()
                response_queue.put((request_id, True, None))
                return

            try:
                response_queue.put((request_id, True, await _execute(manager, command, args, kwargs)))
            except BaseException:
                response_queue.put((request_id, False, traceback.format_exc()))
    finally:
        await manager.close()


def _worker_main(command_queue: mp.Queue, response_queue: mp.Queue) -> None:
    asyncio.run(_worker_loop(command_queue, response_queue))


class SimulationProcess:
    def __init__(self) -> None:
        self._ctx = _multiprocessing_context()
        self._command_queue: mp.Queue | None = None
        self._response_queue: mp.Queue | None = None
        self._process: mp.Process | None = None
        self._call_lock = asyncio.Lock()
        self._has_running_slot = False
        self._start_process()

    def _start_process(self) -> None:
        self._command_queue = self._ctx.Queue()
        self._response_queue = self._ctx.Queue()
        self._process = self._ctx.Process(
            target=_worker_main,
            args=(self._command_queue, self._response_queue),
            daemon=True,
        )
        self._process.start()

    async def ensure_loop(self) -> None:
        if self._process is None or not self._process.is_alive():
            self._release_running_slot()
            await self.close()
            self._start_process()

    def _release_running_slot(self) -> None:
        if self._has_running_slot:
            self._has_running_slot = False
            _running_slots.release()

    async def _acquire_running_slot(self) -> None:
        if self._has_running_slot:
            return
        try:
            await asyncio.wait_for(_running_slots.acquire(), timeout=SIM_SLOT_WAIT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise SimulationCapacityError(
                f"server is already running {MAX_RUNNING_SIMULATIONS} simulations; try again in a moment"
            ) from exc
        self._has_running_slot = True

    def _sync_slot_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        if self._has_running_slot and not snapshot.get("running") and not snapshot.get("autoEvolve"):
            self._release_running_slot()

    async def _call(self, command: str, *args: Any, timeout: float = COMMAND_TIMEOUT_SECONDS, **kwargs: Any) -> Any:
        await self.ensure_loop()
        assert self._command_queue is not None
        assert self._response_queue is not None
        assert self._process is not None

        async with self._call_lock:
            request_id = uuid4().hex
            await asyncio.to_thread(self._command_queue.put, (request_id, command, args, kwargs), True, timeout)
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"simulation worker timed out during {command}")
                try:
                    response_id, ok, payload = await asyncio.to_thread(self._response_queue.get, True, remaining)
                except queue.Empty as exc:
                    raise TimeoutError(f"simulation worker timed out during {command}") from exc
                if response_id != request_id:
                    continue
                if ok:
                    return payload
                raise RuntimeError(payload)

    async def close(self) -> None:
        self._release_running_slot()
        process = self._process
        command_queue = self._command_queue
        response_queue = self._response_queue
        self._process = None
        self._command_queue = None
        self._response_queue = None

        if process is None:
            return

        if process.is_alive() and command_queue is not None:
            request_id = uuid4().hex
            try:
                command_queue.put((request_id, "__close__", (), {}), block=False)
            except Exception:
                pass
            await asyncio.to_thread(process.join, SHUTDOWN_TIMEOUT_SECONDS)

        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, SHUTDOWN_TIMEOUT_SECONDS)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, SHUTDOWN_TIMEOUT_SECONDS)

        for q in (command_queue, response_queue):
            if q is not None:
                q.close()
                q.join_thread()

    async def snapshot(self) -> dict[str, Any]:
        snapshot = await self._call("snapshot")
        self._sync_slot_from_snapshot(snapshot)
        return snapshot

    async def road(self) -> dict[str, Any]:
        return await self._call("road")

    async def random_car(self) -> dict[str, Any]:
        return await self._call("random_car")

    async def start(self) -> None:
        await self._acquire_running_slot()
        try:
            await self._call("start")
        except BaseException:
            self._release_running_slot()
            raise

    async def pause(self, value: bool) -> None:
        if not value:
            await self._acquire_running_slot()
        try:
            await self._call("pause", value)
        except BaseException:
            if not value:
                self._release_running_slot()
            raise
        if value:
            self._release_running_slot()

    async def set_speed(self, speed: float) -> None:
        await self._call("set_speed", speed)

    async def randomize(self) -> None:
        await self._call("randomize")
        self._release_running_slot()

    async def set_map(self, preset: str, seed: int | None = None) -> None:
        await self._call("set_map", preset, seed)
        self._release_running_slot()

    async def evolve(self, elite_count: int = 2, copy_count: int = 1, mutation_rate: float = 0.22) -> None:
        await self._call("evolve", elite_count, copy_count, mutation_rate)
        self._release_running_slot()

    async def set_auto_evolve(
        self,
        enabled: bool,
        elite_count: int = 2,
        copy_count: int = 1,
        mutation_rate: float = 0.22,
    ) -> None:
        if enabled:
            await self._acquire_running_slot()
        try:
            await self._call("set_auto_evolve", enabled, elite_count, copy_count, mutation_rate)
        except BaseException:
            if enabled:
                self._release_running_slot()
            raise
        if not enabled:
            self._release_running_slot()

    async def import_car(self, gene_data: dict[str, Any], index: int) -> None:
        await self._call("import_car", gene_data, index)
        self._release_running_slot()

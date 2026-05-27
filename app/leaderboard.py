from __future__ import annotations

import asyncio
import json
import os
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from .road import ROAD_PRESETS

LEADERBOARD_LIMIT = 10
LEADERBOARD_FILE = Path(os.getenv("LEADERBOARD_FILE", "/app/data/leaderboard.json"))


class LeaderboardStore:
    """Persistent top-10 leaderboard, grouped by road preset.

    Stores at most one entry per visitor per map. The visitor id is a short hash of
    the session cookie, so the file has stable dedupe without storing the raw cookie.
    """

    def __init__(self, path: Path = LEADERBOARD_FILE, limit: int = LEADERBOARD_LIMIT) -> None:
        self.path = path
        self.limit = limit
        self._lock = asyncio.Lock()

    def _empty(self) -> dict[str, Any]:
        return {
            "version": 1,
            "updatedAt": None,
            "maps": {preset: [] for preset in ROAD_PRESETS.keys()},
        }

    def _user_id(self, session_id: str) -> str:
        return sha256(session_id.encode("utf-8")).hexdigest()[:12]

    def _load_sync(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return self._empty()

        if not isinstance(data, dict):
            return self._empty()
        data.setdefault("version", 1)
        data.setdefault("updatedAt", None)
        maps = data.setdefault("maps", {})
        for preset in ROAD_PRESETS.keys():
            entries = maps.get(preset, [])
            maps[preset] = entries if isinstance(entries, list) else []
        return data

    def _save_sync(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        tmp.replace(self.path)

    def _best_entry_from_snapshot(self, session_id: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        road = snapshot.get("road") or {}
        preset = road.get("preset")
        if preset not in ROAD_PRESETS:
            return None

        population = snapshot.get("population") or []
        if not population:
            return None

        genes_by_id = {gene.get("id"): gene for gene in population if isinstance(gene, dict)}
        cars = [car for car in (snapshot.get("cars") or []) if isinstance(car, dict)]
        best_car = max(cars, key=lambda car: float(car.get("fitness") or 0), default=None)
        if best_car and float(best_car.get("fitness") or 0) > 0:
            best = dict(genes_by_id.get(best_car.get("id")) or {})
            if not best:
                return None
            fitness = float(best_car.get("fitness") or 0)
            distance = max(0.0, float(best_car.get("maxX") or 4.0) - 4.0)
            best["fitness"] = round(fitness, 3)
            best["distance"] = round(distance, 3)
            best["time_alive"] = round(float(snapshot.get("simTime") or 0), 3)
        else:
            best = max(population, key=lambda gene: float(gene.get("fitness") or 0), default=None)
            if not best:
                return None
            fitness = float(best.get("fitness") or 0)
            distance = float(best.get("distance") or 0)

        # Avoid filling the leaderboard with untouched/random cars before any run.
        if fitness <= 0 and distance <= 0:
            return None

        user_id = self._user_id(session_id)
        now = int(time.time())
        return {
            "userId": user_id,
            "displayName": f"visitor-{user_id[:6]}",
            "map": preset,
            "mapLabel": ROAD_PRESETS[preset]["label"],
            "fitness": round(fitness, 3),
            "distance": round(distance, 3),
            "generation": int(snapshot.get("generation") or best.get("generation") or 0),
            "carId": best.get("id"),
            "recordedAt": now,
            "gene": best,
        }

    def _upsert_entry_sync(self, data: dict[str, Any], entry: dict[str, Any]) -> bool:
        maps = data.setdefault("maps", {})
        entries = maps.setdefault(entry["map"], [])
        user_id = entry["userId"]

        previous = next((item for item in entries if item.get("userId") == user_id), None)
        if previous and float(previous.get("fitness") or 0) >= float(entry.get("fitness") or 0):
            return False

        entries = [item for item in entries if item.get("userId") != user_id]
        entries.append(entry)
        entries.sort(key=lambda item: (float(item.get("fitness") or 0), float(item.get("distance") or 0)), reverse=True)
        maps[entry["map"]] = entries[: self.limit]
        data["updatedAt"] = int(time.time())
        return True

    async def record_snapshot(self, session_id: str, snapshot: dict[str, Any]) -> bool:
        entry = self._best_entry_from_snapshot(session_id, snapshot)
        if entry is None:
            return False
        async with self._lock:
            data = await asyncio.to_thread(self._load_sync)
            changed = self._upsert_entry_sync(data, entry)
            if changed:
                await asyncio.to_thread(self._save_sync, data)
            return changed

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            data = await asyncio.to_thread(self._load_sync)
        maps = data.get("maps", {})
        return {
            "updatedAt": data.get("updatedAt"),
            "limit": self.limit,
            "maps": [
                {
                    "id": preset,
                    "label": ROAD_PRESETS[preset]["label"],
                    "entries": maps.get(preset, [])[: self.limit],
                }
                for preset in ROAD_PRESETS.keys()
            ],
        }

from __future__ import annotations

import asyncio
import json
import os
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

DISPLAY_NAME_MAX_LENGTH = 28
DISPLAY_NAME_REPLACEMENTS = str.maketrans({"\n": " ", "\r": " ", "\t": " "})

from .road import ROAD_PRESETS

LEADERBOARD_LIMIT = 10
LEADERBOARD_FILE = Path(os.getenv("LEADERBOARD_FILE", "/app/data/leaderboard.json"))


class LeaderboardStore:
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

    def user_id(self, session_id: str) -> str:
        return sha256(session_id.encode("utf-8")).hexdigest()[:12]

    def _clean_display_name(self, display_name: str, fallback_user_id: str) -> str:
        name = " ".join(display_name.translate(DISPLAY_NAME_REPLACEMENTS).strip().split())
        if not name:
            return f"visitor-{fallback_user_id[:6]}"
        return name[:DISPLAY_NAME_MAX_LENGTH]

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
        profiles = data.setdefault("profiles", {})
        if not isinstance(profiles, dict):
            data["profiles"] = {}
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

        if fitness <= 0 and distance <= 0:
            return None

        user_id = self.user_id(session_id)
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

        profiles = data.setdefault("profiles", {})
        if isinstance(profiles, dict) and profiles.get(user_id):
            entry["displayName"] = profiles[user_id]

        previous = next((item for item in entries if item.get("userId") == user_id), None)
        if previous:
            entry["displayName"] = previous.get("displayName") or entry["displayName"]
            if isinstance(profiles, dict) and profiles.get(user_id):
                previous["displayName"] = profiles[user_id]
                entry["displayName"] = profiles[user_id]
            if float(previous.get("fitness") or 0) >= float(entry.get("fitness") or 0):
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

    async def set_display_name(self, session_id: str, display_name: str) -> bool:
        user_id = self.user_id(session_id)
        clean_name = self._clean_display_name(display_name, user_id)
        async with self._lock:
            data = await asyncio.to_thread(self._load_sync)
            profiles = data.setdefault("profiles", {})
            changed = not isinstance(profiles, dict) or profiles.get(user_id) != clean_name
            if isinstance(profiles, dict):
                profiles[user_id] = clean_name
            else:
                data["profiles"] = {user_id: clean_name}
            for entries in (data.get("maps") or {}).values():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if entry.get("userId") == user_id and entry.get("displayName") != clean_name:
                        entry["displayName"] = clean_name
                        changed = True
            if changed:
                data["updatedAt"] = int(time.time())
                await asyncio.to_thread(self._save_sync, data)
            return changed

    async def snapshot(self, current_session_id: str | None = None) -> dict[str, Any]:
        current_user_id = self.user_id(current_session_id) if current_session_id else None
        async with self._lock:
            data = await asyncio.to_thread(self._load_sync)
        maps = data.get("maps", {})
        return {
            "updatedAt": data.get("updatedAt"),
            "limit": self.limit,
            "currentUserId": current_user_id,
            "maps": [
                {
                    "id": preset,
                    "label": ROAD_PRESETS[preset]["label"],
                    "entries": [
                        {**entry, "isCurrentUser": bool(current_user_id and entry.get("userId") == current_user_id)}
                        for entry in maps.get(preset, [])[: self.limit]
                    ],
                }
                for preset in ROAD_PRESETS.keys()
            ],
        }

from __future__ import annotations

import bisect
import math
import random
from typing import Tuple

from pydantic import BaseModel, PrivateAttr


ROAD_PRESETS = {
    "easy": {
        "label": "Easy rolling test road",
        "hill": 0.45,
        "random_slope": 0.16,
        "max_slope": 0.62,
        "ragged": 0.12,
        "obstacle_min": 0.12,
        "obstacle_max": 0.65,
        "obstacle_spacing": (10.0, 19.0),
        "obstacle_width": (0.85, 2.7),
        "up_bias": 0.48,
    },
    "mixed": {
        "label": "Mixed evolution road",
        "hill": 0.72,
        "random_slope": 0.24,
        "max_slope": 0.82,
        "ragged": 0.18,
        "obstacle_min": 0.18,
        "obstacle_max": 0.95,
        "obstacle_spacing": (8.0, 16.0),
        "obstacle_width": (0.75, 2.5),
        "up_bias": 0.56,
    },
    "brutal": {
        "label": "Brutal rollover gauntlet",
        "hill": 1.0,
        "random_slope": 0.42,
        "max_slope": 1.25,
        "ragged": 0.30,
        "obstacle_min": 0.38,
        "obstacle_max": 1.55,
        "obstacle_spacing": (5.5, 12.0),
        "obstacle_width": (0.45, 1.9),
        "up_bias": 0.68,
    },
}


class Road(BaseModel):
    seed: int = 1337
    preset: str = "easy"
    length: float = 650.0
    dx: float = 0.75
    width: float = 26.0
    samples: list[Tuple[float, float]] | None = None

    _xs: list[float] = PrivateAttr(default_factory=list)
    _ys: list[float] = PrivateAttr(default_factory=list)

    def model_post_init(self, __context: object) -> None:
        if self.samples is None:
            self.samples = self._generate()
        self._xs = [p[0] for p in self.samples]
        self._ys = [p[1] for p in self.samples]

    def _generate(self) -> list[Tuple[float, float]]:
        rng = random.Random(self.seed)
        params = ROAD_PRESETS.get(self.preset, ROAD_PRESETS["mixed"])
        count = int(self.length / self.dx) + 1
        samples: list[Tuple[float, float]] = []

        # Pre-generate narrow triangular teeth/rocks. They are constant across the
        # road width but abrupt along x, so short wheelbases and tall cars flip.
        obstacles: list[tuple[float, float, float, float]] = []
        x = 24.0
        spacing_min, spacing_max = params["obstacle_spacing"]
        width_min, width_max = params["obstacle_width"]
        while x < self.length - 20:
            x += rng.uniform(spacing_min, spacing_max)
            half_width = rng.uniform(width_min, width_max)
            height = rng.choice([-1.0, 1.0]) * rng.uniform(params["obstacle_min"], params["obstacle_max"])
            # Most obstacles are upward rocks; some are sharp potholes.
            if rng.random() < params["up_bias"]:
                height = abs(height)
            skew = rng.uniform(-0.45, 0.45)
            obstacles.append((x, half_width, height, skew))

        y = 0.0
        slope = 0.0
        for i in range(count):
            x = i * self.dx
            # Rolling hills are only the base. The high-frequency random walk and
            # triangular obstacles make the terrain intentionally hostile.
            hill = params["hill"]
            target = hill * (
                1.1 * math.sin(x * 0.029)
                + 0.55 * math.sin(x * 0.081 + 1.1)
                + 0.18 * math.sin(x * 0.47 + 0.5)
            )
            slope = 0.76 * slope + 0.12 * (target - y) + rng.uniform(-params["random_slope"], params["random_slope"])
            slope = max(-params["max_slope"], min(params["max_slope"], slope))
            y += slope * self.dx
            rag = params["ragged"]
            ragged = rng.uniform(-rag, rag) + rag * 0.65 * math.sin(x * 2.9) + rag * 0.35 * math.sin(x * 7.1)

            obstacle_y = 0.0
            for ox, half_width, height, skew in obstacles:
                d = x - ox
                if abs(d) <= half_width:
                    left_w = half_width * (1.0 + max(0.0, skew))
                    right_w = half_width * (1.0 + max(0.0, -skew))
                    w = left_w if d < 0 else right_w
                    obstacle_y += height * max(0.0, 1.0 - abs(d) / max(0.15, w))

            # Keep a launch pad flat so all cars reach the hostile section fairly,
            # then blend the roughness in over a few meters.
            if x < 16.0:
                y = 0.0
                slope = 0.0
                ragged = 0.0
                obstacle_y = 0.0
                roughness = 0.0
            else:
                roughness = min(1.0, (x - 16.0) / 7.0)
            samples.append((x, y + (ragged + obstacle_y) * roughness))
        # Pin the whole road near y=0 around the start.
        origin_y = samples[0][1]
        return [(x, yy - origin_y) for x, yy in samples]

    def height(self, x: float) -> float:
        if x <= 0:
            return self._ys[0]
        if x >= self._xs[-1]:
            return self._ys[-1]
        j = bisect.bisect_right(self._xs, x)
        x0, y0 = self.samples[j - 1]
        x1, y1 = self.samples[j]
        t = (x - x0) / (x1 - x0)
        return y0 * (1 - t) + y1 * t

    def slope(self, x: float) -> float:
        eps = self.dx
        return (self.height(x + eps) - self.height(x - eps)) / (2 * eps)

    def tangent_normal(self, x: float) -> tuple[tuple[float, float], tuple[float, float]]:
        m = self.slope(x)
        inv = 1.0 / math.sqrt(1.0 + m * m)
        tangent = (inv, m * inv)
        normal = (-m * inv, inv)
        return tangent, normal

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "preset": self.preset,
            "length": self.length,
            "width": self.width,
            "samples": self.samples,
        }

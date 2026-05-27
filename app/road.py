from __future__ import annotations

import bisect
import math
import random
from typing import Tuple

from pydantic import BaseModel, PrivateAttr


class Road(BaseModel):
    seed: int = 1337
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
        count = int(self.length / self.dx) + 1
        samples: list[Tuple[float, float]] = []

        # Pre-generate narrow triangular teeth/rocks. They are constant across the
        # road width but abrupt along x, so short wheelbases and tall cars flip.
        obstacles: list[tuple[float, float, float, float]] = []
        x = 22.0
        while x < self.length - 20:
            x += rng.uniform(5.0, 12.0)
            half_width = rng.uniform(0.45, 1.9)
            height = rng.choice([-1.0, 1.0]) * rng.uniform(0.45, 1.75)
            # Most obstacles are upward rocks; some are sharp potholes.
            if rng.random() < 0.68:
                height = abs(height)
            skew = rng.uniform(-0.45, 0.45)
            obstacles.append((x, half_width, height, skew))

        y = 0.0
        slope = 0.0
        for i in range(count):
            x = i * self.dx
            # Rolling hills are only the base. The high-frequency random walk and
            # triangular obstacles make the terrain intentionally hostile.
            target = (
                1.25 * math.sin(x * 0.032)
                + 0.7 * math.sin(x * 0.091 + 1.1)
                + 0.28 * math.sin(x * 0.53 + 0.5)
            )
            slope = 0.72 * slope + 0.16 * (target - y) + rng.uniform(-0.48, 0.48)
            slope = max(-1.45, min(1.45, slope))
            y += slope * self.dx
            ragged = rng.uniform(-0.34, 0.34) + 0.22 * math.sin(x * 2.9) + 0.12 * math.sin(x * 7.1)

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
            "length": self.length,
            "width": self.width,
            "samples": self.samples,
        }

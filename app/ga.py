from __future__ import annotations

from copy import deepcopy
import math
import random
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

POPULATION_SIZE = 10
POWER_BUDGET = 110.0  # same available power for every car, in prototype physics units


class WheelGene(BaseModel):
    x: float
    y: float
    radius: float
    power_fraction: float


class CarGene(BaseModel):
    id: str
    generation: int
    body: list[list[float]]
    width: float
    density: float
    wheels: list[WheelGene]
    color: str
    lineage: str = "random"
    fitness: float = 0.0
    distance: float = 0.0
    time_alive: float = 0.0

    def copy_for_generation(self, generation: int, lineage: str) -> "CarGene":
        clone = self.model_copy(deep=True)
        clone.id = str(uuid4())[:8]
        clone.generation = generation
        clone.lineage = lineage
        clone.fitness = 0.0
        clone.distance = 0.0
        clone.time_alive = 0.0
        return clone

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data["used_power_fraction"] = sum(w.power_fraction for w in self.wheels)
        data["power_budget"] = POWER_BUDGET
        # Python-generated side-body mesh: the browser may render from this or
        # rebuild the same extrusion locally. Keeping it in the gene payload makes
        # the backend the source of truth for 3D generation.
        data["body_mesh"] = extruded_body_mesh(self.body, self.width)
        return data


def extruded_body_mesh(body: list[list[float]], width: float) -> dict[str, Any]:
    """Return a simple prism mesh for the 2D side profile with z-width."""
    half = width / 2.0
    vertices = [[x, y, -half] for x, y in body] + [[x, y, half] for x, y in body]
    n = len(body)
    faces: list[list[int]] = []
    for i in range(1, n - 1):
        faces.append([0, i, i + 1])
        faces.append([n, n + i + 1, n + i])
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j])
        faces.append([i, n + j, n + i])
    return {"vertices": vertices, "faces": faces}


def _hex_color(rng: random.Random) -> str:
    palette = ["#ff6b6b", "#feca57", "#48dbfb", "#1dd1a1", "#5f27cd", "#ff9ff3", "#54a0ff", "#c8d6e5"]
    return rng.choice(palette)


def _sort_polygon(points: list[list[float]]) -> list[list[float]]:
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


def _body_bounds(body: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in body]
    ys = [p[1] for p in body]
    return min(xs), max(xs), min(ys), max(ys)


def random_body(rng: random.Random) -> list[list[float]]:
    n = rng.randint(6, 9)
    pts: list[list[float]] = []
    for i in range(n):
        a = (i / n) * math.tau + rng.uniform(-0.18, 0.18)
        rx = rng.uniform(1.0, 1.9)
        ry = rng.uniform(0.35, 0.85)
        # Bias the top larger than the bottom so wheels usually fit under it.
        y_bias = 0.08 if math.sin(a) > 0 else -0.03
        pts.append([round(math.cos(a) * rx, 3), round(math.sin(a) * ry + y_bias, 3)])
    pts = _sort_polygon(pts)
    min_x, max_x, min_y, max_y = _body_bounds(pts)
    sx = max(2.0, max_x - min_x)
    sy = max(0.8, max_y - min_y)
    # Normalize into a stable local coordinate range while retaining raggedness.
    norm = []
    for x, y in pts:
        norm.append([round((x - (min_x + max_x) / 2) * (2.8 / sx), 3), round((y - (min_y + max_y) / 2) * (1.25 / sy), 3)])
    return norm


def lower_y_at(body: list[list[float]], x: float) -> float:
    # Intersect vertical line with polygon edges and return the lower crossing.
    hits: list[float] = []
    for i, p0 in enumerate(body):
        p1 = body[(i + 1) % len(body)]
        x0, y0 = p0
        x1, y1 = p1
        if abs(x1 - x0) < 1e-6:
            if abs(x - x0) < 0.04:
                hits.extend([y0, y1])
            continue
        if min(x0, x1) <= x <= max(x0, x1):
            t = (x - x0) / (x1 - x0)
            if 0 <= t <= 1:
                hits.append(y0 + t * (y1 - y0))
    return min(hits) if hits else min(p[1] for p in body)


def _cap_power(wheels: list[WheelGene], rng: random.Random | None = None) -> None:
    total_power = sum(w.power_fraction for w in wheels)
    if total_power > 1.0:
        scale = ((rng.uniform(0.75, 1.0) if rng else 1.0) / total_power)
        for w in wheels:
            w.power_fraction = round(w.power_fraction * scale, 3)


def repair_wheels(body: list[list[float]], wheels: list[WheelGene], rng: random.Random | None = None) -> list[WheelGene]:
    """Clamp and space wheels so their 2D circles never intersect."""
    if not wheels:
        return wheels
    wheels = sorted(wheels[:4], key=lambda w: w.x)
    min_x, max_x, _min_y, _max_y = _body_bounds(body)
    left = min_x + 0.14
    right = max_x - 0.14
    if right - left < 0.35:
        center = (min_x + max_x) / 2
        left, right = center - 0.175, center + 0.175
    n = len(wheels)
    available = right - left
    gap = min(0.08, available / max(12, n * 6))

    # If many wheels are requested, shrink them enough to physically fit across
    # the side silhouette. They may touch the body, but not each other.
    max_r = max(0.10, min(0.7, (available - gap * (n - 1)) / max(1, 2 * n)))
    for w in wheels:
        w.radius = round(clamp_float(w.radius, 0.10, max_r), 3)

    needed = sum(2 * w.radius for w in wheels) + gap * (n - 1)
    if needed > available:
        radius_budget = max(0.08 * n, available - gap * (n - 1))
        scale = radius_budget / max(1e-6, sum(2 * w.radius for w in wheels))
        for w in wheels:
            w.radius = round(max(0.04, w.radius * scale), 3)
        needed = sum(2 * w.radius for w in wheels) + gap * (n - 1)

    # Pack wheels with random slack gaps. This guarantees non-intersection even
    # after extreme body/wheel mutations while preserving wheel ordering.
    slack = max(0.0, available - needed)
    if rng:
        weights = [rng.random() + 0.15 for _ in range(n + 1)]
    else:
        weights = [1.0 for _ in range(n + 1)]
    weight_total = sum(weights) or 1.0
    slack_gaps = [slack * w / weight_total for w in weights]
    cursor = left + slack_gaps[0]
    for i, w in enumerate(wheels):
        cursor += w.radius
        w.x = round(cursor, 3)
        cursor += w.radius
        if i < n - 1:
            cursor += gap + slack_gaps[i + 1]

    for w in wheels:
        w.y = round(lower_y_at(body, w.x) - 0.075, 3)
        w.power_fraction = round(clamp_float(w.power_fraction, 0.0, 1.0), 3)
    _cap_power(wheels, rng)
    return wheels


def clamp_float(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def random_gene(generation: int = 0, rng: random.Random | None = None) -> CarGene:
    rng = rng or random.Random()
    body = random_body(rng)
    min_x, max_x, _min_y, _max_y = _body_bounds(body)
    wheel_count = rng.randint(2, 4)
    usage = rng.uniform(0.55, 1.0)
    weights = [rng.random() ** 1.3 for _ in range(wheel_count)]
    total = sum(weights) or 1.0
    span = max_x - min_x
    wheels = []
    for i, weight in enumerate(weights):
        slot_left = min_x + 0.18 + span * i / wheel_count
        slot_right = min_x + span * (i + 1) / wheel_count - 0.18
        x = rng.uniform(min(slot_left, slot_right), max(slot_left, slot_right))
        radius = rng.uniform(0.24, 0.58)
        wheels.append(
            WheelGene(
                x=round(x, 3),
                y=round(lower_y_at(body, x) - rng.uniform(0.02, 0.2), 3),
                radius=round(radius, 3),
                power_fraction=round(usage * weight / total, 3),
            )
        )
    wheels = repair_wheels(body, wheels, rng)
    return CarGene(
        id=str(uuid4())[:8],
        generation=generation,
        body=body,
        width=round(rng.uniform(0.75, 1.65), 3),
        density=round(rng.uniform(0.85, 1.45), 3),
        wheels=wheels,
        color=_hex_color(rng),
    )


def random_population(generation: int = 0, seed: int | None = None, size: int = POPULATION_SIZE) -> list[CarGene]:
    rng = random.Random(seed)
    return [random_gene(generation, rng) for _ in range(size)]


def _mut(v: float, rng: random.Random, amount: float, low: float, high: float) -> float:
    return max(low, min(high, v + rng.gauss(0, amount)))


def mutate(gene: CarGene, rng: random.Random, rate: float = 0.22, strength: float = 1.0) -> CarGene:
    g = gene.copy_for_generation(gene.generation, "mutation")
    g.color = gene.color
    if rng.random() < rate:
        g.width = round(_mut(g.width, rng, 0.18 * strength, 0.55, 1.9), 3)
    if rng.random() < rate:
        g.density = round(_mut(g.density, rng, 0.12 * strength, 0.65, 1.8), 3)
    for p in g.body:
        if rng.random() < rate:
            p[0] = round(_mut(p[0], rng, 0.16 * strength, -1.8, 1.8), 3)
        if rng.random() < rate:
            p[1] = round(_mut(p[1], rng, 0.12 * strength, -0.9, 0.9), 3)
    g.body = _sort_polygon(g.body)
    min_x, max_x, _min_y, _max_y = _body_bounds(g.body)
    for w in g.wheels:
        if rng.random() < rate:
            w.x = round(_mut(w.x, rng, 0.18 * strength, min_x + 0.1, max_x - 0.1), 3)
            w.y = round(lower_y_at(g.body, w.x) - abs(rng.gauss(0.08, 0.05)), 3)
        if rng.random() < rate:
            w.radius = round(_mut(w.radius, rng, 0.08 * strength, 0.18, 0.7), 3)
        if rng.random() < rate:
            w.power_fraction = round(_mut(w.power_fraction, rng, 0.12 * strength, 0.0, 1.0), 3)
    if rng.random() < rate * 0.25 and len(g.wheels) < 4:
        x = rng.uniform(min_x + 0.15, max_x - 0.15)
        g.wheels.append(
            WheelGene(
                x=round(x, 3),
                y=round(lower_y_at(g.body, x) - 0.1, 3),
                radius=round(rng.uniform(0.22, 0.58), 3),
                power_fraction=round(rng.uniform(0.05, 0.3), 3),
            )
        )
    if rng.random() < rate * 0.15 and len(g.wheels) > 2:
        g.wheels.pop(rng.randrange(len(g.wheels)))
    # Keep wheels valid after mutations: circles do not intersect, x stays on the
    # body, and total used power remains <= 100%.
    g.wheels = repair_wheels(g.body, g.wheels, rng)
    return g


def crossover(a: CarGene, b: CarGene, rng: random.Random, generation: int) -> CarGene:
    # Use one body as the topology and gently blend matching vertices where possible.
    base = deepcopy(a if rng.random() < 0.5 else b)
    other = b if base.id == a.id else a
    child = base.copy_for_generation(generation, f"crossover {a.id} x {b.id}")
    for i, p in enumerate(child.body):
        if i < len(other.body) and rng.random() < 0.55:
            p[0] = round((p[0] + other.body[i][0]) / 2 + rng.gauss(0, 0.035), 3)
            p[1] = round((p[1] + other.body[i][1]) / 2 + rng.gauss(0, 0.03), 3)
    child.width = round((a.width + b.width) / 2 + rng.gauss(0, 0.05), 3)
    child.density = round((a.density + b.density) / 2 + rng.gauss(0, 0.04), 3)
    max_wheels = max(len(a.wheels), len(b.wheels))
    wheels: list[WheelGene] = []
    for i in range(max_wheels):
        candidates = []
        if i < len(a.wheels):
            candidates.append(a.wheels[i])
        if i < len(b.wheels):
            candidates.append(b.wheels[i])
        if not candidates or rng.random() < 0.12:
            continue
        if len(candidates) == 2 and rng.random() < 0.6:
            wa, wb = candidates
            x = (wa.x + wb.x) / 2
            radius = (wa.radius + wb.radius) / 2
            power = (wa.power_fraction + wb.power_fraction) / 2
        else:
            wc = rng.choice(candidates)
            x, radius, power = wc.x, wc.radius, wc.power_fraction
        wheels.append(
            WheelGene(
                x=round(x, 3),
                y=round(lower_y_at(child.body, x) - 0.08, 3),
                radius=round(radius, 3),
                power_fraction=round(power, 3),
            )
        )
    if len(wheels) < 2:
        wheels = deepcopy((a.wheels if len(a.wheels) >= 2 else b.wheels)[:2])
    child.wheels = repair_wheels(child.body, wheels[:4], rng)
    return child


def tournament(population: list[CarGene], rng: random.Random, k: int = 3) -> CarGene:
    contenders = rng.sample(population, min(k, len(population)))
    return max(contenders, key=lambda g: g.fitness)


def evolve_population(
    population: list[CarGene],
    generation: int,
    seed: int | None = None,
    elite_count: int = 2,
    copy_count: int = 1,
    mutation_rate: float = 0.22,
) -> list[CarGene]:
    rng = random.Random(seed)
    ranked = sorted(population, key=lambda g: g.fitness, reverse=True)
    next_gen: list[CarGene] = []
    for elite in ranked[:elite_count]:
        next_gen.append(elite.copy_for_generation(generation, "elite"))
    for source in ranked[:copy_count]:
        clone = source.copy_for_generation(generation, "copy+small-mutation")
        next_gen.append(mutate(clone, rng, rate=mutation_rate * 0.45, strength=0.55))
    while len(next_gen) < POPULATION_SIZE:
        parent_a = tournament(ranked, rng)
        parent_b = tournament(ranked, rng)
        child = crossover(parent_a, parent_b, rng, generation)
        child = mutate(child, rng, rate=mutation_rate, strength=1.0)
        child.lineage = "crossover+mutation"
        next_gen.append(child)
    return next_gen[:POPULATION_SIZE]

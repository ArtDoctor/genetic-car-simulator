from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Any

from pydantic import BaseModel, Field

from .ga import CarGene, POWER_BUDGET, evolve_population, random_gene, random_population
from .road import ROAD_PRESETS, Road


def dot(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class SimCar(BaseModel):
    gene: CarGene
    lane_z: float
    index: int
    x: float = 4.0
    y: float = 1.7
    vx: float = 0.0
    vy: float = 0.0
    theta: float = 0.0
    omega: float = 0.0
    wheel_spin: list[float] = Field(default_factory=list)
    body_contacts: list[tuple[float, float]] = Field(default_factory=list)
    mass: float = 1.0
    inertia: float = 1.0
    max_x: float = 4.0
    last_progress_x: float = 4.0
    last_progress_time: float = 0.0
    done: bool = False
    reason: str = ""
    fitness: float = 0.0

    def model_post_init(self, __context: object) -> None:
        area = polygon_area(self.gene.body)
        self.mass = clamp(area * self.gene.width * self.gene.density * 12.0, 8.0, 55.0)
        span_x = max(p[0] for p in self.gene.body) - min(p[0] for p in self.gene.body)
        span_y = max(p[1] for p in self.gene.body) - min(p[1] for p in self.gene.body)
        self.inertia = max(1.0, self.mass * (span_x * span_x + span_y * span_y) / 8.0)
        self.wheel_spin = [0.0 for _ in self.gene.wheels]
        self.body_contacts = body_contact_points(self.gene.body)

    def local_to_world(self, p: list[float] | tuple[float, float]) -> tuple[float, float]:
        c = math.cos(self.theta)
        s = math.sin(self.theta)
        return (self.x + p[0] * c - p[1] * s, self.y + p[0] * s + p[1] * c)

    def apply_force(self, force: tuple[float, float], point_world: tuple[float, float], dt: float) -> None:
        self.vx += force[0] / self.mass * dt
        self.vy += force[1] / self.mass * dt
        rx = point_world[0] - self.x
        ry = point_world[1] - self.y
        torque = rx * force[1] - ry * force[0]
        self.omega += torque / self.inertia * dt

    def velocity_at(self, point_world: tuple[float, float]) -> tuple[float, float]:
        rx = point_world[0] - self.x
        ry = point_world[1] - self.y
        return (self.vx - self.omega * ry, self.vy + self.omega * rx)

    def step(self, road: Road, dt: float, sim_time: float, stall_seconds: float, max_time: float) -> None:
        if self.done:
            return
        self.vy -= 9.81 * dt
        # Air drag / angular damping keep the toy model numerically stable.
        self.vx *= (1.0 - min(0.08, 0.08 * dt))
        self.omega *= (1.0 - min(0.4, 0.65 * dt))

        # Wheel contacts: spring normal force plus motor force along terrain tangent.
        for i, wg in enumerate(self.gene.wheels):
            center = self.local_to_world((wg.x, wg.y))
            ground_y = road.height(center[0])
            tangent, normal = road.tangent_normal(center[0])
            penetration = ground_y + wg.radius - center[1]
            if penetration > 0:
                v_at = self.velocity_at(center)
                v_n = dot(v_at, normal)
                normal_force_mag = max(0.0, 620.0 * penetration - 36.0 * v_n)
                normal_force = (normal[0] * normal_force_mag, normal[1] * normal_force_mag)
                self.apply_force(normal_force, center, dt)

                v_t = dot(v_at, tangent)
                wheel_power = POWER_BUDGET * wg.power_fraction
                startup_bonus = 18.0 * wg.power_fraction
                motor_force_mag = wheel_power / max(1.5, abs(v_t)) + startup_bonus
                traction_limit = normal_force_mag * 0.95
                motor_force_mag = min(motor_force_mag, traction_limit)
                self.apply_force((tangent[0] * motor_force_mag, tangent[1] * motor_force_mag), center, dt)
                # Rolling resistance opposing current motion.
                resistance = clamp(v_t * 1.1, -traction_limit * 0.25, traction_limit * 0.25)
                self.apply_force((-tangent[0] * resistance, -tangent[1] * resistance), center, dt)
                self.wheel_spin[i] += (v_t / max(0.08, wg.radius)) * dt
            else:
                self.wheel_spin[i] += self.omega * dt

        # Body collision: sample vertices plus edge midpoints/thirds so sharp rocks
        # cannot tunnel through long polygon edges. Contact is high-friction and
        # off-center, making bad bodies flip instead of ghosting through terrain.
        for p in self.body_contacts:
            wp = self.local_to_world(p)
            ground_y = road.height(wp[0])
            clearance = 0.045
            if wp[1] < ground_y + clearance:
                tangent, normal = road.tangent_normal(wp[0])
                v_at = self.velocity_at(wp)
                pen = ground_y + clearance - wp[1]
                f_n = max(0.0, 1350.0 * pen - 58.0 * dot(v_at, normal))
                self.apply_force((normal[0] * f_n, normal[1] * f_n), wp, dt)
                vt = dot(v_at, tangent)
                # High body-ground friction: bad low bodies should scrape, slow down,
                # and catch on obstacles instead of sliding over them easily.
                scrape = clamp(vt * 18.0, -f_n * 1.85, f_n * 1.85)
                self.apply_force((-tangent[0] * scrape, -tangent[1] * scrape), wp, dt)

        self.x += self.vx * dt
        self.y += self.vy * dt
        self.theta += self.omega * dt
        self.theta = ((self.theta + math.pi) % (2 * math.pi)) - math.pi

        # Safety clamps for runaway numerical states.
        self.vx = clamp(self.vx, -35.0, 45.0)
        self.vy = clamp(self.vy, -45.0, 45.0)
        self.omega = clamp(self.omega, -10.0, 10.0)

        if self.x > self.max_x:
            self.max_x = self.x
        if self.max_x > self.last_progress_x + 0.35:
            self.last_progress_x = self.max_x
            self.last_progress_time = sim_time
        self.fitness = max(0.0, self.max_x - 4.0) + max(0.0, self.vx) * 0.08 + sim_time * 0.015

        if sim_time - self.last_progress_time > stall_seconds:
            self.done = True
            self.reason = "stalled"
        if sim_time > max_time:
            self.done = True
            self.reason = "time-limit"
        if self.y < road.height(self.x) - 10 or abs(self.theta) > math.pi * 0.92:
            self.done = True
            self.reason = "crashed"
        if self.max_x >= road.length - 8:
            self.done = True
            self.reason = "finished"

        if self.done:
            self.gene.fitness = round(self.fitness, 3)
            self.gene.distance = round(max(0.0, self.max_x - 4.0), 3)
            self.gene.time_alive = round(sim_time, 3)

    def state(self) -> dict[str, Any]:
        return {
            "id": self.gene.id,
            "index": self.index,
            "laneZ": self.lane_z,
            "x": self.x,
            "y": self.y,
            "theta": self.theta,
            "vx": self.vx,
            "maxX": self.max_x,
            "fitness": self.fitness,
            "done": self.done,
            "reason": self.reason,
            "bodyWorld": [self.local_to_world(p) for p in self.gene.body],
            "wheels": [
                {
                    "x": self.local_to_world((w.x, w.y))[0],
                    "y": self.local_to_world((w.x, w.y))[1],
                    "radius": w.radius,
                    "spin": self.wheel_spin[i],
                    "power": w.power_fraction,
                }
                for i, w in enumerate(self.gene.wheels)
            ],
        }


def body_contact_points(points: list[list[float]]) -> list[tuple[float, float]]:
    contacts: list[tuple[float, float]] = []
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        contacts.append((p[0], p[1]))
        contacts.append(((p[0] * 2 + q[0]) / 3, (p[1] * 2 + q[1]) / 3))
        contacts.append(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2))
        contacts.append(((p[0] + q[0] * 2) / 3, (p[1] + q[1] * 2) / 3))
    return contacts


def polygon_area(points: list[list[float]]) -> float:
    area = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        area += p[0] * q[1] - q[0] * p[1]
    return abs(area) * 0.5


class SimulationManager:
    def __init__(self) -> None:
        self.road = Road(seed=1337, preset="easy")
        self.generation = 0
        self.population: list[CarGene] = random_population(generation=0, seed=42)
        self.genealogy: list[dict[str, Any]] = []
        self.cars: list[SimCar] = []
        self.running = False
        self.sim_time = 0.0
        self.speed = 1.0
        self.stall_seconds = 3.5
        self.max_time = 35.0
        self.last_wall_tick = time.time()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last_seed = 1000
        self._record_generation()
        self.reset_cars()

    def _record_generation(self) -> None:
        entry = {
            "generation": self.generation,
            "cars": [
                {
                    "id": gene.id,
                    "color": gene.color,
                    "lineage": gene.lineage,
                    "reproduction": gene.reproduction,
                    "parentIds": gene.parent_ids,
                    "fitness": gene.fitness,
                    "distance": gene.distance,
                    "timeAlive": gene.time_alive,
                    "wheelCount": len(gene.wheels),
                    "usedPowerFraction": round(sum(w.power_fraction for w in gene.wheels), 3),
                    "body": gene.body,
                    "wheels": [w.model_dump() for w in gene.wheels],
                    "width": gene.width,
                }
                for gene in self.population
            ],
        }
        self.genealogy = [g for g in self.genealogy if g["generation"] != self.generation]
        self.genealogy.append(entry)
        self.genealogy.sort(key=lambda item: item["generation"])

    def reset_cars(self) -> None:
        lane_gap = self.road.width / max(1, len(self.population))
        start_z = -self.road.width / 2 + lane_gap / 2
        self.cars = []
        for i, gene in enumerate(self.population):
            gene.fitness = 0.0
            gene.distance = 0.0
            gene.time_alive = 0.0
            car = SimCar(gene=gene, lane_z=start_z + i * lane_gap, index=i)
            car.x = 4.0
            car.y = self.road.height(car.x) + 1.5 + i * 0.015
            car.last_progress_time = 0.0
            self.cars.append(car)
        self.sim_time = 0.0

    async def ensure_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        base_dt = 1.0 / 60.0
        while True:
            await asyncio.sleep(base_dt)
            async with self._lock:
                if not self.running:
                    continue
                speed = clamp(self.speed, 0.05, 40.0)
                substeps = max(1, min(80, math.ceil(speed)))
                step_dt = base_dt * speed / substeps
                for _ in range(substeps):
                    self.sim_time += step_dt
                    for car in self.cars:
                        car.step(self.road, step_dt, self.sim_time, self.stall_seconds, self.max_time)
                if all(car.done for car in self.cars):
                    self.running = False
                    self._finalize_scores()

    def _finalize_scores(self) -> None:
        for car in self.cars:
            car.gene.fitness = round(car.fitness, 3)
            car.gene.distance = round(max(0.0, car.max_x - 4.0), 3)
            car.gene.time_alive = round(self.sim_time, 3) if car.gene.time_alive == 0 else car.gene.time_alive
        self._record_generation()

    async def start(self) -> None:
        async with self._lock:
            self.reset_cars()
            self._record_generation()
            self.running = True
        await self.ensure_loop()

    async def pause(self, value: bool) -> None:
        async with self._lock:
            self.running = not value

    async def set_speed(self, speed: float) -> None:
        async with self._lock:
            self.speed = clamp(speed, 0.05, 40.0)

    async def randomize(self, seed: int | None = None) -> None:
        async with self._lock:
            self.generation = 0
            self._last_seed = seed if seed is not None else self._last_seed + 1
            self.population = random_population(generation=0, seed=self._last_seed)
            self.genealogy = []
            self.running = False
            self._record_generation()
            self.reset_cars()

    async def set_map(self, preset: str, seed: int | None = None) -> None:
        async with self._lock:
            if preset not in ROAD_PRESETS:
                preset = "mixed"
            road_seed = self.road.seed if seed is None else seed
            self.road = Road(seed=road_seed, preset=preset)
            self.running = False
            self.reset_cars()
            self._record_generation()

    async def evolve(self, elite_count: int = 2, copy_count: int = 1, mutation_rate: float = 0.22) -> None:
        async with self._lock:
            self._finalize_scores()
            self.generation += 1
            self._last_seed += 1
            self.population = evolve_population(
                self.population,
                generation=self.generation,
                seed=self._last_seed,
                elite_count=elite_count,
                copy_count=copy_count,
                mutation_rate=mutation_rate,
            )
            self.running = False
            self._record_generation()
            self.reset_cars()

    async def random_car(self, seed: int | None = None) -> CarGene:
        rng = random.Random(seed if seed is not None else time.time_ns())
        return random_gene(self.generation, rng)

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            best = max(self.population, key=lambda g: g.fitness, default=None)
            return {
                "generation": self.generation,
                "running": self.running,
                "simTime": self.sim_time,
                "speed": self.speed,
                "stallSeconds": self.stall_seconds,
                "maxTime": self.max_time,
                "road": self.road.to_dict(),
                "mapOptions": [
                    {"id": key, "label": value["label"]}
                    for key, value in ROAD_PRESETS.items()
                ],
                "genealogy": self.genealogy,
                "population": [g.to_dict() for g in self.population],
                "cars": [c.state() for c in self.cars],
                "bestId": best.id if best else None,
            }

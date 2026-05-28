import math

from app.ga import CarGene, WheelGene
from app.road import Road
from app.sim import SimCar, wheel_ground_contact


def test_wheel_contact_uses_footprint_on_slopes():
    road = Road(samples=[(0, 0), (10, 10)], length=10, dx=1, width=5)

    penetration, tangent, normal = wheel_ground_contact(road, center=(5, 6), radius=1)

    assert math.isclose(penetration, math.sqrt(2) - 1, rel_tol=0.02)
    assert tangent[0] > 0 and tangent[1] > 0
    assert normal[0] < 0 and normal[1] > 0


def test_settled_wheels_do_not_sink_deeply_into_flat_ground():
    road = Road(samples=[(0, 0), (20, 0)], length=20, dx=1, width=5)
    gene = CarGene(
        id="test",
        generation=0,
        body=[[-1, -0.2], [1, -0.2], [1, 0.45], [-1, 0.45]],
        width=1,
        density=1,
        wheels=[
            WheelGene(x=-0.75, y=-0.35, radius=0.35, power_fraction=0),
            WheelGene(x=0.75, y=-0.35, radius=0.35, power_fraction=0),
        ],
        color="#ffffff",
    )
    car = SimCar(gene=gene, lane_z=0, index=0)
    car.x = 5
    car.y = 2

    for step in range(5 * 240):
        car.step(road, 1 / 240, step / 240, stall_seconds=999, max_time=999)

    penetrations = [
        wheel_ground_contact(road, car.local_to_world((wheel.x, wheel.y)), wheel.radius)[0]
        for wheel in gene.wheels
    ]
    assert max(penetrations) < 0.05

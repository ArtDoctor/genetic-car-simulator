import math
import random

from app.ga import WheelGene, repair_wheels, random_body, random_population


def test_random_population_has_expected_gene_shape():
    population = random_population(seed=123)
    assert len(population) == 10
    assert len({gene.id for gene in population}) == 10
    assert all(gene.body for gene in population)
    assert all(gene.wheels for gene in population)
    assert all(0 <= sum(w.power_fraction for w in gene.wheels) <= 1.001 for gene in population)


def test_repair_wheels_removes_overlaps_and_caps_power():
    body = random_body(random.Random(7))
    wheels = [
        WheelGene(x=0, y=0, radius=0.5, power_fraction=0.8),
        WheelGene(x=0, y=0, radius=0.5, power_fraction=0.8),
        WheelGene(x=0.1, y=0, radius=0.5, power_fraction=0.8),
    ]

    repaired = repair_wheels(body, wheels)

    assert sum(w.power_fraction for w in repaired) <= 1.001
    for i, a in enumerate(repaired):
        for b in repaired[i + 1 :]:
            assert math.hypot(b.x - a.x, b.y - a.y) + 1e-6 >= a.radius + b.radius

import asyncio

from app.ga import new_rng, random_gene
from app.sim import SimulationManager


def test_import_car_replaces_requested_slot_and_resets_scores():
    async def scenario():
        manager = SimulationManager()
        try:
            original_slot_id = manager.population[3].id
            source = random_gene(generation=99, rng=new_rng(123)).to_dict()
            source["fitness"] = 999
            source["distance"] = 88

            await manager.import_car(source, 3)
            snapshot = await manager.snapshot()
        finally:
            await manager.close()
        return original_slot_id, source, snapshot

    original_slot_id, source, snapshot = asyncio.run(scenario())

    imported = snapshot["population"][3]
    assert imported["id"] != original_slot_id
    assert imported["id"] != source["id"]
    assert imported["lineage"] == "imported"
    assert imported["reproduction"] == "imported"
    assert imported["parent_ids"] == [source["id"]]
    assert imported["fitness"] == 0
    assert imported["distance"] == 0
    assert snapshot["cars"][3]["id"] == imported["id"]
    assert snapshot["running"] is False


def test_import_car_clamps_out_of_range_slot():
    async def scenario():
        manager = SimulationManager()
        try:
            source = random_gene(generation=1, rng=new_rng(456)).to_dict()
            await manager.import_car(source, 999)
            return await manager.snapshot(), source
        finally:
            await manager.close()

    snapshot, source = asyncio.run(scenario())
    assert snapshot["population"][-1]["parent_ids"] == [source["id"]]

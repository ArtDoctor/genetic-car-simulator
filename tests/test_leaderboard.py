import asyncio

from app.ga import new_rng, random_gene
from app.leaderboard import LeaderboardStore


def make_snapshot(fitness=42.5, preset="easy"):
    gene = random_gene(generation=2, rng=new_rng(321)).to_dict()
    return {
        "generation": 2,
        "simTime": 12.3,
        "road": {"preset": preset},
        "population": [gene],
        "cars": [{"id": gene["id"], "fitness": fitness, "maxX": 4 + fitness}],
    }


def test_leaderboard_marks_current_user_and_renames(tmp_path):
    async def scenario():
        store = LeaderboardStore(path=tmp_path / "leaderboard.json")
        session_id = "session-abc"
        assert await store.record_snapshot(session_id, make_snapshot()) is True
        before = await store.snapshot(session_id)
        changed = await store.set_display_name(session_id, "  Speedy\nDriver  ")
        after = await store.snapshot(session_id)
        return before, changed, after

    before, changed, after = asyncio.run(scenario())

    entry_before = before["maps"][0]["entries"][0]
    assert entry_before["isCurrentUser"] is True
    assert entry_before["displayName"].startswith("visitor-")
    assert changed is True

    entry_after = after["maps"][0]["entries"][0]
    assert entry_after["displayName"] == "Speedy Driver"
    assert entry_after["isCurrentUser"] is True


def test_leaderboard_uses_custom_name_for_future_records(tmp_path):
    async def scenario():
        store = LeaderboardStore(path=tmp_path / "leaderboard.json")
        session_id = "session-future"
        await store.record_snapshot(session_id, make_snapshot(fitness=10, preset="easy"))
        await store.set_display_name(session_id, "Future Ace")
        await store.record_snapshot(session_id, make_snapshot(fitness=12, preset="brutal"))
        return await store.snapshot(session_id)

    snapshot = asyncio.run(scenario())
    names = [entry["displayName"] for map_data in snapshot["maps"] for entry in map_data["entries"]]
    assert names == ["Future Ace", "Future Ace"]


def test_leaderboard_preserves_custom_name_on_better_score(tmp_path):
    async def scenario():
        store = LeaderboardStore(path=tmp_path / "leaderboard.json")
        session_id = "session-xyz"
        await store.record_snapshot(session_id, make_snapshot(fitness=10))
        await store.set_display_name(session_id, "Ace")
        await store.record_snapshot(session_id, make_snapshot(fitness=20))
        return await store.snapshot(session_id)

    snapshot = asyncio.run(scenario())
    entry = snapshot["maps"][0]["entries"][0]
    assert entry["displayName"] == "Ace"
    assert entry["fitness"] == 20

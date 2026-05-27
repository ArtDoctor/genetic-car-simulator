# Genetic Car Simulator

A browser-controlled simulator for evolving simple 3D cars over a fixed ragged road.

- Python owns genes, random car generation, crossover/elitism/copying/mutation, road generation, and toy physics evaluation.
- The browser visualizes the simulation in 3D and shows each car's 2D body projection plus full gene JSON.
- A separate **Random car lab** tab generates standalone random car genes.

## Run locally

```bash
./setup_venv.sh
./run_server.sh
```

Or manually:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.server
```

Open <http://localhost:8000>.

## Run with Docker

```bash
docker build -t genetic-car-simulator .
docker run --rm -p 8000:8000 genetic-car-simulator
```

Or with Docker Compose:

```bash
docker compose up --build
```

Open <http://localhost:8000>.

## Controls

- **Start evaluation** resets and evaluates the current generation.
- Cars are marked done when they stop making progress for a few simulated seconds, crash, finish, or hit the time limit.
- **Time speed** slider runs more/fewer physics substeps per wall-clock frame (up to 30x from the UI; backend supports up to 40x).
- **Re-generate from performance** creates the next generation using elites, one copied/mutated survivor, tournament-selected crossover, and mutation.
- **Randomize generation** starts over with 10 fresh random genes.
- **Map** selects between easy, mixed, and brutal road presets. Easy is now the default.
- Wheels can spawn anywhere around the side profile — inside, top, sides, or bottom — while still being repaired to avoid wheel-wheel intersections.
- Color is a visual-only gene now: it crosses over and mutates for family/lineage tracking, but has no physics or fitness effect.
- **Genealogy** tab shows the left-to-right reproduction tree: elite reuse, copies, crossover/mutation, and removed genes with no descendants.
- 3D camera: `WASD` free-fly, `Q/E` down/up, hold `Shift` for faster movement, click-drag to orbit around the road center near your view, mouse wheel to center/dolly toward what is under the cursor, click a car to smoothly follow it, drag while a car is selected to orbit around that car, and press `Esc` to return to automatic overview.

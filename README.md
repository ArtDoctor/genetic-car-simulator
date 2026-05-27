# Genetic Car Simulator Prototype

A browser-controlled prototype for evolving simple 3D cars over a fixed ragged road.

- Python owns genes, random car generation, crossover/elitism/copying/mutation, road generation, and toy physics evaluation.
- The browser visualizes the simulation in 3D and shows each car's 2D body projection plus full gene JSON.
- A separate **Random car lab** tab generates standalone random car genes.

## Run

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

## Controls

- **Start evaluation** resets and evaluates the current generation.
- Cars are marked done when they stop making progress for a few simulated seconds, crash, finish, or hit the time limit.
- **Time speed** slider runs more/fewer physics substeps per wall-clock frame (up to 30x from the UI; backend supports up to 40x).
- **Re-generate from performance** creates the next generation using elites, one copied/mutated survivor, tournament-selected crossover, and mutation.
- **Randomize generation** starts over with 10 fresh random genes.
- 3D camera: `WASD` free-fly, `Q/E` down/up, hold `Shift` for faster movement, click-drag to orbit around the road center near your view, mouse wheel to center/dolly toward what is under the cursor, click a car to smoothly follow it, drag while a car is selected to orbit around that car, and press `Esc` to return to automatic overview.

## Project layout

```text
app/ga.py       Genes, random generation, crossover, mutation, evolution
app/road.py     Deterministic ragged road generator
app/sim.py      Server-side toy vehicle physics + evaluation manager
app/server.py   FastAPI app, REST controls, websocket state stream
app/static/     Served HTML/CSS/JS UI
frontend/src/   TypeScript source mirror for the UI
```

This is intentionally a prototype physics model, not a precision vehicle simulator. It is built to make genetic choices visibly matter and to provide a clean base for swapping in a stronger physics engine later.

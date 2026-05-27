import * as THREE from "three";

const $ = (id) => document.getElementById(id);
const state = {
  data: null,
  meshes: new Map(),
  roadMesh: null,
  followX: 12,
  cameraMode: "auto",
  followId: null,
  yaw: -0.65,
  pitch: -0.33,
  dragging: false,
  dragMoved: false,
  dragStart: { x: 0, y: 0 },
  dragPrevMode: "auto",
  dragPrevFollowId: null,
  orbitTarget: null,
  orbitYaw: -0.8,
  orbitPitch: 0.35,
  orbitRadius: 28,
  keys: new Set(),
};

const viewport = $("viewport");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x050914);
scene.fog = new THREE.Fog(0x050914, 60, 180);
const camera = new THREE.PerspectiveCamera(58, 1, 0.1, 1000);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.shadowMap.enabled = true;
viewport.appendChild(renderer.domElement);

const hemi = new THREE.HemisphereLight(0xb8d7ff, 0x101018, 2.4);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(12, 28, 18);
sun.castShadow = true;
scene.add(sun);
const grid = new THREE.GridHelper(700, 70, 0x243044, 0x172033);
grid.rotation.x = Math.PI / 2;
grid.position.y = -2.5;
scene.add(grid);
const clock = new THREE.Clock();
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
camera.position.set(-6, 9, 20);

function resize() {
  const rect = viewport.getBoundingClientRect();
  renderer.setSize(rect.width, rect.height);
  camera.aspect = rect.width / Math.max(1, rect.height);
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

window.addEventListener("keydown", (event) => {
  if (["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE", "Space", "ControlLeft", "ShiftLeft", "ShiftRight"].includes(event.code)) {
    state.keys.add(event.code);
    setFreeMode();
    event.preventDefault();
  }
  if (event.code === "Escape") {
    state.cameraMode = "auto";
    state.followId = null;
  }
});
window.addEventListener("keyup", (event) => state.keys.delete(event.code));

renderer.domElement.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  state.dragging = true;
  state.dragMoved = false;
  state.dragStart = { x: event.clientX, y: event.clientY };
  state.dragPrevMode = state.cameraMode;
  state.dragPrevFollowId = state.followId;
  beginOrbitDrag(event);
  renderer.domElement.setPointerCapture(event.pointerId);
});
renderer.domElement.addEventListener("pointermove", (event) => {
  if (!state.dragging) return;
  const dx = event.movementX || 0;
  const dy = event.movementY || 0;
  if (Math.hypot(event.clientX - state.dragStart.x, event.clientY - state.dragStart.y) > 4) state.dragMoved = true;
  updateOrbitFromDrag(dx, dy);
});
renderer.domElement.addEventListener("pointerup", (event) => {
  if (state.dragging) renderer.domElement.releasePointerCapture(event.pointerId);
  const wasClick = !state.dragMoved;
  state.dragging = false;
  if (!wasClick) return;
  const hit = raycastFromEvent(event);
  const carId = hit ? carIdForObject(hit.object) : null;
  if (carId) {
    state.cameraMode = "follow";
    state.followId = carId;
  } else {
    state.cameraMode = state.dragPrevMode;
    state.followId = state.dragPrevFollowId;
  }
});
renderer.domElement.addEventListener("wheel", (event) => {
  event.preventDefault();
  const hit = raycastFromEvent(event);
  const carId = hit ? carIdForObject(hit.object) : null;
  const target = carId && state.meshes.has(carId)
    ? state.meshes.get(carId).position.clone().add(new THREE.Vector3(0, 1.2, 0))
    : (hit ? hit.point.clone() : roadCenterTarget());
  syncOrbitFromTarget(target);
  state.cameraMode = state.followId ? "followOrbit" : "orbit";
  state.orbitRadius = THREE.MathUtils.clamp(state.orbitRadius + event.deltaY * 0.035, 2.2, 180);
  applyOrbitCamera(true);
}, { passive: false });

function makeRoad(road) {
  if (state.roadMesh) scene.remove(state.roadMesh);
  const verts = [];
  const indices = [];
  const half = road.width / 2;
  road.samples.forEach(([x, y], i) => {
    verts.push(x, y - 0.04, -half, x, y - 0.04, half);
    if (i < road.samples.length - 1) {
      const a = i * 2;
      indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
  });
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  geom.setIndex(indices);
  geom.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({ color: 0x303a27, roughness: 0.92, metalness: 0.02, side: THREE.DoubleSide });
  state.roadMesh = new THREE.Mesh(geom, mat);
  state.roadMesh.receiveShadow = true;
  scene.add(state.roadMesh);

  const linePts = road.samples.map(([x, y]) => new THREE.Vector3(x, y + 0.02, -half - 0.05));
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(linePts), new THREE.LineBasicMaterial({ color: 0x86efac }));
  state.roadMesh.add(line);
}

function shapeGeometry(gene) {
  const shape = new THREE.Shape();
  gene.body.forEach(([x, y], i) => (i ? shape.lineTo(x, y) : shape.moveTo(x, y)));
  shape.closePath();
  const geom = new THREE.ExtrudeGeometry(shape, { depth: gene.width, bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.025, bevelSegments: 1 });
  geom.translate(0, 0, -gene.width / 2);
  geom.computeVertexNormals();
  return geom;
}

function makeCarMesh(gene) {
  const group = new THREE.Group();
  group.userData.carId = gene.id;
  const body = new THREE.Mesh(
    shapeGeometry(gene),
    new THREE.MeshStandardMaterial({ color: new THREE.Color(gene.color), roughness: 0.55, metalness: 0.08 })
  );
  body.castShadow = true;
  body.userData.carId = gene.id;
  group.add(body);
  const wheelMat = new THREE.MeshStandardMaterial({ color: 0x111827, roughness: 0.85 });
  const hubMat = new THREE.MeshStandardMaterial({ color: 0xd1d5db, roughness: 0.5 });
  const axleMat = new THREE.MeshStandardMaterial({ color: 0x6b7280, roughness: 0.6 });
  const wheelGroups = [];
  gene.wheels.forEach((w) => {
    const wheel = new THREE.Group();
    wheel.userData.carId = gene.id;
    const tireThickness = Math.min(0.26, Math.max(0.16, gene.width * 0.24));
    const sideZ = gene.width / 2 + tireThickness / 2 + 0.035;
    const axle = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, gene.width + tireThickness * 2 + 0.08, 12), axleMat);
    axle.rotation.x = Math.PI / 2;
    axle.userData.carId = gene.id;
    wheel.add(axle);
    [-1, 1].forEach((side) => {
      const tire = new THREE.Mesh(new THREE.CylinderGeometry(w.radius, w.radius, tireThickness, 28), wheelMat);
      tire.rotation.x = Math.PI / 2;
      tire.position.z = side * sideZ;
      tire.castShadow = true;
      tire.userData.carId = gene.id;
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(w.radius * 0.35, w.radius * 0.35, tireThickness + 0.025, 20), hubMat);
      hub.rotation.x = Math.PI / 2;
      hub.position.z = side * sideZ;
      hub.userData.carId = gene.id;
      const spoke = new THREE.Mesh(new THREE.BoxGeometry(w.radius * 1.45, 0.035, 0.025), hubMat);
      spoke.position.z = side * sideZ;
      spoke.userData.carId = gene.id;
      wheel.add(tire, hub, spoke);
    });
    wheel.userData.local = { x: w.x, y: w.y };
    wheelGroups.push(wheel);
    group.add(wheel);
  });
  group.userData.wheelGroups = wheelGroups;
  return group;
}

function syncMeshes(data) {
  if (!state.roadMesh || state.roadSeed !== data.road.seed || state.roadPreset !== data.road.preset) {
    state.roadSeed = data.road.seed;
    state.roadPreset = data.road.preset;
    makeRoad(data.road);
  }
  const ids = new Set(data.population.map((g) => g.id));
  for (const [id, mesh] of state.meshes) {
    if (!ids.has(id)) {
      scene.remove(mesh);
      state.meshes.delete(id);
      if (state.followId === id) {
        state.followId = null;
        state.cameraMode = "auto";
      }
    }
  }
  data.population.forEach((gene) => {
    if (!state.meshes.has(gene.id)) {
      const mesh = makeCarMesh(gene);
      state.meshes.set(gene.id, mesh);
      scene.add(mesh);
    }
  });
}

function updateMeshes(data) {
  syncMeshes(data);
  let bestX = 0;
  data.cars.forEach((car) => {
    const mesh = state.meshes.get(car.id);
    if (!mesh) return;
    mesh.position.set(car.x, car.y, car.laneZ);
    mesh.rotation.set(0, 0, car.theta);
    mesh.visible = true;
    bestX = Math.max(bestX, car.maxX);
    const wheels = mesh.userData.wheelGroups || [];
    wheels.forEach((wheel, i) => {
      const local = wheel.userData.local;
      wheel.position.set(local.x, local.y, 0);
      wheel.rotation.z = car.wheels[i]?.spin || 0;
    });
  });
  state.followX = state.followX * 0.94 + Math.max(12, bestX + 10) * 0.06;
}

function roadHeightAt(x) {
  const samples = state.data?.road?.samples;
  if (!samples?.length) return 0;
  if (x <= samples[0][0]) return samples[0][1];
  for (let i = 1; i < samples.length; i++) {
    if (x <= samples[i][0]) {
      const [x0, y0] = samples[i - 1];
      const [x1, y1] = samples[i];
      const t = (x - x0) / Math.max(0.0001, x1 - x0);
      return y0 * (1 - t) + y1 * t;
    }
  }
  return samples[samples.length - 1][1];
}

function roadCenterTarget() {
  if (state.roadMesh) {
    raycaster.setFromCamera(new THREE.Vector2(0, 0), camera);
    const hit = raycaster.intersectObject(state.roadMesh, true)[0];
    if (hit) return new THREE.Vector3(hit.point.x, hit.point.y + 1.0, 0);
  }
  const dir = camera.getWorldDirection(new THREE.Vector3());
  const t = Math.abs(dir.y) > 0.03 ? (roadHeightAt(camera.position.x) - camera.position.y) / dir.y : 18;
  const distance = t > 0 ? THREE.MathUtils.clamp(t, 8, 60) : 18;
  const x = camera.position.x + dir.x * distance;
  return new THREE.Vector3(x, roadHeightAt(x) + 1.0, 0);
}

function cameraDirection() {
  return new THREE.Vector3(
    Math.cos(state.pitch) * Math.cos(state.yaw),
    Math.sin(state.pitch),
    Math.cos(state.pitch) * Math.sin(state.yaw)
  ).normalize();
}

function syncAnglesFromCamera(target = null) {
  const dir = target ? target.clone().sub(camera.position).normalize() : camera.getWorldDirection(new THREE.Vector3());
  state.yaw = Math.atan2(dir.z, dir.x);
  state.pitch = Math.asin(THREE.MathUtils.clamp(dir.y, -0.98, 0.98));
}

function setFreeMode() {
  if (state.cameraMode !== "free") syncAnglesFromCamera();
  state.cameraMode = "free";
  state.followId = null;
}

function syncOrbitFromTarget(target) {
  state.orbitTarget = target.clone();
  const offset = camera.position.clone().sub(target);
  state.orbitRadius = THREE.MathUtils.clamp(offset.length() || 24, 2.2, 180);
  state.orbitYaw = Math.atan2(offset.z, offset.x);
  state.orbitPitch = Math.asin(THREE.MathUtils.clamp(offset.y / state.orbitRadius, -0.98, 0.98));
}

function orbitOffset() {
  const cp = Math.cos(state.orbitPitch);
  return new THREE.Vector3(
    cp * Math.cos(state.orbitYaw),
    Math.sin(state.orbitPitch),
    cp * Math.sin(state.orbitYaw)
  ).multiplyScalar(state.orbitRadius);
}

function applyOrbitCamera(instant = false) {
  if (!state.orbitTarget) state.orbitTarget = roadCenterTarget();
  const desired = state.orbitTarget.clone().add(orbitOffset());
  if (instant) camera.position.copy(desired);
  else camera.position.lerp(desired, 0.22);
  camera.lookAt(state.orbitTarget);
  syncAnglesFromCamera(state.orbitTarget);
}

function beginOrbitDrag(event) {
  const target = state.followId && state.meshes.has(state.followId)
    ? state.meshes.get(state.followId).position.clone().add(new THREE.Vector3(0, 1.2, 0))
    : roadCenterTarget();
  syncOrbitFromTarget(target);
  state.cameraMode = state.followId ? "followOrbit" : "orbit";
}

function updateOrbitFromDrag(dx, dy) {
  state.orbitYaw -= dx * 0.006;
  state.orbitPitch = THREE.MathUtils.clamp(state.orbitPitch + dy * 0.0045, -1.25, 1.35);
  applyOrbitCamera(true);
}

function setPointerFromEvent(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
}

function raycastFromEvent(event) {
  setPointerFromEvent(event);
  raycaster.setFromCamera(pointer, camera);
  const objects = [...state.meshes.values()];
  if (state.roadMesh) objects.push(state.roadMesh);
  return raycaster.intersectObjects(objects, true)[0] || null;
}

function carIdForObject(object) {
  let current = object;
  while (current) {
    if (current.userData?.carId) return current.userData.carId;
    current = current.parent;
  }
  return null;
}

function updateFreeCamera(dt) {
  const dir = cameraDirection();
  const flatForward = new THREE.Vector3(dir.x, 0, dir.z).normalize();
  const right = new THREE.Vector3().crossVectors(flatForward, new THREE.Vector3(0, 1, 0)).normalize();
  const up = new THREE.Vector3(0, 1, 0);
  const speed = (state.keys.has("ShiftLeft") || state.keys.has("ShiftRight") ? 32 : 13) * dt;
  const move = new THREE.Vector3();
  if (state.keys.has("KeyW")) move.add(dir);
  if (state.keys.has("KeyS")) move.sub(dir);
  if (state.keys.has("KeyD")) move.add(right);
  if (state.keys.has("KeyA")) move.sub(right);
  if (state.keys.has("KeyE") || state.keys.has("Space")) move.add(up);
  if (state.keys.has("KeyQ") || state.keys.has("ControlLeft")) move.sub(up);
  if (move.lengthSq() > 0) camera.position.add(move.normalize().multiplyScalar(speed));
  camera.lookAt(camera.position.clone().add(dir));
}

function updateCamera(dt) {
  if (state.cameraMode === "followOrbit" && state.followId && state.meshes.has(state.followId)) {
    const target = state.meshes.get(state.followId).position.clone().add(new THREE.Vector3(0, 1.2, 0));
    state.orbitTarget = state.orbitTarget ? state.orbitTarget.lerp(target, 1 - Math.exp(-dt * 7.0)) : target;
    applyOrbitCamera(false);
    return;
  }
  if (state.cameraMode === "follow" && state.followId && state.meshes.has(state.followId)) {
    const mesh = state.meshes.get(state.followId);
    const target = mesh.position.clone().add(new THREE.Vector3(0, 1.2, 0));
    const desired = target.clone().add(new THREE.Vector3(-10, 5.2, 8.5));
    camera.position.lerp(desired, 1 - Math.exp(-dt * 4.2));
    const look = target.clone().add(new THREE.Vector3(2.2, 0.2, 0));
    const currentDir = camera.getWorldDirection(new THREE.Vector3());
    const currentLook = camera.position.clone().add(currentDir.multiplyScalar(camera.position.distanceTo(look)));
    camera.lookAt(currentLook.lerp(look, 1 - Math.exp(-dt * 6.0)));
    syncAnglesFromCamera(look);
    return;
  }
  if (state.cameraMode === "orbit") {
    if (!state.dragging) applyOrbitCamera(false);
    return;
  }
  if (state.cameraMode === "free") {
    updateFreeCamera(dt);
    return;
  }
  const target = new THREE.Vector3(state.followX, 1.8, 0);
  const desired = new THREE.Vector3(state.followX - 18, 11, 26);
  camera.position.lerp(desired, 1 - Math.exp(-dt * 3.5));
  camera.lookAt(target);
  syncAnglesFromCamera(target);
}

function render() {
  requestAnimationFrame(render);
  const dt = Math.min(0.08, clock.getDelta());
  updateCamera(dt);
  renderer.render(scene, camera);
}
render();

function svgForGene(gene, carState = null, large = false) {
  const pts = gene.body.map(([x, y]) => `${100 + x * 45},${large ? 170 - y * 75 : 40 - y * 28}`).join(" ");
  const wheelSvg = gene.wheels.map((w) => {
    const cx = 100 + w.x * 45;
    const cy = large ? 170 - w.y * 75 : 40 - w.y * 28;
    const r = w.radius * (large ? 75 : 28);
    return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#111827" stroke="#e5e7eb" stroke-width="2"/><text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="${large ? 13 : 8}" fill="#fff">${Math.round(w.power_fraction * 100)}</text>`;
  }).join("");
  const w = large ? 420 : 260;
  const h = large ? 340 : 78;
  const yLine = large ? 250 : 64;
  const score = carState ? `distance ${Math.max(0, carState.maxX - 4).toFixed(1)} | fitness ${carState.fitness.toFixed(1)}` : `uses ${Math.round(gene.used_power_fraction * 100)}% power`;
  return `<svg class="car-svg" viewBox="0 0 ${w} ${h}" role="img">
    <line x1="10" y1="${yLine}" x2="${w - 10}" y2="${yLine}" stroke="#334155" stroke-width="2" stroke-dasharray="5 4" />
    <polygon points="${pts}" fill="${gene.color}" stroke="#fff" stroke-width="1.6" opacity="0.9" />
    ${wheelSvg}
    <text x="10" y="16" fill="#94a3b8" font-size="${large ? 14 : 10}">${gene.id} · ${gene.lineage}</text>
    <text x="10" y="${h - 8}" fill="#86efac" font-size="${large ? 14 : 10}">${score}</text>
  </svg>`;
}

function reproductionClass(value = "") {
  return value.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase() || "random";
}

function reproductionColor(value = "") {
  const cls = reproductionClass(value);
  if (cls.includes("elite")) return "#3fb950";
  if (cls.includes("copy")) return "#58a6ff";
  if (cls.includes("crossover")) return "#d29922";
  return "#94a3b8";
}

function updateGenealogy(data) {
  const holder = $("genealogy-tree");
  if (!holder || !data.genealogy?.length) return;
  const key = data.genealogy.map((g) => `${g.generation}:${g.cars.map((c) => `${c.id}-${c.fitness}`).join(",")}`).join("|");
  if (state.genealogyKey === key) return;
  state.genealogyKey = key;
  const generations = data.genealogy;
  const nodeById = new Map();
  generations.forEach((gen, gi) => gen.cars.forEach((car, ci) => nodeById.set(car.id, { ...car, gi, ci })));
  const hasChild = new Set();
  generations.forEach((gen) => gen.cars.forEach((car) => (car.parentIds || []).forEach((pid) => hasChild.add(pid))));
  const colW = 178;
  const rowH = 68;
  const marginX = 64;
  const marginY = 52;
  const maxRows = Math.max(...generations.map((g) => g.cars.length), 1);
  const width = marginX * 2 + Math.max(1, generations.length - 1) * colW + 120;
  const height = marginY * 2 + maxRows * rowH;
  const pos = (node) => ({ x: marginX + node.gi * colW, y: marginY + node.ci * rowH });
  const edges = [];
  generations.forEach((gen) => gen.cars.forEach((car) => {
    const child = nodeById.get(car.id);
    (car.parentIds || []).forEach((pid) => {
      const parent = nodeById.get(pid);
      if (parent && child) edges.push({ parent, child, reproduction: car.reproduction || car.lineage });
    });
  }));
  const edgeSvg = edges.map((e) => {
    const a = pos(e.parent);
    const b = pos(e.child);
    const x1 = a.x + 20, y1 = a.y;
    const x2 = b.x - 20, y2 = b.y;
    const mid = (x1 + x2) / 2;
    const cls = reproductionClass(e.reproduction);
    return `<path class="gene-edge ${cls}" d="M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}" />`;
  }).join("");
  const generationLabels = generations.map((gen, gi) => `<text x="${marginX + gi * colW - 22}" y="24" fill="#8b949e" font-size="12">Gen ${gen.generation}</text>`).join("");
  const nodeSvg = generations.flatMap((gen) => gen.cars.map((car, ci) => {
    const node = nodeById.get(car.id);
    const p = pos(node);
    const removed = gen.generation < data.generation && !hasChild.has(car.id);
    const color = reproductionColor(car.reproduction || car.lineage);
    const title = `${car.id} · ${car.reproduction}\nfitness ${Number(car.fitness || 0).toFixed(1)}\nparents ${(car.parentIds || []).join(", ") || "none"}`;
    return `<g class="gene-node ${removed ? "removed" : ""}" data-gene-id="${car.id}" transform="translate(${p.x},${p.y})">
      <title>${title}</title>
      <circle r="18" fill="${color}" stroke="#e6edf3" stroke-width="1.2" />
      <text x="0" y="4" text-anchor="middle" fill="#0b1220" font-weight="700">${ci + 1}</text>
      <text x="25" y="-4">${car.id}</text>
      <text x="25" y="11" fill="#8b949e">${car.reproduction || car.lineage}</text>
      <text x="25" y="26" fill="#86efac">fit ${Number(car.fitness || 0).toFixed(1)}</text>
    </g>`;
  })).join("");
  holder.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">${generationLabels}${edgeSvg}${nodeSvg}</svg>`;
  holder.querySelectorAll(".gene-node").forEach((node) => {
    node.addEventListener("click", () => {
      const gene = nodeById.get(node.dataset.geneId);
      $("genealogy-details").textContent = JSON.stringify(gene, null, 2);
    });
  });
}

function updateUI(data) {
  $("status").textContent = `gen ${data.generation} · ${data.road?.preset || "map"} · ${data.running ? "running" : "stopped"} · t=${data.simTime.toFixed(1)}s · ${data.speed.toFixed(2)}×`;
  if ($("map-select") && data.road?.preset && $("map-select").value !== data.road.preset) $("map-select").value = data.road.preset;
  const bestCar = [...data.cars].sort((a, b) => b.fitness - a.fitness)[0];
  $("best").textContent = bestCar ? `best ${bestCar.id}: ${bestCar.fitness.toFixed(1)} (${Math.max(0, bestCar.maxX - 4).toFixed(1)}m)` : "best: —";
  const list = $("cars-list");
  updateGenealogy(data);
  list.innerHTML = data.population.map((gene) => {
    const car = data.cars.find((c) => c.id === gene.id);
    const cls = car?.done ? (car.reason === "crashed" ? "done crashed" : "done") : "";
    return `<article class="car-card ${cls}">
      <div class="car-top"><strong>#${car?.index ?? "?"} ${gene.id}</strong><span class="metric">${(car?.fitness ?? gene.fitness).toFixed(1)}</span></div>
      <div class="badge">${gene.lineage} · ${gene.wheels.length} wheels · power ${Math.round(gene.used_power_fraction * 100)}% · ${car?.reason || "evaluating"}</div>
      ${svgForGene(gene, car)}
      <details><summary>gene</summary><pre>${JSON.stringify(gene, null, 2)}</pre></details>
    </article>`;
  }).join("");
}

function connectWs() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws/sim`);
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    state.data = data;
    updateMeshes(data);
    const now = performance.now();
    if (!state.lastUi || now - state.lastUi > 300 || state.lastGeneration !== data.generation || state.lastRunning !== data.running) {
      updateUI(data);
      state.lastUi = now;
      state.lastGeneration = data.generation;
      state.lastRunning = data.running;
    }
  };
  ws.onclose = () => setTimeout(connectWs, 1000);
}
connectWs();

async function post(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fire(promise) {
  promise.catch((err) => {
    console.error(err);
    $("status").textContent = `request failed: ${err.message || err}`;
  });
}

$("start").addEventListener("click", () => fire(post("/api/start")));
$("pause").addEventListener("click", () => fire(post("/api/pause")));
$("randomize").addEventListener("click", () => fire(post("/api/randomize")));
$("evolve").addEventListener("click", () => fire(post("/api/evolve", { elite_count: 2, copy_count: 1, mutation_rate: Number($("mutation").value) })));
$("speed").addEventListener("input", (event) => {
  const value = Number(event.target.value);
  $("speed-label").textContent = `${value.toFixed(value < 10 ? 2 : 0)}×`;
  fire(post("/api/speed", { speed: value }));
});
$("map-select").addEventListener("change", (event) => fire(post("/api/map", { preset: event.target.value })));
$("mutation").addEventListener("input", (event) => { $("mutation-label").textContent = Number(event.target.value).toFixed(2); });

function setTab(name) {
  $("simulation-view").classList.toggle("active", name === "sim");
  $("random-view").classList.toggle("active", name === "random");
  $("genealogy-view").classList.toggle("active", name === "genealogy");
  $("tab-sim").classList.toggle("active", name === "sim");
  $("tab-random").classList.toggle("active", name === "random");
  $("tab-genealogy").classList.toggle("active", name === "genealogy");
  if (name === "genealogy" && state.data) updateGenealogy(state.data);
  setTimeout(resize, 0);
}
$("tab-sim").addEventListener("click", () => setTab("sim"));
$("tab-random").addEventListener("click", () => setTab("random"));
$("tab-genealogy").addEventListener("click", () => setTab("genealogy"));

async function generateOne() {
  const gene = await (await fetch(`/api/random-car?seed=${Date.now()}`)).json();
  $("random-svg").innerHTML = svgForGene(gene, null, true);
  $("random-json").textContent = JSON.stringify(gene, null, 2);
}
$("generate-one").addEventListener("click", generateOne);
generateOne();

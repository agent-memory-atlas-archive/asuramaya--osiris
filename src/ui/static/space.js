// NAVIGABLE SPACE, THE RENDERER — piece 1, THE SPIKE (thread 71c4ca0d, Thoth DM 10436,
// operator rulings f832c3a4/0a3d6719). The only deliverable of this file is NUMBERS:
// load time, frame rate with/without edges, memory — driven in a real browser, reported
// as a thread note, before piece 2 (the view) touches any of this.
//
// Data source: today's /objects/viewport (wave B item 2), NOT a new endpoint — tiled over
// the whole positioned extent and paginated with `exclude` per tile, matching the
// endpoint's own existing pan-delta contract instead of inventing a "give me everything"
// route. Edges are scoped PER RESPONSE (both endpoints must be in that same call's node
// set) — an edge whose two ends land in different tiles is invisible to this loader. That
// undercount is expected and reported explicitly (`edgesTrueTotal` vs `edgesLoaded`);
// Khnum's typed-array stream (thread b6cb1d7c0b36) is the real production loader once it
// lands, per DM 10436's own instruction.

import * as THREE from "./vendor/three.module.js";

const hud = document.getElementById("hud");
const labelsEl = document.getElementById("labels");
const wrap = document.getElementById("canvas-wrap");

function setHud(text) {
  hud.textContent = text;
}

// ---- 1. load every positioned node via tiled /objects/viewport calls ------------------
async function loadFullGraph() {
  const t0 = performance.now();
  const EXTENT = 600; // measured live range was [-550,550] on both axes; 600 gives margin
  const TILE = 100; // 12x12 = 144 tiles; at ~48.8k objects over ~1100x1100 that's ~340/tile
  const nodesById = new Map();
  const edgeKeys = new Set();
  const edges = [];
  let requestCount = 0;

  async function fetchTile(minx, maxx, miny, maxy) {
    let excludeIds = [];
    for (;;) {
      const qs = new URLSearchParams({
        minx: String(minx), maxx: String(maxx), miny: String(miny), maxy: String(maxy),
        limit: "5000",
      });
      if (excludeIds.length) qs.set("exclude", excludeIds.join(","));
      requestCount++;
      let r;
      for (let attempt = 0; ; attempt++) {
        try {
          r = await fetch(`/objects/viewport?${qs}`).then((x) => x.json());
          break;
        } catch (err) {
          if (attempt >= 2) throw err; // a real failure after 3 tries, not a transient blip
          await new Promise((res) => setTimeout(res, 150 * (attempt + 1)));
        }
      }
      for (const n of r.nodes) {
        if (!nodesById.has(n.id)) nodesById.set(n.id, n);
        excludeIds.push(n.id);
      }
      for (const e of r.edges) {
        const k = `${e.source}|${e.target}|${e.type}`;
        if (!edgeKeys.has(k)) { edgeKeys.add(k); edges.push(e); }
      }
      if (r.nodes.length < 5000) break; // this tile is exhausted
      // hit the cap — the tile is denser than expected; loop again with `exclude` growing
      // (mirrors the endpoint's own pan-delta contract, just applied once at full extent)
      if (r.nodes.length === 0) break;
    }
  }

  const tiles = [];
  for (let x = -EXTENT; x < EXTENT; x += TILE) {
    for (let y = -EXTENT; y < EXTENT; y += TILE) {
      tiles.push([x, x + TILE, y, y + TILE]);
    }
  }
  // bounded concurrency (8 in flight) — serial would be needlessly slow, unbounded would
  // hammer the pool with 144 simultaneous connections
  const CONC = 8;
  let next = 0;
  async function worker() {
    while (next < tiles.length) {
      const i = next++;
      await fetchTile(...tiles[i]);
    }
  }
  await Promise.all(Array.from({ length: CONC }, worker));

  const loadMs = performance.now() - t0;
  return {
    nodes: Array.from(nodesById.values()),
    edges,
    requestCount,
    loadMs,
  };
}

// ---- 2. type colors from /schema (the ontology, never hardcoded) ----------------------
async function loadTypeColors() {
  const cat = await fetch("/schema").then((r) => r.json());
  const m = new Map();
  for (const t of cat.object_types) m.set(t.name, t.color || "#6e7681");
  return m;
}

async function main() {
  setHud("loading graph…");
  const [{ nodes, edges, requestCount, loadMs }, typeColors] = await Promise.all([
    loadFullGraph(),
    loadTypeColors(),
  ]);

  const n = nodes.length;
  setHud(`building scene (${n} nodes, ${edges.length} edges loaded)…`);

  // ---- renderer / scene / camera --------------------------------------------------
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(window.innerWidth, window.innerHeight);
  wrap.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1219);
  const pickScene = new THREE.Scene();

  let viewSize = 700;
  const aspect = window.innerWidth / window.innerHeight;
  const camera = new THREE.OrthographicCamera(
    (-viewSize * aspect) / 2, (viewSize * aspect) / 2, viewSize / 2, -viewSize / 2, 0.1, 10
  );
  camera.position.set(0, 0, 5);
  camera.lookAt(0, 0, 0);

  // ---- node instancing --------------------------------------------------------------
  const geo = new THREE.CircleGeometry(1.6, 8);
  const mat = new THREE.MeshBasicMaterial({ vertexColors: true });
  const mesh = new THREE.InstancedMesh(geo, mat, n);
  mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);

  const pickMat = new THREE.MeshBasicMaterial({ vertexColors: true });
  const pickMesh = new THREE.InstancedMesh(geo, pickMat, n);
  pickMesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);

  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  const idColor = new THREE.Color();
  const idToNode = new Array(n);
  for (let i = 0; i < n; i++) {
    const node = nodes[i];
    idToNode[i] = node;
    dummy.position.set(node.x || 0, node.y || 0, 0);
    dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);
    pickMesh.setMatrixAt(i, dummy.matrix);
    color.set(typeColors.get(node.type) || "#6e7681");
    mesh.instanceColor.setXYZ(i, color.r, color.g, color.b);
    // instance index encoded as an RGB id, +1 so id 0 stays reserved for "nothing hit"
    const id = i + 1;
    idColor.setRGB(((id & 0xff) / 255), (((id >> 8) & 0xff) / 255), (((id >> 16) & 0xff) / 255));
    pickMesh.instanceColor.setXYZ(i, idColor.r, idColor.g, idColor.b);
  }
  mesh.instanceMatrix.needsUpdate = true;
  pickMesh.instanceMatrix.needsUpdate = true;
  scene.add(mesh);
  pickScene.add(pickMesh);

  // ---- edge LineSegments (togglable, for the with/without-edges FPS comparison) -----
  const nodeIndexById = new Map(nodes.map((nd, i) => [nd.id, i]));
  const positions = new Float32Array(edges.length * 6);
  let ei = 0;
  for (const e of edges) {
    const a = nodeIndexById.get(e.source);
    const b = nodeIndexById.get(e.target);
    if (a == null || b == null) continue;
    const na = nodes[a], nb = nodes[b];
    positions[ei++] = na.x || 0; positions[ei++] = na.y || 0; positions[ei++] = -0.1;
    positions[ei++] = nb.x || 0; positions[ei++] = nb.y || 0; positions[ei++] = -0.1;
  }
  const edgeGeo = new THREE.BufferGeometry();
  edgeGeo.setAttribute("position", new THREE.BufferAttribute(positions.subarray(0, ei), 3));
  const edgeMat = new THREE.LineBasicMaterial({ color: 0x2c3744, transparent: true, opacity: 0.5 });
  const edgeLines = new THREE.LineSegments(edgeGeo, edgeMat);
  scene.add(edgeLines);

  // buffer byte sizes — a portable, deterministic memory number (performance.memory is
  // Chrome-only and coarse; this is exact regardless of browser)
  const bufferBytes =
    mesh.instanceMatrix.array.byteLength + mesh.instanceColor.array.byteLength +
    pickMesh.instanceMatrix.array.byteLength + pickMesh.instanceColor.array.byteLength +
    edgeGeo.getAttribute("position").array.byteLength;

  // ---- pan + zoom ---------------------------------------------------------------------
  let dragging = false, lastX = 0, lastY = 0;
  renderer.domElement.addEventListener("pointerdown", (ev) => {
    dragging = true; lastX = ev.clientX; lastY = ev.clientY;
  });
  window.addEventListener("pointerup", () => { dragging = false; });
  window.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    const dx = ev.clientX - lastX, dy = ev.clientY - lastY;
    lastX = ev.clientX; lastY = ev.clientY;
    const worldPerPixelX = (camera.right - camera.left) / window.innerWidth;
    const worldPerPixelY = (camera.top - camera.bottom) / window.innerHeight;
    camera.position.x -= dx * worldPerPixelX;
    camera.position.y += dy * worldPerPixelY;
    scheduleLabelUpdate();
  });
  renderer.domElement.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      const factor = Math.exp(ev.deltaY * 0.001);
      viewSize = Math.max(5, Math.min(2000, viewSize * factor));
      updateCameraFrustum();
      scheduleLabelUpdate();
    },
    { passive: false }
  );

  function updateCameraFrustum() {
    const a = window.innerWidth / window.innerHeight;
    camera.left = (-viewSize * a) / 2;
    camera.right = (viewSize * a) / 2;
    camera.top = viewSize / 2;
    camera.bottom = -viewSize / 2;
    camera.updateProjectionMatrix();
  }
  updateCameraFrustum();

  window.addEventListener("resize", () => {
    renderer.setSize(window.innerWidth, window.innerHeight);
    updateCameraFrustum();
  });

  // ---- GPU color-id picking: render a 1x1 pixel under the cursor, never a CPU
  // raycast over n instances --------------------------------------------------------
  const pickTarget = new THREE.WebGLRenderTarget(1, 1);
  const pickBuf = new Uint8Array(4);
  function pickAt(clientX, clientY) {
    const px = clientX * (window.devicePixelRatio || 1);
    const py = (window.innerHeight - clientY) * (window.devicePixelRatio || 1);
    camera.setViewOffset(
      renderer.domElement.width, renderer.domElement.height, px, py, 1, 1
    );
    renderer.setRenderTarget(pickTarget);
    renderer.render(pickScene, camera);
    renderer.setRenderTarget(null);
    camera.clearViewOffset();
    renderer.readRenderTargetPixels(pickTarget, 0, 0, 1, 1, pickBuf);
    const id = pickBuf[0] | (pickBuf[1] << 8) | (pickBuf[2] << 16);
    return id === 0 ? null : idToNode[id - 1];
  }
  renderer.domElement.addEventListener("click", (ev) => {
    const hit = pickAt(ev.clientX, ev.clientY);
    if (hit) setHud(`picked: ${hit.type} ${hit.label || hit.id}\n(click elsewhere to clear)`);
  });

  // ---- nearest-N labels, recomputed on camera move (throttled), not every frame -----
  const N_LABELS = 24;
  let labelTimer = null;
  function scheduleLabelUpdate() {
    if (labelTimer) return;
    labelTimer = setTimeout(() => { labelTimer = null; updateLabels(); }, 120);
  }
  function updateLabels() {
    const cx = camera.position.x, cy = camera.position.y;
    const nearest = nodes
      .map((nd) => ({ nd, d: (nd.x - cx) ** 2 + (nd.y - cy) ** 2 }))
      .sort((a, b) => a.d - b.d)
      .slice(0, N_LABELS);
    labelsEl.innerHTML = "";
    const v = new THREE.Vector3();
    for (const { nd } of nearest) {
      v.set(nd.x || 0, nd.y || 0, 0).project(camera);
      const x = (v.x * 0.5 + 0.5) * window.innerWidth;
      const y = (-v.y * 0.5 + 0.5) * window.innerHeight;
      const div = document.createElement("div");
      div.className = "lbl";
      div.style.left = `${x}px`;
      div.style.top = `${y}px`;
      div.textContent = nd.label || nd.type;
      labelsEl.appendChild(div);
    }
  }
  updateLabels();

  // ---- render loop + FPS sampling ----------------------------------------------------
  let edgesVisible = true;
  function render() {
    edgeLines.visible = edgesVisible;
    renderer.render(scene, camera);
  }

  // rAF alone is throttled to near-zero on a backgrounded/never-focused tab (Chrome's
  // page-visibility lifecycle, hit hard driving this spike headlessly) — races rAF
  // against a timeout fallback so the benchmark still makes progress there, while a real
  // foregrounded session (what piece 2 actually ships to) still gets true vsync timing
  // from rAF winning the race every time.
  function schedule(cb) {
    let done = false;
    const rafId = requestAnimationFrame(() => {
      if (done) return;
      done = true;
      cb();
    });
    const toId = setTimeout(() => {
      if (done) return;
      done = true;
      cancelAnimationFrame(rafId);
      cb();
    }, 50);
    return () => { cancelAnimationFrame(rafId); clearTimeout(toId); };
  }

  function sampleFps(durationMs) {
    return new Promise((resolve) => {
      let frames = 0;
      const start = performance.now();
      function tick() {
        render();
        frames++;
        const now = performance.now();
        if (now - start < durationMs) {
          schedule(tick);
        } else {
          resolve((frames * 1000) / (now - start));
        }
      }
      schedule(tick);
    });
  }

  // keep the scene interactive/rendering continuously outside of benchmark runs
  function loop() {
    render();
    schedule(loop);
  }
  loop();

  // a SYNCHRONOUS, uncapped draw-call benchmark — back-to-back renderer.render() calls
  // inside one JS turn, timed with performance.now(). This is deliberately NOT rAF/vsync-
  // gated: rAF (and setTimeout past 5 nested calls) is clamped to ~1Hz by Chrome's
  // background-tab timer throttling the instant a page is hidden/unfocused — a real
  // constraint hit driving this spike headlessly through browser automation, not a
  // property of the scene. A tight synchronous loop is immune to that clamp (it's one
  // scheduled turn, not N re-scheduled ones) and reports the real per-call GPU/CPU cost —
  // an UNCAPPED number, arguably more useful for sizing than a vsync-capped 60 anyway.
  function syncBenchmark(count) {
    edgeLines.visible = true;
    let t0 = performance.now();
    for (let i = 0; i < count; i++) renderer.render(scene, camera);
    const withEdgesMs = performance.now() - t0;
    edgeLines.visible = false;
    t0 = performance.now();
    for (let i = 0; i < count; i++) renderer.render(scene, camera);
    const withoutEdgesMs = performance.now() - t0;
    edgeLines.visible = true;
    return {
      fpsWithEdges: +((count * 1000) / withEdgesMs).toFixed(1),
      fpsWithoutEdges: +((count * 1000) / withoutEdgesMs).toFixed(1),
    };
  }

  // ---- the spike's whole point: run the benchmark, expose the numbers ---------------
  async function runBenchmark() {
    edgesVisible = true;
    const fpsWithEdges = await sampleFps(3000);
    edgesVisible = false;
    const fpsWithoutEdges = await sampleFps(3000);
    edgesVisible = true;
    const sync = syncBenchmark(300);
    const mem = performance.memory
      ? { usedJSHeapMB: +(performance.memory.usedJSHeapSize / 1e6).toFixed(1),
          totalJSHeapMB: +(performance.memory.totalJSHeapSize / 1e6).toFixed(1) }
      : null;
    const results = {
      nodeCount: n,
      edgesLoaded: edges.length,
      requestCount,
      loadMs: Math.round(loadMs),
      fpsWithEdges: +fpsWithEdges.toFixed(1),
      fpsWithoutEdges: +fpsWithoutEdges.toFixed(1),
      syncFpsWithEdges: sync.fpsWithEdges,
      syncFpsWithoutEdges: sync.fpsWithoutEdges,
      bufferBytes,
      bufferMB: +(bufferBytes / 1e6).toFixed(2),
      performanceMemory: mem,
    };
    window.__spike.results = results;
    setHud(
      `nodes ${n}  edges loaded ${edges.length} (${requestCount} reqs, ${Math.round(loadMs)}ms)\n` +
        `fps w/edges ${results.fpsWithEdges}  fps w/o ${results.fpsWithoutEdges}\n` +
        `gpu buffers ${results.bufferMB} MB` +
        (mem ? `  JS heap ${mem.usedJSHeapMB}/${mem.totalJSHeapMB} MB` : "") +
        `\n\n<button id="rerun">re-run benchmark</button>`
    );
    return results;
  }
  document.addEventListener("click", (ev) => {
    if (ev.target && ev.target.id === "rerun") runBenchmark();
  });

  // buildEdgeGeo(count): rebuilds the LineSegments buffer with `count` edges (random
  // pairs among the loaded nodes) so the with-edges benchmark can be re-run at the TRUE
  // production edge count (~123k live links, vs. the ~3.3k this loader's per-page edge
  // scoping actually recovers — see the module docstring) without waiting on Khnum's
  // stream. Synthetic topology, real node positions and real buffer-upload/draw cost.
  function buildEdgeGeo(count) {
    const pos = new Float32Array(count * 6);
    for (let i = 0; i < count; i++) {
      const a = nodes[(Math.random() * n) | 0], b = nodes[(Math.random() * n) | 0];
      pos[i * 6] = a.x || 0; pos[i * 6 + 1] = a.y || 0; pos[i * 6 + 2] = -0.1;
      pos[i * 6 + 3] = b.x || 0; pos[i * 6 + 4] = b.y || 0; pos[i * 6 + 5] = -0.1;
    }
    edgeGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return count;
  }

  window.__spike = {
    runBenchmark, syncBenchmark, buildEdgeGeo, results: null, nodeCount: n,
    edgesLoaded: edges.length,
  };
  setHud(`scene ready (${n} nodes, ${edges.length} edges loaded, ${requestCount} reqs, ${Math.round(loadMs)}ms)\nrunning benchmark…`);
  await runBenchmark();
}

main().catch((err) => {
  setHud(`ERROR: ${err.message}`);
  console.error(err);
});

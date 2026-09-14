// NAVIGABLE SPACE, THE RENDERER — piece 2, THE VIEW (thread 71c4ca0d, Thoth DM 10436,
// operator rulings f832c3a4/0a3d6719), now INTEGRATED (decision "NAVIGABLE SPACE,
// INTEGRATION SHAPE", mail 10550): mounted inside /ui/'s own browse stage in place of the
// old #cy cytoscape container, not a separate page. The operator's own repeated corrections
// settled the interaction shape, twice: (1) zoom is LOOKING only, never a data-tier switch —
// navigation is a CLICK; (2) a click doesn't change what's loaded either — "more like click
// to highlight" — every positioned object is drawn AT ONCE (matching 0a3d6719's own original
// wording, "an engine that can handle all objects at once"), and a click only lights the
// clicked object's neighborhood, dims everything else, and opens the inspector. There is no
// tier concept in this file.
//
// Labels: the operator caught a real lag bug — DOM label positions were only recomputed on
// a debounce, so they visibly fell behind the WebGL scene during a drag. Fixed by splitting
// "which nodes are labeled" (nearest-N, genuinely expensive, stays debounced) from "where do
// the ALREADY-CHOSEN labels sit on screen" (cheap — one Vector3.project() per label, no
// resort), which now runs every render frame, not just after panning/zooming settles.
//
// Data source: Khnum's GET /graph/stream (thread b6cb1d7c0b36, wire format frozen by DM
// 10439/10449/10451) — one binary snapshot, decoded client-side (decodeSnapshot below),
// no more client-side tiling/pagination. GET /graph/stream/deltas SSE-polls the outbox for
// incremental moves/retirements after the initial snapshot lands (op:'moved'|'retired').
// Positions and collision avoidance (rings) are entirely Khnum's layout heartbeat's own —
// this module reads x/y as given and never relaxes them client-side.
//
// KNOWN GAP (flagged to Khnum, DM 10554/10555, not blocking): the wire header's `types`/
// `projects` arrays resolve node type_code/project_code, but edge_type_code has no matching
// name array yet — edge color-coding below hashes the raw int until that lands, then swaps
// to real relationship names with no shape change on this side.
//
// Mounts via initSpace(container) rather than running as a page-load IIFE, so console.js
// (the live /ui/ shell) can own the container lifecycle; space.html keeps working as a
// standalone dev harness by calling initSpace() against its own fixed ids.

import * as THREE from "./vendor/three.module.js";

// colour-code an edge by its relationship type — a stable hash-to-hue, since no link-type
// palette exists server-side yet (only /schema's own object_types carry colours). Falls back
// to hashing the raw edge_type_code int until Khnum's edge_types name array lands.
const _edgeColorCache = new Map();
function colorForEdgeType(type) {
  const key = String(type);
  if (_edgeColorCache.has(key)) return _edgeColorCache.get(key);
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  const c = `hsl(${hue}, 55%, 55%)`;
  _edgeColorCache.set(key, c);
  return c;
}

// THE READING LAYER, part A: EDGE CLASSES (ruling c5953bb1, Thoth DM 10596). Structural
// edges are pure containment/membership (an object belongs to a repo, an agent operates in
// a project, a seat holds a mind) — real, but not what a reader is tracing when they ask
// "how did we get here"; their degree dwarfs everything else (repo:osiris alone: 20,352).
// Semantic edges are the actual provenance/evidence trail (possible_upstream, cites,
// derived_from, spawned_by, succeeded_from, supersedes, resolves, grounded_by, and the
// rest) — what "focus really focusing" (the operator's own words) needs to walk and show.
//
// DEFAULT, picked and noted here per Thoth's own instruction not to park on visual choices
// (thread 71c4ca0d carries this note too): every link type in src/ontology/schema.py whose
// own docstring reads as "X belongs to / operates in / is a member or officer of Y" is
// structural; everything else defaults to semantic (the safer default — an edge that's
// actually structural but misclassified just draws a bit more clutter; one that's actually
// meaningful but misclassified as structural would go invisible, the worse failure).
// Swaps to Khnum's real per-request `edge_classes` header field the moment it lands (DM
// 10603), same fallback pattern as colorForEdgeType/edge_types before it.
const STRUCTURAL_EDGE_TYPES = new Set([
  "in_repo", "works_in", "governs", "holds", "acts_for", "member_of", "employs",
  "worktree_of", "succeeds_seat", "owns", "owned_by", "subsidiary_of", "ultimate_parent",
  "sent_by", "addressed_to", "broadcast_to", "in_thread",
]);
function classOfEdgeType(type) {
  return STRUCTURAL_EDGE_TYPES.has(type) ? "structural" : "semantic";
}

// THE READING LAYER, part B (ruling c5953bb1): the curated provenance/evidence edge-type
// allowlist a real FOCUS walks — the actual "long paths leading back and upstream" the
// operator asked to see, as opposed to part A's structural containment edges, which never
// widen a path. Pure, DOM-free module-level functions (not closures inside initSpace) so
// the acceptance test Thoth's own dispatch named — "a synthetic 5-hop chain where focus at
// the tail lights exactly the chain and nothing else" — can exercise the real algorithm
// directly via Node, not a string-presence proof.
export const PATH_EDGE_TYPES = new Set([
  "possible_upstream", "cites", "derived_from", "spawned_by",
  "succeeded_from", "supersedes", "resolves",
]);
export function buildPathAdjacency(edges) {
  const outAdj = new Map(), inAdj = new Map(); // node id -> [neighbor ids]
  for (const e of edges) {
    if (!PATH_EDGE_TYPES.has(e.type)) continue;
    if (!outAdj.has(e.source)) outAdj.set(e.source, []);
    outAdj.get(e.source).push(e.target);
    if (!inAdj.has(e.target)) inAdj.set(e.target, []);
    inAdj.get(e.target).push(e.source);
  }
  return { outAdj, inAdj };
}
// bidirectional BFS, depth-limited (a widen control raises depth interactively rather than
// a hardcoded ceiling) — Osiris convention: from_id = the dependent/newer fact, to_id =
// what it points at, so "upstream" follows outAdj (X.source -> target) and "downstream...
// over the same reversed" follows inAdj.
export function walkPath(outAdj, inAdj, startId, depth) {
  const seen = new Set([startId]);
  let frontier = [startId];
  for (let d = 0; d < depth && frontier.length; d++) {
    const next = [];
    for (const cur of frontier) {
      for (const t of outAdj.get(cur) || []) { if (!seen.has(t)) { seen.add(t); next.push(t); } }
      for (const t of inAdj.get(cur) || []) { if (!seen.has(t)) { seen.add(t); next.push(t); } }
    }
    frontier = next;
  }
  return seen;
}

// ---- GET /graph/stream wire decode (a JS twin of graph_stream.decode_snapshot) ---------
// 4-byte LE uint32 header length, that many bytes of UTF-8 JSON header, then the raw arrays
// back to back at the byte offsets the header's own `arrays` map names.
const _DTYPE_CTOR = { f: Float32Array, H: Uint16Array, B: Uint8Array, I: Uint32Array };
function decodeSnapshot(buf) {
  const dv = new DataView(buf);
  const headerLen = dv.getUint32(0, true);
  const headerBytes = new Uint8Array(buf, 4, headerLen);
  const header = JSON.parse(new TextDecoder().decode(headerBytes));
  const bodyStart = 4 + headerLen;
  const out = { ...header, arrays: undefined };
  for (const [name, meta] of Object.entries(header.arrays)) {
    const Ctor = _DTYPE_CTOR[meta.dtype];
    // typed-array views need an offset that's a multiple of their own element size — the
    // wire format packs arrays back to back with no padding, so a Float32/Uint32 view at a
    // non-4-aligned offset throws; slice+copy is the safe general case (arrays here are a
    // few hundred KB at most, not worth hand-padding the server's own byte layout for).
    const byteOff = bodyStart + meta.offset;
    const bytes = buf.slice(byteOff, byteOff + meta.length * Ctor.BYTES_PER_ELEMENT);
    out[name] = new Ctor(bytes);
  }
  return out;
}

async function fetchStreamSnapshot() {
  const buf = await fetch("/graph/stream").then((r) => r.arrayBuffer());
  const snap = decodeSnapshot(buf);
  const nodes = [];
  for (let i = 0; i < snap.count; i++) {
    nodes.push({
      id: snap.object_ids[i],
      type: snap.types[snap.type_code[i]],
      project: snap.projects[snap.project_code[i]],
      x: snap.x[i], y: snap.y[i],
      degree: snap.weight[i],
      statusFlag: snap.status_flag[i],
    });
  }
  const edges = [];
  for (let i = 0; i < snap.edge_count; i++) {
    const type = (snap.edge_types && snap.edge_types[snap.edge_type_code[i]]) ?? snap.edge_type_code[i];
    // prefers Khnum's own real per-type classification (DM 10603) once the header carries
    // one; falls back to the client-side default (classOfEdgeType) until then.
    const edgeClass = snap.edge_classes && snap.edge_classes[snap.edge_type_code[i]]
      ? snap.edge_classes[snap.edge_type_code[i]]
      : classOfEdgeType(type);
    edges.push({
      source: snap.object_ids[snap.edge_src[i]],
      target: snap.object_ids[snap.edge_dst[i]],
      type, edgeClass,
    });
  }
  return { nodes, edges };
}

// resolves DOM refs from a passed-in container map, falling back to the same fixed ids
// space.html's own standalone page has always used — lets console.js mount this against
// its own #cy-replacement markup while space.html keeps working unchanged.
function resolveContainer(container) {
  const byId = (id) => document.getElementById(id);
  return {
    wrap: (container && container.wrap) || byId("canvas-wrap"),
    labelsEl: (container && container.labels) || byId("labels"),
    statusEl: (container && container.status) || byId("status-line"),
    levelBadge: (container && container.levelBadge) || byId("graph-level-badge"),
    searchInput: (container && container.searchInput) || byId("graph-search"),
    searchDd: (container && container.searchDd) || byId("graph-search-dd"),
    rightRail: (container && container.rightRail) || byId("right"),
    fitBtn: (container && container.fitBtn) || byId("fit-btn"),
    upBtn: (container && container.upBtn) || byId("up-btn"),
    legendBtn: (container && container.legendBtn) || byId("legend-btn"),
    legendPanel: (container && container.legendPanel) || byId("legend-panel"),
    backBtn: (container && container.backBtn) || byId("back-btn"),
    widenBtn: (container && container.widenBtn) || byId("widen-btn"),
    onFocus: (container && container.onFocus) || null, // (id) => void, shares selection with the table
  };
}

export async function initSpace(container) {
  const { wrap, labelsEl, statusEl, levelBadge, searchInput, searchDd, rightRail, fitBtn, upBtn,
    legendBtn, legendPanel, backBtn, widenBtn, onFocus } =
    resolveContainer(container);
  function setStatus(text) { statusEl.textContent = text; }

  const typeColors = await loadTypeColors();

  // ---- renderer / scene / camera -----------------------------------------------------
  // pixel ratio capped at 1.5 and antialias only below/at native DPR (Thoth's own live
  // measurement on an Iris Xe box, mail 10581): AA is a real GPU cost that scales with
  // resolution, and stacking it on top of an already-high device pixel ratio was part of
  // what made a real laptop GPU choke on this scene.
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  const renderer = new THREE.WebGLRenderer({ antialias: dpr <= 1 });
  // three.js's ColorManagement converts every hex colour (THREE.Color.set('#8ab4f8')) from
  // sRGB into LINEAR space internally — without this, the renderer displays those linear
  // values as-is, reading systematically darker than the real colour.
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setPixelRatio(dpr);
  renderer.setSize(wrap.clientWidth, wrap.clientHeight);
  wrap.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1219);
  const pickScene = new THREE.Scene();

  // world extent depends entirely on Khnum's own layout heartbeat (deterministic hash
  // placement, piece A) and is NOT a fixed constant — a project-center hash can land
  // anywhere; fitToNodes() (below) frames the camera from the real loaded bbox instead of
  // a guessed number the moment the first snapshot lands, and Fit re-measures live rather
  // than resetting to a stale guess. minViewSize/maxViewSize (Thoth's own live-verified fix,
  // mail 10581) are likewise derived from the real fitted bbox, not the old hardcoded
  // [8, 2000] clamp — that clamp predated the deterministic layout and let one wheel tick
  // snap a 259,779-unit-wide view down to 2,862 (a 90x jump into a single dense cluster,
  // read by the operator as "zoom does not work").
  let viewSize = 1300;
  let minViewSize = 20, maxViewSize = 2000;
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
  camera.position.set(0, 0, 5);
  camera.lookAt(0, 0, 0);
  function updateFrustum() {
    const a = wrap.clientWidth / wrap.clientHeight;
    camera.left = (-viewSize * a) / 2;
    camera.right = (viewSize * a) / 2;
    camera.top = viewSize / 2;
    camera.bottom = -viewSize / 2;
    camera.updateProjectionMatrix();
  }
  updateFrustum();
  window.addEventListener("resize", () => {
    renderer.setSize(wrap.clientWidth, wrap.clientHeight);
    updateFrustum();
    edgeFadeUniforms.uViewportPx.value.set(wrap.clientWidth, wrap.clientHeight);
    markDirty();
  });

  // ---- render ON DEMAND (Thoth's own live measurement, mail 10581): the render loop used
  // to run every frame forever (rAF plus a 50ms setTimeout fallback), even when browse
  // isn't the active surface or the tab is hidden — pure waste, and on top of the
  // per-wheel-event instance-buffer rewrite this fix removes below, it compounded into the
  // "super fried" report. Now a frame only renders when something actually changed
  // (camera move, data, focus, label pick); the loop stops scheduling itself entirely once
  // idle rather than polling at 20fps forever.
  let dirty = true, running = true, rafPending = false;
  function markDirty() {
    dirty = true;
    if (!running || rafPending) return;
    rafPending = true;
    requestAnimationFrame(renderIfDirty);
  }
  function renderIfDirty() {
    rafPending = false;
    if (!running || !dirty) return;
    renderer.render(scene, camera);
    positionLabels();
    dirty = false;
  }
  function pause() { running = false; }
  function resume() { running = true; markDirty(); }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pause(); else resume();
  });

  // WebGL context loss (Thoth's own live report: the first load in her tab was refused
  // outright, "Web page caused context loss and was blocked", and a later tab vanished —
  // a GPU reset Chrome then blocks the page from reusing). preventDefault on loss keeps the
  // browser from tearing the canvas down permanently; rebuild GPU resources on restore
  // instead of leaving a dead black canvas or crashing the tab.
  renderer.domElement.addEventListener("webglcontextlost", (ev) => {
    ev.preventDefault();
    pause();
    setStatus("WebGL context lost — recovering…");
  }, false);
  renderer.domElement.addEventListener("webglcontextrestored", () => {
    setStatus("WebGL context restored — rebuilding…");
    if (idToNode.length) {
      buildScene(idToNode, edges);
      fitToNodes(idToNode);
      setStatus(`${idToNode.length} objects, ${edges.length} edges (recovered)`);
    }
    resume();
  }, false);

  let mesh = null, pickMesh = null, edgeLines = null;
  let meshUniforms = null, pickUniforms = null;
  let idToNode = [];
  // THE READING LAYER, part B: FOCUS = PATH LENS (ruling c5953bb1, Thoth DM 10596). SELECT
  // (a plain click) and FOCUS (double-click, Enter, or the inspector's Focus button) are now
  // two different acts — selectedId just shows the inspector; pathFocusId/pathReachable are
  // the real path-lens state (only non-empty while an actual focus is active).
  let selectedId = null;
  let pathFocusId = null;
  let pathReachable = new Set();
  let focusStack = []; // ids, most recent last — back() pops, Escape/Clear focus wipes the overlay

  function disposeCurrent() {
    for (const m of [mesh, pickMesh, edgeLines]) {
      if (!m) continue;
      scene.remove(m); pickScene.remove(m);
      m.geometry.dispose();
      if (Array.isArray(m.material)) m.material.forEach((x) => x.dispose());
      else m.material.dispose();
    }
    mesh = pickMesh = edgeLines = null;
  }

  // SIZE: a category base (agent > plain object) x a narrow 1x-3x asymptotic multiplier
  // off the object's own live link degree — bounded in SCREEN PIXELS, converted to world
  // units against the current viewSize so a node reads the same visual size across a zoom
  // range (rescaleForZoom, called on every wheel step).
  //
  // The divisor was originally guessed (20) and the operator correctly called it out as
  // not really working — MEASURED against the real distribution instead (48,997 objects):
  // p50=3, p75=4, p90=4, p95=6, p99=23, max=20,295. A divisor of 20 barely moves the curve
  // for the 90% of objects sitting at degree 3-4 (~1.15-1.33x), so almost everything looked
  // the same size. Retuned to 14 so degree=6 (p95, "clearly above average") already reads
  // ~1.6x and true hubs (p99+, 20+) approach the 3x cap, while the P50-P90 bulk (3-4) still
  // stays a modest ~1.3-1.4x — distinguishable without every ordinary node looking inflated.
  const CATEGORY_BASE_PX = { agent: 8, object: 5 };
  const DEGREE_CURVE_DIVISOR = 14;
  function categoryOf(nd) { return nd.type === "Agent" ? "agent" : "object"; }
  function degreeFactor(nd) {
    const signal = nd.degree || 0;
    return 1 + 2 * (1 - 1 / (1 + signal / DEGREE_CURVE_DIVISOR)); // asymptotic 1x -> 3x
  }
  function nodeRadiusPx(nd) { return CATEGORY_BASE_PX[categoryOf(nd)] * degreeFactor(nd); }
  function worldPerPx() { return viewSize / wrap.clientHeight; }

  // SCREEN-CONSTANT SIZE ON THE GPU (Thoth's own live measurement, mail 10581): a node's
  // pixel radius must stay constant across zoom, but rewriting 49,019 instance matrices
  // (getMatrixAt/decompose/setMatrixAt on BOTH meshes, two ~3MB buffer re-uploads) on every
  // single wheel event measured at 18.6ms JS for one tick — a real scroll gesture is 20-60
  // events, so seconds of main-thread stall plus a GPU upload storm per scroll. Fixed by
  // moving the per-frame-varying part (worldPerPx) into a material uniform and the
  // per-node-but-frame-CONSTANT part (radiusPx) into a static instanced attribute: the
  // vertex shader multiplies them together at draw time, so a zoom step touches exactly one
  // float per material (two total) no matter how many nodes are on screen, and the instance
  // matrices only ever hold translation (+ the existing degree z-bias), set once at build
  // time and never rewritten again until the data itself changes.
  function makeInstancedCircleMaterial() {
    const uniforms = { uWorldPerPx: { value: worldPerPx() } };
    const mat = new THREE.MeshBasicMaterial({ vertexColors: true });
    mat.onBeforeCompile = (shader) => {
      shader.uniforms.uWorldPerPx = uniforms.uWorldPerPx;
      shader.vertexShader =
        "attribute float aRadiusPx;\nuniform float uWorldPerPx;\n" + shader.vertexShader;
      shader.vertexShader = shader.vertexShader.replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\n\ttransformed *= (aRadiusPx * uWorldPerPx);"
      );
    };
    mat.customProgramCacheKey = () => "circleInstancedUniformScale";
    return { material: mat, uniforms };
  }

  // THE READING LAYER, part A: edges fade by SCREEN length, not by zoom level — a long line
  // crossing most of the view (two clusters that happen to be linked) reads as noise; a
  // short local one is the actual signal. Same GPU-uniform discipline as node sizing (mail
  // 10581): each vertex carries the OTHER endpoint's world position too (`otherPosition`),
  // so the vertex shader can project both ends to screen pixels and compute the segment's
  // own on-screen length using nothing but modelViewMatrix/projectionMatrix — already
  // updated by three.js every frame for free. No per-zoom CPU work, no material.opacity
  // scalar to keep in sync (replaces the old viewSize-based updateEdgeStyle entirely).
  const edgeFadeUniforms = {
    uViewportPx: { value: new THREE.Vector2(wrap.clientWidth, wrap.clientHeight) },
    uMaxFadePx: { value: 320 },
    uMinAlpha: { value: 0.04 },
    uMaxAlpha: { value: 0.5 },
  };
  function makeEdgeFadeMaterial() {
    return new THREE.ShaderMaterial({
      uniforms: edgeFadeUniforms,
      transparent: true,
      depthWrite: false,
      vertexShader: `
        attribute vec3 color;
        attribute vec3 otherPosition;
        uniform vec2 uViewportPx;
        uniform float uMaxFadePx;
        uniform float uMinAlpha;
        uniform float uMaxAlpha;
        varying vec3 vColor;
        varying float vAlpha;
        void main() {
          vColor = color;
          vec4 clip = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          vec4 otherClip = projectionMatrix * modelViewMatrix * vec4(otherPosition, 1.0);
          vec2 pxA = (clip.xy / clip.w * 0.5 + 0.5) * uViewportPx;
          vec2 pxB = (otherClip.xy / otherClip.w * 0.5 + 0.5) * uViewportPx;
          float screenLen = distance(pxA, pxB);
          vAlpha = mix(uMaxAlpha, uMinAlpha, clamp(screenLen / uMaxFadePx, 0.0, 1.0));
          gl_Position = clip;
        }
      `,
      fragmentShader: `
        varying vec3 vColor;
        varying float vAlpha;
        void main() { gl_FragColor = vec4(vColor, vAlpha); }
      `,
    });
  }

  // legend state: which edge classes/types are hidden from the base render. Structural is
  // hidden by DEFAULT ("not drawn at rest", ruling c5953bb1) — the legend is how a reader
  // opts back into seeing it without needing to focus a specific node.
  const hiddenEdgeClasses = new Set(["structural"]);
  const hiddenEdgeTypes = new Set();
  function buildEdgeLines(nodes, edgeList) {
    if (edgeLines) { scene.remove(edgeLines); edgeLines.geometry.dispose(); edgeLines.material.dispose(); edgeLines = null; }
    const visible = edgeList.filter((e) => !hiddenEdgeClasses.has(e.edgeClass) && !hiddenEdgeTypes.has(e.type));
    const positions = new Float32Array(visible.length * 6);
    const otherPositions = new Float32Array(visible.length * 6);
    const edgeColors = new Float32Array(visible.length * 6);
    const idx = new Map(nodes.map((nd, i) => [nd.id, i]));
    const ec = new THREE.Color();
    let vi = 0;
    for (const e of visible) {
      const a = idx.get(e.source), b = idx.get(e.target);
      if (a == null || b == null) continue;
      const na = nodes[a], nb = nodes[b];
      positions[vi] = na.x || 0; positions[vi + 1] = na.y || 0; positions[vi + 2] = -0.1;
      otherPositions[vi] = nb.x || 0; otherPositions[vi + 1] = nb.y || 0; otherPositions[vi + 2] = -0.1;
      vi += 3;
      positions[vi] = nb.x || 0; positions[vi + 1] = nb.y || 0; positions[vi + 2] = -0.1;
      otherPositions[vi] = na.x || 0; otherPositions[vi + 1] = na.y || 0; otherPositions[vi + 2] = -0.1;
      vi += 3;
      // colour-coded by relationship type ("that would make a ton of sense" — no link-type
      // palette exists server-side, so a stable hash-to-hue keeps a given edge type the
      // same colour across reloads without inventing new server state).
      ec.set(colorForEdgeType(e.type));
      edgeColors[vi - 6] = ec.r; edgeColors[vi - 5] = ec.g; edgeColors[vi - 4] = ec.b;
      edgeColors[vi - 3] = ec.r; edgeColors[vi - 2] = ec.g; edgeColors[vi - 1] = ec.b;
    }
    const edgeGeo = new THREE.BufferGeometry();
    edgeGeo.setAttribute("position", new THREE.BufferAttribute(positions.subarray(0, vi), 3));
    edgeGeo.setAttribute("otherPosition", new THREE.BufferAttribute(otherPositions.subarray(0, vi), 3));
    edgeGeo.setAttribute("color", new THREE.BufferAttribute(edgeColors.subarray(0, vi), 3));
    edgeLines = new THREE.LineSegments(edgeGeo, makeEdgeFadeMaterial());
    scene.add(edgeLines);
    renderLegend(edgeList);
    markDirty();
  }

  // legend: lists every class + type actually present in the loaded data, checkbox per
  // row, toggling straight into hiddenEdgeClasses/hiddenEdgeTypes and rebuilding the edge
  // geometry — a legend toggle is a rare, deliberate act, never a per-frame cost.
  function renderLegend(edgeList) {
    if (!legendPanel) return;
    const classOf = new Map();
    for (const e of edgeList) classOf.set(e.type, e.edgeClass);
    const byClass = { semantic: [], structural: [] };
    for (const [type, cls] of classOf) (byClass[cls] || (byClass[cls] = [])).push(type);
    for (const k of Object.keys(byClass)) byClass[k].sort();

    const classRow = (cls) => {
      const checked = hiddenEdgeClasses.has(cls) ? "" : "checked";
      const count = (byClass[cls] || []).length;
      return `<label class="legend-row legend-class"><input type="checkbox" data-legend-class="${cls}" ${checked} /> <strong>${cls}</strong> <span class="o-faint">(${count})</span></label>`;
    };
    const typeRow = (type) => {
      const checked = hiddenEdgeTypes.has(type) ? "" : "checked";
      const esc = String(type).replace(/"/g, "&quot;");
      return `<label class="legend-row legend-type"><input type="checkbox" data-legend-type="${esc}" ${checked} /> <span class="legend-swatch" style="background:${colorForEdgeType(type)}"></span>${esc}</label>`;
    };
    legendPanel.innerHTML =
      classRow("semantic") + (byClass.semantic || []).map(typeRow).join("") +
      classRow("structural") + (byClass.structural || []).map(typeRow).join("");

    legendPanel.querySelectorAll("[data-legend-class]").forEach((el) => {
      el.addEventListener("change", () => {
        const cls = el.dataset.legendClass;
        if (el.checked) hiddenEdgeClasses.delete(cls); else hiddenEdgeClasses.add(cls);
        buildEdgeLines(idToNode, edges);
      });
    });
    legendPanel.querySelectorAll("[data-legend-type]").forEach((el) => {
      el.addEventListener("change", () => {
        const type = el.dataset.legendType;
        if (el.checked) hiddenEdgeTypes.delete(type); else hiddenEdgeTypes.add(type);
        buildEdgeLines(idToNode, edges);
      });
    });
  }
  if (legendBtn && legendPanel) {
    legendBtn.addEventListener("click", () => { legendPanel.hidden = !legendPanel.hidden; });
  }

  function buildScene(nodes, edges) {
    disposeCurrent();
    idToNode = nodes;
    const n = nodes.length;
    const geo = new THREE.CircleGeometry(1, 10);
    // this three.js build's fragment shader only multiplies by vColor (and so only shows
    // instanceColor) when USE_COLOR/USE_COLOR_ALPHA is defined, which is driven by a
    // GEOMETRY-level `color` attribute, not instanceColor alone — the vertex shader
    // computes the right colour into vColor but the fragment shader silently drops it
    // without this, rendering flat black regardless of instanceColor.
    geo.setAttribute("color", new THREE.Float32BufferAttribute(
      new Float32Array(geo.attributes.position.count * 3).fill(1), 3));
    const radiusAttr = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1)), 1);
    geo.setAttribute("aRadiusPx", radiusAttr); // shared by mesh + pickMesh, same geometry instance

    const built = makeInstancedCircleMaterial();
    const mat = built.material;
    meshUniforms = built.uniforms;
    mesh = new THREE.InstancedMesh(geo, mat, Math.max(n, 1));
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1) * 3), 3);

    const builtPick = makeInstancedCircleMaterial();
    const pickMat = builtPick.material;
    pickUniforms = builtPick.uniforms;
    pickMesh = new THREE.InstancedMesh(geo, pickMat, Math.max(n, 1));
    pickMesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1) * 3), 3);

    const dummy = new THREE.Object3D();
    const color = new THREE.Color();
    const idColor = new THREE.Color();
    for (let i = 0; i < n; i++) {
      const nd = nodes[i];
      nd.radiusPx = nodeRadiusPx(nd);
      radiusAttr.setX(i, nd.radiusPx);
      // "sizing more intuitive where high-degree nodes stand out without obfuscating
      // smaller nodes" — a bigger circle can still sit BEHIND a smaller one drawn later
      // in the same z-plane; give every node a tiny z bias proportional to its own radius
      // so the important (bigger) ones are always nearer the camera and never occluded.
      // Scale stays 1 here deliberately — the shader (aRadiusPx * uWorldPerPx) owns sizing.
      dummy.position.set(nd.x || 0, nd.y || 0, nd.radiusPx * 0.002);
      dummy.scale.setScalar(1);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      pickMesh.setMatrixAt(i, dummy.matrix);
      color.set(typeColors.get(nd.type) || "#6e7681");
      mesh.instanceColor.setXYZ(i, color.r, color.g, color.b);
      const id = i + 1;
      idColor.setRGB((id & 0xff) / 255, ((id >> 8) & 0xff) / 255, ((id >> 16) & 0xff) / 255);
      pickMesh.instanceColor.setXYZ(i, idColor.r, idColor.g, idColor.b);
    }
    radiusAttr.needsUpdate = true;
    mesh.instanceMatrix.needsUpdate = true;
    pickMesh.instanceMatrix.needsUpdate = true;
    scene.add(mesh);
    pickScene.add(pickMesh);

    buildEdgeLines(nodes, edges);
    applyDim();
    markDirty();
  }

  // SELECT (a plain click) never dims the graph — only FOCUS does, and only focus dims
  // "to near invisible" (ruling c5953bb1's own wording, stronger than the old 0.12 factor):
  // a real path lens has to actually read as a lens, not a faint tint.
  const FOCUS_DIM_FACTOR = 0.03;
  function applyDim() {
    if (!mesh) return;
    const color = new THREE.Color();
    const focused = !!pathFocusId;
    for (let i = 0; i < idToNode.length; i++) {
      const nd = idToNode[i];
      color.set(typeColors.get(nd.type) || "#6e7681");
      if (focused) {
        if (nd.id === pathFocusId) color.set("#58a6ff");
        else if (!pathReachable.has(nd.id)) color.multiplyScalar(FOCUS_DIM_FACTOR);
        // else: reachable-but-not-focused nodes keep their normal type colour — still
        // legible as part of the path, the focused node alone gets the accent colour.
      } else if (nd.id === selectedId) {
        color.set("#58a6ff");
      }
      mesh.instanceColor.setXYZ(i, color.r, color.g, color.b);
    }
    mesh.instanceColor.needsUpdate = true;
    markDirty();
  }

  async function loadTypeColors() {
    const cat = await fetch("/schema").then((r) => r.json());
    const m = new Map();
    for (const t of cat.object_types) m.set(t.name, t.color || "#6e7681");
    return m;
  }

  // frames the camera around the REAL bounding box of whatever's currently loaded —
  // the fixed viewSize=1300 default this used to reset to was measured against the old
  // force-relax layout's small extent and reads as "zoomed into one dense cluster" against
  // Khnum's new deterministic hash-placement layout, whose extent can run tens of
  // thousands of world units wide depending on how far apart two project hashes land.
  function fitToNodes(list) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const nd of list) {
      if (nd.x == null || nd.y == null) continue;
      minX = Math.min(minX, nd.x); maxX = Math.max(maxX, nd.x);
      minY = Math.min(minY, nd.y); maxY = Math.max(maxY, nd.y);
    }
    if (!Number.isFinite(minX)) return;
    camera.position.x = (minX + maxX) / 2;
    camera.position.y = (minY + maxY) / 2;
    const span = Math.max(maxX - minX, maxY - minY, 0);
    viewSize = Math.max(30, span * 1.1 + 40);
    // the wheel clamp's own bounds (Thoth's fix, mail 10581) — derived from THIS fit's real
    // span, not a guess: a floor small enough to inspect one dense cluster, a ceiling about
    // 2x the whole fitted graph so "zoom out" can't run away past anything meaningful.
    minViewSize = 20;
    maxViewSize = Math.max(span * 2, 200);
    updateFrustum();
    rescaleForZoom();
    markDirty();
  }

  setStatus("loading the whole graph…");
  let { nodes, edges } = await fetchStreamSnapshot();
  buildScene(nodes, edges);
  fitToNodes(nodes);
  setStatus(`${nodes.length} objects, ${edges.length} edges`);
  levelBadge.textContent = "whole graph";

  // ---- deltas: GET /graph/stream/deltas is an SSE poll-diff over the outbox, keyed by
  // object id (not array index — see the module docstring). Applied live so the canvas
  // never needs a full reload after the first snapshot; a 'retired' delta drops the node
  // from the next full rebuild rather than trying to hide a single InstancedMesh instance
  // (there is no per-instance visibility toggle cheaper than a rebuild at this node count).
  let nodesById = new Map(nodes.map((nd) => [nd.id, nd]));
  let pendingRebuild = false;
  function scheduleRebuild() {
    if (pendingRebuild) return;
    pendingRebuild = true;
    setTimeout(() => {
      pendingRebuild = false;
      nodes = Array.from(nodesById.values());
      buildScene(nodes, edges);
      if (pathFocusId || selectedId) applyDim();
      setStatus(`${nodes.length} objects, ${edges.length} edges (live)`);
    }, 250);
  }
  try {
    const es = new EventSource("/graph/stream/deltas");
    es.onmessage = (ev) => {
      let delta;
      try { delta = JSON.parse(ev.data); } catch { return; }
      if (delta.op === "retired") {
        nodesById.delete(delta.id);
        scheduleRebuild();
      } else if (delta.op === "moved") {
        const nd = nodesById.get(delta.id);
        if (nd && delta.x != null && delta.y != null) { nd.x = delta.x; nd.y = delta.y; scheduleRebuild(); }
      }
    };
    es.onerror = () => { /* browser auto-reconnects an EventSource; nothing to do here */ };
  } catch (err) {
    console.error("graph/stream/deltas unavailable", err);
  }

  // THE READING LAYER, part B: the PATH — walked CLIENT-SIDE off the already-loaded edge
  // list, over a curated allowlist of provenance/evidence link types only (ruling c5953bb1's
  // own list) — structural containment (in_repo, works_in, ...) never widens a path, that's
  // exactly what part A excludes from "what a reader is tracing". Osiris convention: from_id
  // = the dependent/newer fact, to_id = what it points at — "upstream" from X follows
  // OUTGOING edges (X.source -> target); "downstream... over the same reversed" follows
  // INCOMING edges. Both directions, one BFS, depth-limited (not count-capped like the old
  // walkUpstream) with a widen control (focusDepth) instead of a hardcoded ceiling.
  // buildPathAdjacency/walkPath are pure, DOM-free, module-level functions (below the
  // module docstring) precisely so THE ACCEPTANCE TEST Thoth's own dispatch named — "a
  // synthetic 5-hop chain where focus at the tail lights exactly the chain and nothing
  // else" — can exercise the real algorithm directly via Node, not a string-presence proof.
  const FOCUS_DEPTH_DEFAULT = 4;
  let focusDepth = FOCUS_DEPTH_DEFAULT;
  const { outAdj: outAdjPath, inAdj: inAdjPath } = buildPathAdjacency(edges);

  // a second LineSegments drawn OVER the dim base edges: the reachable PATH edges (bright,
  // WITH DIRECTION — a vertex-colour gradient, brighter at the source/dependent end, dimmer
  // at the target/depended-on end, per Osiris's own from_id->to_id convention) plus, per
  // ruling c5953bb1, "the focused object's structural edges draw on focus only" — the
  // focused node's own containment (which project, which agent) becomes visible exactly
  // because it's focused, even though part A hides structural edges at rest.
  let pathHighlightEdges = null;
  const PATH_EDGE_BRIGHT = new THREE.Color(0x58a6ff);
  const PATH_EDGE_DIM = new THREE.Color(0x58a6ff).multiplyScalar(0.35);
  function updatePathEdges() {
    if (pathHighlightEdges) {
      scene.remove(pathHighlightEdges);
      pathHighlightEdges.geometry.dispose();
      pathHighlightEdges.material.dispose();
      pathHighlightEdges = null;
    }
    markDirty();
    if (!pathFocusId) return;
    const idx = new Map(idToNode.map((nd) => [nd.id, nd]));
    const pos = [], col = [];
    for (const e of edges) {
      const onPath = PATH_EDGE_TYPES.has(e.type) && pathReachable.has(e.source) && pathReachable.has(e.target);
      const structuralOfFocus = e.edgeClass === "structural" && (e.source === pathFocusId || e.target === pathFocusId);
      if (!onPath && !structuralOfFocus) continue;
      const a = idx.get(e.source), b = idx.get(e.target);
      if (!a || !b) continue;
      pos.push(a.x || 0, a.y || 0, -0.05, b.x || 0, b.y || 0, -0.05);
      col.push(PATH_EDGE_BRIGHT.r, PATH_EDGE_BRIGHT.g, PATH_EDGE_BRIGHT.b,
        PATH_EDGE_DIM.r, PATH_EDGE_DIM.g, PATH_EDGE_DIM.b);
    }
    if (!pos.length) return;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(pos), 3));
    geo.setAttribute("color", new THREE.Float32BufferAttribute(new Float32Array(col), 3));
    pathHighlightEdges = new THREE.LineSegments(
      geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.85 }));
    scene.add(pathHighlightEdges);
  }

  // ---- pan + zoom — LOOKING ONLY, never changes what's loaded ------------------------
  // a browser fires a real "click" event at pointerup even after a long drag, as long as
  // it lands back on the same element — the operator caught this exactly ("dragging and
  // releasing... refocuses on a random object"). Track total drag distance and only treat
  // the click handler's pick as genuine below a small pixel threshold; anything past that
  // was a pan, not a click, and the trailing click event is swallowed.
  let dragging = false, lastX = 0, lastY = 0, dragDistance = 0;
  const CLICK_SLOP_PX = 4;
  renderer.domElement.addEventListener("pointerdown", (ev) => {
    dragging = true; lastX = ev.clientX; lastY = ev.clientY; dragDistance = 0;
  });
  window.addEventListener("pointerup", () => { dragging = false; });
  window.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    const dx = ev.clientX - lastX, dy = ev.clientY - lastY;
    lastX = ev.clientX; lastY = ev.clientY;
    dragDistance += Math.hypot(dx, dy);
    const wpx = (camera.right - camera.left) / wrap.clientWidth;
    const wpy = (camera.top - camera.bottom) / wrap.clientHeight;
    camera.position.x -= dx * wpx;
    camera.position.y += dy * wpy;
    // no scheduleLabelUpdate() here — label POSITIONS are repainted on every render this
    // markDirty() triggers (render-on-demand, see the loop below); only WHICH labels show
    // is still debounced (scheduleLabelPick).
    markDirty();
    scheduleLabelPick();
  });

  // O(1) regardless of node count now — sizing lives in the shader (aRadiusPx * uniform,
  // see makeInstancedCircleMaterial), so a zoom step only ever writes two floats (the draw
  // mesh's and pick mesh's own uWorldPerPx) instead of rewriting 49,019 instance matrices.
  function rescaleForZoom() {
    const wpp = worldPerPx();
    if (meshUniforms) meshUniforms.uWorldPerPx.value = wpp;
    if (pickUniforms) pickUniforms.uWorldPerPx.value = wpp;
    // no edge-style call here any more — the edge-fade shader (makeEdgeFadeMaterial) reads
    // screen length straight off projectionMatrix/modelViewMatrix every render, already
    // current every frame with zero extra work on a zoom step.
  }

  // wheel = LOOKING ONLY, cursor-anchored (Thoth's own live fix, mail 10581 item 5: "zoom
  // is not anchored at the cursor"), coalesced to one update per animation frame no matter
  // how many wheel events land in that frame (a real trackpad/mouse burst is 20-60 events —
  // each one used to trigger its own full rescale; now each just accumulates a delta, and
  // ONE zoomAt() runs per frame).
  let pendingWheelDelta = 0, wheelClientX = 0, wheelClientY = 0, wheelRafPending = false;
  function zoomAt(clientX, clientY, deltaY) {
    const rect = wrap.getBoundingClientRect();
    const nx = rect.width ? (clientX - rect.left) / rect.width : 0.5;
    const ny = rect.height ? (clientY - rect.top) / rect.height : 0.5;
    const worldX = camera.position.x + THREE.MathUtils.lerp(camera.left, camera.right, nx);
    const worldY = camera.position.y + THREE.MathUtils.lerp(camera.top, camera.bottom, ny);
    viewSize = Math.max(minViewSize, Math.min(maxViewSize, viewSize * Math.exp(deltaY * 0.001)));
    updateFrustum();
    // re-anchor: keep the same world point under the cursor after the frustum resize.
    camera.position.x = worldX - THREE.MathUtils.lerp(camera.left, camera.right, nx);
    camera.position.y = worldY - THREE.MathUtils.lerp(camera.top, camera.bottom, ny);
    rescaleForZoom();
    scheduleLabelPick();
    markDirty();
  }
  function applyPendingWheel() {
    wheelRafPending = false;
    if (pendingWheelDelta === 0) return;
    const deltaY = pendingWheelDelta;
    pendingWheelDelta = 0;
    zoomAt(wheelClientX, wheelClientY, deltaY);
  }
  renderer.domElement.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      pendingWheelDelta += ev.deltaY;
      wheelClientX = ev.clientX; wheelClientY = ev.clientY;
      if (!wheelRafPending) { wheelRafPending = true; requestAnimationFrame(applyPendingWheel); }
    },
    { passive: false }
  );

  // ---- GPU picking (unchanged from the spike) ----------------------------------------
  const pickTarget = new THREE.WebGLRenderTarget(1, 1);
  const pickBuf = new Uint8Array(4);
  function pickAt(clientX, clientY) {
    const rect = renderer.domElement.getBoundingClientRect();
    // setViewOffset's (x,y) origin is TOP-LEFT (matching a mouse event's own coordinates,
    // per its own docstring's tile-grid example: A/B/C at y=0 sit ABOVE D/E/F at y=h) —
    // NOT WebGL's bottom-left convention. The earlier flip here was the real cause of
    // clicks missing the node visually under the cursor.
    const px = (clientX - rect.left) * (window.devicePixelRatio || 1);
    const py = (clientY - rect.top) * (window.devicePixelRatio || 1);
    camera.setViewOffset(renderer.domElement.width, renderer.domElement.height, px, py, 1, 1);
    renderer.setRenderTarget(pickTarget);
    renderer.render(pickScene, camera);
    renderer.setRenderTarget(null);
    camera.clearViewOffset();
    renderer.readRenderTargetPixels(pickTarget, 0, 0, 1, 1, pickBuf);
    const id = pickBuf[0] | (pickBuf[1] << 8) | (pickBuf[2] << 16);
    return id === 0 ? null : idToNode[id - 1];
  }

  // click = SELECT (inspector only, no dim, no camera move); dblclick/Enter/the inspector's
  // own Focus button = the real path lens (focusObject, below). A plain click also clears
  // any active focus overlay first — a fresh inspection supersedes the last path, only the
  // explicit Back/Escape acts are about navigating the focus history itself.
  renderer.domElement.addEventListener("click", (ev) => {
    if (dragDistance > CLICK_SLOP_PX) return; // the trailing click after a real pan/drag
    const hit = pickAt(ev.clientX, ev.clientY);
    if (hit) selectObject(hit.id);
    else clearFocus();
  });
  renderer.domElement.addEventListener("dblclick", (ev) => {
    const hit = pickAt(ev.clientX, ev.clientY);
    if (hit) focusObject(hit.id);
  });

  function clearFocus() {
    selectedId = null;
    pathFocusId = null;
    pathReachable = new Set();
    applyDim();
    updatePathEdges();
    rightRail.className = "rail";
    rightRail.innerHTML =
      '<div class="insp-empty" id="insp"><div style="font-weight:700;font-size:13px;' +
      'letter-spacing:0.5px;text-transform:uppercase;color:var(--text);margin-bottom:8px">' +
      "Provenance Inspector</div>Click any object to inspect its evidence grade and relationships.</div>";
    setStatus(`${idToNode.length} objects, ${edges.length} edges`);
    if (onFocus) onFocus(null); // shares the clear with an embedding table (console.js)
  }

  // SELECT: inspector only, no dim, no path walk — the lightweight act. `opts.pan` (THE
  // READING LAYER part C, "harmony": "a table selection pans the graph") re-centers the
  // camera on the node at the CURRENT zoom level, without space's own click doing this too
  // — clicking a node already on screen has no reason to re-pan under the cursor.
  async function selectObject(id, opts) {
    selectedId = id;
    pathFocusId = null;
    pathReachable = new Set();
    if (onFocus) onFocus(id);
    if (opts && opts.pan) {
      const nd = idToNode.find((n) => n.id === id);
      if (nd && nd.x != null && nd.y != null) {
        camera.position.x = nd.x;
        camera.position.y = nd.y;
        markDirty();
      }
    }
    applyDim();
    updatePathEdges();
    await inspect(id);
  }

  fitBtn.addEventListener("click", () => {
    fitToNodes(idToNode);
    scheduleLabelPick();
  });
  upBtn.addEventListener("click", clearFocus);
  if (backBtn) backBtn.addEventListener("click", goBack);
  if (widenBtn) widenBtn.addEventListener("click", () => {
    focusDepth = Math.min(focusDepth + 1, 20);
    if (pathFocusId) focusObject(pathFocusId, { skipStackPush: true, depth: focusDepth });
  });

  function pushFocusStack(id) {
    if (focusStack[focusStack.length - 1] === id) return;
    focusStack.push(id);
    if (focusStack.length > 50) focusStack.shift();
  }
  function goBack() {
    if (focusStack.length < 2) { clearFocus(); return; }
    focusStack.pop(); // the current focus
    const prev = focusStack[focusStack.length - 1];
    focusObject(prev, { skipStackPush: true });
  }

  // ---- FOCUS = PATH LENS (ruling c5953bb1): walks upstream+downstream over the curated
  // provenance edge types, dims everything unreachable to near invisible, draws the
  // reachable path bright with direction, fits the camera to the reachable set, labels the
  // path, reveals the focused object's own structural edges. Never a data reload — the
  // whole graph is already loaded, this only ever changes what's highlighted.
  async function focusObject(id, opts) {
    const options = opts || {};
    selectedId = id;
    pathFocusId = id;
    focusDepth = options.depth || FOCUS_DEPTH_DEFAULT;
    pathReachable = walkPath(outAdjPath, inAdjPath, id, focusDepth);
    if (!options.skipStackPush) pushFocusStack(id);
    if (onFocus) onFocus(id); // shares the selection with an embedding table (console.js)

    // zoom-to-fit: frame the camera around exactly the reachable set's own bounding box
    // (padded), not a fixed small viewSize centered on the click — "zooming them to where
    // they make sense," per the operator. A single-node path (nothing else reachable) still
    // gets a sane close-in view rather than a zero-size frustum.
    const idx = new Map(idToNode.map((nd) => [nd.id, nd]));
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const rid of pathReachable) {
      const nd = idx.get(rid);
      if (!nd || nd.x == null || nd.y == null) continue;
      minX = Math.min(minX, nd.x); maxX = Math.max(maxX, nd.x);
      minY = Math.min(minY, nd.y); maxY = Math.max(maxY, nd.y);
    }
    if (Number.isFinite(minX)) {
      camera.position.x = (minX + maxX) / 2;
      camera.position.y = (minY + maxY) / 2;
      const span = Math.max(maxX - minX, maxY - minY, 0);
      // padding + a sane floor/ceiling — the ceiling rides maxViewSize (the real fitted
      // graph's own extent, set in fitToNodes) rather than a hardcoded 1300: the same class
      // of stale-constant bug Thoth caught in the wheel clamp (mail 10581) would otherwise
      // clip a legitimately wide-spread path back down to a fixed small view.
      viewSize = Math.max(30, Math.min(maxViewSize, span * 1.6 + 40));
      updateFrustum();
      rescaleForZoom();
    }

    applyDim();
    updatePathEdges();
    if (widenBtn) widenBtn.textContent = `Widen (${focusDepth})`;
    setStatus(`focused: ${pathReachable.size} reachable within ${focusDepth} hops`);
    scheduleLabelPick();
    markDirty();
    await inspect(id);
  }

  async function inspect(id) {
    const obj = await fetch(`/objects/${id}`).then((r) => r.json());
    rightRail.className = "rail";
    rightRail.innerHTML = Osiris.objectDetail(obj, "");
    // the inspector's own Focus button — one of the three ways to trigger a real focus
    // (ruling c5953bb1: double-click, Enter, or this button).
    const focusBtn = document.createElement("button");
    focusBtn.className = "iconbtn";
    focusBtn.textContent = pathFocusId === id ? "Focused" : "Focus";
    focusBtn.style.cssText = "margin-bottom:10px";
    focusBtn.addEventListener("click", () => focusObject(id));
    rightRail.prepend(focusBtn);
    // every object reference in the inspector (upstream_ids, readers, links) walks the
    // focus — ruling c5953bb1's own "harmony" requirement, part C, but the wiring lives
    // here since it's the same click-through this inspector has always used.
    const relsEl = rightRail.querySelector("[data-rels]");
    if (relsEl) await Osiris.loadRels(relsEl, id, (pickId) => focusObject(pickId), () => {});
  }

  // Enter focuses the currently selected node (ruling c5953bb1's own second trigger) —
  // guarded the same way console.js's own keydown handler guards Ctrl+K/Escape, never
  // firing while a real text field has focus.
  window.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter") return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (selectedId) focusObject(selectedId);
  });

  // ---- search (stays HTML) -----------------------------------------------------------
  let searchTimer = null, searchToken = 0;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (!q) { searchDd.style.display = "none"; return; }
    const myToken = ++searchToken;
    searchTimer = setTimeout(async () => {
      const res = await fetch(`/search?q=${encodeURIComponent(q)}&limit=8`).then((r) => r.json());
      if (myToken !== searchToken) return;
      const hits = Array.isArray(res.hits) ? res.hits : [];
      searchDd.innerHTML = "";
      searchDd.style.display = hits.length ? "block" : "none";
      for (const h of hits) {
        const row = document.createElement("div");
        row.className = "dd-item";
        row.textContent = h.label || h.id;
        row.addEventListener("click", async () => {
          searchDd.style.display = "none";
          searchInput.value = "";
          await focusObject(h.id); // zoom-to-fit reads the lit set's own bbox, no x/y needed here
        });
        searchDd.appendChild(row);
      }
    }, 200);
  });

  // ---- labels: WHICH ones (debounced, expensive nearest-N) vs WHERE they sit (every
  // frame, cheap) — the split that fixes the lag the operator caught. ------------------
  // "show node ids, agent names, project names... things that are short, let expansion
  // happen in the inspector" — resolve_label() already returns a short display name for
  // most types (Agent/SoftwareProject/etc), but for Decision/Thread it falls back to the
  // FULL summary text, which is exactly the "wall of garbage text" in the screenshot.
  // Never truncate-with-ellipsis (a chopped sentence still reads as garbage) — fall back
  // to a clean type + short id instead, the same shape a real "node id" reads as.
  const SHORT_LABEL_MAX = 26;
  function shortLabel(nd) {
    const label = nd.label || "";
    if (label && label.length <= SHORT_LABEL_MAX) return label;
    return `${nd.type} ${nd.id.slice(0, 8)}`;
  }

  const N_LABELS = 40;
  let labeledNodes = [];
  const labelDivs = new Map(); // node -> div, reused across frames instead of rebuilt
  let labelPickTimer = null;
  function scheduleLabelPick() {
    if (labelPickTimer) return;
    labelPickTimer = setTimeout(() => { labelPickTimer = null; pickLabels(); }, 150);
  }
  function pickLabels() {
    const cx = camera.position.x, cy = camera.position.y;
    const n = Math.max(10, Math.round(N_LABELS - (viewSize / 2000) * 30));
    const pool = pathFocusId ? idToNode.filter((nd) => pathReachable.has(nd.id)) : idToNode;
    labeledNodes = pool
      .map((nd) => ({ nd, d: (nd.x - cx) ** 2 + (nd.y - cy) ** 2 }))
      .sort((a, b) => a.d - b.d)
      .slice(0, n)
      .map((e) => e.nd);
    // reconcile DOM: remove divs for nodes no longer labeled, add for newly labeled ones —
    // reuses existing elements instead of an innerHTML rebuild every pick.
    const wanted = new Set(labeledNodes);
    for (const [nd, div] of labelDivs) {
      if (!wanted.has(nd)) { div.remove(); labelDivs.delete(nd); }
    }
    for (const nd of labeledNodes) {
      if (labelDivs.has(nd)) continue;
      const div = document.createElement("div");
      div.className = "lbl";
      div.textContent = shortLabel(nd);
      labelsEl.appendChild(div);
      labelDivs.set(nd, div);
    }
    markDirty(); // newly (un)labeled divs need one more positionLabels() pass to place them
  }
  // runs on every render (render-on-demand now, not an unconditional per-frame loop — see
  // below) — cheap (one project() + style write per already-chosen label, no sort, no DOM
  // create/destroy) so labels track the scene with zero perceptible lag whenever it fires.
  // DECLUTTER: "present text without it looking like garbage" — labeledNodes is already
  // nearest-to-camera-first (from pickLabels' own sort), so a plain greedy pass — show a
  // label unless its screen box would overlap one already placed this frame — keeps the
  // closest/most-relevant labels and silently drops the rest, rather than stacking dozens
  // of overlapping strings into an unreadable wall of text.
  const _v = new THREE.Vector3();
  const _placed = []; // [x0,y0,x1,y1] boxes already shown this frame
  const LABEL_W = 90, LABEL_H = 16, LABEL_GAP = 4;
  function overlapsPlaced(x, y) {
    const x0 = x - LABEL_W / 2, x1 = x + LABEL_W / 2, y0 = y - LABEL_H, y1 = y;
    for (const b of _placed) {
      if (x0 < b[2] + LABEL_GAP && x1 > b[0] - LABEL_GAP && y0 < b[3] + LABEL_GAP && y1 > b[1] - LABEL_GAP) return true;
    }
    return false;
  }
  function positionLabels() {
    _placed.length = 0;
    for (const nd of labeledNodes) {
      const div = labelDivs.get(nd);
      if (!div) continue;
      _v.set(nd.x || 0, nd.y || 0, 0).project(camera);
      const x = (_v.x * 0.5 + 0.5) * wrap.clientWidth;
      const y = (-_v.y * 0.5 + 0.5) * wrap.clientHeight;
      const lit = nd.id === pathFocusId || pathReachable.has(nd.id) || nd.id === selectedId;
      // lit/focused labels always win their spot (never declutter the thing you asked to
      // see); ordinary labels yield to anything already placed.
      if (!lit && overlapsPlaced(x, y)) { div.hidden = true; continue; }
      div.hidden = false;
      div.style.left = `${x}px`;
      div.style.top = `${y}px`;
      div.className = "lbl" + (lit ? " lit" : "");
      _placed.push([x - LABEL_W / 2, y - LABEL_H, x + LABEL_W / 2, y]);
    }
  }

  // the render loop itself is defined above (markDirty/renderIfDirty, right after the
  // camera/resize setup) — render-on-demand per Thoth's own live measurement (mail 10581):
  // the old unconditional rAF-plus-50ms-fallback loop rendered forever regardless of
  // whether anything changed or the tab/surface was even visible, which is real waste this
  // fix removes rather than papering over.
  pickLabels();
  markDirty();

  const api = {
    focusObject, selectObject, clearFocus, inspect, pause, resume, goBack,
    get idToNode() { return idToNode; },
    get pathReachable() { return pathReachable; },
    get pathFocusId() { return pathFocusId; },
    get selectedId() { return selectedId; },
    camera, pickAt, mesh: () => mesh, worldPerPx, nodeRadiusPx, renderer,
    // debug/test hooks only (same convention as window.__space always being exposed) —
    // zoomAt bypasses the rAF-coalesced wheel path for direct exercise; forceRender skips
    // the dirty check for a synchronous frame.
    zoomAt, forceRender: () => { renderer.render(scene, camera); positionLabels(); },
  };
  window.__space = api; // kept for existing debugging/test scripts, same shape as before
  return api;
}
